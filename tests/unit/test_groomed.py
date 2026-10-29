from __future__ import annotations

import datetime as dt

from mini_app_polis.api.contract import GroomingRecord

from task_manager_cog.groomed import record_groomed
from task_manager_cog.records import fingerprint
from task_manager_cog.sweep import assess, sweep

from .test_sweep import REPOS, FakeBoard, FakeRecords, _task

READY_TASK = _task("1", "TASK-1 · Ready by hand", section="Refining")
VAGUE_TASK = _task("2", "TASK-2 · Still vague", notes="look into this")
DONE_TASK = _task("3", "TASK-3 · Finished", completed=True)


def _record(store: FakeRecords, gids: list[str], *, write: bool = True) -> list[str]:
    lines: list[str] = []
    refused = record_groomed(
        gids,
        board=FakeBoard([READY_TASK, VAGUE_TASK, DONE_TASK]),
        store=store,
        repos=REPOS,
        write=write,
        out=lines.append,
    )
    lines.append(f"refused={refused}")
    return lines


def test_a_ready_task_is_recorded_ready_and_groomed() -> None:
    store = FakeRecords()
    lines = _record(store, ["1"])
    record = store.stored["1"]
    assert record.outcome == "ready" and record.problems == []
    assert record.groomed_fingerprint == record.fingerprint == fingerprint(READY_TASK)
    assert "recorded as groomed; problems: none" in lines[0]


def test_a_task_not_ready_is_recorded_with_its_problems() -> None:
    store = FakeRecords()
    _record(store, ["2"])
    record = store.stored["2"]
    assert record.outcome == "not_ready"
    assert record.problems
    assert record.groomed_fingerprint == fingerprint(VAGUE_TASK)


def test_open_questions_are_cleared_and_the_count_kept() -> None:
    store = FakeRecords()
    store.stored["1"] = GroomingRecord(
        task_gid="1",
        task_id="TASK-1",
        fingerprint="a" * 64,
        outcome="questions",
        returned_count=2,
        groomed_fingerprint="a" * 64,
        updated_at=dt.datetime(2026, 10, 23, tzinfo=dt.UTC),
    )
    _record(store, ["1"])
    record = store.stored["1"]
    assert record.outcome == "ready"
    assert record.returned_count == 2
    # The sweep does not hold it for questions now, and moves it to Ready.
    (verdict,) = assess(
        [READY_TASK], swept=("Refining",), repos=REPOS, records=store.stored
    )
    assert verdict.ready


def test_a_dry_run_writes_nothing() -> None:
    store = FakeRecords()
    lines = _record(store, ["1", "2"], write=False)
    assert store.written == []
    assert "would record as groomed" in lines[0]


def test_an_unknown_or_finished_task_is_refused() -> None:
    store = FakeRecords()
    lines = _record(store, ["404", "3", "1"])
    assert [w.task_gid for w in store.written] == ["1"]
    assert lines[0] == "404: not an open task on the board; nothing recorded"
    assert lines[1] == "3: not an open task on the board; nothing recorded"
    assert lines[-1] == "refused=2"


def test_awaiting_owner_holds_a_task_until_its_owner_answers() -> None:
    store = FakeRecords()
    lines: list[str] = []
    record_groomed(
        ["2"],
        board=FakeBoard([VAGUE_TASK]),
        store=store,
        repos=REPOS,
        write=True,
        awaiting_owner=True,
        out=lines.append,
    )
    record = store.stored["2"]
    assert record.outcome == "questions"
    assert record.groomed_fingerprint == fingerprint(VAGUE_TASK)
    assert "recorded as waiting on its owner" in lines[0]

    # Held while nothing answers it, and sent once something does.
    reported = "Proposed split (not created: GROOM_SPLIT_DRY_RUN is on; content abc)"
    sent: list[object] = []

    def send(job: object) -> bool:
        sent.append(job)
        return True

    held = FakeBoard([VAGUE_TASK], comments_={"2": [reported]})
    sweep(held, repos=REPOS, move=True, records=store.stored, send=send, limit=5)
    assert sent == []
    answered = FakeBoard([VAGUE_TASK], comments_={"2": [reported, "Create it."]})
    sweep(answered, repos=REPOS, move=True, records=store.stored, send=send, limit=5)
    assert len(sent) == 1
