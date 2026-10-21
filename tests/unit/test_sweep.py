from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pytest
from mini_app_polis.api.contract import GroomingRecord, GroomingRecordWrite
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord

from task_manager_cog import sweep as sweep_mod
from task_manager_cog.records import fingerprint
from task_manager_cog.sweep import assess, run_sweep, sweep

REPOS = frozenset({"infra"})

READY = """Done when: the plan is clean
Where: infra
Constraints: None
Effort: S
PR:
Blocked by:

Because the plan drifted."""

SECTIONS = [
    AsanaSection("s-ideas", "Ideas"),
    AsanaSection("s-refining", "Refining"),
    AsanaSection("s-ready", "Ready"),
    AsanaSection("s-blocked", "Blocked"),
    AsanaSection("s-progress", "In progress"),
]


def _task(
    gid: str,
    name: str,
    *,
    notes: str = READY,
    section: str = "Ideas",
    completed: bool = False,
) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=name,
        notes=notes,
        completed=completed,
        section_gid=f"s-{section.lower()}",
        section_name=section,
    )


@dataclass
class FakeBoard:
    records: list[AsanaTaskRecord]
    sections_: list[AsanaSection] = field(default_factory=lambda: list(SECTIONS))
    moves: list[tuple[str, str]] = field(default_factory=list)

    def sections(self) -> list[AsanaSection]:
        return self.sections_

    def tasks(self) -> list[AsanaTaskRecord]:
        return self.records

    def move(self, task_gid: str, section_gid: str) -> None:
        self.moves.append((task_gid, section_gid))

    def url(self, gid: str) -> str:
        return f"https://board/{gid}"


BOARD_TASKS = [
    _task("1", "MINI-1 · Ready idea"),
    _task("2", "MINI-2 · Vague idea", notes="look into this"),
    _task("3", "MINI-3 · Ready but in progress", section="In progress"),
    _task("4", "MINI-4 · Done already", completed=True),
    _task("5", "MINI-5 · TEMPLATE — do not work"),
    _task(
        "6",
        "MINI-6 · Waits on MINI-7",
        notes=READY.replace("Blocked by:", "Blocked by: MINI-7"),
        section="Blocked",
    ),
    _task("7", "MINI-7 · Open blocker", section="Refining", notes="later"),
]


def test_assess_checks_open_tasks_in_the_swept_columns_only() -> None:
    verdicts = assess(BOARD_TASKS, swept=("ideas", "Refining", "Blocked"), repos=REPOS)
    by_name = {v.task.name: v for v in verdicts}
    assert set(by_name) == {
        "MINI-1 · Ready idea",
        "MINI-2 · Vague idea",
        "MINI-6 · Waits on MINI-7",
        "MINI-7 · Open blocker",
    }
    assert by_name["MINI-1 · Ready idea"].ready
    assert by_name["MINI-6 · Waits on MINI-7"].problems == (
        "Blocked by MINI-7, which is not done",
    )


def test_a_blocker_that_is_done_no_longer_blocks() -> None:
    tasks = [
        _task(
            "6",
            "MINI-6 · Waits",
            notes=READY.replace("Blocked by:", "Blocked by: MINI-4"),
        ),
        _task("4", "MINI-4 · Done", completed=True),
    ]
    (verdict,) = assess(tasks, swept=("Ideas",), repos=REPOS)
    assert verdict.ready


def test_sweep_moves_ready_tasks_when_told_to() -> None:
    board = FakeBoard(list(BOARD_TASKS))
    result = sweep(board, repos=REPOS, move=True)
    assert board.moves == [("1", "s-ready")]
    assert [t.name for t in result.moved] == ["MINI-1 · Ready idea"]


def test_sweep_only_reports_when_moving_is_off() -> None:
    board = FakeBoard(list(BOARD_TASKS))
    result = sweep(board, repos=REPOS, move=False)
    assert board.moves == []
    assert [v.task.name for v in result.ready] == ["MINI-1 · Ready idea"]


def test_a_board_without_the_ready_column_stops_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READY_SECTION", "Up next")
    with pytest.raises(sweep_mod.SweepMisconfigured):
        sweep(FakeBoard(list(BOARD_TASKS)), repos=REPOS, move=True)


def test_columns_and_moving_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sweep_mod.groom_sections() == ("Ideas", "Refining", "Blocked")
    assert sweep_mod.ready_section() == "Ready"
    assert not sweep_mod.auto_ready()
    monkeypatch.setenv("GROOM_SECTIONS", "Inbox, Triage")
    monkeypatch.setenv("READY_SECTION", "Up next")
    monkeypatch.setenv("GROOM_AUTO_READY", "TRUE")
    assert sweep_mod.groom_sections() == ("Inbox", "Triage")
    assert sweep_mod.ready_section() == "Up next"
    assert sweep_mod.auto_ready()


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)


