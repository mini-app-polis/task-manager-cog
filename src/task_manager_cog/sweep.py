"""The sweep: every task waiting to be worked, checked against the definition of ready.

A schedule puts one ``task.sweep`` message on this cog's queue every half hour,
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

A task whose only problems are blockers that are not done, or a date in
Blocked by still ahead, is never sent: grooming cannot finish another task
or bring a date closer. It waits in the blocked column, moved there from
the column it was in, and moves to Ready on the first sweep after its
blockers are done and on or after its date (a UTC day). Moving it changes
nothing in its content, so it is not sent for grooming either way.

The ready column is linted too, so a task put there by hand, or edited
after it got there, is checked again. One that passes is left alone. One
that fails moves to the refining column with one comment listing its
problems, or to the blocked column when blockers are its only problem, and
counts as sent back from Ready. Moving it back does not send it for
grooming: it waits for a person to change it. The agent dispatch at the end
of the sweep never takes a task the sweep found failing in Ready.

A task that is not ready, has no grooming questions out, is not waiting
only on blockers, and is not sent because it is unchanged since it was last
groomed is held with nobody asked: nothing will happen to it until a person
changes it. The sweep leaves it as it is and counts it in the run report
(``held_without_questions``). Its record carries ``HELD`` among its
problems, so the record changes, and is written, on the one sweep that
finds it newly held; that sweep names it as a notice, which does not make
the run WARN. A task that stays held is counted, not named again.

Moving, to Ready, back from it, or to the blocked column, is a setting,
``GROOM_AUTO_READY``, off unless set, so the sweep can run and say what it
would move before it moves anything. The columns are named, not configured
by gid, so they read the same in Doppler as on the board:

  GROOM_SECTIONS    the columns swept (default: Ideas, Refining, Blocked)
  READY_SECTION     the column a ready task moves to, and is checked in
                    again (default: Ready)
  REFINING_SECTION  the column a task failing in Ready moves back to
                    (default: Refining)
  BLOCKED_SECTION   the column a task waiting on blockers moves to
                    (default: Blocked)
  GROOM_AUTO_READY  true to move tasks; anything else only reports
  GROOM_DISPATCH    true to send tasks for grooming, which is paid; off
                    unless set
  GROOM_DISPATCH_LIMIT  tasks sent a sweep (default: 5)
  GROOM_TIMES       the times of day tasks are sent at, comma-separated
                    HH:MM (e.g. 04:15,05:15); every sweep unless set
  SWEEP_TIMEZONE    the time zone GROOM_TIMES and AGENT_TIMES are read in
                    (default: UTC)

A sweep is the one for a time in GROOM_TIMES or AGENT_TIMES when it starts
within half an hour after it, the interval the schedule runs the sweep at.
  GROOM_TASK_SHAPES true to hold tasks to the leaf or compound shape
                    (lint.py); off unless set

The board's template task says so in its name ("do not work") and is never
swept. Nor are tasks from a source the sources table marks unswept: the
problems the fleet detected (``CI-``, ``RUN-``, ``CONF-``, ``API-``) wait
for a person.

Every sweep also reads the Review column: it posts ``Landed:`` on each task
whose pull request is merged with CI on dev green (landed.py), then marks
complete each task whose latest comment starts with ``Landed:`` (review.py).

After grooming, the sweep dispatches an agent for each Ready task it can
take, when ``AGENT_DISPATCH`` or ``AGENT_DRY_RUN`` is on (dispatch.py), and
only in the sweeps for ``AGENT_TIMES`` when that is set.

A sweep asked for on demand (``POST /v1/tasks/sweeps``) may carry ``groom``,
which makes it the sweep for ``GROOM_TIMES``, and ``agents``, which makes it
the one for ``AGENT_TIMES``, whatever the time. Nothing else changes:
``GROOM_DISPATCH``, ``GROOM_DISPATCH_LIMIT``, ``AGENT_DISPATCH``,
``AGENT_DRY_RUN`` and the agent's caps apply as they do to any sweep. Its
run report says it was requested on demand, and for which.

Run it locally, read-only, to see every task's verdict:

    doppler run --config prd -- uv run python -m task_manager_cog.sweep
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from mini_app_polis import logger as logger_mod
from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import (
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentSpend,
    GroomingJobAccepted,
    GroomingJobRequest,
    GroomingRecord,
    GroomingRecordWrite,
    GroomingReturnRate,
    GroomingTask,
)
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import run_report

from . import dispatch as agent
from . import executor, landed, review
from ._version import __version__
from .board import Board
from .github import AgentApp
from .lint import lint, mentioned_ids, only_blocked, task_id_of
from .records import (
    answers,
    awaiting_grooming,
    changed,
    fingerprint,
    needs_grooming,
    next_record,
    sent_back_record,
    unanswered,
)
from .registry import load_repos
from .sources import UNSWEPT_PREFIXES

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "groom-sweep"

#: This cog's name to the API, which picks its key.
MACHINE_NAME = "task-manager-cog"

DEFAULT_GROOM_SECTIONS: tuple[str, ...] = ("Ideas", "Refining", "Blocked")
DEFAULT_READY_SECTION = "Ready"
DEFAULT_REFINING_SECTION = "Refining"
DEFAULT_BLOCKED_SECTION = "Blocked"
DEFAULT_DISPATCH_LIMIT = 5
DEFAULT_SWEEP_TIMEZONE = "UTC"

#: How often the schedule runs a sweep: a sweep is the one for a time in
#: GROOM_TIMES or AGENT_TIMES when it starts within this after it.
SWEEP_INTERVAL = dt.timedelta(minutes=30)

#: Starts the comment on a task the sweep moved back out of Ready.
SENT_BACK_PREFIX = "Sent back from Ready:"

#: In the template task's name. A task saying this is never swept.
DO_NOT_WORK = "do not work"

#: The problem a task has while its grooming questions are unanswered. The
#: linter only sees the template, and a groomer that asked questions may
#: still have filled every field; a task waiting on its owner is not ready.
OPEN_QUESTIONS = "its grooming questions are not answered yet"
#: Kept among a held task's recorded problems, so its record changes on the
#: sweep that first finds it held and on no other.
HELD = "held: groomed as it reads, and nobody is asked"

#: The run report's sections: what the sweep moved or sent, what it found on
#: the board, and what the agent dispatch did.
MOVED = "Moved"
BOARD = "Board"
AGENT = "Agent"

#: The notice a sweep asked for on demand opens with, naming what it was
#: asked to do whatever the time (``groom``, ``agents``).
ON_DEMAND = "sweep requested on demand"

#: Each Board counter's words in the report. The counter names posted to
#: the API are the keys; only these labels are what a reader sees.
BOARD_LABELS: dict[str, str] = {
    "tasks_checked": "tasks checked",
    "not_ready": "not ready",
    "changed": "changed since they were last judged",
    "records_written": "grooming records written",
    "waiting_for_grooming": "waiting to be sent for grooming",
    "waiting_on_blockers": "waiting on blockers",
    "waiting_on_owner": "waiting on the owner's answers",
    "awaiting_grooming": "sent for grooming, not back yet",
    "held_without_questions": "held without questions",
    "sent_back_from_ready": "sent back from Ready",
    "ready_not_moved": "ready, not moved (GROOM_AUTO_READY is off)",
    "blocked_not_moved": "waiting on blockers, not moved (GROOM_AUTO_READY is off)",
    "failing_in_ready_not_moved": "failing in Ready, not moved (GROOM_AUTO_READY is off)",
    "review_landed": "Review tasks given Landed:, merged with CI on dev green",
    "review_closed": "Review tasks closed, work landed",
}

#: The window the return-from-Ready ratio is read over.
RETURN_RATE_DAYS = 30
RETURN_RATE = "returned_from_ready_30_days"

#: A minted id, ``<PREFIX>-<n>``.
_MINTED_ID = re.compile(r"([A-Z][A-Z0-9]*)-\d+")


class SweepMisconfigured(RuntimeError):
    """The board has no column by the configured ready name."""


class Records(Protocol):
    """Where grooming and agent-run records are kept: the API, or a fake."""

    def list_groomings(self) -> list[GroomingRecord]:
        """Every task's record."""
        ...

    def record_grooming(self, payload: GroomingRecordWrite) -> GroomingRecord:
        """Write one task's record."""
        ...

    def request_grooming(self, payload: GroomingJobRequest) -> GroomingJobAccepted:
        """Send one task to be groomed."""
        ...

    def record_agent_run(self, payload: AgentRunCreate) -> AgentRun:
        """Record an agent run about to be started."""
        ...

    def list_agent_runs(
        self, *, status: str | None = None, repository: str | None = None
    ) -> list[AgentRun]:
        """Agent runs, narrowed by status and repository."""
        ...

    def agent_spend(self) -> AgentSpend:
        """This month's agent runs."""
        ...

    def fail_agent_run(self, payload: AgentRunFailure) -> AgentRun:
        """Mark a dispatched agent run failed."""
        ...

    def grooming_return_rate(self, days: int | None = None) -> GroomingReturnRate:
        """How often a task moved to Ready came back out, over ``days``."""
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
    #: Ready but for a "Grooming questions:" comment nothing answers yet.
    #: Such a task waits for its owner and is not sent for grooming.
    awaiting_owner: bool = False

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
    #: Tasks held back by a "Grooming questions:" comment nothing answers.
    awaiting_owner: int = 0
    #: Tasks sent for grooming whose grooming has not come back yet.
    awaiting_grooming: int = 0
    #: Tasks not ready only because a blocker is not done: never sent.
    blocked: int = 0
    #: Tasks not ready, unchanged since they were groomed, and with no
    #: questions out: nobody is asked about them, and nothing is done.
    held: list[AsanaTaskRecord] = field(default_factory=list)
    #: Of those, the ones this sweep found held for the first time.
    newly_held: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks waiting on blockers that were moved to the blocked column.
    moved_to_blocked: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks waiting on blockers that would have been moved there, had
    #: moving been on.
    blocked_not_moved: int = 0
    #: Tasks in Ready that no longer pass, moved or not.
    failed_ready: list[AsanaTaskRecord] = field(default_factory=list)
    #: Of those, the ones moved back, with the column each went to.
    sent_back: list[tuple[AsanaTaskRecord, str]] = field(default_factory=list)

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


