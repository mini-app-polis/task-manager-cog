from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field

import pytest
from mini_app_polis.api import KaianoApiError
from mini_app_polis.api.contract import (
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentSpend,
)
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord

from task_manager_cog import dispatch as agent

NOW = dt.datetime(2026, 10, 23, 3, 0, tzinfo=dt.UTC)

SECTIONS = [
    AsanaSection("s-refining", "Refining"),
    AsanaSection("s-ready", "Ready"),
    AsanaSection("s-progress", "In progress"),
    AsanaSection("s-review", "Review"),
]


def _notes(*, where: str = "watcher-cog", effort: str = "S") -> str:
    return (
        f"Done when: it works\nWhere: {where}\nConstraints: None\n"
        f"Effort: {effort}\nPR:\nBlocked by: None\n\nBecause."
    )


def _task(gid: str, name: str, *, section: str = "Ready", **notes) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=name,
        notes=_notes(**notes),
        completed=False,
        section_gid=f"s-{section.lower().replace(' ', '')}"
        if section != "In progress"
        else "s-progress",
        section_name=section,
    )


def _run(run_id: str, repo: str, *, minutes_ago: int = 5, gid: str = "9") -> AgentRun:
    return AgentRun(
        id=run_id,
        task_gid=gid,
        task_id="MINI-9",
        repository=repo,
        model="claude-sonnet-5-5",
        status="dispatched",
        dispatched_at=NOW - dt.timedelta(minutes=minutes_ago),
    )


RUN_IDS = [f"00000000-0000-4000-8000-00000000000{i}" for i in range(10)]


@dataclass
class FakeStore:
    in_flight: list[AgentRun] = field(default_factory=list)
    spend: float = 0.0
    recorded: list[AgentRunCreate] = field(default_factory=list)
    failed: list[AgentRunFailure] = field(default_factory=list)
    conflict: set[str] = field(default_factory=set)

    def record_agent_run(self, payload: AgentRunCreate) -> AgentRun:
        if payload.repository in self.conflict:
            raise KaianoApiError(409, "run_in_flight", "/v1/tasks/agent-runs")
        self.recorded.append(payload)
        return AgentRun(
            id=RUN_IDS[len(self.recorded)],
            status="dispatched",
            dispatched_at=NOW,
            **payload.model_dump(),
        )

    def list_agent_runs(self, *, status=None, repository=None) -> list[AgentRun]:
        assert status == "dispatched"
        return list(self.in_flight)

    def agent_spend(self) -> AgentSpend:
        return AgentSpend(month="2026-10", runs=0, cost_usd=self.spend)

    def fail_agent_run(self, payload: AgentRunFailure) -> AgentRun:
        self.failed.append(payload)
        return _run(payload.run_id, "x")


@dataclass
class FakeStarter:
    callers: set[str] = field(
        default_factory=lambda: {"watcher-cog", "wiki-curator-cog"}
    )
    started: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    fail: bool = False

    def has_caller(self, repo: str) -> bool:
        return repo in self.callers

    def dispatch(self, repo: str, *, ref: str, inputs: dict[str, str]) -> None:
        if self.fail:
            raise RuntimeError("422 workflow not found on dev")
        self.started.append((repo, ref, inputs))


@dataclass
class FakeBoard:
    moves: list[tuple[str, str]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)

    def move(self, task_gid: str, section_gid: str) -> None:
        self.moves.append((task_gid, section_gid))

    def comment(self, task_gid: str, text: str) -> None:
        self.comments.append((task_gid, text))


def _dispatch(tasks, *, store=None, starter=None, board=None, write=True):
    store = store or FakeStore()
    starter = starter or FakeStarter()
    board = board or FakeBoard()
    result = agent.dispatch(
        tasks,
        SECTIONS,
        ready_gid="s-ready",
        board=board,
        store=store,
        starter=starter,
        unswept_prefixes=frozenset({"CI", "RUN", "CONF", "API"}),
        template_marker="do not work",
        write=write,
        now=lambda: NOW,
    )
    return result, store, starter, board


