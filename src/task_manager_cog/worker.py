"""The Lambda entrypoint: one queue message in, at most one task out, or a sweep.

task-manager-cog runs on Lambda behind its own FIFO queue,
``task-manager-jobs.fifo``, with one message group, so jobs run one at a
time. That is what makes minting safe: two jobs can never both read the
board's highest id and take the same next one.

The API is the only producer. A source asks it for a task through
``POST /v1/tasks``, and it enqueues one message:

    {"type": "task.create", "version": 1,
     "payload": <TaskCreateRequest>}

The payload is the request as the API validated it, so it is validated here
against the same model from the shared contract.

The one other producer is this cog's own schedule, which puts a sweep on the
same queue every hour (sweep.py):

    {"type": "task.sweep", "version": 1}

On the queue rather than invoking the function directly, so a sweep never
runs beside a job that is creating a task.

**A duplicate request makes no second task.** The source's ``external_id`` is stored on
the task, and looked up before anything is composed or minted. A job that
created its task and then failed (a timeout on the response, a lost report)
finds that task when it comes back, and stops.

**Nothing is deleted until the work is done.** The event source mapping
deletes every record the handler does not name in ``batchItemFailures``, so
every failure below appends to that list.

**An unrecognised message is a producer bug.** The queue is this cog's
alone, so a message it cannot read was enqueued wrongly. It is reported
once, exhausts its receives and lands in the dead-letter queue where
someone can see it, rather than being guessed at or dropped.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import TaskCreateRequest
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import post_run_finding, run_report
from mini_app_polis.sentry import init_sentry
from pydantic import ValidationError

from ._deadline import deadline
from .board import Board
from .compose import compose
from .ids import next_id, task_name
from .sources import SOURCES
from .sweep import run_sweep

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "create-task"

#: Must match api-kaianolevine-com's task dispatch. A mismatch is a message
#: this consumer refuses rather than misreads.
MESSAGE_VERSION = 1
TYPE_CREATE = "task.create"
TYPE_SWEEP = "task.sweep"

# At import, not per invocation: a Lambda container is reused across
# invocations, so this runs once per cold start.
init_sentry(REPO)


class UnprocessableMessage(RuntimeError):
    """The message cannot be handled by this consumer, ever."""


@dataclass(frozen=True)
class SweepJob:
    """A scheduled sweep of the board. It carries nothing."""


def parse_message(body: str) -> TaskCreateRequest | SweepJob:
    """The job a message carries. Raises UnprocessableMessage otherwise."""
    try:
        message = json.loads(body)
    except ValueError as exc:
        raise UnprocessableMessage(f"body is not JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise UnprocessableMessage("body is not an object")
    if message.get("version") != MESSAGE_VERSION:
        raise UnprocessableMessage(
            f"message version {message.get('version')!r}, "
            f"this consumer speaks {MESSAGE_VERSION}"
        )
    if message.get("type") == TYPE_SWEEP:
        return SweepJob()
    if message.get("type") != TYPE_CREATE:
        raise UnprocessableMessage(f"unknown message type {message.get('type')!r}")
    try:
        request = TaskCreateRequest.model_validate(message.get("payload"))
    except ValidationError as exc:
        raise UnprocessableMessage(f"payload is not a task request: {exc}") from exc
    if request.source not in SOURCES:
        raise UnprocessableMessage(f"no handling for source {request.source!r}")
    return request


def create_task(
    request: TaskCreateRequest,
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
) -> None:
    """Put the requested task on the board, once. Raises to be redelivered."""
    source = SOURCES[request.source]
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id) as report:
        task = compose(request.draft)

        if not effect_enabled(Effect.ASANA):
            # One board, and no development copy of it.
            log.info(
                "worker: board writes are off here; would create a %s task %r for %s",
                request.source,
                task.title,
                request.external_id,
            )
            report.note("board writes off outside production", request.external_id)
            return

        board = (board_factory or Board.from_env)()
        existing = board.find(request.external_id)
        if existing is not None:
            log.info("worker: %s is already task %s", request.external_id, existing)
            report.note("already on the board", request.external_id)
            return

        if source.prefix is None:
            # The contract requires an id for every source without a prefix.
            task_id = request.id or ""
        else:
            task_id = next_id(source.prefix, board.names())
        name = task_name(task_id, task.title)

        gid = board.create(name, task, external_id=request.external_id)
        log.info("worker: created %s as task %s", name, gid)
        report.created("asana task", name, link=board.url(gid))


def process_message(
    body: str,
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
) -> None:
    """Handle one message. Raises if it must be redelivered."""
    request = parse_message(body)
    if isinstance(request, SweepJob):
        log.info("worker: run %s sweep", run_id)
        run_sweep(run_id=run_id, board_factory=board_factory)
        return
    log.info(
        "worker: run %s source=%s external_id=%s",
        run_id,
        request.source,
        request.external_id,
    )
    create_task(request, run_id=run_id, board_factory=board_factory)


def _report_unprocessable(exc: BaseException, run_id: str) -> None:
    """Say a message could not be read. Only this case: no run reported it."""
    try:
        post_run_finding(
            FLOW_NAME,
            "ERROR",
            f"an unprocessable message failed: {type(exc).__name__}: {exc}",
            repo=REPO,
            source="queue_consumer",
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        log.exception("worker: could not report the unprocessable message")


def _source_label(body: str) -> str:
    """The message's source, for the timing line. Never raises."""
    try:
        job = parse_message(body)
        return "sweep" if isinstance(job, SweepJob) else job.source
    except Exception:  # noqa: BLE001 — a label must not fail the run
        return "unreadable"


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job, and name the records that must come back.

    Never raises: an exception escaping here fails the whole batch, and
    reporting per record is correct at every batch size.
    """
    records = event.get("Records", []) if isinstance(event, dict) else []

    # Settings first, so a credential rotated in Doppler applies now rather
    # than at the next cold start. Without them nothing can run.
    try:
        load_secrets(refresh=True)
    except Exception:  # noqa: BLE001 — nothing can run without its settings
        log.exception("worker: could not refresh settings from SSM")
        return {
            "batchItemFailures": [
                {"itemIdentifier": str(r.get("messageId") or "")} for r in records
            ]
        }

    failures: list[dict[str, str]] = []
    for record in records:
        message_id = str(record.get("messageId") or "")
        body = record.get("body") or ""
        attempt = (record.get("attributes") or {}).get("ApproximateReceiveCount", "?")

        with timing.invocation(
            cog="task-manager", source=_source_label(body), attempt=attempt
        ) as timed:
            try:
                with deadline(context):
                    process_message(body, run_id=message_id)
            except UnprocessableMessage as exc:
                timed.label(outcome="unprocessable")
                log.error(
                    "worker: unprocessable message (attempt %s): %s", attempt, exc
                )
                # Once, on the first receive; the dead-letter queue has an
                # alarm of its own for where it ends up.
                if attempt in ("1", "?"):
                    _report_unprocessable(exc, message_id)
                failures.append({"itemIdentifier": message_id})
            except Exception:  # noqa: BLE001 — every failure is a retry
                timed.label(outcome="failed")
                # Already reported by the run report on its way out.
                log.exception("worker: run failed (attempt %s)", attempt)
                failures.append({"itemIdentifier": message_id})
            else:
                timed.label(outcome="ok")

    return {"batchItemFailures": failures}
