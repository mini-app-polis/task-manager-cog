"""Applying a split: a groomer's proposal that divides its task into children.

A task that is neither a leaf (one executor; one repository for the agent) nor a compound
task comes back from grooming with children. Each child is created on the
board as a leaf, in the template, with its own :data:`MANUAL_PREFIX` id and executor
tag; a child that waits on an earlier one names it in Blocked by. The task
itself is rewritten as their compound task: done when its children are,
blocked by every one of them, with no work of its own.

**Dry run first.** Unless ``GROOM_SPLIT_DRY_RUN`` is ``false``, a split is
only reported: the children it would create are posted on the task as one
comment and named in the run report, and nothing is created.

**A cap.** A split with more children than ``GROOM_SPLIT_MAX_CHILDREN`` is
not applied: it is reported on the task and in the run report.

**Waits it names.** A child whose title, Done when or body says it waits
("waits on", "wait on", "after the … child") but names nothing it is
blocked by, neither an earlier child nor an existing task or date, would be
created with an empty Blocked by and moved to Ready ahead of what it waits
for. Such a proposal is inconsistent and is reported like a split over the
cap, naming each such child. The check reads the proposal only.

Only the child's own waiting counts. A wait whose object is the child itself
("… wait on this", "… waits on it", "… wait on this child") or whose subject,
just before the verb in the same clause, is other children ("the service
children wait on …", "every later child waits on …", "the others start after
the … child") says what waits on this child, not what it waits on, and is
not counted. Anything else that says it waits is: when in doubt the check
still refuses, so a real wait is not missed. It is a word rule, not a parse:
a wait whose subject sits more than two words before the verb, or that uses
another word for "this", is still counted.

**Questions first.** A proposal with children and questions is not split:
its questions are posted as one ``Grooming questions:`` comment with the
children listed under them, and any child that says it waits but names no
blocker, and the task moves to Refining and waits on its owner. Answering
sends it to be groomed again, and that grooming may split it.

A reported split waits on its owner as grooming questions do: the task is
recorded as having questions, and is not groomed again until the owner
answers in a comment (approving it, or correcting it) or edits the task.
The next sweep then sends it to be groomed again with the answers.

**Once.** Each child is created with an external id made of its parent, the
content the split was made for, and its position, so a redelivered proposal
finds the children it already created and creates no second set. Once the
parent is rewritten, a redelivery does not match its new content and is not
applied at all.

The children are recorded as groomed, so the sweep moves the ones that pass
the definition of ready without sending them to be groomed again.

  GROOM_SPLIT_DRY_RUN       "false" to create children; anything else reports
  GROOM_SPLIT_MAX_CHILDREN  children one split may create (default: 25)
  BLOCKED_SECTION           where a compound task waits (default: Blocked)
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import (
    GroomingChild,
    GroomingOutcome,
    GroomingProposal,
    GroomingRecord,
    GroomingRecordWrite,
)
from mini_app_polis.asana import AsanaTaskRecord
from mini_app_polis.pipeline_status import RunReport

from .board import Board
from .compose import REVIEW_TAG, ComposedTask
from .executor import AGENT, plain_line
from .groom import MANUAL_PREFIX, groomed_html, groomed_text, questions_comment
from .ids import next_id, task_name
from .lint import NONE_WORDS, lint, only_blocked, parse, task_id_of, where_repos
from .records import SPLIT_REPORT_PREFIX, fingerprint
from .sweep import Records, _section_named, blocked_section, task_shapes

log = logger_mod.get_logger()

DEFAULT_MAX_CHILDREN = 25

#: Starts the comment a dry run leaves; with the content it was made for,
#: so a redelivered proposal posts no second one.
DRY_RUN_MARKER = SPLIT_REPORT_PREFIX

#: What a compound task's Constraints say.
COMPOUND_CONSTRAINTS = "A compound task: no work of its own."

_EFFORT_ORDER = ("S", "M", "L")

#: What a child says when it waits on something: "waits on", "wait on",
#: "after the … child". A match counts only when it is the child's own wait:
#: see :data:`_WAITED_ON_BY_OTHERS` and :data:`_OTHERS_WAIT`.
_WAIT_WORDS = re.compile(r"\bwaits? on\b|\bafter the\b[^.\n]*?\bchild\b", re.I)

#: After "wait(s) on": the child itself ("this", "it", "this child", "this
#: task", "this one"), then the end of the clause or a word that does not
#: make "this" a determiner ("to", "first", "landing", …). "this week's
#: release" or "this repo" is something else, and still counts.
_WAITED_ON_BY_OTHERS = re.compile(
    r"\s+(?:it|this(?:\s+(?:child|task|one))?)"
    r"(?=\s*(?:$|[.;:,!?)\n]|(?:to|first|before|and|or|being|so)\b|\w+ing\b))",
    re.I,
)

#: Just before the verb, in the same clause: other children are the subject
#: ("the service children", "the others", "the rest", "every later child",
#: "each other child"), with at most two words between them and the wait.
#: "This child" is not on the list: it is the child's own wait.
_OTHERS_WAIT = re.compile(
    r"(?:\bchildren|\bothers|\bthe rest"
    r"|\b(?:every|each|later|other|subsequent|following|remaining)\b"
    r"[^.;:,!?\n]*?\bchild)"
    r"\s+(?:[\w'-]+\s+){0,2}$",
    re.I,
)

_CLAUSE_END = re.compile(r"[.;:,!?\n]")


def dry_run() -> bool:
    """Whether a split is only reported: ``GROOM_SPLIT_DRY_RUN``, on unless false."""
    return (os.environ.get("GROOM_SPLIT_DRY_RUN") or "").strip().lower() != "false"


def max_children() -> int:
    """The most children one split may create: ``GROOM_SPLIT_MAX_CHILDREN``."""
    raw = (os.environ.get("GROOM_SPLIT_MAX_CHILDREN") or "").strip()
    return max(0, int(raw)) if raw else DEFAULT_MAX_CHILDREN


def external_id(parent_gid: str, content: str, position: int) -> str:
    """A child's external id: its parent, the split's content, its place."""
    return f"split.{parent_gid}.{content[:16]}.{position}"


def _join(ids: list[str]) -> str:
    """``A``, ``A and B``, ``A, B and C``."""
    return ids[0] if len(ids) == 1 else f"{', '.join(ids[:-1])} and {ids[-1]}"


def child_blocked_by(child: GroomingChild, ids: list[str]) -> str:
    """A child's Blocked by: the earlier children it waits on, by id, then
    the existing tasks and dates the proposal named."""
    waits = [ids[s] for s in child.blocked_by_siblings]
    own = (child.blocked_by or "").strip()
    if own and own.lower() not in NONE_WORDS:
        waits.append(own)
    return ", ".join(waits) or "None"


def _names_a_blocker(child: GroomingChild) -> bool:
    own = (child.blocked_by or "").strip()
    return bool(child.blocked_by_siblings) or (
        bool(own) and own.lower() not in NONE_WORDS
    )


def _own_wait(text: str) -> re.Match[str] | None:
    """The first place ``text`` says the child itself waits, if any."""
    for match in _WAIT_WORDS.finditer(text):
        if match[0].lower().endswith("on") and _WAITED_ON_BY_OTHERS.match(
            text, match.end()
        ):
            continue
        before = text[: match.start()]
        clause = _CLAUSE_END.split(before)[-1]
        if _OTHERS_WAIT.search(clause):
            continue
        return match
    return None


def unblocked_waits(proposal: GroomingProposal) -> list[str]:
    """Each child that says it waits but names no blocker, and where it says so."""
    found = []
    for position, child in enumerate(proposal.children):
        if _names_a_blocker(child):
            continue
        for part, text in (
            ("title", child.title),
            ("Done when", child.done_when),
            ("body", child.body),
        ):
            if match := _own_wait(text):
                found.append(
                    f'child {position} ({child.title}) says "{match[0]}" in its '
                    f"{part} but names no blocker"
                )
                break
    return found


def child_notes(
    child: GroomingChild, ids: list[str], parent_id: str, parent_title: str
) -> tuple[str, str]:
    """A child's template block and body, as plain text."""
    block = "\n".join(
        [
            f"Done when: {_one_line(child.done_when)}",
            f"Where: {_one_line(child.where)}",
            f"Constraints: {_one_line(child.constraints)}",
            f"Effort: {child.effort}",
            "PR:",
            f"Blocked by: {child_blocked_by(child, ids)}",
            plain_line(child.executor or AGENT),
        ]
    )
    body = f"{child.body.strip()}\n\nPart of {parent_id} · {parent_title}."
    return block, body


