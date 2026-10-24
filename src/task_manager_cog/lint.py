"""The definition of ready: whether a task says enough to be worked.

The test is whether someone holding only the task and its repositories could
finish it without asking anything. Every task is held to the bar an agent
needs, because a task defined well enough for an agent is defined well
enough for a person. What can be checked mechanically is checked here, with
no model involved; whether the content is *enough* is a judgment, and is
not.

A task is read as the board writes it (compose.py): the template block of
``<Label>: <value>`` lines, then a blank line, then the body. A line inside
the block that starts with no label continues the field above it. A line
shaped like a label the template does not have (``Why: …``) is body that
was written inside the block: it and the lines that continue it are read as
body, not as part of the field above, and the template fields around it are
still read.

Every rule that fails is reported, not just the first, so one pass says
everything a task is missing.

**Shapes.** With ``shapes`` on, a task must also be one of two shapes:

- a *leaf*: Where names at most one repository, and the task carries at
  most one executor tag. Only a task tagged for another executor (board or
  console work) may name no repository.
- a *compound* task: its Done when says it is done when its children are
  (``MINI-1, MINI-2 and MINI-3 are done``), and its Blocked by lists every
  one of them. It has no work of its own, so it may span repositories.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import get_args

from mini_app_polis.api.contract import GroomingExecutor

from .compose import EMPTY_TITLE_FALLBACK, REVIEW_TAG, TEMPLATE_FIELDS
from .ids import SEPARATOR

#: Effort sizes. L is ready like the others: implementing it means creating
#: its subtasks.
EFFORTS: frozenset[str] = frozenset({"S", "M", "L"})

#: Ends the problem for a blocker that is on the board and not done yet.
NOT_DONE = ", which is not done"

#: Starts the problem for a task that waits on a date still ahead.
BLOCKED_UNTIL = "Blocked until "

#: What an empty ``Blocked by`` or ``Constraints`` may say instead of nothing.
NONE_WORDS: frozenset[str] = frozenset({"none", "-", "\u2014", "\u2013", "n/a"})

#: A task id as the board names them: a prefixed number (``MINI-12``) or a
#: repository issue (``infra#4``).
_TASK_ID = re.compile(r"\b(?:[A-Z][A-Z0-9]*-\d+|[a-z0-9][a-z0-9._-]*#\d+)\b")

#: A date in ``Blocked by``: the task waits until that UTC day.
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Anything in ``Blocked by`` written as a date, well formed or not
#: (``2026-11-2``, ``2026-13-01``, ``11/02/2026``).
_DATE_LIKE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")

#: Text that is only task ids and the words joining them, as a compound
#: task's Done when lists its children before "are done".
_ID_LIST = re.compile(r"(?:[A-Z][A-Z0-9]*-\d+|[a-z0-9][a-z0-9._-]*#\d+|,|\band\b|\s)+")
_DONE_CLAUSE = re.compile(r"\s*(?P<ids>.+?)\s+(?:are|is)\s+done\b", re.DOTALL)

#: A placeholder in ``Done when``: an outcome cannot be still to be decided.
#: A question mark ends a word or the line; one inside a URL's query does not.
_UNDECIDED = re.compile(r"\bTBD\b|\?(?!\w)", re.IGNORECASE)

#: Heads of a section in a task's body that lists what is still undecided,
#: matched at the start of a line, ignoring case. A head with no colon of
#: its own may say more before one (``Still open, for the owner:``).
OPEN_SECTION_HEADS: tuple[str, ...] = (
    "Open decisions:",
    "Open questions:",
    "Still open",
)

#: Heads the task's original text, kept below the groomed body. What follows
#: it is the author's text before grooming, not the task's current state.
ORIGINAL_MARKER = "Before grooming:"

#: A list item's marker: ``-``, ``*``, ``•``, ``1.`` or ``1)``.
_LIST_MARKER = re.compile(r"^(?:[-*\u2022]|\d+[.)])\s*")

#: A line that reads as ``<Word or two>: …``: a label, known or not.
_ANY_LABEL = re.compile(r"^[A-Z][A-Za-z]*(?: [a-z]+)?:(?:\s|$)")

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
    stray: list[str] = []
    lines = notes.replace("\r\n", "\n").split("\n")
    current: str | None = None
    in_stray = False
    end = len(lines)
    for position, line in enumerate(lines):
        text = line.strip()
        if not text:
            if fields:
                # The blank line after the block: the body starts here.
                end = position
                break
            continue
        match = _LABEL.match(text)
        if match:
            current, in_stray = match["label"], False
            fields[current] = match["value"].strip()
        elif current is None and not in_stray:
            # Text before any label: there is no block, and this is body.
            return ParsedTask(fields={}, body=notes.strip())
        elif in_stray or _ANY_LABEL.match(text):
            # A label the template does not have, and what continues it.
            in_stray = True
            stray.append(text)
        elif current is not None:
            fields[current] = f"{fields[current]}\n{text}".strip()
    body = "\n".join([*stray, "\n".join(lines[end:]).strip()]).strip()
    return ParsedTask(fields=fields, body=body)


def _is_item(text: str) -> bool:
    """Whether a line under an open section names something still open."""
    item = _LIST_MARKER.sub("", text.strip()).strip().rstrip(".")
    return bool(item) and item.lower() not in NONE_WORDS


def open_section(body: str) -> str | None:
    """The head of the first open-questions section in ``body`` that lists
    anything, or None.

    The section's items are what follows the head on its line, and the lines
    after it up to the next blank line. Nothing from the
    :data:`ORIGINAL_MARKER` line on is read: that is the author's text from
    before grooming. A head with nothing after it, or only
    ``None``, is an emptied section and lists nothing.
    """
    lines = body.replace("\r\n", "\n").split("\n")
    original = next(
        (i for i, line in enumerate(lines) if line.strip() == ORIGINAL_MARKER),
        len(lines),
    )
    lines = lines[:original]
    for position, line in enumerate(lines):
        text = line.strip()
        head = next(
            (h for h in OPEN_SECTION_HEADS if text.lower().startswith(h.lower())),
            None,
        )
        if head is None:
            continue
        rest = text[len(head) :]
        if not head.endswith(":"):
            if rest[:1].isalnum():
                continue  # A longer word: "Still opening …".
            rest = rest.split(":", 1)[1] if ":" in rest else ""
        items = [rest]
        for following in lines[position + 1 :]:
            if not following.strip():
                break
            items.append(following)
        if any(_is_item(item) for item in items):
            return head
    return None


def looks_like_label(line: str) -> bool:
    """Whether a line reads as ``<Label>: …``, a template label or not."""
    return bool(_ANY_LABEL.match(line.strip()))


def mentioned_ids(text: str) -> list[str]:
    """Every task id the text names (``MINI-12``, ``infra#4``), in order, once."""
    return list(dict.fromkeys(_TASK_ID.findall(text)))


def task_id_of(name: str) -> str | None:
    """The id a task's name starts with, or None for a task with none."""
    head, sep, _ = name.partition(SEPARATOR)
    return head.strip() if sep and _TASK_ID.fullmatch(head.strip()) else None


def where_repos(where: str) -> list[str]:
    """The repositories ``Where`` names: the head of each top-level item.

    Items are separated by commas or semicolons outside parentheses, and
    may say more after the name: ``api-kaianolevine-com (the notify route),
    infra``. A path after the name is allowed:
    ``task-manager-cog/src/worker.py``.
    """
    items: list[str] = []
    depth = 0
    current = ""
    for char in where:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in ",;" and depth == 0:
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


def executor_of(tags: Collection[str]) -> str | None:
    """The executor a task is tagged with, or None: the agent may take it.

    The names are the shared contract's (``GroomingExecutor``). A tag never
    makes a task less ready; it only says who may work it.
    """
    names = get_args(GroomingExecutor)
    return next((t.lower() for t in tags if t.lower() in names), None)


def children_of(done_when: str) -> list[str]:
    """The children a compound task's Done when names, or [] for a leaf.

    A compound task's Done when opens with its children's ids and says they
    are done: ``MINI-183, MINI-184 and MINI-186 are done. Between them: …``.
    """
    match = _DONE_CLAUSE.match(done_when)
    if match is None or not _ID_LIST.fullmatch(match["ids"]):
        return []
    return list(dict.fromkeys(_TASK_ID.findall(match["ids"])))


def _shape_problems(
    *, done_when: str, where: str, blocked_ids: Collection[str], tags: Collection[str]
) -> list[str]:
    """What keeps a task from being a leaf or a compound task."""
    problems: list[str] = []
    names = get_args(GroomingExecutor)
    executors = sorted({t.lower() for t in tags if t.lower() in names})
    if len(executors) > 1:
        problems.append(f"tagged for more than one executor ({', '.join(executors)})")

    children = children_of(done_when)
    if children:
        for child in children:
            if child not in blocked_ids:
                problems.append(f"compound task's Blocked by does not list {child}")
        return problems

    repos = list(dict.fromkeys(where_repos(where))) if where else []
    if len(repos) > 1:
        problems.append(
            f"Where names more than one repository ({', '.join(repos)}): a leaf "
            "task names one, so this is a compound task over one task each"
        )
    return problems


def only_blocked(problems: Collection[str]) -> bool:
    """Whether every problem is a blocker that is on the board and not done,
    or a date still ahead.

    Such a task is complete but waiting: grooming cannot finish its blocker
    or bring its date closer.
    """
    return bool(problems) and all(
        (p.startswith("Blocked by ") and p.endswith(NOT_DONE))
        or p.startswith(BLOCKED_UNTIL)
        for p in problems
    )


def _blocking_dates(blocked_by: str, today: dt.date) -> tuple[list[str], int]:
    """The problems the dates in ``Blocked by`` raise, and how many it names.

    A date ahead of ``today`` blocks; one on or before it no longer does. A
    date that is not ``YYYY-MM-DD``, or is no real day, is a problem of its
    own.
    """
    problems: list[str] = []
    tokens = _DATE_LIKE.findall(blocked_by)
    for token in tokens:
        try:
            day = dt.date.fromisoformat(token) if _DATE.fullmatch(token) else None
        except ValueError:
            day = None
        if day is None:
            problems.append(f"Blocked by {token}, which is not a date (YYYY-MM-DD)")
        elif day > today:
            problems.append(f"{BLOCKED_UNTIL}{day.isoformat()}")
    return problems, len(tokens)


def lint(
    *,
    name: str,
    notes: str,
    tags: Collection[str],
    repos: Collection[str],
    completed_by_id: Mapping[str, bool],
    today: dt.date | None = None,
    shapes: bool = False,
) -> list[str]:
    """Every rule the task fails. Empty means ready.

    ``repos`` is the fleet's registry (ecosystem.yaml); ``completed_by_id``
    maps every task id on the board to whether that task is done. ``today``
    is the UTC day a date in ``Blocked by`` is judged against (default: now).
    ``shapes`` also holds the task to the leaf or compound shape.
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
    no_repository = not where or where.lower() in NONE_WORDS
    if shapes and no_repository and executor_of(tags) is not None:
        # Board or console work: a leaf with no repository.
        where = ""
    elif not where:
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
        date_problems, dates = _blocking_dates(
            blocked_by, today or dt.datetime.now(dt.UTC).date()
        )
        if not ids and not dates:
            problems.append("Blocked by names no task or date")
        for task_id in ids:
            if task_id not in completed_by_id:
                problems.append(f"Blocked by {task_id}, which is not on the board")
            elif not completed_by_id[task_id]:
                problems.append(f"Blocked by {task_id}{NOT_DONE}")
        problems.extend(date_problems)

    if not parsed.body:
        problems.append("no body saying why, with links to what is needed")
    elif (head := open_section(parsed.body)) is not None:
        problems.append(f"body lists open questions ({head.rstrip(':')})")

    if shapes:
        problems.extend(
            _shape_problems(
                done_when=done_when,
                where=where,
                blocked_ids=_TASK_ID.findall(blocked_by),
                tags=tags,
            )
        )

    return problems
