from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord

from task_manager_cog import sweep as sweep_mod
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
    _task("t1", "MINI-1 · Ready idea"),
    _task("t2", "MINI-2 · Vague idea", notes="look into this"),
    _task("t3", "MINI-3 · Ready but in progress", section="In progress"),
    _task("t4", "MINI-4 · Done already", completed=True),
    _task("t5", "MINI-5 · TEMPLATE — do not work"),
    _task(
        "t6",
        "MINI-6 · Waits on MINI-7",
        notes=READY.replace("Blocked by:", "Blocked by: MINI-7"),
        section="Blocked",
    ),
    _task("t7", "MINI-7 · Open blocker", section="Refining", notes="later"),
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
            "t6",
            "MINI-6 · Waits",
            notes=READY.replace("Blocked by:", "Blocked by: MINI-4"),
        ),
        _task("t4", "MINI-4 · Done", completed=True),
    ]
    (verdict,) = assess(tasks, swept=("Ideas",), repos=REPOS)
    assert verdict.ready


def test_sweep_moves_ready_tasks_when_told_to() -> None:
    board = FakeBoard(list(BOARD_TASKS))
    result = sweep(board, repos=REPOS, move=True)
    assert board.moves == [("t1", "s-ready")]
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


def test_run_sweep_moves_and_reports_in_production(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    board = FakeBoard(list(BOARD_TASKS))
    run_sweep(run_id="r-1", board_factory=lambda: board, repos_loader=lambda: REPOS)
    assert board.moves == [("t1", "s-ready")]
    ((args, kwargs),) = _no_outbound_reports
    assert "MINI-1 · Ready idea" in kwargs["text"]
    assert kwargs["tasks_checked"] == 4


def test_run_sweep_does_nothing_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)

    def no_board():
        raise AssertionError("the board must not be read")

    run_sweep(run_id="r-1", board_factory=no_board, repos_loader=lambda: REPOS)


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