def _one_line(value: str) -> str:
    return " ".join(value.split())


def compound_fields(
    children: list[GroomingChild], ids: list[str], current_where: str
) -> dict[str, str]:
    """What the parent's template says once it is their compound task."""
    repos = list(
        dict.fromkeys(
            repo
            for child in children
            if child.where.strip().lower() not in NONE_WORDS
            for repo in where_repos(child.where)
        )
    )
    effort = max((c.effort for c in children), key=_EFFORT_ORDER.index, default="S")
    return {
        "done_when": f"{_join(ids)} {'is' if len(ids) == 1 else 'are'} done.",
        "where": ", ".join(repos) or current_where,
        "constraints": COMPOUND_CONSTRAINTS,
        "effort": effort,
        "blocked_by": ", ".join(ids),
    }


def child_lines(proposal: GroomingProposal) -> list[str]:
    """The split as it would be created, one child a line."""
    lines = []
    for position, child in enumerate(proposal.children):
        waits = ", ".join(str(s) for s in child.blocked_by_siblings) or "none"
        lines.append(
            f"{position}. {child.title} — {child.where}; effort {child.effort}; "
            f"executor {child.executor or 'agent'}; waits on children {waits}"
            + (f"; blocked by {child.blocked_by}" if child.blocked_by else "")
        )
    return lines


