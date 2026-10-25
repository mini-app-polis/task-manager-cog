"""The hourly sweep through the Lambda handler, end to end on an SQS event.

A ``task.sweep`` reads the board, the registry and the grooming records,
moves ready tasks to Ready and writes the records that changed. These tests
look at the stubbed Asana's columns and the stubbed API's records after the
handler returns.
"""

from __future__ import annotations

import pytest

from .harness import (
    SECTIONS,
    failures,
    sqs_event,
    sqs_record,
    sweep_body,
)


@pytest.fixture
def auto_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")


def test_a_sweep_moves_the_ready_task_and_records_both_verdicts(
    world, handler, auto_ready
) -> None:
    asana, api = world
    ready = asana.add_task("MINI-1 · Pin runners")
    blank = asana.add_task("MINI-2 · Idea", notes="Done when:\nWhere:\n")

    result = handler(sqs_event(sqs_record(sweep_body(), "s-1")))

    assert failures(result) == []
    assert asana.moves == [(ready, SECTIONS["Ready"])]
    assert asana.section_of(blank) == "Ideas"
    assert api.records[ready]["outcome"] == "ready"
    assert api.records[ready]["moved_to_ready_at"] is not None
    assert api.records[blank]["outcome"] == "not_ready"
    assert api.records[blank]["problems"]
    assert api.auth == {"Bearer task-manager-cog-test-key"}


def test_a_redelivered_sweep_changes_nothing_twice(world, handler, auto_ready) -> None:
    asana, api = world
    ready = asana.add_task("MINI-1 · Pin runners")
    asana.add_task("MINI-2 · Idea", notes="Done when:\nWhere:\n")

    first = handler(sqs_event(sqs_record(sweep_body(), "s-1")))
    writes = len(api.writes)
    second = handler(sqs_event(sqs_record(sweep_body(), "s-1", receive_count=2)))

    assert failures(first) == [] and failures(second) == []
    # One move, and no record written again for a board that has not changed.
    assert asana.moves == [(ready, SECTIONS["Ready"])]
    assert len(api.writes) == writes


def test_a_task_failing_in_ready_is_sent_back_once(world, handler, auto_ready) -> None:
    asana, _api = world
    failing = asana.add_task(
        "MINI-1 · Idea", notes="Done when:\nWhere:\n", section="Ready"
    )

    first = handler(sqs_event(sqs_record(sweep_body(), "s-1")))
    second = handler(sqs_event(sqs_record(sweep_body(), "s-1", receive_count=2)))

    assert failures(first) == [] and failures(second) == []
    assert asana.moves == [(failing, SECTIONS["Refining"])]
    (comment,) = asana.comments[failing]
    assert comment.startswith("Sent back from Ready:")


def test_without_auto_ready_the_sweep_only_reports(world, handler) -> None:
    asana, api = world
    ready = asana.add_task("MINI-1 · Pin runners")

    result = handler(sqs_event(sqs_record(sweep_body(), "s-1")))

    assert failures(result) == []
    assert asana.moves == []
    assert api.records[ready]["outcome"] == "ready"
    assert api.records[ready]["moved_to_ready_at"] is None


def test_unanswered_grooming_questions_hold_a_task_out_of_ready(
    world, handler, auto_ready
) -> None:
    asana, _api = world
    held = asana.add_task("MINI-1 · Pin runners")
    asana.comments[held] = ["Grooming questions: which runner image?"]

    result = handler(sqs_event(sqs_record(sweep_body(), "s-1")))

    assert failures(result) == []
    assert asana.moves == []


def test_a_sweep_that_cannot_read_the_board_is_redelivered(
    world, handler, auto_ready
) -> None:
    asana, api = world
    asana.add_task("MINI-1 · Pin runners")
    asana.fail("GET tasks", 500)

    result = handler(sqs_event(sqs_record(sweep_body(), "s-1")))

    assert failures(result) == ["s-1"]
    assert asana.moves == []
    assert api.writes == []