@dataclass
class FakeRecords:
    """The API's grooming records, in memory."""

    stored: dict[str, GroomingRecord] = field(default_factory=dict)
    written: list[GroomingRecordWrite] = field(default_factory=list)

    def list_groomings(self) -> list[GroomingRecord]:
        return list(self.stored.values())

    def record_grooming(self, payload: GroomingRecordWrite) -> GroomingRecord:
        self.written.append(payload)
        record = GroomingRecord(
            **payload.model_dump(), updated_at=dt.datetime.now(dt.UTC)
        )
        self.stored[payload.task_gid] = record
        return record


def test_run_sweep_moves_and_reports_in_production(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    board = FakeBoard(list(BOARD_TASKS))
    store = FakeRecords()
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
    )
    assert board.moves == [("1", "s-ready")]
    ((args, kwargs),) = _no_outbound_reports
    assert "MINI-1 · Ready idea" in kwargs["text"]
    assert kwargs["tasks_checked"] == 4
    assert kwargs["records_written"] == 4
    moved = store.stored["1"]
    assert moved.outcome == "ready"
    assert moved.moved_to_ready_at is not None
    assert store.stored["2"].outcome == "not_ready"
    assert store.stored["2"].problems


def test_a_quiet_board_writes_no_records(production, _no_outbound_reports) -> None:
    board = FakeBoard(list(BOARD_TASKS))
    store = FakeRecords()
    for _ in range(2):
        run_sweep(
            run_id="r-1",
            board_factory=lambda: board,
            repos_loader=lambda: REPOS,
            records_factory=lambda: store,
        )
    assert len(store.written) == 4
    (_, second) = _no_outbound_reports
    assert second[1]["changed"] == 0
    assert second[1].get("records_written", 0) == 0


def test_an_edited_task_is_changed_and_its_record_rewritten() -> None:
    task = _task("2", "MINI-2 · Vague idea", notes="look into this")
    first = sweep(FakeBoard([task]), repos=REPOS, move=False)
    stored = GroomingRecord(
        **first.writes[0].model_dump(), updated_at=dt.datetime.now(dt.UTC)
    )

    same = sweep(FakeBoard([task]), repos=REPOS, move=False, records={"2": stored})
    assert not same.verdicts[0].changed
    assert same.writes == []

    edited = _task("2", "MINI-2 · Vague idea", notes="look into this, today")
    after = sweep(FakeBoard([edited]), repos=REPOS, move=False, records={"2": stored})
    assert after.verdicts[0].changed
    assert after.writes[0].fingerprint == fingerprint(edited)


def test_a_task_sent_back_from_ready_is_counted_once() -> None:
    task = _task("12", "MINI-2 · Vague idea", notes="look into this")
    moved = GroomingRecord(
        task_gid="12",
        task_id="MINI-2",
        fingerprint=fingerprint(task),
        outcome="ready",
        moved_to_ready_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
        updated_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
    )
    result = sweep(FakeBoard([task]), repos=REPOS, move=False, records={"12": moved})
    assert [t.gid for t in result.returned] == ["12"]
    (write,) = result.writes
    assert write.returned_count == 1
    assert write.moved_to_ready_at is None
    assert write.outcome == "not_ready"

    again = sweep(
        FakeBoard([task]),
        repos=REPOS,
        move=False,
        records={
            "12": GroomingRecord(**write.model_dump(), updated_at=moved.updated_at)
        },
    )
    assert again.returned == []
    assert again.writes == []


def test_a_question_waits_until_the_task_changes() -> None:
    task = _task("12", "MINI-2 · Vague idea", notes="look into this")
    first = sweep(FakeBoard([task]), repos=REPOS, move=False)
    asked = GroomingRecord(
        **{**first.writes[0].model_dump(), "outcome": "questions"},
        updated_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
    )
    unchanged = sweep(FakeBoard([task]), repos=REPOS, move=False, records={"12": asked})
    assert unchanged.writes == []

    answered = _task("12", "MINI-2 · Vague idea", notes="look into this: answered")
    after = sweep(FakeBoard([answered]), repos=REPOS, move=False, records={"12": asked})
    assert after.writes[0].outcome == "not_ready"


def test_run_sweep_does_nothing_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)

    def no_board():
        raise AssertionError("the board must not be read")

    def no_records():
        raise AssertionError("the records must not be read")

    run_sweep(
        run_id="r-1",
        board_factory=no_board,
        repos_loader=lambda: REPOS,
        records_factory=no_records,
    )


def test_main_prints_every_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    board = FakeBoard(list(BOARD_TASKS))
    monkeypatch.setattr(sweep_mod.Board, "from_env", classmethod(lambda cls: board))
    monkeypatch.setattr(sweep_mod, "load_repos", lambda: REPOS)
    assert sweep_mod.main([]) == 0
    out = capsys.readouterr().out
    assert "READY  [Ideas] MINI-1 · Ready idea" in out
    assert "- Blocked by MINI-7, which is not done" in out
    assert "1 of 4 tasks ready" in out
    assert board.moves == []