def refining_section() -> str:
    """Where a task failing in Ready goes back to: ``REFINING_SECTION``."""
    return (
        os.environ.get("REFINING_SECTION") or ""
    ).strip() or DEFAULT_REFINING_SECTION


def blocked_section() -> str:
    """Where a task waits on its blockers: ``BLOCKED_SECTION``, or Blocked."""
    return (os.environ.get("BLOCKED_SECTION") or "").strip() or DEFAULT_BLOCKED_SECTION


def auto_ready() -> bool:
    """Whether ready tasks are moved (``GROOM_AUTO_READY``), not only reported."""
    return (os.environ.get("GROOM_AUTO_READY") or "").strip().lower() == "true"


def task_shapes() -> bool:
    """Whether a task must be a leaf or a compound task (``GROOM_TASK_SHAPES``)."""
    return (os.environ.get("GROOM_TASK_SHAPES") or "").strip().lower() == "true"


def sweep_timezone() -> ZoneInfo:
    """The time zone times of day are read in: ``SWEEP_TIMEZONE``, or UTC."""
    name = (os.environ.get("SWEEP_TIMEZONE") or "").strip()
    return ZoneInfo(name or DEFAULT_SWEEP_TIMEZONE)


def times_of_day(name: str) -> tuple[dt.time, ...] | None:
    """The comma-separated HH:MM times in setting ``name``; None when unset."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    return tuple(dt.time.fromisoformat(t.strip()) for t in raw.split(",") if t.strip())


def due(name: str, now: dt.datetime | None = None) -> bool:
    """Whether the sweep at ``now`` is the one for a time in setting ``name``.

    It is when it starts within ``SWEEP_INTERVAL`` after one of the times,
    read in ``SWEEP_TIMEZONE``. Every sweep is when the setting is unset.
    """
    times = times_of_day(name)
    if times is None:
        return True
    local = (now or dt.datetime.now(dt.UTC)).astimezone(sweep_timezone())
    for time in times:
        start = local.replace(
            hour=time.hour, minute=time.minute, second=0, microsecond=0
        )
        # A time late in the evening is due just after midnight too.
        for at in (start, start - dt.timedelta(days=1)):
            if at <= local < at + SWEEP_INTERVAL:
                return True
    return False


def dispatch_limit(now: dt.datetime | None = None, *, on_demand: bool = False) -> int:
    """How many tasks a sweep may send for grooming.

    0 when sending is off, or when the sweep is not one for ``GROOM_TIMES``.
    ``on_demand`` makes it one whatever the time; sending must still be on,
    and the limit is the same.
    """
    if (os.environ.get("GROOM_DISPATCH") or "").strip().lower() != "true":
        return 0
    if not on_demand and not due("GROOM_TIMES", now):
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
    comments: Callable[[str], list[str]] | None = None,
) -> list[Verdict]:
    """A verdict for every open task in a swept column, in board order.

    A task whose last grooming asked questions is not ready until a
    grooming answers them, whatever the linter says. So is a task that
    would otherwise be ready while a "Grooming questions:" comment on it,
    from anyone, has nothing after it; ``comments`` reads a task's comments,
    and is called only for such a task.
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
            shapes=task_shapes(),
        )
        previous = records.get(task.gid)
        awaiting_owner = False
        if previous is not None and previous.outcome == "questions":
            problems.append(OPEN_QUESTIONS)
        elif not problems and comments is not None and unanswered(comments(task.gid)):
            problems.append(OPEN_QUESTIONS)
            awaiting_owner = True
        verdicts.append(
            Verdict(
                task=task,
                problems=tuple(problems),
                changed=changed(task, records, replies.get(task.gid, ())),
                replies=replies.get(task.gid, ()),
                awaiting_owner=awaiting_owner,
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


def sent_back_comment(problems: Collection[str]) -> str:
    """The one comment on a task moved back out of Ready: what it fails."""
    lines = "\n".join(f"- {p}" for p in problems)
    return (
        f"{SENT_BACK_PREFIX} this task no longer meets the definition of "
        f"ready.\n{lines}"
    )


def _already_said(comments: list[str], text: str) -> bool:
    """Whether the last sent-back comment on a task is this one."""
    said = [c for c in comments if c.startswith(SENT_BACK_PREFIX)]
    return bool(said) and said[-1] == text


def sweep(
    board: Board,
    *,
    repos: Collection[str],
    move: bool,
    records: Mapping[str, GroomingRecord] | None = None,
    send: Callable[[GroomingJobRequest], bool] | None = None,
    limit: int = 0,
    now: dt.datetime | None = None,
) -> SweepResult:
    """Check the board, and move what is ready when ``move`` is set.

    ``records`` are the tasks' grooming records by task gid. The records
    this sweep changes are returned in ``writes``, not written: writing is
    the caller's. ``send`` sends one task for grooming and says whether it
    landed; at most ``limit`` are sent. ``now`` is when the sweep runs.
    """
    records = records or {}
    now = now or dt.datetime.now(dt.UTC)
    sections = board.sections()
    ready = _section_named(sections, ready_section())
    blocked: AsanaSection | None = None
    refining: AsanaSection | None = None
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
            swept=(*groom_sections(), ready.name),
            repos=repos,
            records=records,
            replies=replies,
            comments=board.comments,
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
        if task.section_gid == ready.gid:
            # Already in Ready: a pass leaves it, and its record, alone.
            if verdict.ready:
                continue
            result.failed_ready.append(task)
            if not move:
                log.info(
                    "sweep: %s in %s no longer passes (GROOM_AUTO_READY is off): %s",
                    task.name,
                    ready.name,
                    "; ".join(verdict.problems),
                )
                continue
            if only_blocked(verdict.problems):
                blocked = blocked or _section_named(sections, blocked_section())
                target = blocked
                board.move(task.gid, target.gid)
            else:
                refining = refining or _section_named(sections, refining_section())
                target = refining
                board.move(task.gid, target.gid)
                text = sent_back_comment(verdict.problems)
                if not _already_said(board.comments(task.gid), text):
                    board.comment(task.gid, text)
            result.sent_back.append((task, target.name))
            result.writes.append(
                sent_back_record(
                    task, verdict.problems, previous, replies=verdict.replies
                )
            )
            log.info(
                "sweep: %s sent back from %s to %s: %s",
                task.name,
                ready.name,
                target.name,
                "; ".join(verdict.problems),
            )
            continue

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
        held = False
        if only_blocked(verdict.problems):
            result.blocked += 1
            blocked = blocked or _section_named(sections, blocked_section())
            if task.section_gid == blocked.gid:
                log.info("sweep: %s waits on blockers", task.name)
            elif move:
                board.move(task.gid, blocked.gid)
                result.moved_to_blocked.append(task)
                log.info("sweep: %s moved to %s", task.name, blocked.name)
            else:
                result.blocked_not_moved += 1
                log.info(
                    "sweep: %s waits on blockers (GROOM_AUTO_READY is off)", task.name
                )
        elif verdict.awaiting_owner:
            result.awaiting_owner += 1
            log.info("sweep: %s waits on its owner's answers", task.name)
        elif not verdict.ready and needs_grooming(task, previous, verdict.replies, now):
            if send is not None and len(result.sent) < limit:
                sent = send(grooming_job(verdict, open_names, done_by_id))
            if sent:
                result.sent.append(task)
                log.info("sweep: %s sent for grooming", task.name)
            else:
                result.waiting += 1
        elif not verdict.ready and awaiting_grooming(
            task, previous, verdict.replies, now
        ):
            result.awaiting_grooming += 1
            log.info("sweep: %s waits for its grooming to come back", task.name)
        elif not verdict.ready and OPEN_QUESTIONS not in verdict.problems:
            held = True
            result.held.append(task)
            log.info("sweep: %s held, groomed as it reads; nobody is asked", task.name)

        record = next_record(
            task,
            (*verdict.problems, HELD) if held else verdict.problems,
            previous,
            moved_at=moved_at,
            replies=verdict.replies,
            sent=sent,
            now=now,
        )
        if record is not None:
            result.writes.append(record)
            if held:
                result.newly_held.append(task)
    return result


def _api() -> Records:
    return KaianoApiClient.from_env(MACHINE_NAME)


def run_sweep(
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
    repos_loader: Callable[[], Collection[str]] = load_repos,
    records_factory: Callable[[], Records] = _api,
    starter_factory: Callable[[], agent.Starter] = AgentApp.from_env,
    reader_factory: Callable[[], landed.Reader] = AgentApp.from_env,
    groom: bool = False,
    agents: bool = False,
) -> None:
    """One sweep, with its run report. Raises to be redelivered.

    ``groom`` and ``agents`` are a sweep asked for on demand: it counts as
    the sweep for ``GROOM_TIMES``, or for ``AGENT_TIMES``, whatever the
    time. Every switch and cap still applies.
    """
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id, version=__version__) as report:
        if groom or agents:
            parts = [name for name, on in (("groom", groom), ("agents", agents)) if on]
            report.notice(ON_DEMAND, ", ".join(parts))
        if not effect_enabled(Effect.ASANA):
            # One board, and no development copy of it.
            log.info("sweep: board writes are off here; nothing swept")
            report.note("board writes off outside production")
            return

        board = (board_factory or Board.from_env)()
        store = records_factory()
        records = {r.task_gid: r for r in store.list_groomings()}

        # Executor lines, tags and assignments first, so what follows reads
        # the board as it will stay. With moving off, only reported.
        stepped = executor.keep_in_step(board, board.tasks(), write=auto_ready())
        for change in stepped:
            report.updated(
                "executor kept in step" if auto_ready() else "executor out of step",
                f"{change.task.name}: {change.describe()}",
                link=board.url(change.task.gid),
            )

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
            limit=dispatch_limit(on_demand=groom),
        )
        for record in result.writes:
            store.record_grooming(record)

        def board_count(key: str, value: Any) -> None:
            report.count(key, value, label=BOARD_LABELS[key], section=BOARD)

        board_count("tasks_checked", len(result.verdicts))
        board_count("not_ready", len(result.verdicts) - len(result.ready))
        board_count("changed", sum(v.changed for v in result.verdicts))
        board_count("records_written", len(result.writes))
        board_count("waiting_for_grooming", result.waiting)
        board_count("waiting_on_blockers", result.blocked)
        board_count("waiting_on_owner", result.awaiting_owner)
        board_count("awaiting_grooming", result.awaiting_grooming)
        board_count("held_without_questions", len(result.held))
        board_count("sent_back_from_ready", len(result.sent_back))
        if not auto_ready():
            board_count("ready_not_moved", len(result.ready))
            board_count("blocked_not_moved", result.blocked_not_moved)
            board_count("failing_in_ready_not_moved", len(result.failed_ready))
        _report_return_rate(store, report)
        _report_landings(board, reader_factory, report)
        reviewed = review.review(board)
        if reviewed is not None:
            board_count("review_closed", len(reviewed.closed))
            for task in reviewed.missing_handoff:
                report.issue("review task missing a handoff", review.task_label(task))
            for task in reviewed.unverified:
                report.issue("review task unverified", review.task_label(task))
        for task in result.newly_held:
            report.notice(
                "task newly held with nobody asked",
                f"[{task.name}]({board.url(task.gid)})",
            )

        # One line per task moved or sent, saying where it went.
        def moved(where: str, task: AsanaTaskRecord) -> None:
            report.updated(MOVED, f"{where}: {task.name}", link=board.url(task.gid))

        for task in result.moved:
            moved(ready_section(), task)
        for task in result.moved_to_blocked:
            moved(blocked_section(), task)
        for task, column in result.sent_back:
            moved(f"back from Ready to {column}", task)
        for task in result.returned:
            moved("came back out of Ready", task)
        for task in result.sent:
            moved("sent for grooming", task)

        if (agent.enabled() or agent.dry_run()) and (agents or due("AGENT_TIMES")):
            _dispatch_agents(
                board,
                store,
                starter_factory,
                report,
                exclude={t.gid for t in result.failed_ready},
            )


