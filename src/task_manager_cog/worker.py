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

A groomer's proposal for a task comes back through the API onto the same
queue, and is applied in line with everything else (groom.py):

    {"type": "task.groom-proposal", "version": 1, "payload": <GroomingProposal>}

So does an agent run's result, which is applied by results.py:

    {"type": "task.agent-result", "version": 1, "payload": <AgentRun>}

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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import (
    AgentRun,
    GroomingProposal,
    TaskAgentResultMessage,
    TaskCreateMessage,
    TaskCreateRequest,
    TaskGroomProposalMessage,
    TaskSweepMessage,
)
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.lambda_worker import (
    UnprocessableMessage,
    all_failed,
    deadline,
    process_records,
)
from mini_app_polis.lambda_worker import parse_message as parse_queue_message
from mini_app_polis.pipeline_status import post_run_finding, run_report
from mini_app_polis.sentry import init_sentry

from ._version import __version__
from .board import Board
from .compose import compose
from .groom import apply_proposal
from .ids import next_id, task_name
from .results import apply_result
from .sources import SOURCES
from .sweep import run_sweep

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "create-task"

#: How long before the function's timeout a run is stopped, leaving room for
#: the run report and the handler's return.
DEADLINE_MARGIN_SECONDS = 20

# At import, not per invocation: a Lambda container is reused across
# invocations, so this runs once per cold start.
init_sentry(REPO)


@dataclass(frozen=True)
class SweepJob:
    """A scheduled sweep of the board. It carries nothing."""


def parse_message(
    body: str,
) -> TaskCreateRequest | SweepJob | GroomingProposal | AgentRun:
    """The job a message carries. Raises UnprocessableMessage otherwise."""
    message = parse_queue_message(
        body,
        TaskCreateMessage,
        TaskSweepMessage,
        TaskGroomProposalMessage,
        TaskAgentResultMessage,
    )
    if isinstance(message, TaskSweepMessage):
        return SweepJob()
    if isinstance(message, TaskCreateMessage):
        if message.payload.source not in SOURCES:
            raise UnprocessableMessage(
                f"no handling for source {message.payload.source!r}"
            )
    return message.payload


def create_task(
    request: TaskCreateRequest,
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
) -> None:
    """Put the requested task on the board, once. Raises to be redelivered."""
    source = SOURCES[request.source]
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id, version=__version__) as report:
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
    if isinstance(request, GroomingProposal):
        log.info("worker: run %s applies grooming of %s", run_id, request.task_gid)
        apply_proposal(request, run_id=run_id, board_factory=board_factory)
        return
    if isinstance(request, AgentRun):
        log.info("worker: run %s applies agent run %s", run_id, request.id)
        apply_result(request, run_id=run_id, board_factory=board_factory)
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
            version=__version__,
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        log.exception("worker: could not report the unprocessable message")


def _source_label(body: str) -> str:
    """The message's source, for the timing line. Never raises."""
    try:
        job = parse_message(body)
        if isinstance(job, SweepJob):
            return "sweep"
        if isinstance(job, GroomingProposal):
            return "grooming"
        if isinstance(job, AgentRun):
            return "agent-result"
        return job.source
    except Exception:  # noqa: BLE001 — a label must not fail the run
        return "unreadable"


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job, and name the records that must come back.

    Never raises: an exception escaping here fails the whole batch, and
    reporting per record is correct at every batch size.
    """
    # Settings first, so a credential rotated in Doppler applies now rather
    # than at the next cold start. Without them nothing can run.
    try:
        load_secrets(refresh=True)
    except Exception:  # noqa: BLE001 — nothing can run without its settings
        log.exception("worker: could not refresh settings from SSM")
        return all_failed(event)

    def handle(record: dict[str, Any]) -> None:
        message_id = str(record.get("messageId") or "")
        body = record.get("body") or ""
        with timing.invocation(
            cog="task-manager", source=_source_label(body), attempt=_attempt(record)
        ) as timed:
            try:
                with deadline(context, margin_seconds=DEADLINE_MARGIN_SECONDS):
                    process_message(body, run_id=message_id)
            except UnprocessableMessage:
                timed.label(outcome="unprocessable")
                raise
            except BaseException:
                # Already reported by the run report on its way out.
                timed.label(outcome="failed")
                raise
            timed.label(outcome="ok")

    def on_unprocessable(exc: UnprocessableMessage, record: dict[str, Any]) -> None:
        # Once, on the first receive; the dead-letter queue has an alarm of
        # its own for where it ends up.
        if _attempt(record) in ("1", "?"):
            _report_unprocessable(exc, str(record.get("messageId") or ""))

    return process_records(event, handle, on_unprocessable=on_unprocessable)


def _attempt(record: dict[str, Any]) -> str:
    """Which receive of this message this is, or "?" when unknown."""
    return str((record.get("attributes") or {}).get("ApproximateReceiveCount", "?"))
