"""A sweep asked for on demand: ``groom`` and ``agents`` stand in for the clock.

Every test here runs at a time no ``GROOM_TIMES`` or ``AGENT_TIMES`` covers,
so anything sent or dispatched was sent because the sweep was flagged.
"""

from __future__ import annotations

import datetime as dt

import pytest

from task_manager_cog import sweep as sweep_mod
from task_manager_cog.sweep import run_sweep
from tests.unit.test_sweep import (
    BOARD_TASKS,
    REPOS,
    FakeBoard,
    FakeRecords,
    FakeStarter,
    _task,
)


@pytest.fixture
def off_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production, with both times of day set three hours from now."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)
    monkeypatch.delenv("SWEEP_TIMEZONE", raising=False)
    away = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=3)).strftime("%H:%M")
    monkeypatch.setenv("GROOM_TIMES", away)
    monkeypatch.setenv("AGENT_TIMES", away)
    assert not sweep_mod.due("GROOM_TIMES")
    assert not sweep_mod.due("AGENT_TIMES")


def _never():
    raise AssertionError("agent dispatch must not run")


def _run(
    board: FakeBoard,
    store: FakeRecords,
    *,
    starter_factory=_never,
    groom: bool = False,
    agents: bool = False,
) -> None:
    run_sweep(
        run_id="r",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
        starter_factory=starter_factory,
        groom=groom,
        agents=agents,
    )


def _ready_board() -> FakeBoard:
    return FakeBoard([_task("1", "TASK-1 · Ready", section="Ready")])


# ── grooming ──────────────────────────────────────────────────────────────


def test_a_flagged_sweep_off_schedule_sends_for_grooming(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    store = FakeRecords()
    _run(FakeBoard(list(BOARD_TASKS)), store, groom=True)
    assert [j.task.name for j in store.jobs] == [
        "TASK-2 · Vague idea",
        "TASK-7 · Open blocker",
    ]


def test_an_unflagged_sweep_off_schedule_sends_nothing(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    store = FakeRecords()
    _run(FakeBoard(list(BOARD_TASKS)), store)
    assert store.jobs == []
    ((_, kwargs),) = _no_outbound_reports
    assert sweep_mod.ON_DEMAND not in kwargs["text"]


def test_a_flagged_sweep_is_held_to_the_grooming_limit(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    monkeypatch.setenv("GROOM_DISPATCH_LIMIT", "1")
    store = FakeRecords()
    _run(FakeBoard(list(BOARD_TASKS)), store, groom=True)
    assert len(store.jobs) == 1


@pytest.mark.parametrize("setting", [None, "false", "0"])
def test_a_flagged_sweep_sends_nothing_with_grooming_off(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports, setting
) -> None:
    if setting is None:
        monkeypatch.delenv("GROOM_DISPATCH", raising=False)
    else:
        monkeypatch.setenv("GROOM_DISPATCH", setting)
    store = FakeRecords()
    _run(FakeBoard(list(BOARD_TASKS)), store, groom=True)
    assert store.jobs == []


def test_on_demand_stands_in_for_the_time_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROOM_TIMES", "04:15")
    off = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.UTC)
    monkeypatch.delenv("GROOM_DISPATCH", raising=False)
    assert sweep_mod.dispatch_limit(off, on_demand=True) == 0
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    assert sweep_mod.dispatch_limit(off) == 0
    assert sweep_mod.dispatch_limit(off, on_demand=True) == 5
    monkeypatch.setenv("GROOM_DISPATCH_LIMIT", "2")
    assert sweep_mod.dispatch_limit(off, on_demand=True) == 2


def test_the_grooming_flag_alone_dispatches_no_agents(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    _run(_ready_board(), FakeRecords(), groom=True)


# ── agents ────────────────────────────────────────────────────────────────


def test_a_flagged_sweep_off_schedule_dispatches_agents(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    board = _ready_board()
    store, starter = FakeRecords(), FakeStarter()
    _run(board, store, starter_factory=lambda: starter, agents=True)
    assert starter.started == ["infra"]
    assert [r.task_id for r in store.agent_runs] == ["TASK-1"]
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["agent_dispatched"] == 1


def test_an_unflagged_sweep_off_schedule_dispatches_no_agents(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    _run(_ready_board(), FakeRecords())


def test_the_agents_flag_alone_sends_nothing_for_grooming(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    store = FakeRecords()
    _run(FakeBoard(list(BOARD_TASKS)), store, starter_factory=FakeStarter, agents=True)
    assert store.jobs == []


def test_a_flagged_sweep_dispatches_nothing_with_dispatch_off(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.delenv("AGENT_DISPATCH", raising=False)
    monkeypatch.delenv("AGENT_DRY_RUN", raising=False)
    _run(_ready_board(), FakeRecords(), agents=True)


def test_a_flagged_dry_run_dispatches_nothing(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    monkeypatch.setenv("AGENT_DRY_RUN", "true")
    board = _ready_board()
    store, starter = FakeRecords(), FakeStarter()
    _run(board, store, starter_factory=lambda: starter, agents=True)
    assert board.moves == [] and starter.started == [] and store.agent_runs == []
    ((_, kwargs),) = _no_outbound_reports
    assert "would dispatch" in kwargs["text"]


def test_a_flagged_sweep_is_held_to_the_monthly_cap(
    off_schedule, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    monkeypatch.setenv("AGENT_TASK_CAP_USD", "2")
    monkeypatch.setenv("AGENT_MONTHLY_CAP_USD", "1")
    store, starter = FakeRecords(), FakeStarter()
    _run(_ready_board(), store, starter_factory=lambda: starter, agents=True)
    assert starter.started == [] and store.agent_runs == []
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["agent_skipped_monthly_cap"] == 1


# ── the run report ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("groom", "agents", "parts"),
    [(True, False, "groom"), (False, True, "agents"), (True, True, "groom, agents")],
)
def test_the_report_says_the_sweep_was_requested_on_demand_and_for_what(
    off_schedule, _no_outbound_reports, groom, agents, parts
) -> None:
    _run(_ready_board(), FakeRecords(), groom=groom, agents=agents)
    ((_, kwargs),) = _no_outbound_reports
    assert sweep_mod.ON_DEMAND in kwargs["text"]
    assert parts in kwargs["text"]
