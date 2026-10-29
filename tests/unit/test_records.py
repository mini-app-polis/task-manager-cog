from __future__ import annotations

import datetime as dt

from mini_app_polis.api.contract import GroomingRecord
from mini_app_polis.asana import AsanaTaskRecord

from task_manager_cog.records import (
    GROOM_MAX_ATTEMPTS,
    GROOM_RETRY_AFTER,
    answers,
    awaiting_grooming,
    changed,
    fingerprint,
    needs_grooming,
    next_record,
    unanswered,
)

NOW = dt.datetime(2026, 10, 21, 20, tzinfo=dt.UTC)


def _task(**overrides) -> AsanaTaskRecord:
    base = {
        "gid": "42",
        "name": "TASK-9 · Discord commands",
        "notes": "Done when:",
        "completed": False,
        "section_gid": "s1",
        "section_name": "Ideas",
        "tag_names": ("b", "A"),
    }
    base.update(overrides)
    return AsanaTaskRecord(**base)


def _stored(task: AsanaTaskRecord, **overrides) -> GroomingRecord:
    base = {
        "task_gid": task.gid,
        "task_id": "TASK-9",
        "fingerprint": fingerprint(task),
        "outcome": "not_ready",
        "problems": ["Effort is blank, not S, M or L"],
        "updated_at": NOW,
    }
    base.update(overrides)
    return GroomingRecord(**base)


def test_the_fingerprint_follows_content_not_column_or_tag_order() -> None:
    task = _task()
    assert fingerprint(task) == fingerprint(
        _task(section_name="Blocked", section_gid="s3", tag_names=("a", "B"))
    )
    assert fingerprint(task) != fingerprint(_task(notes="Done when: shipped"))
    assert fingerprint(task) != fingerprint(_task(name="TASK-9 · Discord"))
    assert fingerprint(task) != fingerprint(_task(tag_names=("a",)))


def test_a_task_with_no_record_is_changed() -> None:
    task = _task()
    assert changed(task, {})
    assert not changed(task, {"42": _stored(task)})
    assert changed(_task(notes="new"), {"42": _stored(task)})


def test_a_first_record_carries_the_verdict() -> None:
    record = next_record(_task(), ("Done when is blank",), None, moved_at=None)
    assert record is not None
    assert record.task_id == "TASK-9"
    assert record.outcome == "not_ready"
    assert record.problems == ["Done when is blank"]
    assert record.returned_count == 0


def test_an_unchanged_verdict_writes_nothing() -> None:
    task = _task()
    previous = _stored(task)
    assert next_record(task, tuple(previous.problems), previous, moved_at=None) is None


def test_new_problems_on_the_same_content_are_written() -> None:
    task = _task()
    record = next_record(task, ("Where is blank",), _stored(task), moved_at=None)
    assert record is not None
    assert record.problems == ["Where is blank"]


def test_a_move_to_ready_is_recorded() -> None:
    task = _task()
    record = next_record(task, (), _stored(task), moved_at=NOW)
    assert record is not None
    assert record.outcome == "ready"
    assert record.moved_to_ready_at == NOW


def test_a_task_with_no_id_has_none() -> None:
    record = next_record(_task(name="Replace retag music"), (), None, moved_at=None)
    assert record is not None
    assert record.task_id is None


def test_unanswered_questions_are_the_last_questions_with_nothing_after() -> None:
    asked = "Grooming questions:\n1. Which queue?"
    assert unanswered([asked])
    assert unanswered(["An earlier note.", asked])
    assert not unanswered([asked, "The FIFO one."])
    assert unanswered([asked, "The FIFO one.", asked])
    assert not unanswered(["A note, not a question."])
    assert not unanswered([])


def test_a_reported_split_is_asked_like_questions() -> None:
    reported = "Proposed split (not created: GROOM_SPLIT_DRY_RUN is on; content abc)"
    assert unanswered([reported])
    assert not unanswered([reported, "Create it."])
    assert answers([reported, "Create it."]) == ["Create it."]
    # Only what follows the last asking comment answers it.
    asked = "Grooming questions:\n1. Which queue?"
    assert answers([asked, "The FIFO one.", reported, "Swap 1 and 2."]) == [
        "Swap 1 and 2."
    ]
    assert answers([reported, "Create it.", asked]) == []


