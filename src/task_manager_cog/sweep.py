"""The sweep: every task waiting to be worked, checked against the definition of ready.

A schedule puts one ``task.sweep`` message on this cog's queue every hour,
so a sweep runs in line with task creation and never beside it. A sweep
reads the board once, checks each open task in the grooming columns with
the linter (lint.py), and moves each task that passes to the ready column.
Nothing here calls a model: a task that is already ready costs nothing to
find, and a task that is not is only logged.

Each swept task's verdict is kept as its grooming record in the API
(records.py), written only when it changed. The record is what tells a later
sweep whether a task was edited since it was judged, and whether a task this
cog moved to Ready was sent back.

A task that is not ready, and whose content differs from what was last sent
for grooming, is sent to task-groomer-cog through the API: at most
``GROOM_DISPATCH_LIMIT`` a sweep, in board order, and only when
``GROOM_DISPATCH`` is on. The groomer's proposal comes back on this cog's
queue and is applied by groom.py. A job the API refuses is not marked as
sent, so the next sweep sends it again.

A task whose only problems are blockers that are not done is never sent:
grooming cannot finish another task. It waits, and moves to Ready on the
first sweep after its blockers are done.

Moving is a setting, ``GROOM_AUTO_READY``, off unless set, so the sweep can
run and say what it would move before it moves anything. The columns are
named, not configured by gid, so they read the same in Doppler as on the
board:

  GROOM_SECTIONS    the columns swept (default: Ideas, Refining, Blocked)
  READY_SECTION     the column a ready task moves to (default: Ready)
  GROOM_AUTO_READY  true to move ready tasks; anything else only reports
  GROOM_DISPATCH    true to send tasks for grooming, which is paid; off
                    unless set
  GROOM_DISPATCH_LIMIT  tasks sent a sweep (default: 5)

The board's template task says so in its name ("do not work") and is never
swept. Nor are tasks from a source the sources table marks unswept: the
problems the fleet detected (``CI-``, ``RUN-``, ``CONF-``, ``API-``) wait
for a person.

Run it locally, read-only, to see every task's verdict:

    doppler run -- uv run python -m task_manager_cog.sweep
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from mini_app_polis import logger as logger_mod
from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import (
    GroomingJobAccepted,
    GroomingJobRequest,
    GroomingRecord,
    GroomingRecordWrite,
    GroomingTask,
)
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import run_report

from .board import Board
from .lint import lint, mentioned_ids, only_blocked, task_id_of
from .records import answers, changed, fingerprint, needs_grooming, next_record
from .registry import load_repos
from .sources import UNSWEPT_PREFIXES

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "groom-sweep"

#: This cog's name to the API, which picks its key.
MACHINE_NAME = "task-manager-cog"

DEFAULT_GROOM_SECTIONS: tuple[str, ...] = ("Ideas", "Refining", "Blocked")
DEFAULT_READY_SECTION = "Ready"
DEFAULT_DISPATCH_LIMIT = 5

#: In the template task's name. A task saying this is never swept.
DO_NOT_WORK = "do not work"

#: The problem a task has while its grooming questions are unanswered. The
#: linter only sees the template, and a groomer that asked questions may
#: still have filled every field; a task waiting on its owner is not ready.
OPEN_QUESTIONS = "its grooming questions are not answered yet"

#: A minted id, ``<PREFIX>-<n>``.
_MINTED_ID = re.compile(r"([A-Z][A-Z0-9]*)-\d+")


class SweepMisconfigured(RuntimeError):
    """The board has no column by the configured ready name."""


class Records(Protocol):
    """Where grooming records are kept: the API, or a fake in a test."""

    def list_groomings(self) -> list[GroomingRecord]:
        """Every task's record."""
        ...

    def record_grooming(self, payload: GroomingRecordWrite) -> GroomingRecord:
        """Write one task's record."""
        ...

    def request_grooming(self, payload: GroomingJobRequest) -> GroomingJobAccepted:
        """Send one task to be groomed."""
        ...


@dataclass(frozen=True)
class Verdict:
    """One task, and every rule it fails. No problems means ready."""

    task: AsanaTaskRecord
    problems: tuple[str, ...]
    #: New, or edited since its record was written.
    changed: bool = True
    #: The owner's answers to the task's grooming questions, if it has any.
    replies: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """Whether the task meets the definition of ready."""
        return not self.problems


@dataclass
class SweepResult:
    """What one sweep found and did. The records it changed are not yet written."""

    verdicts: list[Verdict] = field(default_factory=list)
    moved: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks this cog moved to Ready that a person moved back.
    returned: list[AsanaTaskRecord] = field(default_factory=list)
    #: The records this sweep changed, to be written.
    writes: list[GroomingRecordWrite] = field(default_factory=list)
    #: Tasks sent for grooming.
    sent: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks that needed grooming and were not sent: over the limit, off,
    #: or refused.
    waiting: int = 0
    #: Tasks not ready only because a blocker is not done: never sent.
    blocked: int = 0

    @property
    def ready(self) -> list[Verdict]:
        """The verdicts of the tasks that are ready."""
        return [v for v in self.verdicts if v.ready]


