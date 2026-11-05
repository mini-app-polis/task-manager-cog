"""A ``Grooming questions:`` comment holds its task like a refining one."""

from __future__ import annotations

from task_manager_cog.records import answers, unanswered


def test_old_grooming_questions_still_hold_their_task() -> None:
    comments = ["Grooming questions:\n1. Which repo?"]
    assert unanswered(comments)
    assert answers([*comments, "infra"]) == ["infra"]
