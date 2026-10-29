from __future__ import annotations

import dataclasses
import datetime as dt
import html
from dataclasses import dataclass, field

import pytest
from mini_app_polis import pipeline_status
from mini_app_polis.api.contract import (
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentSpend,
    GroomingJobAccepted,
    GroomingJobRequest,
    GroomingRecord,
    GroomingRecordWrite,
    GroomingReturnRate,
)
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord

from task_manager_cog import sweep as sweep_mod
from task_manager_cog.records import (
    GROOM_MAX_ATTEMPTS,
    GROOM_RETRY_AFTER,
    fingerprint,
)
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
    """The board in memory: tasks, columns, comments, and what was done."""

    records: list[AsanaTaskRecord]
    sections_: list[AsanaSection] = field(default_factory=lambda: list(SECTIONS))
    moves: list[tuple[str, str]] = field(default_factory=list)
    comments_: dict[str, list[str]] = field(default_factory=dict)
    untagged: list[tuple[str, str]] = field(default_factory=list)
    tagged: list[tuple[str, str]] = field(default_factory=list)
    created: list[tuple[str, str]] = field(default_factory=list)
    by_external_id: dict[str, str] = field(default_factory=dict)

    def sections(self) -> list[AsanaSection]:
        return self.sections_

    def tasks(self) -> list[AsanaTaskRecord]:
        return self.records

    def names(self) -> list[str]:
        return [t.name for t in self.records]

    def get(self, gid: str) -> AsanaTaskRecord | None:
        return next((t for t in self.records if t.gid == gid), None)

    def _replace(self, gid: str, **changes) -> None:
        self.records = [
            dataclasses.replace(t, **changes) if t.gid == gid else t
            for t in self.records
        ]

    def move(self, task_gid: str, section_gid: str) -> None:
        self.moves.append((task_gid, section_gid))
        name = next(s.name for s in self.sections_ if s.gid == section_gid)
        self._replace(task_gid, section_gid=section_gid, section_name=name)

    def update(self, task_gid: str, *, name: str, html_notes: str) -> None:
        text = html_notes.removeprefix("<body>").removesuffix("</body>")
        self._replace(task_gid, name=name, notes=html.unescape(text))

    def update_body(self, task_gid: str, html_notes: str) -> None:
        text = html_notes.removeprefix("<body>").removesuffix("</body>")
        self._replace(task_gid, notes=html.unescape(text), html_notes=html_notes)

    def assign(self, task_gid: str) -> None:
        self._replace(task_gid, assignee_gid="owner")

    def unassign(self, task_gid: str) -> None:
        self._replace(task_gid, assignee_gid=None)

    def tag(self, task_gid: str, tag_name: str) -> None:
        self.tagged.append((task_gid, tag_name))
        task = self.get(task_gid)
        assert task is not None
        self._replace(task_gid, tag_names=(*task.tag_names, tag_name))

    def untag(self, task_gid: str, tag_name: str) -> None:
        self.untagged.append((task_gid, tag_name))
        task = self.get(task_gid)
        assert task is not None
        self._replace(
            task_gid,
            tag_names=tuple(t for t in task.tag_names if t.lower() != tag_name),
        )

    def comment(self, task_gid: str, text: str) -> None:
        self.comments_.setdefault(task_gid, []).append(text)

    def find(self, external_id: str) -> str | None:
        return self.by_external_id.get(external_id)

    def create(self, name: str, task, *, external_id: str) -> str:
        gid = f"9{len(self.created) + 1}"
        text = task.html_notes.removeprefix("<body>").removesuffix("</body>")
        self.records.append(
            AsanaTaskRecord(
                gid=gid,
                name=name,
                notes=html.unescape(text),
                completed=False,
                section_gid="s-ideas",
                section_name="Ideas",
                tag_names=tuple(task.tag_names),
            )
        )
        self.created.append((name, external_id))
        self.by_external_id[external_id] = gid
        return gid

    def comments(self, task_gid: str) -> list[str]:
        return list(self.comments_.get(task_gid, []))

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
    _task("8", "CI-8 · infra: CI failing on dev"),
    _task("9", "API-9 · API: GET /v1/x answering 500", notes="look into this"),
    _task("10", "ROT-10 · Rotate BREVO_API_KEY in prd", notes="look into this"),
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

    jobs: list[GroomingJobRequest] = field(default_factory=list)
    refuse: bool = False

    def request_grooming(self, payload: GroomingJobRequest) -> GroomingJobAccepted:
        if self.refuse:
            raise RuntimeError("502 dispatch_failed")
        self.jobs.append(payload)
        return GroomingJobAccepted(message_id="m", task_gid=payload.task.task_gid)

    agent_runs: list[AgentRunCreate] = field(default_factory=list)

    def record_agent_run(self, payload: AgentRunCreate) -> AgentRun:
        self.agent_runs.append(payload)
        return AgentRun(
            id="00000000-0000-4000-8000-000000000001",
            status="dispatched",
            dispatched_at=dt.datetime.now(dt.UTC),
            **payload.model_dump(),
        )

    def list_agent_runs(self, *, status=None, repository=None) -> list[AgentRun]:
        return []

    def agent_spend(self) -> AgentSpend:
        return AgentSpend(month="2026-10", runs=0, cost_usd=0.0)

    def fail_agent_run(self, payload: AgentRunFailure) -> AgentRun:
        raise AssertionError("nothing fails here")

    return_rate: GroomingReturnRate | None = None
    return_rate_reads: list[int | None] = field(default_factory=list)

    def grooming_return_rate(self, days: int | None = None) -> GroomingReturnRate:
        self.return_rate_reads.append(days)
        if self.return_rate is None:
            raise RuntimeError("503 from the API")
        return self.return_rate


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
    written = first.writes[0]
    asked = GroomingRecord(
        **{
            **written.model_dump(),
            "outcome": "questions",
            "problems": [*written.problems, sweep_mod.OPEN_QUESTIONS],
        },
        updated_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
    )
    unchanged = sweep(FakeBoard([task]), repos=REPOS, move=False, records={"12": asked})
    assert unchanged.writes == []

    # Still waiting after an edit: questions are out until a grooming answers.
    edited = _task("12", "MINI-2 · Vague idea", notes="look into this: edited")
    after = sweep(FakeBoard([edited]), repos=REPOS, move=False, records={"12": asked})
    assert after.writes[0].outcome == "questions"