def return_rate_line(rate: GroomingReturnRate) -> str:
    """``3 of 40 (8%)``: tasks back out of Ready of those moved there.

    ``0 of 0`` when nothing moved to Ready in the window, which has no ratio.
    """
    line = f"{rate.returned} of {rate.moved_to_ready}"
    return line if rate.ratio is None else f"{line} ({rate.ratio:.0%})"


def _report_return_rate(store: Records, report: Any) -> None:
    """The 30-day return-from-Ready ratio, on its Board line.

    One read a sweep. A failed read leaves the line out and is noted; it
    never fails the sweep.
    """
    try:
        rate = store.grooming_return_rate(RETURN_RATE_DAYS)
    except Exception as exc:  # noqa: BLE001 — the sweep's own work is done
        log.warning("sweep: return-from-Ready ratio not read: %s", exc)
        report.note("return-from-Ready ratio not read")
        return
    report.count(
        RETURN_RATE,
        return_rate_line(rate),
        label=f"returned from Ready, {RETURN_RATE_DAYS} days: {{}}",
        section=BOARD,
    )


def _report_landings(
    board: Board, reader_factory: Callable[[], landed.Reader], report: Any
) -> None:
    """Post ``Landed:`` on the Review tasks whose pull request landed, and
    name the merged ones whose CI on dev failed or could not be confirmed.

    GitHub not configured is an issue; it never fails the sweep.
    """
    try:
        result = landed.land(board, reader_factory)
    except Exception as exc:  # noqa: BLE001 — the sweep's own work is done
        log.warning("sweep: landings not checked: %s", exc)
        report.issue("landings not checked", detail=str(exc))
        return
    if result is None:
        return
    report.count(
        "review_landed",
        len(result.landed),
        label=BOARD_LABELS["review_landed"],
        section=BOARD,
    )
    for task in result.landed:
        report.updated("Landed: posted", task.name, link=board.url(task.gid))
    for task, ref, run_url in result.failed:
        report.issue(
            "merged, CI on dev failed",
            f"{review.task_label(task)} ({ref.label})",
            detail=run_url,
        )
    for task, ref, why in result.unverified:
        report.issue(
            "merged, landing unverified",
            f"{review.task_label(task)} ({ref.label})",
            detail=why,
        )
    for task, error in result.errors:
        report.issue("landing not checked", review.task_label(task), detail=error)


