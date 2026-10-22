"""Turning a source's draft into the task's title, body and tags.

What happens here is the same for every source, which is why it happens in
one place: the board's six-line template block heads every task, so a
groomer fills the blanks in place whatever the task came from.

Everything the draft carries is escaped on the way in. Asana's
``html_notes`` is a restricted HTML subset that answers 400 on malformed
markup, and transcribed or model-written text contains ``<`` and ``&``
often enough to matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from mini_app_polis.api.contract import TaskDraft
from mini_app_polis.asana import escape_rich_text, link, rich_text_body

#: The board's template, in order. A source fills the fields it knows:
#: ``Where`` usually, and ``Done when``, ``Constraints`` and ``Effort`` when
#: it detected the problem itself. The rest are emitted blank, because a
#: blank line is a prompt to fill it and a missing one is a card that does
#: not match the board. ``PR`` and ``Blocked by`` are never a source's.
TEMPLATE_FIELDS: tuple[str, ...] = (
    "Done when",
    "Where",
    "Constraints",
    "Effort",
    "PR",
    "Blocked by",
)

#: Applied when the draft asks for review, or has no title to go by.
REVIEW_TAG = "review"

#: The title used when the draft has none. Asana refuses an empty name, and
#: refusing the job instead would lose the work, so the task lands and says
#: it needs looking at.
EMPTY_TITLE_FALLBACK = "[Needs review]"


@dataclass(frozen=True)
class ComposedTask:
    """A draft, ready for the board: everything but the id."""

    title: str
    html_notes: str
    tag_names: tuple[str, ...]
    due_on: date | None


def _one_line(value: str | None) -> str | None:
    """A field's value on one line, or None when it says nothing.

    A template field is one line: a second line that reads as a label would
    be taken for another field, so line breaks become spaces.
    """
    text = " ".join((value or "").split())
    return text or None


def _template_block(draft: TaskDraft) -> str:
    values = {
        "Done when": _one_line(draft.done_when),
        "Where": _one_line(draft.where),
        "Constraints": _one_line(draft.constraints),
        "Effort": draft.effort,
    }
    lines = []
    for label in TEMPLATE_FIELDS:
        value = values.get(label)
        lines.append(f"{label}: {escape_rich_text(value)}" if value else f"{label}:")
    return "\n".join(lines)


def _section(label: str, value_html: str) -> str:
    """A label directly above its value, so each section reads as one unit."""
    return f"<strong>{escape_rich_text(label)}</strong>\n{value_html}"


def compose(draft: TaskDraft) -> ComposedTask:
    """Compose the task a draft describes."""
    title = draft.title.strip()
    needs_review = draft.needs_review or not title

    parts = [_template_block(draft)]
    if draft.body and draft.body.strip():
        parts.append(escape_rich_text(draft.body.strip()))
    for section in draft.sections:
        parts.append(_section(section.label, escape_rich_text(section.text)))
    if draft.links:
        parts.append(
            _section("Links", "\n".join(link(x.url, x.text) for x in draft.links))
        )

    if needs_review:
        tags: tuple[str, ...] = (REVIEW_TAG,)
    else:
        # Order kept, duplicates dropped: a tag applied twice is a 400.
        seen: dict[str, None] = {}
        for label in draft.labels:
            seen.setdefault(label.strip().lower(), None)
        tags = tuple(seen)

    return ComposedTask(
        title=title or EMPTY_TITLE_FALLBACK,
        html_notes=rich_text_body(*parts),
        tag_names=tags,
        due_on=draft.due_on,
    )
