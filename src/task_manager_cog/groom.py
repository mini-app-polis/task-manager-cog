"""Applying a groomer's proposal to the board.

task-groomer-cog reads a task and the code it names, and proposes what the
task should say. The proposal comes back through the API onto this cog's
queue:

    {"type": "task.groom-proposal", "version": 1, "payload": <GroomingProposal>}

and is applied here, in line with every other write to the board.

**Only to the content it was made for.** A proposal carries the fingerprint
of the task as it was sent. If the task has changed since, the proposal is
not applied: the next sweep sees the change and sends the task again.

**What changes.** The name becomes ``<id> · <title>``; a task without an id
takes the next ``MINI-`` id. The template block is filled from the
proposal, keeping the task's own value for any part the proposal left out,
and its PR line. The proposal's body follows, and then the task's original
text under :data:`ORIGINAL_MARKER`, added the first time only, so a task
groomed twice keeps what its author first wrote. The ``review`` tag comes
off: grooming is the review.

**Where it goes.** The task is linted again as it now reads.

- The proposal has questions: they are posted as one comment starting
  ``Grooming questions:`` and the task moves to Blocked. Answering in a
  comment sends it to be groomed again.
- It passes and ``GROOM_AUTO_READY`` is on: it moves to Ready.
- Otherwise it moves to Refining.

Its grooming record is written with the new content as both what was judged
and what was groomed, so a groomed task is not sent again until someone
changes it.

  REFINING_SECTION  default Refining
  BLOCKED_SECTION   default Blocked
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable, Collection

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import (
    GroomingOutcome,
    GroomingProposal,
    GroomingRecordWrite,
)
from mini_app_polis.asana import AsanaTaskRecord, escape_rich_text, rich_text_body
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import run_report

from .board import Board
from .compose import REVIEW_TAG, TEMPLATE_FIELDS
from .ids import next_id, task_name
from .lint import lint, looks_like_label, parse, task_id_of
from .records import QUESTIONS_PREFIX, answers, fingerprint
from .registry import load_repos
from .sweep import Records, _api, _section_named, auto_ready, ready_section

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "apply-grooming"

#: The prefix a task gets when grooming finds it has no id.
MANUAL_PREFIX = "MINI"

#: Heads the task's original text, below the groomed body.
ORIGINAL_MARKER = "Before grooming:"

DEFAULT_REFINING_SECTION = "Refining"
DEFAULT_BLOCKED_SECTION = "Blocked"

_FIELDS_FROM_PROPOSAL = {
    "Done when": "done_when",
    "Where": "where",
    "Constraints": "constraints",
    "Effort": "effort",
    "Blocked by": "blocked_by",
}


def _section_setting(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def _field_lines(value: str) -> list[str]:
    """A field's value as block lines that read back as that field.

    A blank line would end the block, and a line that reads as a label
    would start the body, so blank lines are dropped and such a line is
    marked as a list item.
    """
    lines = [line.rstrip() for line in value.strip().splitlines() if line.strip()]
    return (
        [
            lines[0],
            *(
                f"- {line.strip()}" if looks_like_label(line) else line
                for line in lines[1:]
            ),
        ]
        if lines
        else [""]
    )


def groomed_text(task: AsanaTaskRecord, proposal: GroomingProposal) -> tuple[str, str]:
    """The task's new template block and body, as plain text."""
    current = parse(task.notes)
    block: list[str] = []
    for label in TEMPLATE_FIELDS:
        attr = _FIELDS_FROM_PROPOSAL.get(label)
        proposed = getattr(proposal, attr) if attr else None
        value = proposed if proposed else current.fields.get(label, "")
        first, *rest = _field_lines(value) if value else [""]
        block.append(f"{label}: {first}".rstrip())
        block.extend(rest)

    if not proposal.body:
        return "\n".join(block), current.body
    original = current.body
    if ORIGINAL_MARKER in original:
        original = original.split(ORIGINAL_MARKER, 1)[1].strip()
    body = proposal.body.strip()
    if original:
        body = f"{body}\n\n{ORIGINAL_MARKER}\n{original}"
    return "\n".join(block), body