def dry_run_comment(proposal: GroomingProposal, why: str) -> str:
    """The split as it would be created, one child a line."""
    first = f"{DRY_RUN_MARKER}: {why}; content {proposal.fingerprint[:12]})"
    return "\n".join([first, *child_lines(proposal)])


def _groomed_record(
    task: AsanaTaskRecord,
    task_id: str | None,
    problems: list[str],
    previous: GroomingRecord | None,
) -> GroomingRecordWrite:
    """A record saying the task, as it reads now, is groomed."""
    current = fingerprint(task)
    outcome: GroomingOutcome = "ready" if not problems else "not_ready"
    return GroomingRecordWrite(
        task_gid=task.gid,
        task_id=task_id,
        fingerprint=current,
        outcome=outcome,
        problems=problems,
        returned_count=previous.returned_count if previous else 0,
        groomed_fingerprint=current,
    )


def _not_applied(
    task: AsanaTaskRecord,
    proposal: GroomingProposal,
    why: str,
    *,
    board: Board,
    store: Records,
    previous: GroomingRecord | None,
) -> None:
    """Report a split without creating it; the task waits on its owner."""
    marker = f"content {proposal.fingerprint[:12]})"
    if not any(
        c.startswith(DRY_RUN_MARKER) and marker in c for c in board.comments(task.gid)
    ):
        board.comment(task.gid, dry_run_comment(proposal, why))
    store.record_grooming(
        _groomed_record(
            task,
            task_id_of(task.name),
            list(previous.problems) if previous else [],
            previous,
        ).model_copy(update={"outcome": "questions"})
    )


def ask_before_splitting(
    proposal: GroomingProposal,
    task: AsanaTaskRecord,
    *,
    board: Board,
    store: Records,
    previous: GroomingRecord | None,
    report: RunReport,
) -> None:
    """Post a split's questions, with its children under them; create nothing.

    The task is left as it reads, moved to Refining, and recorded as having
    questions, so an answer sends it to be groomed again.
    """
    label = task_id_of(task.name) or task.name
    count = len(proposal.children)
    split = child_lines(proposal) + [f"- {w}" for w in unblocked_waits(proposal)]
    text = questions_comment(list(proposal.questions), split)
    if text not in board.comments(task.gid):
        board.comment(task.gid, text)
    column = _section_named(board.sections(), _refining())
    if task.section_gid != column.gid:
        board.move(task.gid, column.gid)
    store.record_grooming(
        _groomed_record(
            task,
            task_id_of(task.name),
            list(previous.problems) if previous else [],
            previous,
        ).model_copy(update={"outcome": "questions"})
    )
    report.count("questions", len(proposal.questions))
    report.count("children_proposed", count)
    report.count("cost_usd", f"{proposal.cost_usd:.4f}")
    report.updated(
        f"split proposed with questions, now in {column.name}",
        f"{label} children={count}",
        link=board.url(task.gid),
    )


