"""Applying an update to a task this cog created: a comment, and a column.

Whatever asked for a task can change it later through the API, naming it by
the ``external_id`` it was created under, and the API puts it on this cog's
queue:

    {"type": "task.update", "version": 1, "payload": <TaskUpdateRequest>}

and it is applied here, in line with every other write to the board. The
update is general: it says nothing about why, so any source that created a
task can use it.

**Not on the board.** No task carries the ``external_id``, or the one that
did is gone. The run reports it and stops; there is nothing to redeliver to.

**Done, or already there.** A completed task, or one already in the column
the update names, is left alone: no comment and no move. Nothing here ever
completes a task, and nothing reopens one.

**In progress.** An agent is working the task, so it is not moved from
under it. It gets the comment.

**Anywhere else.** The task gets the comment, then moves to the named
column, if one is named. The comment goes first, so a move that fails is
redone on redelivery without losing it.

**The column.** It is named, not given by gid, and found on the board as
the sweep finds its own columns, ignoring case. ``Review`` is this board's
Review column, ``REVIEW_SECTION`` as results.py resolves it. A name the board
has no column for still gets its comment, and the run reports the name as
an issue: the comment is what someone reads, and redelivering it would only
send it to the dead-letter queue without it.

**Once.** A comment whose text is already on the task is not posted again,
and a task already in its column is not moved, so a redelivered update
changes nothing.

**A secret's rotation, by key prefix.** An ``external_id`` that starts with
``secret-rotation.`` and ends in ``.`` (``secret-rotation.<config>.<name>.``)
names a secret, not one task: a secret has a task per due rotation, each
under its own key. The update goes to the one open task whose external id
starts with the prefix, and only when the comment's first line,
``Rotated <ISO timestamp>; verification <ok|failed|unchecked>``, gives a time
after that task was created: the rotation answers that task, not an earlier
one. The task moves to Review, whatever column the request names, with the
rotation as its handoff: a ``Handed off:`` comment answering each Done when
item with the rotator's record (when it was rotated, its verification, and
``Old value retired <ISO timestamp>`` once the old value is gone). A rotation
verified ``ok`` whose old value is retired has nothing left for a person to
confirm, so a ``Landed:`` comment follows the move, and the sweep's Review
check closes the task (review.py). Anything less waits in Review for a
person, as any task does. A task already in Review that carries the
rotator's handoff is not moved again, but when a later update brings the
retired line (a routine rotation's old value is retired a day after it),
it gets a handoff with that record and its ``Landed:``. A prefix with no
open task, a first line that does not parse, a time not after the task's
creation and a task already in Review with nothing to land are each left
alone and noted in the run; more than one open task is left alone and
reported as an issue. Any other ``external_id`` is an exact key, as above.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import TaskUpdateRequest
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import RunReport, run_report

from ._version import __version__
from .board import Board, find_or_scan
from .dispatch import in_progress_section
from .lint import parse
from .results import DEFAULT_REVIEW_SECTION, review_section
from .review import HANDED_OFF_PREFIX, LANDED_PREFIX, done_when_items
from .sweep import SweepMisconfigured, _section_named

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "update-task"

ROTATION_KEY_PREFIX = "secret-rotation."

_ROTATED = re.compile(r"Rotated (\S+); verification (ok|failed|unchecked)")

#: The first line of the handoff a rotated secret's task gets.
ROTATION_HANDOFF_HEAD = f"{HANDED_OFF_PREFIX} rotator-cog recorded the rotation."

#: A rotation comment's line saying when the old value was retired.
_RETIRED = re.compile(r"Old value retired (\S+)")


def section_name(requested: str) -> str:
    """The board's name for a requested column: ``Review`` is REVIEW_SECTION."""
    if requested.strip().lower() == DEFAULT_REVIEW_SECTION.lower():
        return review_section()
    return requested.strip()


def _target(sections: list[AsanaSection], requested: str) -> AsanaSection | None:
    """The column a request names, or None when the board has no such column."""
    try:
        return _section_named(sections, section_name(requested))
    except SweepMisconfigured:
        return None


def _posted(comment: str, comments: list[str]) -> bool:
    """Whether this comment's text is already on the task."""
    text = comment.strip()
    return any(c.strip() == text for c in comments)


def is_rotation_prefix(external_id: str) -> bool:
    """Whether ``external_id`` names a secret's rotation tasks by key prefix."""
    return external_id.startswith(ROTATION_KEY_PREFIX) and external_id.endswith(".")


def rotated_at(comment: str) -> datetime | None:
    """The time on a rotation comment's first line, or None when it does not parse.

    The line is ``Rotated <ISO timestamp>; verification <ok|failed|unchecked>``.
    A timestamp without an offset is read as UTC.
    """
    lines = comment.strip().splitlines()
    match = _ROTATED.fullmatch(lines[0].strip()) if lines else None
    if match is None:
        return None
    try:
        when = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def _rotation_evidence(comment: str) -> str:
    """The rotator's comment on one line: what the handoff gives as evidence."""
    return "; ".join(line.strip() for line in comment.splitlines() if line.strip())


def rotation_handoff(task: AsanaTaskRecord, comment: str) -> str:
    """The ``Handed off:`` comment a rotated secret's task gets.

    One list item per Done when item, each the rotator's record, so the
    Review check finds every item answered with its evidence.
    """
    items = done_when_items(parse(task.notes).fields.get("Done when", "")) or [""]
    evidence = _rotation_evidence(comment)
    return "\n".join([ROTATION_HANDOFF_HEAD] + [f"- {evidence}" for _ in items])


