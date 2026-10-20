"""Task ids: reading the ones in use, minting the next, naming the task.

A task's name is ``<id> · <title>``. The id is the part an automation
parses, so it leads, and the separator is fixed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Between the id and the title in every task name.
SEPARATOR = " · "


def task_name(task_id: str, title: str) -> str:
    """The task's name on the board."""
    return f"{task_id}{SEPARATOR}{title}"


def next_id(prefix: str, names: Iterable[str]) -> str:
    """The next id for ``prefix``: the highest in use, plus one.

    ``names`` must include completed tasks, so a finished task keeps its id.
    A name counts when it starts with ``<prefix>-<n>`` followed by a space or
    nothing, so a name edited by hand to "VN-3 - title" still holds VN-3,
    while "VN-3x" or "VNX-3" do not.
    """
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)(?=\s|$)")
    highest = 0
    for name in names:
        match = pattern.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1}"
