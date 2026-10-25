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
"""

from __future__ import annotations

from collections.abc import Callable

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import TaskUpdateRequest
from mini_app_polis.asana import AsanaSection
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import run_report

from ._version import __version__
from .board import Board
from .dispatch import in_progress_section
from .results import DEFAULT_REVIEW_SECTION, review_section
from .sweep import SweepMisconfigured, _section_named

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "update-task"


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
        gid = board.find(request.external_id)
        task = board.get(gid) if gid is not None else None
        if task is None:
            log.info("update: no task on the board carries %s", label)
            report.note("no task on the board", label)
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
