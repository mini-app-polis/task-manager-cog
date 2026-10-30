"""Who works a task, as its ``Executor:`` line says, and keeping the board
in step with it.

Every task names its executor in its template: ``Executor: agent``,
``Executor: cowork`` or ``Executor: person``. The line is the source of
truth because anyone who can write a task's body can set it: a working
session, the groomer, a source filing a task. Tags and assignments are
derived from it, and this cog is the only thing that writes them:

- a cowork or person task carries that tag; an agent task carries neither;
- every task is assigned to the owner, whoever does the work: the owner
  owns every task on the board.

A task written before the line existed has none. Its executor is what its
tag says (no tag is the agent), and the sweep writes that into the body, so
the board fills itself in without anyone editing it. The line goes in the
rich body (``html_notes``) after ``Blocked by``, so the task's formatting
survives.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Protocol, get_args

from mini_app_polis.api.contract import GroomingExecutor
from mini_app_polis.asana import AsanaTaskRecord

#: The template label.
LABEL = "Executor"

#: The executor a task has when nothing says otherwise.
AGENT = "agent"

#: The executors that are tags, and that need the owner.
TAGGED: tuple[str, ...] = get_args(GroomingExecutor)

#: Every value the line may hold.
EXECUTORS: tuple[str, ...] = (AGENT, *TAGGED)

#: Who a cowork or person task is assigned to: the token's own user.
OWNER = "me"

_LINE = re.compile(rf"^{LABEL}:[ \t]*(?P<value>[^\n]*)$", re.MULTILINE)
_LINE_WITH_BREAK = re.compile(rf"\n{LABEL}:[^\n]*")
_HTML_LINE = re.compile(rf"(?m)^(?P<head>{LABEL}:)[ \t]*(?P<value>[^\n<]*)")
_BLOCKED_BY = re.compile(r"(?m)^(?:<body>)?Blocked by:[^\n<]*")


def tagged_executor(tags: Collection[str]) -> str | None:
    """The executor a task is tagged with (cowork or person), or None."""
    return next((t.lower() for t in tags if t.lower() in TAGGED), None)


def line_value(notes: str) -> str | None:
    """What the task's ``Executor:`` line says, lowercased, or None when it
    has no line. A line that names no executor is returned as written, so
    the caller can tell it apart from a missing one."""
    match = _LINE.search(notes.replace("\r\n", "\n"))
    if match is None:
        return None
    return match["value"].strip().lower()


def executor_of_task(notes: str, tags: Collection[str]) -> str:
    """Who works the task: its line when that names an executor, else its
    tag, else the agent."""
    value = line_value(notes)
    if value in EXECUTORS:
        return value
    return tagged_executor(tags) or AGENT


def with_line(html_notes: str, executor: str) -> str | None:
    """``html_notes`` with its ``Executor:`` line saying ``executor``, or
    None when it cannot be placed (no ``Blocked by:`` line to put it after).

    An existing line is rewritten in place; a missing one goes on the line
    after ``Blocked by`` and the lines that continue it, at the end of the
    template block.
    """
    if _HTML_LINE.search(html_notes):
        return _HTML_LINE.sub(lambda m: f"{m['head']} {executor}", html_notes, count=1)
    match = _BLOCKED_BY.search(html_notes)
    if match is None:
        return None
    end = match.end()
    # The block ends at the first blank line or at the end of the body.
    blank = html_notes.find("\n\n", end)
    closing = html_notes.find("</body>", end)
    candidates = [p for p in (blank, closing) if p != -1]
    at = min(candidates) if candidates else len(html_notes)
    return f"{html_notes[:at]}\n{LABEL}: {executor}{html_notes[at:]}"


def without_line(notes: str) -> str:
    """``notes`` with its ``Executor:`` line taken out, as it read before the
    sweep wrote one in."""
    return _LINE_WITH_BREAK.sub("", notes.replace("\r\n", "\n"), count=1)


def plain_line(executor: str) -> str:
    """The line as plain text, for a block written from scratch."""
    return f"{LABEL}: {executor}"


@dataclass
class Change:
    """What keeping one task in step with its executor did, or would do."""

    task: AsanaTaskRecord
    executor: str
    body: str | None = None
    """The body with the line written in, when the line is missing."""
    tagged: list[str] = field(default_factory=list)
    untagged: list[str] = field(default_factory=list)
    assigned: bool = False

    @property
    def wrote_line(self) -> bool:
        """Whether the change writes the ``Executor:`` line into the body."""
        return self.body is not None

    @property
    def any(self) -> bool:
        """Whether the change does anything to the task at all."""
        return bool(self.wrote_line or self.tagged or self.untagged or self.assigned)

    def describe(self) -> str:
        """One line naming what changed, for the run report."""
        parts = []
        if self.wrote_line:
            parts.append(f"Executor: {self.executor} written")
        parts.extend(f"tagged {t}" for t in self.tagged)
        parts.extend(f"untagged {t}" for t in self.untagged)
        if self.assigned:
            parts.append("assigned to the owner")
        return ", ".join(parts)


def plan(task: AsanaTaskRecord) -> Change:
    """What keeping ``task`` in step with its executor needs, without doing it."""
    executor = executor_of_task(task.notes, task.tag_names)
    # A missing line is filled in. A line naming no executor is the
    # writer's mistake, left for the linter to report rather than guessed.
    # A task with no Blocked by line has no block to put it in, and is left.
    body = (
        with_line(task.html_notes, executor)
        if line_value(task.notes) is None and task.html_notes
        else None
    )
    change = Change(task=task, executor=executor, body=body)
    have = {t.lower() for t in task.tag_names}
    for tag in TAGGED:
        if tag == executor and tag not in have:
            change.tagged.append(tag)
        elif tag != executor and tag in have:
            change.untagged.append(tag)
    if task.assignee_gid is None:
        change.assigned = True
    return change


class ExecutorBoard(Protocol):
    """The board methods keeping executors in step needs (a Board)."""

    def update_body(self, task_gid: str, html_notes: str) -> None:
        """Rewrite a task's body, leaving its name alone."""
        ...

    def tag(self, task_gid: str, tag_name: str) -> None:
        """Put a tag on a task."""
        ...

    def untag(self, task_gid: str, tag_name: str) -> None:
        """Take a tag off a task."""
        ...

    def assign(self, task_gid: str) -> None:
        """Assign a task to the owner."""
        ...


def apply(board: ExecutorBoard, change: Change) -> None:
    """Make the board match ``change``."""
    task = change.task
    if change.body is not None:
        board.update_body(task.gid, change.body)
    for tag in change.tagged:
        board.tag(task.gid, tag)
    for tag in change.untagged:
        board.untag(task.gid, tag)
    if change.assigned:
        board.assign(task.gid)


#: The most tasks one sweep brings into step. The first sweep after the line
#: was introduced has the whole board to fill in; spreading it keeps that
#: sweep inside its time, and the rest are done by the next ones.
STEP_LIMIT = 20


def keep_in_step(
    board: ExecutorBoard,
    tasks: Collection[AsanaTaskRecord],
    *,
    write: bool,
    limit: int = STEP_LIMIT,
) -> list[Change]:
    """Bring open tasks' lines, tags and assignments into step with their
    executors, at most ``limit`` of them. With ``write`` off, only says what
    it would change."""
    changes: list[Change] = []
    for task in tasks:
        if task.completed:
            continue
        change = plan(task)
        if not change.any:
            continue
        if write:
            apply(board, change)
        changes.append(change)
        if len(changes) >= limit:
            break
    return changes
