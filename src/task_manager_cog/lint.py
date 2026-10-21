"""The definition of ready: whether a task says enough to be worked.

The test is whether someone holding only the task and its repositories could
finish it without asking anything. Every task is held to the bar an agent
needs, because a task defined well enough for an agent is defined well
enough for a person. What can be checked mechanically is checked here, with
no model involved; whether the content is *enough* is a judgment, and is
not.

A task is read as the board writes it (compose.py): the template block of
``<Label>: <value>`` lines, then a blank line, then the body. A line inside
the block that starts with no label continues the field above it.

Every rule that fails is reported, not just the first, so one pass says
everything a task is missing.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from .compose import EMPTY_TITLE_FALLBACK, REVIEW_TAG, TEMPLATE_FIELDS
from .ids import SEPARATOR

#: Effort sizes. L is ready like the others: implementing it means creating
#: its subtasks.
EFFORTS: frozenset[str] = frozenset({"S", "M", "L"})

#: What an empty ``Blocked by`` or ``Constraints`` may say instead of nothing.
NONE_WORDS: frozenset[str] = frozenset({"none", "-", "\u2014", "\u2013", "n/a"})

#: A task id as the board names them: a prefixed number (``MINI-12``) or a
#: repository issue (``infra#4``).
_TASK_ID = re.compile(r"\b(?:[A-Z][A-Z0-9]*-\d+|[a-z0-9][a-z0-9._-]*#\d+)\b")

#: A placeholder in ``Done when``: an outcome cannot be still to be decided.
#: A question mark ends a word or the line; one inside a URL's query does not.
_UNDECIDED = re.compile(r"\bTBD\b|\?(?!\w)", re.IGNORECASE)

_LABEL = re.compile(
    r"^(?P<label>"
    + "|".join(re.escape(f) for f in TEMPLATE_FIELDS)
    + r"):\s*(?P<value>.*)$"
)


@dataclass(frozen=True)
class ParsedTask:
    """A task's template fields and body, as written on the board."""

    fields: dict[str, str]
    """Each template label found, mapped to its value ("" when blank)."""

    body: str


def parse(notes: str) -> ParsedTask:
    """Split a task's plain-text body into its template fields and the rest."""
    fields: dict[str, str] = {}
    lines = notes.replace("\r\n", "\n").split("\n")
    current: str | None = None
    end = len(lines)
    for position, line in enumerate(lines):
        if not line.strip():
            if fields:
                # The blank line after the block: the body starts here.
                end = position
                break
            continue
        match = _LABEL.match(line.strip())
        if match:
            current = match["label"]
            fields[current] = match["value"].strip()
        elif current is not None:
            fields[current] = f"{fields[current]}\n{line.strip()}".strip()
        else:
            # Text before any label: there is no block, and this is body.
            return ParsedTask(fields={}, body=notes.strip())
    return ParsedTask(fields=fields, body="\n".join(lines[end:]).strip())


def task_id_of(name: str) -> str | None:
    """The id a task's name starts with, or None for a task with none."""
    head, sep, _ = name.partition(SEPARATOR)
    return head.strip() if sep and _TASK_ID.fullmatch(head.strip()) else None


def where_repos(where: str) -> list[str]:
    """The repositories ``Where`` names: the head of each top-level item.

    Items are separated by commas outside parentheses, and may say more after
    the name: ``api-kaianolevine-com (the notify route), infra``. A path
    after the name is allowed: ``task-manager-cog/src/worker.py``.
    """
    items: list[str] = []
    depth = 0
    current = ""
    for char in where:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += char
    items.append(current)

    repos = []
    for item in items:
        head = item.strip().split("(", 1)[0].split()
        if head:
            repos.append(head[0].split("/", 1)[0].rstrip(":"))
    return repos


def lint(
    *,
    name: str,
    notes: str,
    tags: Collection[str],
    repos: Collection[str],
    completed_by_id: Mapping[str, bool],
) -> list[str]:
    """Every rule the task fails. Empty means ready.

    ``repos`` is the fleet's registry (ecosystem.yaml); ``completed_by_id``
    maps every task id on the board to whether that task is done.
    """
    problems: list[str] = []

    title = name.partition(SEPARATOR)[2].strip() if task_id_of(name) else ""
    if task_id_of(name) is None:
        problems.append("title has no id")
    elif not title or title == EMPTY_TITLE_FALLBACK:
        problems.append("title says nothing")

    if REVIEW_TAG in {t.lower() for t in tags}:
        problems.append(f"tagged {REVIEW_TAG}")

    parsed = parse(notes)
    missing = [f for f in TEMPLATE_FIELDS if f not in parsed.fields]
    if missing:
        problems.append(f"template is missing {', '.join(missing)}")
    fields = parsed.fields

    done_when = fields.get("Done when", "")
    if not done_when:
        problems.append("Done when is blank")
    elif _UNDECIDED.search(done_when):
        problems.append("Done when is undecided (TBD or ?)")

    where = fields.get("Where", "")
    if not where:
        problems.append("Where is blank")
    else:
        unknown = [r for r in where_repos(where) if r not in repos]
        if unknown:
            problems.append(f"Where names no repository called {', '.join(unknown)}")

    if not fields.get("Constraints", ""):
        problems.append("Constraints is blank (say None if there are none)")

    effort = fields.get("Effort", "")
    if effort.upper() not in EFFORTS:
        problems.append(f"Effort is {effort or 'blank'}, not S, M or L")

    if fields.get("PR", ""):
        problems.append("PR is filled in before the work started")

    blocked_by = fields.get("Blocked by", "")
    if blocked_by and blocked_by.lower() not in NONE_WORDS:
        ids = _TASK_ID.findall(blocked_by)
        if not ids:
            problems.append("Blocked by names no task")
        for task_id in ids:
            if task_id not in completed_by_id:
                problems.append(f"Blocked by {task_id}, which is not on the board")
            elif not completed_by_id[task_id]:
                problems.append(f"Blocked by {task_id}, which is not done")

    if not parsed.body:
        problems.append("no body saying why, with links to what is needed")

    return problems
