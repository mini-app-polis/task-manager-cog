from __future__ import annotations

import datetime as dt

import pytest
from mini_app_polis.api.contract import GroomingProposal, GroomingRecord

from task_manager_cog.groom import (
    ORIGINAL_MARKER,
    apply_proposal,
    groomed_text,
)
from task_manager_cog.lint import parse
from task_manager_cog.records import QUESTIONS_PREFIX, fingerprint

from .test_sweep import REPOS, FakeBoard, FakeRecords, _task

VAGUE = _task("7", "Look into the runners", notes="ubuntu-latest is moving")


def _proposal(task=VAGUE, **overrides) -> GroomingProposal:
    base = {
        "task_gid": task.gid,
        "fingerprint": fingerprint(task),
        "title": "Pin runners to ubuntu-24.04",
        "done_when": "No workflow uses ubuntu-latest.\nNote: CI is green.",
        "where": "infra (.github/workflows)",
        "constraints": "None",
        "effort": "S",
        "blocked_by": "None",
        "body": "GitHub is moving ubuntu-latest to Ubuntu 26.",
        "questions": [],
        "model": "claude-sonnet-5-5",
        "cost_usd": 0.09,
    }
    base.update(overrides)
    return GroomingProposal(**base)


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)


def _apply(proposal, board, store, _reports=None) -> None:
    apply_proposal(
        proposal,
        run_id="r",
        board_factory=lambda: board,
        records_factory=lambda: store,
        repos_loader=lambda: REPOS,
    )


def test_the_text_fills_the_template_and_keeps_the_original_below() -> None:
    block, body = groomed_text(VAGUE, _proposal())
    fields = parse(f"{block}\n\n{body}").fields
    assert (
        fields["Done when"] == "No workflow uses ubuntu-latest.\n- Note: CI is green."
    )
    assert fields["Effort"] == "S"
    assert fields["PR"] == ""
    assert body == (
        "GitHub is moving ubuntu-latest to Ubuntu 26.\n\n"
        f"{ORIGINAL_MARKER}\nubuntu-latest is moving"
    )


def test_a_second_grooming_keeps_the_first_original() -> None:
    block, body = groomed_text(VAGUE, _proposal())
    once = _task("7", "MINI-9 · Pin", notes=f"{block}\n\n{body}")
    _, again = groomed_text(once, _proposal(once, body="Second reading."))
    assert again == f"Second reading.\n\n{ORIGINAL_MARKER}\nubuntu-latest is moving"


def test_what_the_proposal_leaves_out_keeps_the_tasks_own_value() -> None:
    task = _task("7", "MINI-9 · Pin")  # the ready template, from test_sweep
    block, body = groomed_text(
        task, _proposal(task, where=None, effort=None, body=None)
    )
    fields = parse(f"{block}\n\n{body}").fields
    assert fields["Where"] == "infra"
    assert body == parse(task.notes).body


def test_a_ready_proposal_lands_in_ready_with_an_id(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    board = FakeBoard([VAGUE, _task("1", "MINI-41 · Other")])
    store = FakeRecords()
    _apply(_proposal(), board, store)

    task = board.get("7")
    assert task is not None
    assert task.name == "MINI-42 · Pin runners to ubuntu-24.04"
    assert task.section_name == "Ready"
    record = store.stored["7"]
    assert record.outcome == "ready"
    assert record.moved_to_ready_at is not None
    assert record.fingerprint == record.groomed_fingerprint == fingerprint(task)


def test_questions_go_up_as_a_comment_and_the_task_waits_in_refining(
    production, _no_outbound_reports
) -> None:
    board = FakeBoard([VAGUE])
    store = FakeRecords()
    _apply(_proposal(questions=["Now, or only after a failure?"]), board, store)

    task = board.get("7")
    assert task is not None and task.section_name == "Refining"
    (comment,) = board.comments("7")
    assert comment.startswith(QUESTIONS_PREFIX)
    assert "1. Now, or only after a failure?" in comment
    assert store.stored["7"].outcome == "questions"


def test_not_yet_ready_goes_to_refining_and_loses_its_review_tag(
    production, _no_outbound_reports
) -> None:
    tagged = _task("7", "Look into the runners", notes="ubuntu-latest is moving")
    tagged = type(tagged)(**{**tagged.__dict__, "tag_names": ("review",)})
    board = FakeBoard([tagged])
    store = FakeRecords()
    _apply(_proposal(tagged, where="somewhere-else"), board, store)

    task = board.get("7")
    assert task is not None and task.section_name == "Refining"
    assert board.untagged == [("7", "review")]
    assert store.stored["7"].outcome == "not_ready"
    assert (
        "Where names no repository called somewhere-else" in store.stored["7"].problems
    )


def test_a_proposal_for_changed_content_is_not_applied(
    production, _no_outbound_reports
) -> None:
    edited = _task("7", "Look into the runners", notes="edited since")
    board = FakeBoard([edited])
    store = FakeRecords()
    _apply(_proposal(), board, store)
    assert board.get("7") == edited
    assert store.written == []


def test_answers_are_part_of_the_content_a_proposal_was_made_for(
    production, _no_outbound_reports
) -> None:
    board = FakeBoard(
        [VAGUE], comments_={"7": [f"{QUESTIONS_PREFIX}\n1. Why?", "Because."]}
    )
    store = FakeRecords()
    store.stored["7"] = GroomingRecord(
        task_gid="7",
        fingerprint="0" * 64,
        outcome="questions",
        updated_at=dt.datetime.now(dt.UTC),
    )
    _apply(_proposal(fingerprint=fingerprint(VAGUE, ["Because."])), board, store)
    # Applied: ready, and left in Refining while GROOM_AUTO_READY is off.
    assert store.stored["7"].outcome == "ready"
    task = board.get("7")
    assert task is not None and task.section_name == "Refining"


def test_a_task_that_is_gone_is_left_alone(production, _no_outbound_reports) -> None:
    store = FakeRecords()
    _apply(_proposal(), FakeBoard([]), store)
    assert store.written == []


def test_outside_production_nothing_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)

    def no_board():
        raise AssertionError("the board must not be read")

    apply_proposal(_proposal(), run_id="r", board_factory=no_board)
