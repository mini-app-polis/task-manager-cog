"""A ``task.update`` through the Lambda handler, end to end on an SQS event.

The update names a task by the external id it was created under. These tests
look at the stubbed Asana's comments and columns after the handler returns.
"""

from __future__ import annotations

from .harness import (
    SECTIONS,
    failures,
    sqs_event,
    sqs_record,
    update_body,
)

COMMENT = "CI on infra main is green again."


def test_a_recovery_moves_the_task_to_review_once(world, handler) -> None:
    asana, _api = world
    gid = asana.add_task("CI-1 · infra main is failing", external_id="ci.infra.main")

    first = handler(sqs_event(sqs_record(update_body(), "u-1")))
    second = handler(sqs_event(sqs_record(update_body(), "u-1", receive_count=2)))

    assert failures(first) == [] and failures(second) == []
    assert asana.moves == [(gid, SECTIONS["Review"])]
    assert asana.comments[gid] == [COMMENT]
    assert asana.tasks[gid]["completed"] is False


def test_a_task_in_progress_is_commented_and_not_moved(world, handler) -> None:
    asana, _api = world
    gid = asana.add_task(
        "CI-1 · infra main is failing",
        external_id="ci.infra.main",
        section="In progress",
    )

    result = handler(sqs_event(sqs_record(update_body(), "u-1")))

    assert failures(result) == []
    assert asana.moves == []
    assert asana.comments[gid] == [COMMENT]


def test_a_task_not_on_the_board_is_not_redelivered(world, handler) -> None:
    asana, _api = world

    result = handler(sqs_event(sqs_record(update_body(), "u-1")))

    assert failures(result) == []
    assert asana.moves == [] and asana.comments == {}


def test_a_failed_asana_call_is_redelivered(world, handler) -> None:
    asana, _api = world
    gid = asana.add_task("CI-1 · infra main is failing", external_id="ci.infra.main")
    asana.fail("POST addTask", 500)

    first = handler(sqs_event(sqs_record(update_body(), "u-1")))
    assert failures(first) == ["u-1"]
    assert asana.comments[gid] == [COMMENT]

    second = handler(sqs_event(sqs_record(update_body(), "u-1", receive_count=2)))
    assert failures(second) == []
    assert asana.moves == [(gid, SECTIONS["Review"])]
    assert asana.comments[gid] == [COMMENT]
