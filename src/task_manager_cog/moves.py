"""Moves by comment: a session moves a task by commenting, never directly.

This cog is the board's one writer. A working session that picks a task up,
or finishes one, says so in a comment, and the sweep makes the move:

- ``Started:`` on a task in Ready moves it to In progress;
- ``Handed off:`` on a task in In progress moves it to Review;
- both on a task still in Ready (started and finished between two sweeps)
  move it straight to Review.

A comment counts only when it was posted after the task last entered its
column (or after it was created, when it never moved). Once the task has
moved, that comment is from before the move, so it moves nothing again.
A ``Handed off:`` comment on a task in Ready with no ``Started:`` is counted
as in the wrong column and the task is left where it is.
Nothing here calls a model, and a task on hold is never read.

  READY_SECTION, IN_PROGRESS_SECTION, REVIEW_SECTION  the columns named
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from mini_app_polis import logger as logger_mod
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from .dispatch import in_progress_section
from .review import COMMENT, HANDED_OFF_PREFIX, entered_review, review_section

log = logger_mod.get_logger()

#: Starts the comment a session posts when it picks a task up.
STARTED_PREFIX = "Started:"

DEFAULT_READY_SECTION = "Ready"


class MovesBoard(Protocol):
    """What the moves pass needs of the board."""

    def sections(self) -> list[AsanaSection]:
        """The board's columns."""
        ...

    def tasks(self) -> list[AsanaTaskRecord]:
        """Every task on the board."""
        ...

    def stories(self, task_gid: str) -> list[AsanaStory]:
        """A task's history, oldest first."""
        ...

    def move(self, task_gid: str, section_gid: str) -> None:
        """Move a task to a column."""
        ...


@dataclass
class MovesResult:
    """What one moves pass did and found."""

    #: Tasks moved, and the column each went to.
    moved: list[tuple[AsanaTaskRecord, str]] = field(default_factory=list)
    #: Tasks with a ``Handed off:`` comment while still in Ready.
    wrong_column: list[AsanaTaskRecord] = field(default_factory=list)


def ready_section() -> str:
    """The Ready column: ``READY_SECTION``, or Ready."""
    return (os.environ.get("READY_SECTION") or "").strip() or DEFAULT_READY_SECTION


def _named(sections: list[AsanaSection], name: str) -> AsanaSection | None:
    return next((s for s in sections if s.name.lower() == name.lower()), None)


def _comments_since_entering(
    task: AsanaTaskRecord, stories: list[AsanaStory], column_gid: str
) -> list[str]:
    """The task's comments posted since it last entered its column."""
    entered = entered_review(task, stories, column_gid)
    return [
        (s.text or "").lstrip()
        for s in stories
        if s.kind == COMMENT
        and s.text is not None
        and (entered is None or s.created_at is None or s.created_at >= entered)
    ]


def apply_moves(board: MovesBoard) -> MovesResult | None:
    """Move the tasks whose comments ask for it. None when the board lacks
    one of the three columns: the pass is skipped."""
    sections = board.sections()
    ready = _named(sections, ready_section())
    in_progress = _named(sections, in_progress_section())
    review = _named(sections, review_section())
    if ready is None or in_progress is None or review is None:
        log.warning("moves: the board lacks Ready, In progress or Review; skipped")
        return None
    result = MovesResult()
    for task in board.tasks():
        if task.completed or task.section_gid not in (ready.gid, in_progress.gid):
            continue
        comments = _comments_since_entering(
            task, board.stories(task.gid), task.section_gid
        )
        started = any(c.startswith(STARTED_PREFIX) for c in comments)
        handed_off = any(c.startswith(HANDED_OFF_PREFIX) for c in comments)
        target: AsanaSection | None = None
        if task.section_gid == in_progress.gid:
            target = review if handed_off else None
        elif started:
            target = review if handed_off else in_progress
        elif handed_off:
            result.wrong_column.append(task)
            log.info("moves: %s handed off in Ready without Started:", task.name)
        if target is not None:
            board.move(task.gid, target.gid)
            result.moved.append((task, target.name))
            log.info("moves: %s moved to %s by its comment", task.name, target.name)
    return result
