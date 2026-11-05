"""Comments already on the board that say grooming.

Grooming was renamed refining. Tasks still held by a ``Grooming questions:``
comment stay held until someone answers it. This test goes when no open
task depends on that.
"""

from __future__ import annotations

from task_manager_cog.records import answers, unanswered


def test_old_grooming_questions_still_hold_their_task() -> None:
    comments = ["Grooming questions:\n1. Which repo?"]
    assert unanswered(comments)
    assert answers([*comments, "infra"]) == ["infra"]