def test_a_task_whose_grooming_the_budget_stopped_is_sent_again() -> None:
    task = _task()
    current = fingerprint(task)
    stopped = _stored(
        task, groomed_fingerprint=current, budget_spent_fingerprint=current
    )
    assert needs_grooming(task, stopped)
    # The stop is not written back: sending again leaves the record as it is.
    assert (
        next_record(task, tuple(stopped.problems), stopped, moved_at=None, sent=True)
        is None
    )


def test_a_stop_for_older_content_sends_nothing() -> None:
    task = _task()
    older = fingerprint(_task(notes="Done when: before"))
    stopped = _stored(
        task, groomed_fingerprint=fingerprint(task), budget_spent_fingerprint=older
    )
    assert not needs_grooming(task, stopped)


def test_a_groomed_task_with_no_stop_is_not_sent_again() -> None:
    task = _task()
    groomed = _stored(task, groomed_fingerprint=fingerprint(task))
    assert not needs_grooming(task, groomed)
    assert needs_grooming(_task(notes="Done when: edited"), groomed)


def test_a_send_is_stamped_and_counted() -> None:
    task = _task()
    write = next_record(task, ("x",), None, moved_at=None, sent=True, now=NOW)
    assert write is not None
    assert (write.groom_sent_at, write.groom_attempts) == (NOW, 1)
    # Not sent: the stamp is carried as it was.
    stored = _stored(
        task, groomed_fingerprint=fingerprint(task), groom_sent_at=NOW, groom_attempts=1
    )
    later = NOW + dt.timedelta(hours=1)
    assert (
        next_record(task, tuple(stored.problems), stored, moved_at=None, now=later)
        is None
    )


def test_a_send_with_no_grooming_back_is_awaited_then_sent_again() -> None:
    task = _task()
    sent = _stored(
        task, groomed_fingerprint=fingerprint(task), groom_sent_at=NOW, groom_attempts=1
    )
    soon = NOW + GROOM_RETRY_AFTER - dt.timedelta(minutes=1)
    assert awaiting_grooming(task, sent, now=soon)
    assert not needs_grooming(task, sent, now=soon)

    lost = NOW + GROOM_RETRY_AFTER
    assert not awaiting_grooming(task, sent, now=lost)
    assert needs_grooming(task, sent, now=lost)
    again = next_record(
        task, tuple(sent.problems), sent, moved_at=None, sent=True, now=lost
    )
    assert again is not None
    assert (again.groom_sent_at, again.groom_attempts) == (lost, 2)


def test_a_lost_grooming_is_sent_at_most_the_most_attempts() -> None:
    task = _task()
    spent = _stored(
        task,
        groomed_fingerprint=fingerprint(task),
        groom_sent_at=NOW,
        groom_attempts=GROOM_MAX_ATTEMPTS,
    )
    later = NOW + 2 * GROOM_RETRY_AFTER
    assert not needs_grooming(task, spent, now=later)
    assert not awaiting_grooming(task, spent, now=later)


def test_a_grooming_that_came_back_is_neither_awaited_nor_sent() -> None:
    task = _task()
    groomed = _stored(task, groomed_fingerprint=fingerprint(task))
    later = NOW + 2 * GROOM_RETRY_AFTER
    assert not awaiting_grooming(task, groomed, now=later)
    assert not needs_grooming(task, groomed, now=later)
    # Changed content starts a new count.
    edited = _task(notes="Done when: edited")
    stamped = _stored(
        task, groomed_fingerprint=fingerprint(task), groom_sent_at=NOW, groom_attempts=3
    )
    write = next_record(edited, ("x",), stamped, moved_at=None, sent=True, now=later)
    assert write is not None and write.groom_attempts == 1


def test_sending_again_for_the_budget_is_not_an_attempt() -> None:
    task = _task()
    current = fingerprint(task)
    stopped = _stored(
        task,
        groomed_fingerprint=current,
        budget_spent_fingerprint=current,
        groom_sent_at=NOW,
        groom_attempts=2,
    )
    later = NOW + dt.timedelta(hours=1)
    assert needs_grooming(task, stopped, now=later)
    assert (
        next_record(
            task, tuple(stopped.problems), stopped, moved_at=None, sent=True, now=later
        )
        is None
    )