def groomed_html(block: str, body: str) -> str:
    """The task's new body: the template block, then the body, escaped."""
    return rich_text_body(escape_rich_text(block), escape_rich_text(body))


def questions_comment(questions: list[str]) -> str:
    """The comment that asks the owner a grooming's questions, numbered."""
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    return (
        f"{QUESTIONS_PREFIX}\n{numbered}\n\n"
        "Answer in a comment below; the task is groomed again with your answers."
    )


def apply_proposal(
    proposal: GroomingProposal,
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
    records_factory: Callable[[], Records] = _api,
    repos_loader: Callable[[], Collection[str]] = load_repos,
) -> None:
    """Apply one proposal to the board, once. Raises to be redelivered."""
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id) as report:
        if not effect_enabled(Effect.ASANA):
            log.info("groom: board writes are off here; not applied")
            report.note("board writes off outside production")
            return

        board = (board_factory or Board.from_env)()
        task = board.get(proposal.task_gid)
        if task is None or task.completed:
            report.note("task gone or done", proposal.task_gid)
            return

        store = records_factory()
        previous = {r.task_gid: r for r in store.list_groomings()}.get(task.gid)
        asked = previous is not None and previous.outcome == "questions"
        replies = answers(board.comments(task.gid)) if asked else []
        if fingerprint(task, replies) != proposal.fingerprint:
            # Changed since it was sent: the next sweep sends it again.
            log.info("groom: %s changed since it was sent; not applied", task.name)
            report.note("task changed since it was sent", task.name)
            return

        names = board.names()
        task_id = task_id_of(task.name) or next_id(MANUAL_PREFIX, names)
        name = task_name(task_id, proposal.title)
        block, body = groomed_text(task, proposal)
        board.update(task.gid, name=name, html_notes=groomed_html(block, body))
        if REVIEW_TAG in {t.lower() for t in task.tag_names}:
            board.untag(task.gid, REVIEW_TAG)

        groomed = board.get(task.gid) or task
        tasks = board.tasks()
        completed_by_id = {
            tid: t.completed for t in tasks if (tid := task_id_of(t.name)) is not None
        }
        problems = lint(
            name=groomed.name,
            notes=groomed.notes,
            tags=groomed.tag_names,
            repos=repos_loader(),
            completed_by_id=completed_by_id,
        )

        sections = board.sections()
        outcome: GroomingOutcome
        moved_at: dt.datetime | None = None
        if proposal.questions:
            board.comment(task.gid, questions_comment(proposal.questions))
            column = _section_named(
                sections, _section_setting("BLOCKED_SECTION", DEFAULT_BLOCKED_SECTION)
            )
            outcome = "questions"
        elif not problems and auto_ready():
            column = _section_named(sections, ready_section())
            moved_at = dt.datetime.now(dt.UTC)
            outcome = "ready"
        else:
            column = _section_named(
                sections, _section_setting("REFINING_SECTION", DEFAULT_REFINING_SECTION)
            )
            outcome = "ready" if not problems else "not_ready"
        if groomed.section_gid != column.gid:
            board.move(task.gid, column.gid)

        current = fingerprint(groomed)
        store.record_grooming(
            GroomingRecordWrite(
                task_gid=task.gid,
                task_id=task_id,
                fingerprint=current,
                outcome=outcome,
                problems=problems,
                moved_to_ready_at=moved_at,
                returned_count=previous.returned_count if previous else 0,
                groomed_fingerprint=current,
            )
        )
        log.info("groom: %s groomed, now in %s", name, column.name)
        report.count("questions", len(proposal.questions))
        report.count("cost_usd", f"{proposal.cost_usd:.4f}")
        report.updated(
            f"task groomed into {column.name}", name, link=board.url(task.gid)
        )