def test_a_task_with_open_questions_is_not_moved_even_when_it_lints_clean() -> None:
    task = BOARD_TASKS[0]  # passes the linter
    asked = GroomingRecord(
        task_gid=task.gid,
        task_id="MINI-1",
        fingerprint=fingerprint(task),
        groomed_fingerprint=fingerprint(task),
        outcome="questions",
        problems=[sweep_mod.OPEN_QUESTIONS],
        updated_at=dt.datetime(2026, 10, 22, tzinfo=dt.UTC),
    )
    board = FakeBoard([task])
    result = sweep(board, repos=REPOS, move=True, records={task.gid: asked})
    (verdict,) = result.verdicts
    assert verdict.problems == (sweep_mod.OPEN_QUESTIONS,)
    assert board.moves == []
    assert result.writes == []


def test_a_task_with_open_questions_in_its_body_is_not_moved_to_ready() -> None:
    task = _task(
        "1", "MINI-1 · Undecided", notes=f"{READY}\n\nOpen decisions:\n- A or B"
    )
    board = FakeBoard([task])
    result = sweep(board, repos=REPOS, move=True)
    assert board.moves == []
    assert result.verdicts[0].problems == (
        "body lists open questions (Open decisions)",
    )

    settled = _task("1", "MINI-1 · Undecided", notes=f"{READY}\n\nOpen decisions:")
    board = FakeBoard([settled])
    sweep(board, repos=REPOS, move=True)
    assert board.moves == [("1", "s-ready")]


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