def groom_sections() -> tuple[str, ...]:
    """The columns swept: ``GROOM_SECTIONS``, or Ideas, Refining and Blocked."""
    raw = os.environ.get("GROOM_SECTIONS") or ""
    names = tuple(n.strip() for n in raw.split(",") if n.strip())
    return names or DEFAULT_GROOM_SECTIONS


def ready_section() -> str:
    """The column a ready task moves to: ``READY_SECTION``, or Ready."""
    return (os.environ.get("READY_SECTION") or "").strip() or DEFAULT_READY_SECTION


def auto_ready() -> bool:
    """Whether ready tasks are moved (``GROOM_AUTO_READY``), not only reported."""
    return (os.environ.get("GROOM_AUTO_READY") or "").strip().lower() == "true"


def dispatch_limit() -> int:
    """How many tasks a sweep may send for grooming; 0 when sending is off."""
    if (os.environ.get("GROOM_DISPATCH") or "").strip().lower() != "true":
        return 0
    raw = (os.environ.get("GROOM_DISPATCH_LIMIT") or "").strip()
    return max(0, int(raw)) if raw else DEFAULT_DISPATCH_LIMIT


def _prefix_of(task_id: str | None) -> str | None:
    """``CI`` for ``CI-4``; None for an issue's ``repo#n`` or no id at all."""
    match = _MINTED_ID.fullmatch(task_id or "")
    return match[1] if match else None


def assess(
    tasks: list[AsanaTaskRecord],
    *,
    swept: Collection[str],
    repos: Collection[str],
    records: Mapping[str, GroomingRecord] | None = None,
    replies: Mapping[str, tuple[str, ...]] | None = None,
) -> list[Verdict]:
    """A verdict for every open task in a swept column, in board order.

    A task whose last grooming asked questions is not ready until a
    grooming answers them, whatever the linter says.
    """
    replies = replies or {}
    records = records or {}
    swept_lower = {name.lower() for name in swept}
    completed_by_id = {
        task_id: task.completed
        for task in tasks
        if (task_id := task_id_of(task.name)) is not None
    }
    verdicts = []
    for task in tasks:
        if task.completed or DO_NOT_WORK in task.name.lower():
            continue
        if _prefix_of(task_id_of(task.name)) in UNSWEPT_PREFIXES:
            continue
        if (task.section_name or "").lower() not in swept_lower:
            continue
        problems = lint(
            name=task.name,
            notes=task.notes,
            tags=task.tag_names,
            repos=repos,
            completed_by_id=completed_by_id,
        )
        previous = records.get(task.gid)
        if previous is not None and previous.outcome == "questions":
            problems.append(OPEN_QUESTIONS)
        verdicts.append(
            Verdict(
                task=task,
                problems=tuple(problems),
                changed=changed(task, records, replies.get(task.gid, ())),
                replies=replies.get(task.gid, ()),
            )
        )
    return verdicts


def _section_named(sections: list[AsanaSection], name: str) -> AsanaSection:
    for section in sections:
        if section.name.lower() == name.lower():
            return section
    raise SweepMisconfigured(
        f"the board has no {name!r} column (see its *_SECTION setting); "
        f"it has {', '.join(s.name for s in sections)}"
    )


#: How a done task the groomed task mentions is marked in the board list.
DONE_MARK = " (done)"


def grooming_job(
    verdict: Verdict,
    board_names: list[str],
    done_by_id: Mapping[str, str] | None = None,
) -> GroomingJobRequest:
    """What the groomer is sent for one task.

    ``board_names`` are the open tasks. ``done_by_id`` maps each completed
    task's id to its name; the ones this task mentions are listed too,
    marked :data:`DONE_MARK`, so a reference to finished work reads as
    finished rather than missing. Done tasks it does not mention are left
    out: they are only noise to the groomer.
    """
    task = verdict.task
    text = "\n".join([task.name, task.notes, *verdict.replies])
    done = [
        f"{done_by_id[task_id]}{DONE_MARK}"
        for task_id in mentioned_ids(text)
        if done_by_id and task_id in done_by_id and task_id != task_id_of(task.name)
    ]
    others = [name for name in board_names if name != task.name]
    return GroomingJobRequest(
        task=GroomingTask(
            task_gid=task.gid,
            task_id=task_id_of(task.name),
            name=task.name,
            notes=task.notes,
            tags=list(task.tag_names),
            section=task.section_name,
            fingerprint=fingerprint(task, verdict.replies),
            # The open-questions marker is the sweep's, not the linter's: the
            # groomer is given the answers instead.
            problems=[p for p in verdict.problems if p != OPEN_QUESTIONS],
            comments=list(verdict.replies),
        ),
        board=(done + others)[:500],
    )


