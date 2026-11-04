"""Review: tasks handed off, and the Review check that closes them.

A task moved to Review carries a ``Handed off:`` comment answering its Done
when, item by item. Once the work landed (green CI on dev), a comment starting
``Landed:`` says so; for a task whose PR line names a pull request, the sweep
posts it itself (landed.py). The Review check then decides. Only the task's
body, comments and column changes are read; nothing here calls GitHub or a
model.

A task entered Review at its latest move into it, or at its creation when
it has none. For each task in Review:

- complete, and marked complete before ``COMPLETE_CHECK_SINCE``: moved to
  Complete with no check. This is the one-time move of the tasks the old pass
  closed in place; once they are gone it finds nothing.
- its latest comment since it entered Review starts with ``Landed:``, or it
  was marked complete by hand: checked. The latest ``Handed off:`` comment
  must answer every Done when item: one list item per Done when item, in
  order, none empty. Done when items are its list items when it has any, and
  its sentences otherwise. With no pull request the answers are the evidence
  (a link, a commit, a run id, or a plain statement from the owner), so the
  same count applies. A task marked complete by hand needs its ``Landed:``
  too.

  Passes: moved to Complete, then marked complete.
  Fails: one ``Rejected:`` comment naming each item not answered, reopened
  when it was complete, and moved to In progress. A mechanical check cannot
  tell an unclear task from unfinished work; a person moves the task to
  Refining when the task is the problem.
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
  COMPLETE_SECTION         where a task that passes goes (default: Complete)
  IN_PROGRESS_SECTION      where a rejected task goes (default: In progress)
  REVIEW_UNVERIFIED_HOURS  hours in Review before a task with no
                           ``Landed:`` comment is unverified (default: 24)
  COMPLETE_CHECK_SINCE     ISO time; a task marked complete before it is
                           moved to Complete unchecked (default:
                           2026-11-05T00:00:00Z, the Complete column's
                           first deploy)
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Protocol

from mini_app_polis import logger as logger_mod
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from .dispatch import in_progress_section
from .lint import parse, task_id_of

log = logger_mod.get_logger()

DEFAULT_REVIEW_SECTION = "Review"
DEFAULT_COMPLETE_SECTION = "Complete"
DEFAULT_UNVERIFIED_HOURS = 24.0
#: When the Complete column first shipped: a task marked complete before it
#: was closed by the old pass, in place, and is moved to Complete unchecked.
DEFAULT_COMPLETE_CHECK_SINCE = dt.datetime(2026, 11, 5, tzinfo=dt.UTC)

#: Starts the comment that says the work landed.
LANDED_PREFIX = "Landed:"
#: Starts the comment that hands the work off as it moves to Review.
HANDED_OFF_PREFIX = "Handed off:"
#: Starts the comment the sweep posts on a task it finds unverified.
UNVERIFIED_PREFIX = "Unverified:"
#: Starts the comment the sweep posts on a task that fails the Review check.
REJECTED_PREFIX = "Rejected:"

COMMENT = "comment_added"
SECTION_CHANGED = "section_changed"
MARKED_COMPLETE = "marked_complete"

#: A list item's marker, as lint reads one: ``-``, ``*``, ``•``, ``1.``, ``1)``.
_LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+(?P<text>.*)$")
#: Where one sentence ends and the next begins.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'`(])")
#: How much of an item a Rejected: comment quotes.
_QUOTE_CHARS = 120


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

    def reopen(self, task_gid: str) -> None:
        """Mark a task incomplete."""
        ...

    def move(self, task_gid: str, section_gid: str) -> None:
        """Move a task to a column."""
        ...

    def comment(self, task_gid: str, text: str) -> None:
        """Post a comment on a task."""
        ...


@dataclass
class ReviewResult:
    """What one review pass did and found."""

    #: Tasks that passed the Review check: moved to Complete, marked complete.
    closed: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks that failed it: given ``Rejected:``, moved to In progress.
    rejected: list[AsanaTaskRecord] = field(default_factory=list)
    #: Tasks already complete before the check shipped, moved to Complete.
    moved_complete: list[AsanaTaskRecord] = field(default_factory=list)
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


def complete_section() -> str:
    """Where a task that passes goes: ``COMPLETE_SECTION``, or Complete."""
    return (
        os.environ.get("COMPLETE_SECTION") or ""
    ).strip() or DEFAULT_COMPLETE_SECTION


def complete_check_since() -> dt.datetime:
    """Tasks marked complete before this are moved to Complete unchecked."""
    raw = (os.environ.get("COMPLETE_CHECK_SINCE") or "").strip()
    if not raw:
        return DEFAULT_COMPLETE_CHECK_SINCE
    try:
        since = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        log.warning("review: COMPLETE_CHECK_SINCE %r is not a time; default", raw)
        return DEFAULT_COMPLETE_CHECK_SINCE
    return since if since.tzinfo else since.replace(tzinfo=dt.UTC)


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


def done_when_items(done_when: str) -> list[str]:
    """The items a handoff must answer: Done when's list items, when it has
    any, and its sentences otherwise."""
    lines = [line for line in done_when.splitlines() if line.strip()]
    items: list[str] = []
    listed = any(_LIST_ITEM.match(line) for line in lines)
    if listed:
        for line in lines:
            match = _LIST_ITEM.match(line)
            if match:
                items.append(match["text"].strip())
            elif items:
                # A line that continues the item above it.
                items[-1] = f"{items[-1]} {line.strip()}"
            else:
                items.append(line.strip())
        return [i for i in items if i]
    text = " ".join(line.strip() for line in lines)
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def answers(handoff: str) -> list[str]:
    """A handoff's answers: its list items, in order."""
    found: list[str] = []
    for line in handoff.splitlines():
        match = _LIST_ITEM.match(line)
        if match:
            found.append(match["text"].strip())
    return found