class _Lines:
    """Stands in for the logger and keeps what was logged."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.lines.append(message % args)


def test_main_logs_every_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    board = FakeBoard(list(BOARD_TASKS))
    monkeypatch.setattr(sweep_mod.Board, "from_env", classmethod(lambda cls: board))
    monkeypatch.setattr(sweep_mod, "load_repos", lambda: REPOS)
    lines = _Lines()
    monkeypatch.setattr(sweep_mod, "log", lines)
    assert sweep_mod.main([]) == 0
    out = "\n".join(lines.lines)
    assert "READY  [Ideas] MINI-1 · Ready idea" in out
    assert "- Blocked by MINI-7, which is not done" in out
    assert "1 of 4 tasks ready" in out
    assert board.moves == []


# ── sending for grooming ──────────────────────────────────────────────────


def _sweep_sending(
    store: FakeRecords, board: FakeBoard, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    monkeypatch.setenv("GROOM_DISPATCH_LIMIT", limit)
    run_sweep(
        run_id="r",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
    )


def test_sending_is_off_unless_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sweep_mod.dispatch_limit() == 0
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    assert sweep_mod.dispatch_limit() == 5
    monkeypatch.setenv("GROOM_DISPATCH_LIMIT", "2")
    assert sweep_mod.dispatch_limit() == 2


def test_sending_only_in_groom_times(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROOM_DISPATCH", "true")
    monkeypatch.setenv("GROOM_TIMES", "04:15,05:15,06:15")
    monkeypatch.setenv("SWEEP_TIMEZONE", "America/Chicago")
    # 09:15 UTC is 04:15 in Chicago in summer (CDT) and 03:15 in winter (CST).
    summer = dt.datetime(2026, 7, 1, 9, 15, tzinfo=dt.UTC)
    winter = dt.datetime(2026, 12, 1, 9, 15, tzinfo=dt.UTC)
    assert sweep_mod.dispatch_limit(summer) == 5
    assert sweep_mod.dispatch_limit(winter) == 0
    assert sweep_mod.dispatch_limit(winter + dt.timedelta(hours=1)) == 5
    # The half-hour sweep after a groom time is not one for it.
    assert sweep_mod.dispatch_limit(summer + dt.timedelta(minutes=30)) == 0
    assert sweep_mod.dispatch_limit(summer + dt.timedelta(hours=3)) == 0


def test_due_every_sweep_unless_set() -> None:
    assert sweep_mod.due("AGENT_TIMES", dt.datetime(2026, 7, 1, 9, 15, tzinfo=dt.UTC))


def test_due_reads_times_in_utc_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TIMES", "09:45")
    assert sweep_mod.due("AGENT_TIMES", dt.datetime(2026, 7, 1, 9, 46, tzinfo=dt.UTC))
    assert not sweep_mod.due(
        "AGENT_TIMES", dt.datetime(2026, 7, 1, 9, 16, tzinfo=dt.UTC)
    )
    assert not sweep_mod.due(
        "AGENT_TIMES", dt.datetime(2026, 7, 1, 10, 15, tzinfo=dt.UTC)
    )


def test_due_across_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TIMES", "23:50")
    assert sweep_mod.due("AGENT_TIMES", dt.datetime(2026, 7, 2, 0, 5, tzinfo=dt.UTC))
    assert not sweep_mod.due(
        "AGENT_TIMES", dt.datetime(2026, 7, 2, 0, 25, tzinfo=dt.UTC)
    )


def test_tasks_not_ready_are_sent_once_up_to_the_limit(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    store = FakeRecords()
    board = FakeBoard(list(BOARD_TASKS))
    _sweep_sending(store, board, monkeypatch, "1")
    assert [j.task.name for j in store.jobs] == ["MINI-2 · Vague idea"]
    job = store.jobs[0]
    assert job.task.problems
    assert "MINI-2 · Vague idea" not in job.board
    assert "MINI-1 · Ready idea" in job.board
    assert store.stored["2"].groomed_fingerprint == fingerprint(BOARD_TASKS[1])

    # The next sweep sends the next one, and never the first again. MINI-6
    # only waits on its blocker, so it is never sent.
    _sweep_sending(store, board, monkeypatch, "1")
    assert [j.task.name for j in store.jobs] == [
        "MINI-2 · Vague idea",
        "MINI-7 · Open blocker",
    ]
    _sweep_sending(store, board, monkeypatch, "5")
    _sweep_sending(store, board, monkeypatch, "5")
    assert len(store.jobs) == 2


def test_a_task_only_waiting_on_a_blocker_is_never_sent() -> None:
    waiting = _task(
        "6",
        "MINI-6 · Waits on MINI-7",
        notes=READY.replace("Blocked by:", "Blocked by: MINI-7"),
    )
    blocker = _task("7", "MINI-7 · Open blocker", section="In progress")
    sent: list[GroomingJobRequest] = []
    result = sweep(
        FakeBoard([waiting, blocker]),
        repos=REPOS,
        move=True,
        send=lambda job: sent.append(job) or True,
        limit=5,
    )
    assert sent == []
    assert result.blocked == 1
    assert result.waiting == 0
    assert result.moved == []


# ── moving a task that waits on blockers to Blocked ─────────────────────


WAITS_ON_OPEN_TASK = READY.replace("Blocked by:", "Blocked by: MINI-7")
WAITS_ON_A_DATE = READY.replace("Blocked by:", "Blocked by: 2999-01-01")
OPEN_BLOCKER = _task("7", "MINI-7 · Open blocker", section="In progress")


@pytest.mark.parametrize(
    "notes", [WAITS_ON_OPEN_TASK, WAITS_ON_A_DATE], ids=["open task", "date ahead"]
)
@pytest.mark.parametrize("section", ["Ideas", "Refining"])
def test_a_task_only_waiting_on_blockers_moves_to_blocked(
    notes: str, section: str
) -> None:
    task = _task("6", "MINI-6 · Waits", notes=notes, section=section)
    board = FakeBoard([task, OPEN_BLOCKER])
    sent: list[GroomingJobRequest] = []
    result = sweep(
        board,
        repos=REPOS,
        move=True,
        send=lambda job: sent.append(job) or True,
        limit=5,
    )
    assert board.moves == [("6", "s-blocked")]
    assert [t.gid for t in result.moved_to_blocked] == ["6"]
    assert sent == []
    # The move is not a change to its content.
    (write,) = result.writes
    assert write.fingerprint == fingerprint(task)
    assert write.groomed_fingerprint is None


def test_with_moving_off_a_waiting_task_is_only_counted() -> None:
    task = _task("6", "MINI-6 · Waits", notes=WAITS_ON_OPEN_TASK, section="Refining")
    board = FakeBoard([task, OPEN_BLOCKER])
    result = sweep(board, repos=REPOS, move=False)
    assert board.moves == []
    assert result.moved_to_blocked == []
    assert result.blocked_not_moved == 1


def test_a_task_already_in_blocked_stays_until_its_blockers_are_done() -> None:
    task = _task("6", "MINI-6 · Waits", notes=WAITS_ON_OPEN_TASK, section="Blocked")
    board = FakeBoard([task, OPEN_BLOCKER])
    result = sweep(board, repos=REPOS, move=True)
    assert board.moves == []
    assert result.moved_to_blocked == []
    assert result.blocked_not_moved == 0

    done = dataclasses.replace(OPEN_BLOCKER, completed=True)
    board = FakeBoard([task, done])
    result = sweep(board, repos=REPOS, move=True)
    assert board.moves == [("6", "s-ready")]


def test_a_blocked_task_with_another_problem_stays_where_it_is() -> None:
    task = _task(
        "6",
        "MINI-6 · Waits",
        notes=WAITS_ON_OPEN_TASK.replace("Done when: the plan is clean", "Done when:"),
        section="Refining",
    )
    board = FakeBoard([task, OPEN_BLOCKER])
    sent: list[GroomingJobRequest] = []
    result = sweep(
        board,
        repos=REPOS,
        move=True,
        send=lambda job: sent.append(job) or True,
        limit=5,
    )
    assert board.moves == []
    assert result.moved_to_blocked == []
    assert [j.task.name for j in sent] == ["MINI-6 · Waits"]


@pytest.mark.parametrize(
    ("name", "section"),
    [
        ("CI-6 · infra: CI failing on dev", "Ideas"),
        ("RUN-6 · a run failed", "Ideas"),
        ("CONF-6 · a conformance finding", "Ideas"),
        ("API-6 · API: GET /v1/x answering 500", "Ideas"),
        ("ROT-6 · Rotate BREVO_API_KEY in prd", "Ideas"),
        ("MINI-6 · TEMPLATE — do not work", "Ideas"),
        ("MINI-6 · Being worked", "In progress"),
    ],
)
def test_unswept_tasks_are_never_moved_to_blocked(name: str, section: str) -> None:
    task = _task("6", name, notes=WAITS_ON_OPEN_TASK, section=section)
    board = FakeBoard([task, OPEN_BLOCKER])
    result = sweep(board, repos=REPOS, move=True)
    assert board.moves == []
    assert result.moved_to_blocked == []


def test_the_blocked_column_comes_from_a_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert sweep_mod.blocked_section() == "Blocked"
    monkeypatch.setenv("BLOCKED_SECTION", "Waiting")
    assert sweep_mod.blocked_section() == "Waiting"
    task = _task("6", "MINI-6 · Waits", notes=WAITS_ON_OPEN_TASK)
    board = FakeBoard(
        [task, OPEN_BLOCKER], sections_=[*SECTIONS, AsanaSection("s-wait", "Waiting")]
    )
    sweep(board, repos=REPOS, move=True)
    assert board.moves == [("6", "s-wait")]


def test_run_sweep_names_each_task_moved_to_blocked(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    task = _task("6", "MINI-6 · Waits", notes=WAITS_ON_OPEN_TASK, section="Refining")
    board = FakeBoard([task, OPEN_BLOCKER])
    run_sweep(
        run_id="r",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: FakeRecords(),
    )
    ((_, kwargs),) = _no_outbound_reports
    assert "Moved\n  ~ [Blocked: MINI-6 · Waits](https://board/6)" in kwargs["text"]


def test_run_sweep_counts_tasks_it_would_move_to_blocked(
    production, _no_outbound_reports
) -> None:
    task = _task("6", "MINI-6 · Waits", notes=WAITS_ON_OPEN_TASK, section="Refining")
    board = FakeBoard([task, OPEN_BLOCKER])
    run_sweep(
        run_id="r",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: FakeRecords(),
    )
    assert board.moves == []
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["blocked_not_moved"] == 1


def test_a_blocked_task_with_another_problem_is_still_sent() -> None:
    task = _task(
        "6",
        "MINI-6 · Waits on MINI-7",
        notes=READY.replace("Blocked by:", "Blocked by: MINI-7").replace(
            "Effort: S", "Effort:"
        ),
    )
    blocker = _task("7", "MINI-7 · Open blocker", section="In progress")
    sent: list[GroomingJobRequest] = []
    result = sweep(
        FakeBoard([task, blocker]),
        repos=REPOS,
        move=True,
        send=lambda job: sent.append(job) or True,
        limit=5,
    )
    assert [j.task.name for j in sent] == ["MINI-6 · Waits on MINI-7"]
    assert result.blocked == 0


def _send_from(
    tasks: list[AsanaTaskRecord],
) -> tuple[list[GroomingJobRequest], sweep_mod.SweepResult]:
    sent: list[GroomingJobRequest] = []
    result = sweep(
        FakeBoard(tasks),
        repos=REPOS,
        move=True,
        send=lambda job: sent.append(job) or True,
        limit=5,
    )
    return sent, result


def test_a_task_waiting_on_a_date_ahead_is_never_sent() -> None:
    waiting = _task(
        "6",
        "MINI-6 · Waits on a date",
        notes=READY.replace("Blocked by:", "Blocked by: 2999-01-01"),
        section="Blocked",
    )
    sent, result = _send_from([waiting])
    assert sent == []
    assert result.blocked == 1
    assert result.moved == []
    assert result.verdicts[0].problems == ("Blocked until 2999-01-01",)


def test_a_task_whose_date_has_come_moves_to_ready() -> None:
    waiting = _task(
        "6",
        "MINI-6 · Waited on a date",
        notes=READY.replace("Blocked by:", "Blocked by: 2000-01-01"),
        section="Blocked",
    )
    sent, result = _send_from([waiting])
    assert sent == []
    assert [t.name for t in result.moved] == ["MINI-6 · Waited on a date"]


def test_a_task_with_a_malformed_date_is_sent_for_grooming() -> None:
    task = _task(
        "6",
        "MINI-6 · Waits on a bad date",
        notes=READY.replace("Blocked by:", "Blocked by: 2026-11-2"),
        section="Blocked",
    )
    sent, result = _send_from([task])
    assert [j.task.name for j in sent] == ["MINI-6 · Waits on a bad date"]
    assert result.blocked == 0


def test_a_blocked_task_moves_to_ready_once_its_blocker_is_done() -> None:
    waiting = _task(
        "6",
        "MINI-6 · Waits on MINI-7",
        notes=READY.replace("Blocked by:", "Blocked by: MINI-7"),
    )
    blocker = _task("7", "MINI-7 · Blocker", section="In progress", completed=True)
    board = FakeBoard([waiting, blocker])
    result = sweep(board, repos=REPOS, move=True)
    assert [t.name for t in result.moved] == ["MINI-6 · Waits on MINI-7"]


def test_a_refused_job_is_sent_again_next_sweep(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    store = FakeRecords(refuse=True)
    board = FakeBoard([BOARD_TASKS[1]])
    _sweep_sending(store, board, monkeypatch, "5")
    assert store.stored["2"].groomed_fingerprint is None
    store.refuse = False
    _sweep_sending(store, board, monkeypatch, "5")
    assert len(store.jobs) == 1


def test_an_answer_sends_a_task_with_questions_again(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    task = BOARD_TASKS[1]
    store = FakeRecords()
    store.stored["2"] = GroomingRecord(
        task_gid="2",
        task_id="MINI-2",
        fingerprint=fingerprint(task),
        groomed_fingerprint=fingerprint(task),
        outcome="questions",
        updated_at=dt.datetime.now(dt.UTC),
    )
    board = FakeBoard([task], comments_={"2": ["Grooming questions:\n1. Why?"]})
    _sweep_sending(store, board, monkeypatch, "5")
    assert store.jobs == []

    board.comment("2", "Because the API needs it.")
    _sweep_sending(store, board, monkeypatch, "5")
    (job,) = store.jobs
    assert job.task.comments == ["Because the API needs it."]
    assert sweep_mod.OPEN_QUESTIONS not in job.task.problems
    # Still not ready while the grooming with the answers is out.
    assert store.stored["2"].outcome == "questions"
    assert job.task.fingerprint == fingerprint(task, ["Because the API needs it."])


# ── what the groomer is sent ──────────────────────────────────────────────


def test_a_job_lists_the_done_tasks_its_task_mentions() -> None:
    task = _task(
        "20",
        "MINI-20 · Epic",
        notes="Delivered by MINI-21 and MINI-22. Related: MINI-30.",
    )
    verdict = sweep_mod.Verdict(task=task, problems=("Effort is blank",))
    done = {
        "MINI-21": "MINI-21 · First part",
        "MINI-23": "MINI-23 · Unrelated and done",
    }
    job = sweep_mod.grooming_job(
        verdict, ["MINI-20 · Epic", "MINI-22 · Second part", "MINI-30 · Other"], done
    )
    assert job.board == [
        "MINI-21 · First part (done)",
        "MINI-22 · Second part",
        "MINI-30 · Other",
    ]


def test_an_answer_that_names_a_done_task_lists_it() -> None:
    task = _task("20", "MINI-20 · Epic", notes="vague")
    verdict = sweep_mod.Verdict(
        task=task, problems=("x",), replies=("MINI-21 is finished.",)
    )
    job = sweep_mod.grooming_job(verdict, [], {"MINI-21": "MINI-21 · First part"})
    assert job.board == ["MINI-21 · First part (done)"]


# ── agent dispatch at the end of the sweep ────────────────────────────────


@dataclass
class FakeStarter:
    started: list[str] = field(default_factory=list)

    def has_caller(self, repo: str) -> bool:
        return True

    def dispatch(self, repo: str, *, ref: str, inputs: dict[str, str]) -> None:
        self.started.append(repo)


def _sweep_dispatching(store, board, starter_factory) -> None:
    run_sweep(
        run_id="r",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
        starter_factory=starter_factory,
    )


def test_dispatch_is_off_unless_set(production, _no_outbound_reports) -> None:
    def never():
        raise AssertionError("dispatch is off")

    board = FakeBoard([_task("1", "MINI-1 · Ready", section="Ready")])
    _sweep_dispatching(FakeRecords(), board, never)


def test_a_ready_task_is_dispatched_after_grooming(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    # In Ideas and ready: the sweep moves it to Ready, then dispatches it.
    board = FakeBoard([_task("1", "MINI-1 · Ready idea")])
    board.sections_.append(AsanaSection("s-review", "Review"))
    store, starter = FakeRecords(), FakeStarter()
    _sweep_dispatching(store, board, lambda: starter)
    assert board.moves == [("1", "s-ready"), ("1", "s-progress")]
    assert starter.started == ["infra"]
    assert [r.task_id for r in store.agent_runs] == ["MINI-1"]
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["agent_dispatched"] == 1


def test_a_dry_run_dispatches_nothing(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    monkeypatch.setenv("AGENT_DRY_RUN", "true")
    board = FakeBoard([_task("1", "MINI-1 · Ready", section="Ready")])
    store, starter = FakeRecords(), FakeStarter()
    _sweep_dispatching(store, board, lambda: starter)
    assert board.moves == [] and starter.started == [] and store.agent_runs == []
    ((_, kwargs),) = _no_outbound_reports
    assert "would dispatch" in kwargs["text"]


def test_dispatch_without_the_app_is_reported_not_raised(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")

    def unconfigured():
        raise RuntimeError("AGENT_APP_CLIENT_ID not set")

    board = FakeBoard([_task("1", "MINI-1 · Ready", section="Ready")])
    _sweep_dispatching(FakeRecords(), board, unconfigured)
    ((_, kwargs),) = _no_outbound_reports
    assert "agent dispatch not configured" in kwargs["text"]


@pytest.mark.parametrize("tag", ["cowork", "person"])
def test_a_tagged_task_is_held_to_ready_like_any_other(tag: str) -> None:
    ready = dataclasses.replace(_task("1", "MINI-1 · Console work"), tag_names=(tag,))
    vague = dataclasses.replace(
        _task("2", "MINI-2 · Vague", notes="look into this"), tag_names=(tag,)
    )
    board = FakeBoard([ready, vague])
    result = sweep(board, repos=REPOS, move=True)
    assert [t.name for t in result.moved] == ["MINI-1 · Console work"]


def test_task_shapes_are_off_unless_set(monkeypatch: pytest.MonkeyPatch) -> None:
    two = _task(
        "1",
        "MINI-1 · Two repos",
        notes=READY.replace("Where: infra", "Where: infra, other"),
    )
    monkeypatch.delenv("GROOM_TASK_SHAPES", raising=False)
    assert sweep_mod.task_shapes() is False
    (verdict,) = assess([two], swept=("Ideas",), repos=frozenset({"infra", "other"}))
    assert verdict.ready
    monkeypatch.setenv("GROOM_TASK_SHAPES", "true")
    (verdict,) = assess([two], swept=("Ideas",), repos=frozenset({"infra", "other"}))
    assert not verdict.ready
    assert verdict.problems[0].startswith("Where names more than one repository")


@dataclass
class CountingBoard(FakeBoard):
    """A board that remembers whose comments were read."""

    read: list[str] = field(default_factory=list)

    def comments(self, task_gid: str) -> list[str]:
        self.read.append(task_gid)
        return super().comments(task_gid)


def test_a_questions_comment_from_anyone_holds_a_ready_task() -> None:
    task = BOARD_TASKS[0]  # passes the linter, and has no grooming record
    board = FakeBoard([task], comments_={"1": ["Grooming questions:\n1. Which queue?"]})
    result = sweep(board, repos=REPOS, move=True)
    (verdict,) = result.verdicts
    assert verdict.problems == (sweep_mod.OPEN_QUESTIONS,)
    assert verdict.awaiting_owner
    assert board.moves == []
    assert result.awaiting_owner == 1

    board.comment("1", "Answers to grooming questions:\n1. The FIFO one.")
    sweep(board, repos=REPOS, move=True)
    assert board.moves == [("1", "s-ready")]


def test_comments_without_questions_do_not_hold_a_task() -> None:
    board = FakeBoard([BOARD_TASKS[0]], comments_={"1": ["A note, not a question."]})
    sweep(board, repos=REPOS, move=True)
    assert board.moves == [("1", "s-ready")]


def test_comments_are_read_only_for_tasks_that_would_be_ready() -> None:
    board = CountingBoard(list(BOARD_TASKS))
    sweep(board, repos=REPOS, move=False)
    # MINI-1 is the only swept task the linter passes; MINI-2, MINI-6 and
    # MINI-7 fail it, and the rest are not swept.
    assert board.read == ["1"]


def test_a_task_waiting_on_its_owner_is_never_sent() -> None:
    board = FakeBoard(
        [BOARD_TASKS[0]], comments_={"1": ["Grooming questions:\n1. Which queue?"]}
    )
    sent: list[GroomingJobRequest] = []

    def send(job: GroomingJobRequest) -> bool:
        sent.append(job)
        return True

    first = sweep(board, repos=REPOS, move=True, send=send, limit=5)
    assert first.verdicts[0].awaiting_owner
    # Edited while the question is out: changed, and still not sent.
    board._replace("1", notes=f"{READY}\nEdited while the question is out.")
    records = {
        w.task_gid: GroomingRecord(**w.model_dump(), updated_at=dt.datetime.now(dt.UTC))
        for w in first.writes
    }
    again = sweep(board, repos=REPOS, move=True, records=records, send=send, limit=5)
    assert again.verdicts[0].changed
    assert sent == []
    assert board.moves == []


# ── Ready re-linted ───────────────────────────────────────────────────────


def _moved_record(task: AsanaTaskRecord) -> GroomingRecord:
    """The record of a task this cog moved to Ready when it passed."""
    return GroomingRecord(
        task_gid=task.gid,
        task_id=task.name.split(" ")[0],
        fingerprint=fingerprint(task),
        groomed_fingerprint=fingerprint(task),
        outcome="ready",
        moved_to_ready_at=dt.datetime(2026, 10, 23, tzinfo=dt.UTC),
        updated_at=dt.datetime(2026, 10, 23, tzinfo=dt.UTC),
    )


def _stored(write: GroomingRecordWrite) -> GroomingRecord:
    return GroomingRecord(**write.model_dump(), updated_at=dt.datetime.now(dt.UTC))


def test_a_passing_ready_task_is_left_alone() -> None:
    task = _task("1", "MINI-1 · Ready", section="Ready")
    board = FakeBoard([task])
    result = sweep(board, repos=REPOS, move=True, records={"1": _moved_record(task)})
    assert result.verdicts[0].ready
    assert board.moves == []
    assert board.comments_ == {}
    assert result.writes == []
    assert result.returned == [] and result.sent_back == []


def test_a_failing_ready_task_goes_back_to_refining_and_is_counted() -> None:
    passed = _task("1", "MINI-1 · Edited", section="Ready")
    task = _task("1", "MINI-1 · Edited", notes="look into this", section="Ready")
    board = FakeBoard([task])
    sent: list[GroomingJobRequest] = []

    def send(job: GroomingJobRequest) -> bool:
        sent.append(job)
        return True

    result = sweep(
        board,
        repos=REPOS,
        move=True,
        records={"1": _moved_record(passed)},
        send=send,
        limit=5,
    )
    assert board.moves == [("1", "s-refining")]
    (comment,) = board.comments_["1"]
    assert comment.startswith(sweep_mod.SENT_BACK_PREFIX)
    for problem in result.verdicts[0].problems:
        assert f"- {problem}" in comment
    assert [(t.gid, column) for t, column in result.sent_back] == [("1", "Refining")]
    (write,) = result.writes
    assert write.returned_count == 1
    assert write.moved_to_ready_at is None
    assert write.outcome == "not_ready"
    # Moving it back is not a change: it is not sent, now or next sweep.
    assert write.groomed_fingerprint == fingerprint(task)
    assert sent == []
    after = sweep(
        board,
        repos=REPOS,
        move=True,
        records={"1": _stored(write)},
        send=send,
        limit=5,
    )
    assert sent == [] and after.returned == []
    # It is now held, which its record says once, and only once.
    (held,) = after.writes
    assert sweep_mod.HELD in held.problems
    assert [t.gid for t in after.newly_held] == ["1"]
    again = sweep(
        board,
        repos=REPOS,
        move=True,
        records={"1": _stored(held)},
        send=send,
        limit=5,
    )
    assert sent == [] and again.writes == [] and again.newly_held == []


def test_a_task_the_budget_stopped_is_sent_again_once_a_sweep() -> None:
    task = _task("2", "MINI-2 · Vague idea", notes="look into this")
    other = _task("3", "MINI-3 · Another vague idea", notes="look into this")
    sent: list[GroomingJobRequest] = []

    def send(job: GroomingJobRequest) -> bool:
        sent.append(job)
        return True

    board = FakeBoard([task, other])
    first = sweep(board, repos=REPOS, move=True, send=send, limit=5)
    records = {w.task_gid: _stored(w) for w in first.writes}
    assert len(sent) == 2

    # The budget stopped MINI-2's run: it alone is sent again, once, and its
    # record is left as it is. MINI-3, sent and unchanged, is not.
    records["2"] = records["2"].model_copy(
        update={"budget_spent_fingerprint": fingerprint(task)}
    )
    again = sweep(board, repos=REPOS, move=True, records=records, send=send, limit=5)
    assert [j.task.task_gid for j in sent[2:]] == ["2"]
    assert [t.gid for t in again.sent] == ["2"]
    assert "2" not in {w.task_gid for w in again.writes}

    # Within the limit like any other send.
    records["3"] = records["3"].model_copy(
        update={"budget_spent_fingerprint": fingerprint(other)}
    )
    capped = sweep(board, repos=REPOS, move=True, records=records, send=send, limit=1)
    assert len(capped.sent) == 1 and capped.waiting == 1


def test_a_grooming_that_never_comes_back_is_sent_again_then_held() -> None:
    task = _task("2", "MINI-2 · Vague idea", notes="look into this")
    sent: list[GroomingJobRequest] = []

    def send(job: GroomingJobRequest) -> bool:
        sent.append(job)
        return True

    board = FakeBoard([task])
    start = dt.datetime(2026, 10, 28, 9, 15, tzinfo=dt.UTC)
    first = sweep(board, repos=REPOS, move=True, send=send, limit=5, now=start)
    records = {w.task_gid: _stored(w) for w in first.writes}
    assert len(sent) == 1 and records["2"].groom_attempts == 1

    # Nothing came back: awaited, not held, until it counts as lost.
    soon = start + dt.timedelta(hours=1)
    waiting = sweep(
        board, repos=REPOS, move=True, records=records, send=send, limit=5, now=soon
    )
    assert len(sent) == 1 and waiting.awaiting_grooming == 1
    assert waiting.held == [] and waiting.writes == []

    at = start
    for attempt in range(2, GROOM_MAX_ATTEMPTS + 1):
        at += GROOM_RETRY_AFTER
        again = sweep(
            board, repos=REPOS, move=True, records=records, send=send, limit=5, now=at
        )
        assert [t.gid for t in again.sent] == ["2"]
        records = {**records, **{w.task_gid: _stored(w) for w in again.writes}}
        assert records["2"].groom_attempts == attempt

    # Out of attempts: held and named, and not sent again.
    at += GROOM_RETRY_AFTER
    spent = sweep(
        board, repos=REPOS, move=True, records=records, send=send, limit=5, now=at
    )
    assert spent.sent == [] and [t.gid for t in spent.newly_held] == ["2"]
    assert len(sent) == GROOM_MAX_ATTEMPTS


def test_a_ready_task_waiting_on_a_blocker_goes_to_blocked() -> None:
    task = _task(
        "6",
        "MINI-6 · Waits on MINI-7",
        notes=READY.replace("Blocked by:", "Blocked by: MINI-7"),
        section="Ready",
    )
    blocker = _task("7", "MINI-7 · Open blocker", section="Refining", notes="later")
    board = FakeBoard([task, blocker])
    result = sweep(board, repos=REPOS, move=True)
    assert ("6", "s-blocked") in board.moves
    assert "6" not in board.comments_
    assert [(t.gid, column) for t, column in result.sent_back] == [("6", "Blocked")]
    write = next(w for w in result.writes if w.task_gid == "6")
    assert write.returned_count == 1


def test_an_unchanged_failed_task_put_back_in_ready_gets_no_second_comment() -> None:
    task = _task("1", "MINI-1 · Vague", notes="look into this", section="Ready")
    board = FakeBoard([task])
    first = sweep(board, repos=REPOS, move=True)
    # A person puts it back in Ready without changing it.
    board._replace("1", section_gid="s-ready", section_name="Ready")
    again = sweep(
        board, repos=REPOS, move=True, records={"1": _stored(first.writes[0])}
    )
    assert board.moves == [("1", "s-refining"), ("1", "s-refining")]
    assert len(board.comments_["1"]) == 1
    assert again.writes[0].returned_count == 2


def test_a_failing_ready_task_is_only_reported_when_moving_is_off() -> None:
    task = _task("1", "MINI-1 · Vague", notes="look into this", section="Ready")
    board = FakeBoard([task])
    result = sweep(board, repos=REPOS, move=False)
    assert [t.gid for t in result.failed_ready] == ["1"]
    assert board.moves == [] and board.comments_ == {}
    assert result.writes == [] and result.sent_back == []


def test_a_task_failing_in_ready_is_never_dispatched(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    # Moving off, so it stays in Ready: dispatch still leaves it.
    board = FakeBoard(
        [_task("1", "MINI-1 · Vague", notes="look into this", section="Ready")]
    )
    store, starter = FakeRecords(), FakeStarter()
    _sweep_dispatching(store, board, lambda: starter)
    assert starter.started == [] and store.agent_runs == []
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["failing_in_ready_not_moved"] == 1


def test_the_run_report_counts_tasks_sent_back_from_ready(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    board = FakeBoard(
        [_task("1", "MINI-1 · Vague", notes="look into this", section="Ready")]
    )
    store = FakeRecords()
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
    )
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["sent_back_from_ready"] == 1
    assert "~ [back from Ready to Refining: MINI-1 · Vague]" in kwargs["text"]
    assert store.stored["1"].returned_count == 1


# ── held with nobody asked ────────────────────────────────────────────────


def _groomed(task: AsanaTaskRecord, outcome: str = "not_ready") -> GroomingRecord:
    """The record of a task groomed as it now reads."""
    return GroomingRecord(
        task_gid=task.gid,
        task_id=task.name.split(" ")[0],
        fingerprint=fingerprint(task),
        groomed_fingerprint=fingerprint(task),
        outcome=outcome,
        problems=["Effort is missing"],
        updated_at=dt.datetime(2026, 10, 23, tzinfo=dt.UTC),
    )


def test_a_task_still_held_is_counted_but_not_named_again(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    held = _task("2", "MINI-2 · Held", notes="look into this", section="Refining")
    board = FakeBoard([held])
    store = FakeRecords(stored={"2": _groomed(held)})
    _sweep_sending(store, board, monkeypatch, "5")
    _sweep_sending(store, board, monkeypatch, "5")

    first, second = _no_outbound_reports
    assert "task newly held" in first[1]["text"]
    args, kwargs = second
    assert kwargs["held_without_questions"] == 1
    assert "MINI-2" not in kwargs["text"]
    assert args[1] == "SUCCESS" and kwargs["notable"] is False
    assert len(store.written) == 1


def test_a_task_held_with_nobody_asked_is_counted_and_named(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    held = _task("2", "MINI-2 · Held", notes="look into this", section="Refining")
    asked = _task("3", "MINI-3 · Asked", notes="look into this")
    waits = _task("6", "MINI-6 · Waits", notes=WAITS_ON_OPEN_TASK, section="Blocked")
    new = _task("8", "MINI-8 · New", notes="look into this")
    unswept = _task("9", "CI-9 · infra: CI failing", notes="look into this")
    template = _task("5", "MINI-5 · TEMPLATE — do not work", notes="x")
    board = FakeBoard([held, asked, waits, OPEN_BLOCKER, new, unswept, template])
    store = FakeRecords(
        stored={
            "2": _groomed(held),
            "3": _groomed(asked, "questions"),
            "9": _groomed(unswept),
            "5": _groomed(template),
        }
    )
    _sweep_sending(store, board, monkeypatch, "5")

    assert [j.task.name for j in store.jobs] == ["MINI-8 · New"]
    ((args, kwargs),) = _no_outbound_reports
    assert kwargs["held_without_questions"] == 1
    assert (
        "task newly held with nobody asked\n  [MINI-2 · Held](https://board/2)"
    ) in kwargs["text"]
    # Named, but not a problem with the run.
    assert args[1] == "SUCCESS" and kwargs["notable"] is True
    assert sweep_mod.HELD in store.stored["2"].problems
    # Nothing about it changes.
    assert board.moves == []
    assert board.comments("2") == []
    assert board.get("2") == held
    assert store.stored["2"].groomed_fingerprint == fingerprint(held)


def test_the_sweep_keeps_executors_in_step_before_it_judges_the_board(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    tagged = dataclasses.replace(
        _task("2", "MINI-2 · Rotate", section="Blocked"),
        tag_names=("person",),
        html_notes=f"<body>{READY}</body>",
    )
    board = FakeBoard([tagged])
    store = FakeRecords(stored={"2": _groomed(tagged)})

    _sweep_sending(store, board, monkeypatch, "5")

    task = board.get("2")
    assert task is not None
    assert "\nExecutor: person\n" in task.notes
    assert task.assignee_gid == "owner"
    assert store.jobs == []  # writing the line is not a change to groom
    (args, kwargs), *_ = _no_outbound_reports
    assert "executor kept in step" in kwargs["text"]
    assert "Executor: person written, assigned to the owner" in kwargs["text"]


# ── the run report's layout ──────────────────────────────────────────────


def _rendered(kwargs: dict) -> str:
    """The run report as it is posted: its text, then its counter sections."""
    reserved = {
        "text",
        "repo",
        "production_only",
        "source",
        "suggestion",
        "notable",
        "run_id",
        "version",
        "counter_labels",
        "counter_sections",
    }
    extras = {k: v for k, v in kwargs.items() if k not in reserved}
    return pipeline_status._merge_extras_into_text(
        kwargs["text"], extras, kwargs["counter_labels"], kwargs["counter_sections"]
    )


def _rate(moved: int, returned: int) -> GroomingReturnRate:
    now = dt.datetime.now(dt.UTC)
    return GroomingReturnRate(
        days=30,
        since=now - dt.timedelta(days=30),
        counting_since=now - dt.timedelta(days=30),
        moved_to_ready=moved,
        returned=returned,
        ratio=returned / moved if moved else None,
    )


def test_a_report_with_moves_has_moved_board_and_no_glued_counters(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_AUTO_READY", "true")
    board = FakeBoard(list(BOARD_TASKS))
    store = FakeRecords(return_rate=_rate(40, 3))
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
    )
    ((_, kwargs),) = _no_outbound_reports
    text = _rendered(kwargs)
    assert "Moved\n  ~ [Ready: MINI-1 · Ready idea](https://board/1)" in text
    board_section = text[text.index("\nBoard\n") :]
    assert "  4 tasks checked\n" in board_section
    assert "  returned from Ready, 30 days: 3 of 40 (8%)" in board_section
    assert "=" not in text.replace("(version=", "")
    for line in text.splitlines():
        if "MINI-1 · Ready idea" in line:
            assert "tasks checked" not in line
    # The names posted to the API are as they were.
    assert kwargs["tasks_checked"] == 4
    assert store.return_rate_reads == [30]


def test_a_report_without_moves_has_no_moved_section(
    production, _no_outbound_reports
) -> None:
    board = FakeBoard(list(BOARD_TASKS))
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: FakeRecords(return_rate=_rate(0, 0)),
    )
    ((_, kwargs),) = _no_outbound_reports
    text = _rendered(kwargs)
    assert "Moved" not in text
    assert "Board\n  4 tasks checked" in text
    assert "returned from Ready, 30 days: 0 of 0\n" in text + "\n"


def test_a_failed_ratio_read_leaves_the_line_out_and_is_noted(
    production, _no_outbound_reports
) -> None:
    board = FakeBoard(list(BOARD_TASKS))
    store = FakeRecords()  # its read raises
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=lambda: store,
    )
    ((_, kwargs),) = _no_outbound_reports
    text = _rendered(kwargs)
    assert "returned from Ready" not in text
    assert "return-from-Ready ratio not read" in text
    assert sweep_mod.RETURN_RATE not in kwargs


def test_the_agent_section_lists_spend_and_skips_one_a_line(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH", "true")
    monkeypatch.setenv("AGENT_DRY_RUN", "true")
    board = FakeBoard(
        [
            _task("1", "MINI-1 · Ready", section="Ready"),
            _task(
                "2",
                "MINI-2 · Medium",
                notes=READY.replace("Effort: S", "Effort: M"),
                section="Ready",
            ),
        ]
    )
    _sweep_dispatching(FakeRecords(return_rate=_rate(1, 0)), board, FakeStarter)
    ((_, kwargs),) = _no_outbound_reports
    text = _rendered(kwargs)
    agent_section = text[text.index("\nAgent\n") :]
    assert "  $0.0000 spent this month\n" in agent_section + "\n"
    assert "  1 skipped: effort not s" in agent_section