def _dispatch_agents(
    board: Board,
    store: Records,
    starter_factory: Callable[[], agent.Starter],
    report: Any,
    *,
    exclude: Collection[str] = (),
) -> None:
    """Dispatch an agent for each Ready task it can take, and report it.

    ``exclude`` are the tasks this sweep found failing in Ready: never
    dispatched, whether or not they were moved. A dry run, or dispatch
    switched off, writes nothing.
    """
    write = agent.enabled() and not agent.dry_run()
    try:
        starter = starter_factory()
    except Exception as exc:  # noqa: BLE001 — the sweep's own work is done
        log.warning("sweep: agent dispatch not configured: %s", exc)
        report.issue("agent dispatch not configured", detail=str(exc))
        return
    sections = board.sections()
    result = agent.dispatch(
        [t for t in board.tasks() if t.gid not in exclude],
        sections,
        ready_gid=_section_named(sections, ready_section()).gid,
        board=board,
        store=store,
        starter=starter,
        unswept_prefixes=UNSWEPT_PREFIXES,
        template_marker=DO_NOT_WORK,
        write=write,
    )
    report.count(
        "agent_spend_usd",
        f"{result.spend_usd:.4f}",
        label="${} spent this month",
        section=AGENT,
    )
    report.count(
        "agent_dispatched", len(result.dispatched), label="dispatched", section=AGENT
    )
    for reason, n in sorted(result.skipped.items()):
        report.count(
            f"agent_skipped_{reason}",
            n,
            label=f"skipped: {agent.skip_label(reason)}",
            section=AGENT,
        )
    for candidate, run in result.dispatched:
        report.updated(
            f"agent dispatched to {candidate.repo} as run {run.id}",
            candidate.task.name,
            link=board.url(candidate.task.gid),
        )
    for candidate in result.would_dispatch:
        report.note(
            f"would dispatch, at most ${agent.task_cap():.2f}",
            f"{candidate.task.name} → {candidate.repo}",
        )
    for candidate in result.not_started:
        report.issue("agent not started", candidate.task.name)
    for run in result.stale:
        report.issue(
            "agent run never reported" if write else "agent run would be failed",
            run.task_id or run.task_gid,
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
        ", ".join((*groom_sections(), ready_section())),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