def sweep(
    board: Board,
    *,
    repos: Collection[str],
    move: bool,
    records: Mapping[str, GroomingRecord] | None = None,
    send: Callable[[GroomingJobRequest], bool] | None = None,
    limit: int = 0,
) -> SweepResult:
    """Check the board, and move what is ready when ``move`` is set.

    ``records`` are the tasks' grooming records by task gid. The records
    this sweep changes are returned in ``writes``, not written: writing is
    the caller's. ``send`` sends one task for grooming and says whether it
    landed; at most ``limit`` are sent.
    """
    records = records or {}
    sections = board.sections()
    ready = _section_named(sections, ready_section())
    tasks = board.tasks()
    open_gids = {t.gid for t in tasks if not t.completed}
    # Only a task with questions out has answers to read: one request each.
    replies = {
        gid: tuple(answers(board.comments(gid)))
        for gid, record in records.items()
        if record.outcome == "questions" and gid in open_gids
    }
    result = SweepResult(
        verdicts=assess(
            tasks,
            swept=groom_sections(),
            repos=repos,
            records=records,
            replies=replies,
        )
    )
    open_names = [t.name for t in tasks if not t.completed]
    done_by_id = {
        task_id: t.name
        for t in tasks
        if t.completed and (task_id := task_id_of(t.name)) is not None
    }
    for verdict in result.verdicts:
        task = verdict.task
        previous = records.get(task.gid)
        if previous is not None and previous.moved_to_ready_at is not None:
            result.returned.append(task)
            log.info("sweep: %s was sent back from Ready", task.name)

        moved_at: dt.datetime | None = None
        if not verdict.ready:
            log.info("sweep: %s not ready: %s", task.name, "; ".join(verdict.problems))
        elif move:
            board.move(task.gid, ready.gid)
            moved_at = dt.datetime.now(dt.UTC)
            result.moved.append(task)
            log.info("sweep: %s moved to %s", task.name, ready.name)
        else:
            log.info("sweep: %s is ready (GROOM_AUTO_READY is off)", task.name)

        sent = False
        if only_blocked(verdict.problems):
            result.blocked += 1
        elif not verdict.ready and needs_grooming(task, previous, verdict.replies):
            if send is not None and len(result.sent) < limit:
                sent = send(grooming_job(verdict, open_names, done_by_id))
            if sent:
                result.sent.append(task)
                log.info("sweep: %s sent for grooming", task.name)
            else:
                result.waiting += 1

        record = next_record(
            task,
            verdict.problems,
            previous,
            moved_at=moved_at,
            replies=verdict.replies,
            sent=sent,
        )
        if record is not None:
            result.writes.append(record)
    return result


def _api() -> Records:
    return KaianoApiClient.from_env(MACHINE_NAME)


def run_sweep(
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
    repos_loader: Callable[[], Collection[str]] = load_repos,
    records_factory: Callable[[], Records] = _api,
) -> None:
    """One scheduled sweep, with its run report. Raises to be redelivered."""
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id) as report:
        if not effect_enabled(Effect.ASANA):
            # One board, and no development copy of it.
            log.info("sweep: board writes are off here; nothing swept")
            report.note("board writes off outside production")
            return

        board = (board_factory or Board.from_env)()
        store = records_factory()
        records = {r.task_gid: r for r in store.list_groomings()}

        def send(job: GroomingJobRequest) -> bool:
            try:
                store.request_grooming(job)
            except Exception as exc:  # noqa: BLE001 — sent again next sweep
                log.warning("sweep: %s not sent: %s", job.task.name, exc)
                report.issue("grooming not sent", job.task.task_id or job.task.task_gid)
                return False
            return True

        result = sweep(
            board,
            repos=repos_loader(),
            move=auto_ready(),
            records=records,
            send=send,
            limit=dispatch_limit(),
        )
        for record in result.writes:
            store.record_grooming(record)

        report.count("tasks_checked", len(result.verdicts))
        report.count("not_ready", len(result.verdicts) - len(result.ready))
        report.count("changed", sum(v.changed for v in result.verdicts))
        report.count("records_written", len(result.writes))
        report.count("waiting_for_grooming", result.waiting)
        report.count("waiting_on_blockers", result.blocked)
        for task in result.sent:
            report.updated(
                "task sent for grooming", task.name, link=board.url(task.gid)
            )
        if not auto_ready():
            report.count("ready_not_moved", len(result.ready))
        for task in result.moved:
            report.updated("task moved to Ready", task.name, link=board.url(task.gid))
        for task in result.returned:
            report.updated(
                "task sent back from Ready", task.name, link=board.url(task.gid)
            )


def main(argv: list[str] | None = None) -> int:
    """Log every swept task's verdict. Reads only; never moves anything."""
    del argv
    board = Board.from_env()
    result = sweep(board, repos=load_repos(), move=False)
    for verdict in result.verdicts:
        mark = "READY" if verdict.ready else "     "
        log.info("%s  [%s] %s", mark, verdict.task.section_name, verdict.task.name)
        for problem in verdict.problems:
            log.info("         - %s", problem)
    log.info(
        "%d of %d tasks ready in %s.",
        len(result.ready),
        len(result.verdicts),
        ", ".join(groom_sections()),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