def apply_split(
    proposal: GroomingProposal,
    task: AsanaTaskRecord,
    *,
    board: Board,
    store: Records,
    previous: GroomingRecord | None,
    repos: Collection[str],
    report: RunReport,
) -> None:
    """Create a split's children and rewrite its task as their compound task."""
    label = task_id_of(task.name) or task.name
    count = len(proposal.children)
    if count > max_children():
        why = f"{count} children is over GROOM_SPLIT_MAX_CHILDREN ({max_children()})"
        _not_applied(task, proposal, why, board=board, store=store, previous=previous)
        report.issue("split over the cap; not applied", label, detail=why)
        return
    if waits := unblocked_waits(proposal):
        why = "; ".join(waits)
        _not_applied(task, proposal, why, board=board, store=store, previous=previous)
        report.issue(
            "split names waits it has no blocker for; not applied", label, detail=why
        )
        return
    if dry_run():
        why = "GROOM_SPLIT_DRY_RUN is on"
        _not_applied(task, proposal, why, board=board, store=store, previous=previous)
        report.count("children_proposed", count)
        report.count("cost_usd", f"{proposal.cost_usd:.4f}")
        report.updated(
            "split proposed (dry run)",
            f"{label} children={count}",
            link=board.url(task.gid),
        )
        return

    names = list(board.names())
    parent_id = task_id_of(task.name) or next_id(MANUAL_PREFIX, names)
    if task_id_of(task.name) is None:
        names.append(task_name(parent_id, proposal.title))

    # Every child's id first: a child names earlier ones in Blocked by.
    ids: list[str] = []
    found: dict[int, str] = {}
    for position, child in enumerate(proposal.children):
        gid = board.find(external_id(task.gid, proposal.fingerprint, position))
        existing = board.get(gid) if gid else None
        existing_id = task_id_of(existing.name) if existing else None
        if gid and existing_id:
            found[position] = gid
            ids.append(existing_id)
            continue
        child_id = next_id(MANUAL_PREFIX, names)
        names.append(task_name(child_id, child.title))
        ids.append(child_id)

    completed_by_id: Mapping[str, bool] = {
        tid: t.completed for t in board.tasks() if (tid := task_id_of(t.name))
    }
    shapes = task_shapes()
    for position, child in enumerate(proposal.children):
        if position in found:
            continue
        block, body = child_notes(child, ids, parent_id, proposal.title)
        gid = board.create(
            task_name(ids[position], child.title),
            ComposedTask(
                title=child.title,
                html_notes=groomed_html(block, body),
                tag_names=(child.executor,) if child.executor else (),
                due_on=None,
            ),
            external_id=external_id(task.gid, proposal.fingerprint, position),
        )
        created = board.get(gid)
        if created is not None:
            problems = lint(
                name=created.name,
                notes=created.notes,
                tags=created.tag_names,
                repos=repos,
                completed_by_id={**completed_by_id, **dict.fromkeys(ids, False)},
                shapes=shapes,
            )
            store.record_grooming(
                _groomed_record(created, ids[position], problems, None)
            )
        report.created(
            "child task", task_name(ids[position], child.title), link=board.url(gid)
        )
        log.info("split: created %s for %s", ids[position], label)

    current_where = parse(task.notes).fields.get("Where", "")
    compound = proposal.model_copy(
        update={
            **compound_fields(list(proposal.children), ids, current_where),
            "children": [],
            "executor": None,
        }
    )
    block, body = groomed_text(task, compound)
    name = task_name(parent_id, proposal.title)
    board.update(task.gid, name=name, html_notes=groomed_html(block, body))
    if REVIEW_TAG in {t.lower() for t in task.tag_names}:
        board.untag(task.gid, REVIEW_TAG)

    rewritten = board.get(task.gid) or task
    problems = lint(
        name=rewritten.name,
        notes=rewritten.notes,
        tags=rewritten.tag_names,
        repos=repos,
        completed_by_id={**completed_by_id, **dict.fromkeys(ids, False)},
        shapes=shapes,
    )
    sections = board.sections()
    column = _section_named(
        sections,
        blocked_section() if only_blocked(problems) else _refining(),
    )
    if rewritten.section_gid != column.gid:
        board.move(task.gid, column.gid)
    store.record_grooming(_groomed_record(rewritten, parent_id, problems, previous))
    report.count("children_created", count - len(found))
    report.updated(
        f"task split into {count}, now in {column.name}", name, link=board.url(task.gid)
    )


def _refining() -> str:
    return (os.environ.get("REFINING_SECTION") or "").strip() or "Refining"