def rotation_landed(comment: str) -> str | None:
    """The ``Landed:`` comment for a rotation that needs no person, or None.

    Only a rotation verified ``ok`` whose old value is recorded as retired:
    the new value works and the old one is gone, which is all its Done when
    asks.
    """
    lines = [line.strip() for line in comment.strip().splitlines()]
    first = _ROTATED.fullmatch(lines[0]) if lines else None
    if first is None or first.group(2) != "ok":
        return None
    retired = next((m for line in lines[1:] if (m := _RETIRED.fullmatch(line))), None)
    if retired is None:
        return None
    return (
        f"{LANDED_PREFIX} the new value verified ok and the old value was "
        f"retired {retired.group(1)}, as rotator-cog recorded."
    )


def _land_in_review(
    request: TaskUpdateRequest,
    task: AsanaTaskRecord,
    board: Board,
    report: RunReport,
    review_name: str,
) -> None:
    """Land a rotation task the rotator already handed off, once it can.

    A routine rotation's old value is retired after the task moved to
    Review, so the update that says so finds it there. A task with the
    rotator's handoff gets a handoff with the full record and ``Landed:``;
    any other task in Review is left alone.
    """
    prefix = request.external_id
    landed = rotation_landed(request.comment)
    comments = board.comments(task.gid)
    handed_off = any(c.lstrip().startswith(ROTATION_HANDOFF_HEAD) for c in comments)
    if landed is None or not handed_off:
        report.note(f"already in {review_name}", prefix)
        return
    handoff = rotation_handoff(task, request.comment)
    if _posted(handoff, comments) and _posted(landed, comments):
        report.note(f"already landed in {review_name}", prefix)
        return
    if not _posted(handoff, comments):
        board.comment(task.gid, handoff)
    if not _posted(landed, comments):
        board.comment(task.gid, landed)
    report.updated(
        f"task handed off and landed in {review_name}",
        task.name,
        link=board.url(task.gid),
    )
    log.info("update: %s (%s) landed in %s", task.name, prefix, review_name)


def _apply_rotation(
    request: TaskUpdateRequest, board: Board, report: RunReport
) -> None:
    """Comment on a secret's open rotation task and move it to Review."""
    prefix = request.external_id
    open_tasks: list[AsanaTaskRecord] = [
        t
        for t in board.tasks()
        if not t.completed and (t.external_id or "").startswith(prefix)
    ]
    if not open_tasks:
        log.info("update: no open task under %s; nothing to do", prefix)
        report.note("no open rotation task", prefix)
        return
    if len(open_tasks) > 1:
        log.warning(
            "update: %d open tasks under %s; none changed", len(open_tasks), prefix
        )
        report.issue(
            "more than one open rotation task",
            prefix,
            detail=", ".join(t.name for t in open_tasks),
        )
        return
    task = open_tasks[0]

    when = rotated_at(request.comment)
    if when is None:
        log.info("update: rotation comment for %s has no Rotated line", prefix)
        report.note("rotation comment does not parse", prefix)
        return
    if task.created_at is None or when <= task.created_at:
        log.info("update: rotation for %s is not after %s", prefix, task.name)
        report.note("rotation not after the task", prefix)
        return

    review = _section_named(board.sections(), review_section())
    if task.section_gid == review.gid:
        _land_in_review(request, task, board, report, review.name)
        return

    handoff = rotation_handoff(task, request.comment)
    if not _posted(handoff, board.comments(task.gid)):
        board.comment(task.gid, handoff)
    board.move(task.gid, review.gid)
    # After the move: the Review check reads Landed: only once the task is in
    # Review.
    landed = rotation_landed(request.comment)
    if landed is not None and not _posted(landed, board.comments(task.gid)):
        board.comment(task.gid, landed)
    report.updated(
        f"task handed off{' and landed' if landed else ''}, moved to {review.name}",
        task.name,
        link=board.url(task.gid),
    )
    log.info("update: %s (%s) commented, moved to %s", task.name, prefix, review.name)


def apply_update(
    request: TaskUpdateRequest,
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
) -> None:
    """Apply one update to its task, once. Raises to be redelivered."""
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id, version=__version__) as report:
        label = request.external_id

        if not effect_enabled(Effect.ASANA):
            log.info("update: board writes are off here; not applied to %s", label)
            report.note("board writes off outside production", label)
            return

        board = (board_factory or Board.from_env)()
        if is_rotation_prefix(request.external_id):
            _apply_rotation(request, board, report)
            return

        gid = find_or_scan(board, request.external_id)
        task = board.get(gid) if gid is not None else None
        if task is None:
            # The sender believes a task exists: a miss here means an update
            # was lost, so it is an issue for a person, not an ordinary skip.
            log.warning("update: no task on the board carries %s", label)
            report.issue("no task on the board", label)
            return
        if task.completed:
            report.note("task done; left alone", label)
            return

        sections = board.sections()
        target = _target(sections, request.section) if request.section else None
        if target is not None and task.section_gid == target.gid:
            report.note(f"already in {target.name}", label)
            return

        in_progress = _section_named(sections, in_progress_section())
        held = task.section_gid == in_progress.gid

        if not _posted(request.comment, board.comments(task.gid)):
            board.comment(task.gid, request.comment)
        move_to = target if not held else None
        if move_to is not None:
            board.move(task.gid, move_to.gid)

        if request.section and target is None:
            report.issue(
                "the board has no column by the requested name",
                label,
                detail=request.section,
            )
        if move_to is not None:
            report.updated(
                f"task commented and moved to {move_to.name}",
                task.name,
                link=board.url(task.gid),
            )
        else:
            report.updated("task commented", task.name, link=board.url(task.gid))
            if held and target is not None:
                report.note("task is in progress; not moved", label)
        log.info(
            "update: %s (%s) commented%s",
            task.name,
            label,
            f", moved to {move_to.name}" if move_to is not None else "",
        )