def test_a_small_ready_task_is_recorded_moved_then_started() -> None:
    task = _task("1", "MINI-1 · Fix the watcher")
    result, store, starter, board = _dispatch([task])

    assert [c.task.gid for c, _ in result.dispatched] == ["1"]
    assert store.recorded == [
        AgentRunCreate(
            task_gid="1",
            task_id="MINI-1",
            repository="watcher-cog",
            model="claude-sonnet-5-5",
        )
    ]
    assert board.moves == [("1", "s-progress")]
    ((repo, ref, inputs),) = starter.started
    assert (repo, ref) == ("watcher-cog", "dev")
    assert inputs["run_id"] == RUN_IDS[1]
    assert inputs["task_id"] == "MINI-1"
    assert inputs["task"].startswith("MINI-1 · Fix the watcher\n\nDone when:")
    assert inputs["budget_usd"] == "2.00"
    assert all(isinstance(v, str) for v in inputs.values())


@pytest.mark.parametrize(
    ("task", "reason"),
    [
        (_task("1", "MINI-1 · Bigger", effort="M"), agent.SKIP_NOT_SMALL),
        (_task("1", "MINI-1 · Large", effort="L"), agent.SKIP_NOT_SMALL),
        (
            _task("1", "MINI-1 · Two repos", where="watcher-cog, infra"),
            agent.SKIP_WHERE,
        ),
        (_task("1", "MINI-1 · Nowhere", where=""), agent.SKIP_WHERE),
        (_task("1", "MINI-1 · No caller", where="infra"), agent.SKIP_NO_CALLER),
    ],
)
def test_a_task_the_agent_cannot_take_stays_in_ready(task, reason) -> None:
    result, store, starter, board = _dispatch([task])
    assert result.dispatched == []
    assert result.skipped == {reason: 1}
    assert store.recorded == [] and board.moves == [] and starter.started == []


def test_one_repository_named_twice_is_one_repository() -> None:
    task = _task("1", "MINI-1 · Paths", where="watcher-cog/src, watcher-cog (tests)")
    result, *_ = _dispatch([task])
    assert len(result.dispatched) == 1


def test_the_template_detected_problems_and_other_columns_are_never_taken() -> None:
    tasks = [
        _task("1", "MINI-8 · TEMPLATE — do not work"),
        _task("2", "CI-4 · watcher-cog: CI failing on dev"),
        _task("3", "RUN-2 · watcher-cog run failed"),
        _task("4", "MINI-4 · Refining", section="Refining"),
        _task("5", "MINI-5 · Already going", section="In progress"),
    ]
    result, store, *_ = _dispatch(tasks)
    assert result.dispatched == [] and result.skipped == {}
    assert store.recorded == []


def test_one_run_per_repository_a_sweep_and_none_beside_one_in_flight() -> None:
    tasks = [
        _task("1", "MINI-1 · First"),
        _task("2", "MINI-2 · Second"),
        _task("3", "MINI-3 · Curator", where="wiki-curator-cog"),
    ]
    store = FakeStore(in_flight=[_run(RUN_IDS[0], "wiki-curator-cog")])
    result, *_ = _dispatch(tasks, store=store)
    assert [c.task.gid for c, _ in result.dispatched] == ["1"]
    assert result.skipped == {agent.SKIP_IN_FLIGHT: 2}


def test_a_run_the_list_missed_is_a_busy_repository_not_a_failure() -> None:
    store = FakeStore(conflict={"watcher-cog"})
    result, _, starter, board = _dispatch([_task("1", "MINI-1 · Raced")], store=store)
    assert result.skipped == {agent.SKIP_IN_FLIGHT: 1}
    assert board.moves == [] and starter.started == []


def test_the_monthly_cap_counts_runs_in_flight_at_the_task_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MONTHLY_CAP_USD", "20")
    tasks = [
        _task("1", "MINI-1 · Watcher"),
        _task("2", "MINI-2 · Curator", where="wiki-curator-cog"),
    ]
    # 15.50 spent and one run in flight (2.00) leaves room for one more.
    store = FakeStore(spend=15.5, in_flight=[_run(RUN_IDS[0], "db-ops")])
    result, *_ = _dispatch(tasks, store=store)
    assert [c.task.gid for c, _ in result.dispatched] == ["1"]
    assert result.skipped == {agent.SKIP_MONTHLY_CAP: 1}

    store = FakeStore(spend=18.5)
    result, *_ = _dispatch(tasks, store=store)
    assert result.dispatched == []
    assert result.skipped == {agent.SKIP_MONTHLY_CAP: 2}


