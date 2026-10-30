"""Creating tasks through the Lambda handler, end to end on SQS events.

A ``task.create`` job reads the board, mints an id and creates one task.
These tests drive ``lambda_handler`` with realistic records and look at what
the stubbed Asana holds afterwards: which tasks exist, under which names,
in which column, and which records the handler gave back for redelivery.
"""

from __future__ import annotations

from .harness import (
    INBOX_SECTION,
    LambdaContext,
    create_body,
    failures,
    sqs_event,
    sqs_record,
)


def _severities(posted: list[tuple]) -> list[str]:
    """The severity of each run report or finding the handler posted."""
    return [args[1] if len(args) > 1 else kwargs["severity"] for args, kwargs in posted]


def _unreadable_reports(posted: list[tuple]) -> list[tuple]:
    """The findings that say a message could not be read."""
    return [
        (args, kwargs)
        for args, kwargs in posted
        if kwargs.get("source") == "queue_consumer"
    ]


def test_a_job_puts_one_composed_task_on_the_board(
    world, handler, _no_outbound_reports
) -> None:
    asana, _api = world
    asana.add_task("VN-3 · An earlier note", completed=True)
    asana.add_task("TASK-90 · Written by hand")

    result = handler(sqs_event(sqs_record(create_body(), "m-1")))

    assert failures(result) == []
    (created,) = asana.created
    # The next voicenote id, counted from completed tasks too, and not from
    # a prefix this cog does not own.
    assert created["name"] == "VN-4 · Call the venue"
    assert created["external"] == {"gid": "voicenote.file-1"}
    # No executor label: the agent's until grooming says otherwise, and
    # assigned to the owner like every task (executor.py).
    assert created["assignee"] == "me"
    # The board's template heads the body, so a groomer fills it in place.
    assert created["html_notes"].startswith("<body>Done when:\nWhere:\n")
    assert "\nExecutor: agent" in created["html_notes"]
    # Placed in the inbox column by the create itself, not by a second call.
    gid = next(g for g, t in asana.tasks.items() if t["name"] == created["name"])
    assert asana.section_of(gid) == INBOX_SECTION
    assert asana.moves == []
    # The label became a workspace tag, created because none existed.
    assert asana.tag_names_of(gid) == ["venue"]
    assert asana.auth == {"Bearer asana-test-token"}
    # The run report names the task it created.
    (args, kwargs) = _no_outbound_reports[-1]
    assert "VN-4 · Call the venue" in kwargs["text"]
    assert kwargs["run_id"] == "m-1"


def test_a_given_id_source_keeps_its_id(world, handler) -> None:
    asana, _api = world
    asana.add_task("VN-3 · An earlier note")
    body = create_body(
        source="github-issue",
        external_id="github-issue.mini-app-polis/infra#12",
        id="infra#12",
        title="Plan fails",
        labels=(),
    )

    result = handler(sqs_event(sqs_record(body, "m-1")))

    assert failures(result) == []
    assert [c["name"] for c in asana.created] == ["infra#12 · Plan fails"]


def test_a_redelivered_message_makes_one_task(world, handler) -> None:
    asana, _api = world
    body = create_body()

    first = handler(sqs_event(sqs_record(body, "m-1")))
    # SQS delivers at least once: the same message, received again.
    second = handler(sqs_event(sqs_record(body, "m-1", receive_count=2)))

    assert failures(first) == [] and failures(second) == []
    assert len(asana.created) == 1
    assert asana.names() == ["VN-1 · Call the venue"]


def test_a_create_that_landed_but_timed_out_is_not_made_twice(world, handler) -> None:
    asana, _api = world
    # Asana creates the task, and the response is lost on the way back.
    asana.commit_then_fail(504)
    body = create_body()

    first = handler(sqs_event(sqs_record(body, "m-1")))
    assert failures(first) == ["m-1"]
    assert asana.names() == ["VN-1 · Call the venue"]

    # The redelivery finds the task by its external id, and stops there.
    second = handler(sqs_event(sqs_record(body, "m-1", receive_count=2)))
    assert failures(second) == []
    assert len(asana.created) == 1
    assert asana.names() == ["VN-1 · Call the venue"]


def test_ids_keep_counting_across_one_batch(world, handler) -> None:
    asana, _api = world
    event = sqs_event(
        sqs_record(create_body(external_id="voicenote.file-1"), "m-1"),
        sqs_record(create_body(external_id="voicenote.file-2"), "m-2"),
    )

    result = handler(event)

    assert failures(result) == []
    assert asana.names() == ["VN-1 · Call the venue", "VN-2 · Call the venue"]


def test_a_bad_record_fails_alone_and_the_batch_completes(
    world, handler, _no_outbound_reports
) -> None:
    asana, _api = world
    event = sqs_event(
        sqs_record(create_body(external_id="voicenote.file-1"), "m-1"),
        # A producer bug: not a message this queue carries.
        sqs_record('{"type": "task.update", "version": 1}', "m-2"),
        sqs_record(create_body(external_id="voicenote.file-3"), "m-3"),
    )

    result = handler(event)

    # Named for redelivery, so it exhausts its receives and reaches the
    # dead-letter queue, rather than being swallowed and deleted.
    assert failures(result) == ["m-2"]
    assert asana.names() == ["VN-1 · Call the venue", "VN-2 · Call the venue"]
    # Reported once, as an error from the queue consumer, beside the two
    # jobs' own run reports.
    (unreadable,) = _unreadable_reports(_no_outbound_reports)
    assert _severities([unreadable]) == ["ERROR"]


def test_an_unreadable_record_is_reported_only_on_its_first_receive(
    world, handler, _no_outbound_reports
) -> None:
    handler(sqs_event(sqs_record("not json", "m-1", receive_count=2)))

    assert _unreadable_reports(_no_outbound_reports) == []


def test_a_failing_board_fails_its_record_and_the_retry_creates_it(
    world, handler
) -> None:
    asana, _api = world
    asana.fail("POST tasks", 503)
    event = sqs_event(
        sqs_record(create_body(external_id="voicenote.file-1"), "m-1"),
        sqs_record(create_body(external_id="voicenote.file-2"), "m-2"),
    )

    result = handler(event)

    assert failures(result) == ["m-1"]
    assert asana.names() == ["VN-1 · Call the venue"]
    (created,) = asana.created
    assert created["external"] == {"gid": "voicenote.file-2"}

    retry = handler(
        sqs_event(
            sqs_record(
                create_body(external_id="voicenote.file-1"), "m-1", receive_count=2
            )
        )
    )
    assert failures(retry) == []
    assert asana.names() == ["VN-1 · Call the venue", "VN-2 · Call the venue"]


def test_a_lost_tag_does_not_fail_the_task(world, handler) -> None:
    asana, _api = world
    asana.fail("GET tags", 500)

    result = handler(sqs_event(sqs_record(create_body(), "m-1")))

    assert failures(result) == []
    (created,) = asana.created
    assert created["name"] == "VN-1 · Call the venue"
    assert "tags" not in created


def test_with_no_time_left_nothing_is_started(world, handler) -> None:
    asana, _api = world

    result = handler(
        sqs_event(sqs_record(create_body(), "m-1")),
        LambdaContext(remaining_ms=5_000),
    )

    assert failures(result) == ["m-1"]
    assert asana.created == []
