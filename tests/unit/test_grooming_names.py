"""A ``Grooming questions:`` comment holds its task like a refining one."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pytest

from task_manager_cog import sweep as sweep_mod
from task_manager_cog.records import (
    NOT_READY_PREFIX,
    SENT_BACK_PREFIX,
    answers,
    unanswered,
)
from task_manager_cog.sweep import sweep

from .test_refine import VAGUE, _apply, _proposal
from .test_sweep import (
    REPOS,
    FakeBoard,
    FakeRecords,
    _moved_record,
    _stored,
    _task,
)


def test_old_grooming_questions_still_hold_their_task() -> None:
    comments = ["Grooming questions:\n1. Which repo?"]
    assert unanswered(comments)
    assert answers([*comments, "infra"]) == ["infra"]


# ── apply-refining and the sweep agree on Ready ──────────────────────────

GROOMING = "Grooming questions:\n1. Which repo?"


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)
    monkeypatch.setenv("REFINE_AUTO_READY", "true")


def test_the_cogs_own_readiness_comments_answer_nothing() -> None:
    sent_back = f"{SENT_BACK_PREFIX} this task no longer meets the definition."
    not_ready = f"{NOT_READY_PREFIX}\n- Where is empty"
    assert unanswered([GROOMING, sent_back, not_ready])
    assert answers([GROOMING, sent_back, "infra"]) == ["infra"]


def test_apply_refining_holds_a_passing_task_with_open_grooming_questions(
    production, _no_outbound_reports
) -> None:
    board = FakeBoard([VAGUE], comments_={"7": [GROOMING]})
    store = FakeRecords()
    _apply(_proposal(), board, store)

    task = board.get("7")
    assert task is not None and task.section_name == "Refining"
    assert board.comments("7") == [GROOMING]  # nothing posted after the questions
    record = store.stored["7"]
    assert record.outcome == "not_ready"
    assert record.problems == [sweep_mod.OPEN_QUESTIONS]
    assert record.moved_to_ready_at is None
    ((_, kwargs),) = _no_outbound_reports
    assert "task refined into Refining" in kwargs["text"]
    assert "task refined into Ready" not in kwargs["text"]

    # The sweeps that follow, with nothing changed, agree and stay agreed.
    for _ in range(2):
        result = sweep(
            board,
            repos=REPOS,
            move=True,
            records=dict(store.stored),
        )
        assert result.verdicts[0].awaiting_owner
        assert board.moves == [("7", "s-refining")]
        assert result.writes == [] and result.sent_back == [] and result.returned == []


@dataclass
class StuckBoard(FakeBoard):
    """A board whose moves do not happen."""

    def move(self, task_gid: str, section_gid: str) -> None:
        self.moves.append((task_gid, section_gid))


def test_apply_refining_reports_where_a_task_is_when_its_move_does_not_happen(
    production, _no_outbound_reports
) -> None:
    board = StuckBoard([VAGUE])
    store = FakeRecords()
    _apply(_proposal(), board, store)

    assert board.moves == [("7", "s-ready")]
    task = board.get("7")
    assert task is not None and task.section_name == "Inbox"
    assert store.stored["7"].moved_to_ready_at is None
    ((_, kwargs),) = _no_outbound_reports
    text = kwargs["text"]
    assert "task refined into Inbox" in text
    assert "task refined into Ready" not in text
    assert "refined task not moved to Ready" in text


def test_only_a_task_in_ready_when_the_sweep_starts_is_sent_back() -> None:
    inbox = _task("1", "TASK-1 · Questions out")  # passes the linter
    board = FakeBoard([inbox], comments_={"1": [GROOMING]})
    result = sweep(board, repos=REPOS, move=True)
    assert result.sent_back == [] and result.returned == []
    assert board.moves == []
    assert not any(c.startswith(SENT_BACK_PREFIX) for c in board.comments("1"))

    ready = dataclasses.replace(inbox, section_gid="s-ready", section_name="Ready")
    board = FakeBoard([ready], comments_={"1": [GROOMING]})
    result = sweep(board, repos=REPOS, move=True, records={"1": _moved_record(ready)})
    assert [(t.gid, c) for t, c in result.sent_back] == [("1", "Refining")]
    assert board.comments("1")[-1].startswith(SENT_BACK_PREFIX)
    (write,) = result.writes
    assert write.returned_count == 1


def test_a_sent_back_comment_does_not_answer_the_questions_that_sent_it_back() -> None:
    ready = _task("1", "TASK-1 · Questions out", section="Ready")
    board = FakeBoard([ready], comments_={"1": [GROOMING]})
    first = sweep(board, repos=REPOS, move=True, records={"1": _moved_record(ready)})
    assert [(t.gid, c) for t, c in first.sent_back] == [("1", "Refining")]
    (write,) = first.writes

    # Nothing changes: the next sweeps keep it out of Ready and count nothing.
    records = {"1": _stored(write)}
    for _ in range(2):
        again = sweep(board, repos=REPOS, move=True, records=records)
        assert again.verdicts[0].awaiting_owner
        assert board.moves == [("1", "s-refining")]
        assert again.writes == [] and again.sent_back == [] and again.returned == []