def unanswered(task: AsanaTaskRecord, handoff: str | None) -> list[str]:
    """The Done when items the handoff leaves unanswered, by position: the
    n-th list item in the handoff answers the n-th Done when item."""
    items = done_when_items(parse(task.notes).fields.get("Done when", ""))
    given = answers(handoff or "")
    return [
        item
        for position, item in enumerate(items)
        if position >= len(given) or not given[position]
    ]


def _latest_handoff(stories: Collection[AsanaStory]) -> str | None:
    """The text of the task's latest ``Handed off:`` comment, or None."""
    handoffs = [
        s.text
        for s in stories
        if s.kind == COMMENT
        and s.text is not None
        and s.text.lstrip().startswith(HANDED_OFF_PREFIX)
    ]
    return handoffs[-1] if handoffs else None


def _marked_complete_at(stories: Collection[AsanaStory]) -> dt.datetime | None:
    """When the task was last marked complete, or None."""
    marks = [
        s.created_at
        for s in stories
        if s.kind == MARKED_COMPLETE and s.created_at is not None
    ]
    return max(marks) if marks else None


def _rejected_comment(missing: list[str], *, landed: bool) -> str:
    lines = [
        f"{REJECTED_PREFIX} the Review check found Done when items the latest "
        f"{HANDED_OFF_PREFIX} comment does not answer. Answer each one, as one "
        "list item per Done when item in order, in a new "
        f"{HANDED_OFF_PREFIX} comment."
    ]
    if not landed:
        lines.append(
            f"- No {LANDED_PREFIX} comment since the task entered Review: "
            "confirm the work landed (green CI on dev)."
        )
    for item in missing:
        quoted = item if len(item) <= _QUOTE_CHARS else item[: _QUOTE_CHARS - 1] + "…"
        lines.append(f"- Not answered: {quoted}")
    lines.append("Moved to In progress; move it to Refining if the task is unclear.")
    return "\n".join(lines)


def _column(board: ReviewBoard, name: str) -> AsanaSection | None:
    return next((s for s in board.sections() if s.name.lower() == name.lower()), None)


def review(
    board: ReviewBoard, *, now: dt.datetime | None = None
) -> ReviewResult | None:
    """Run the Review check: close the tasks that pass, reject the ones that
    fail, and list the ones still waiting.

    None when the board lacks the Review, Complete or In progress column:
    the pass is skipped.
    """
    now = now or dt.datetime.now(dt.UTC)
    columns = {
        name: _column(board, name)
        for name in (review_section(), complete_section(), in_progress_section())
    }
    missing_columns = [name for name, column in columns.items() if column is None]
    if missing_columns:
        log.warning("review: the board has no %s column", ", ".join(missing_columns))
        return None
    section = columns[review_section()]
    complete_to = columns[complete_section()]
    reject_to = columns[in_progress_section()]
    assert section and complete_to and reject_to  # noqa: S101 - checked above
    threshold = unverified_after()
    since = complete_check_since()
    result = ReviewResult()
    for task in board.tasks():
        if task.section_gid != section.gid:
            continue
        stories = board.stories(task.gid)
        if task.completed:
            marked = _marked_complete_at(stories)
            if marked is None or marked < since:
                board.move(task.gid, complete_to.gid)
                result.moved_complete.append(task)
                log.info("review: %s already complete; moved to Complete", task.name)
                continue
        entered = entered_review(task, stories, section.gid)
        comments = [
            s
            for s in stories
            if s.kind == COMMENT
            and s.text is not None
            and (entered is None or s.created_at is None or s.created_at >= entered)
        ]
        landed = [
            i
            for i, s in enumerate(comments)
            if (s.text or "").lstrip().startswith(LANDED_PREFIX)
        ]
        latest_landed = bool(landed) and landed[-1] == len(comments) - 1
        if latest_landed or task.completed:
            missing = unanswered(task, _latest_handoff(stories))
            if landed and not missing:
                board.move(task.gid, complete_to.gid)
                if not task.completed:
                    board.complete(task.gid)
                result.closed.append(task)
                log.info("review: %s passed; moved to Complete", task.name)
            else:
                board.comment(task.gid, _rejected_comment(missing, landed=bool(landed)))
                if task.completed:
                    board.reopen(task.gid)
                board.move(task.gid, reject_to.gid)
                result.rejected.append(task)
                log.info("review: %s rejected; moved to In progress", task.name)
            continue
        if not comments and not _handed_off_before(stories, entered):
            result.missing_handoff.append(task)
        if landed:
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