def test_a_run_that_cannot_start_is_failed_and_left_in_progress() -> None:
    task = _task("1", "MINI-1 · Fix the watcher")
    result, store, _, board = _dispatch([task], starter=FakeStarter(fail=True))
    assert result.dispatched == []
    assert [c.task.gid for c in result.not_started] == ["1"]
    assert board.moves == [("1", "s-progress")]
    (failure,) = store.failed
    assert failure.run_id == RUN_IDS[1]
    assert "could not be started" in failure.reason
    ((gid, text),) = board.comments
    assert gid == "1" and text.startswith(f"Agent run {RUN_IDS[1]}")
    assert "not dispatched again" in text


def test_a_silent_run_past_the_timeout_is_failed_and_frees_its_repository() -> None:
    stale = _run(RUN_IDS[0], "watcher-cog", minutes_ago=120, gid="7")
    store = FakeStore(in_flight=[stale])
    result, _, _, board = _dispatch([_task("1", "MINI-1 · Next")], store=store)
    assert result.stale == [stale]
    assert [f.run_id for f in store.failed] == [RUN_IDS[0]]
    assert board.comments[0][0] == "7"
    assert "no result after 90 minutes" in board.comments[0][1]
    assert [c.task.gid for c, _ in result.dispatched] == ["1"]


def test_a_dry_run_writes_nothing_anywhere() -> None:
    stale = _run(RUN_IDS[0], "wiki-curator-cog", minutes_ago=120)
    store = FakeStore(in_flight=[stale])
    tasks = [_task("1", "MINI-1 · Watcher"), _task("2", "MINI-2 · Big", effort="M")]
    result, store, starter, board = _dispatch(tasks, store=store, write=False)
    assert [c.task.gid for c in result.would_dispatch] == ["1"]
    assert result.stale == [stale]
    assert result.skipped == {agent.SKIP_NOT_SMALL: 1}
    assert store.recorded == [] and store.failed == []
    assert board.moves == [] and board.comments == [] and starter.started == []


def test_settings_have_defaults_and_read_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not agent.enabled() and not agent.dry_run()
    assert (agent.task_cap(), agent.monthly_cap()) == (2.0, 20.0)
    assert agent.model() == "claude-sonnet-5-5"
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    monkeypatch.setenv("AGENT_TASK_CAP_USD", "1.5")
    monkeypatch.setenv("AGENT_RUN_TIMEOUT_MINUTES", "30")
    assert agent.enabled()
    assert agent.task_cap() == 1.5
    assert agent.run_timeout() == dt.timedelta(minutes=30)


def test_a_board_without_the_in_progress_column_stops_dispatch() -> None:
    with pytest.raises(RuntimeError, match="In progress"):
        agent.dispatch(
            [],
            [AsanaSection("s-ready", "Ready")],
            ready_gid="s-ready",
            board=FakeBoard(),
            store=FakeStore(),
            starter=FakeStarter(),
            unswept_prefixes=frozenset(),
            template_marker="do not work",
            write=True,
        )


@pytest.mark.parametrize("tag", ["cowork", "Person"])
def test_a_task_tagged_for_another_executor_is_never_dispatched(tag: str) -> None:
    tagged = dataclasses.replace(_task("1", "MINI-1 · Console work"), tag_names=(tag,))
    other = _task("2", "MINI-2 · Agent work", where="wiki-curator-cog")
    result, store, starter, board = _dispatch([tagged, other])
    assert [c.task.name for c, _ in result.dispatched] == ["MINI-2 · Agent work"]
    assert result.skipped == {f"executor_{tag.lower()}": 1}
    assert [r.task_gid for r in store.recorded] == ["2"]


def test_an_unrelated_tag_does_not_stop_dispatch() -> None:
    tagged = dataclasses.replace(_task("1", "MINI-1 · Admin"), tag_names=("admin",))
    result, *_ = _dispatch([tagged])
    assert len(result.dispatched) == 1
