"""Review: tasks handed off, and the sweep closing the ones that landed.

A task moved to Review carries a ``Handed off:`` comment. Whoever confirms
the work landed (green CI on dev) posts a comment starting ``Landed:``, and
the sweep marks the task complete; for a task whose PR line names a pull
request, the sweep posts it itself (landed.py). It stays in Review: the sweep never moves
a task out of Review and never marks one incomplete. Only comments and the
column are read; nothing here calls GitHub or a model.

A task entered Review at its latest move into it, or at its creation when
it has none. For each incomplete task in Review:

- its latest comment starts with ``Landed:``: marked complete;
- a comment after its ``Landed:`` comment: left open;
- no comment since it entered Review: listed as missing a handoff, on the
  first sweep that sees it, unless its last comment before the move starts
  with ``Handed off:`` (a handoff is often posted just before the move);
- no ``Landed:`` comment more than ``REVIEW_UNVERIFIED_HOURS`` (default 24)
  after it entered Review: unverified. The first sweep to find it so posts
  an ``Unverified:`` comment and lists it, once; while that comment is still
  the latest, later sweeps only count it, so a task that waits does not
  raise the same issue every sweep. A later comment of any kind starts it
  over.

  REVIEW_SECTION           the column read (default: Review)
  REVIEW_UNVERIFIED_HOURS  hours in Review before a task with no
                           ``Landed:`` comment is unverified (default: 24)
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Protocol

from mini_app_polis import logger as logger_mod
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from .lint import task_id_of

log = logger_mod.get_logger()

DEFAULT_REVIEW_SECTION = "Review"
DEFAULT_UNVERIFIED_HOURS = 24.0

#: Starts the comment that says the work landed.
LANDED_PREFIX = "Landed:"
#: Starts the comment that hands the work off as it moves to Review.
HANDED_OFF_PREFIX = "Handed off:"
#: Starts the comment the sweep posts on a task it finds unverified.
UNVERIFIED_PREFIX = "Unverified:"

COMMENT = "comment_added"
SECTION_CHANGED = "section_changed"


class ReviewBoard(Protocol):
    """What the review pass needs of the board."""

    def sections(self) -> list[AsanaSection]:
        """The board's columns."""
        ...

    def tasks(self) -> list[AsanaTaskRecord]:
        """Every task on the board."""
        ...

    def stories(self, task_gid: str) -> list[AsanaStory]:
        """A task's history, oldest first."""
        ...

    def complete(self, task_gid: str) -> None:
        """Mark a task complete."""
        ...

    def comment(self, task_gid: str, text: str) -> None:
        """Post a comment on a task."""
        ...


@dataclass
class ReviewResult:
    """What one review pass did and found."""

    #: Tasks marked complete: their latest comment starts with ``Landed:``.
    closed: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks with no comment since they entered Review.
    missing_handoff: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks found unverified this pass: no ``Landed:`` comment past the
    #: threshold, now given an ``Unverified:`` comment.
    unverified: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks still unverified whose latest comment is the sweep's
    #: ``Unverified:``: already flagged, so only counted.
    awaiting_landed: list[AsanaTaskRecord] = field(default_factory=list)


def review_section() -> str:
    """The column read: ``REVIEW_SECTION``, or Review."""
    return (os.environ.get("REVIEW_SECTION") or "").strip() or DEFAULT_REVIEW_SECTION


def unverified_after() -> dt.timedelta:
    """How long in Review before a task with no ``Landed:`` is unverified."""
    raw = (os.environ.get("REVIEW_UNVERIFIED_HOURS") or "").strip()
    return dt.timedelta(hours=float(raw) if raw else DEFAULT_UNVERIFIED_HOURS)


def task_label(task: AsanaTaskRecord) -> str:
    """The task's id and title, as its name carries them."""
    return task.name if task_id_of(task.name) else f"{task.gid} · {task.name}"


def entered_review(
    task: AsanaTaskRecord, stories: Collection[AsanaStory], review_gid: str
) -> dt.datetime | None:
    """When the task last moved into Review, or was created when it never did."""
    moves = [
        s.created_at
        for s in stories
        if s.kind == SECTION_CHANGED
        and s.section_gid == review_gid
        and s.created_at is not None
    ]
    return max(moves) if moves else task.created_at


def _handed_off_before(
    stories: Collection[AsanaStory], entered: dt.datetime | None
) -> bool:
    """Whether the last comment before the task entered Review is its handoff."""
    before = [
        s
        for s in stories
        if s.kind == COMMENT
        and s.text is not None
        and entered is not None
        and s.created_at is not None
        and s.created_at < entered
    ]
    return bool(before) and (before[-1].text or "").lstrip().startswith(
        HANDED_OFF_PREFIX
    )


def review(
    board: ReviewBoard, *, now: dt.datetime | None = None
) -> ReviewResult | None:
    """Close the Review tasks that landed, and list the ones that did not.

    None when the board has no Review column: the pass is skipped.
    """
    now = now or dt.datetime.now(dt.UTC)
    section = next(
        (s for s in board.sections() if s.name.lower() == review_section().lower()),
        None,
    )
    if section is None:
        log.warning("review: the board has no %r column", review_section())
        return None
    threshold = unverified_after()
    result = ReviewResult()
    for task in board.tasks():
        if task.completed or task.section_gid != section.gid:
            continue
        stories = board.stories(task.gid)
        entered = entered_review(task, stories, section.gid)
        comments = [
            s
            for s in stories
            if s.kind == COMMENT
            and s.text is not None
            and (entered is None or s.created_at is None or s.created_at >= entered)
        ]
        if not comments and not _handed_off_before(stories, entered):
            result.missing_handoff.append(task)
        landed = [
            i
            for i, s in enumerate(comments)
            if (s.text or "").lstrip().startswith(LANDED_PREFIX)
        ]
        if landed:
            if landed[-1] == len(comments) - 1:
                board.complete(task.gid)
                result.closed.append(task)
                log.info("review: %s landed; marked complete", task.name)
            continue
        if entered is not None and now - entered > threshold:
            latest = (comments[-1].text or "").lstrip() if comments else ""
            if latest.startswith(UNVERIFIED_PREFIX):
                result.awaiting_landed.append(task)
                continue
            board.comment(task.gid, _unverified_comment(threshold))
            result.unverified.append(task)
            log.info("review: %s unverified; flagged", task.name)
    return result


def _unverified_comment(threshold: dt.timedelta) -> str:
    hours = threshold.total_seconds() / 3600
    return (
        f"{UNVERIFIED_PREFIX} in Review for more than {hours:g} hours with no "
        f"{LANDED_PREFIX} comment. Confirm the work landed (green CI on dev) "
        f"and comment {LANDED_PREFIX}, or move the task out of Review."
    )
