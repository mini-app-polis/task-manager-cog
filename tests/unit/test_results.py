from __future__ import annotations

import dataclasses
import datetime as dt
import html
from dataclasses import dataclass, field

import pytest
from mini_app_polis.api.contract import AgentRun
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord

from task_manager_cog.lint import parse
from task_manager_cog.results import apply_result

RUN_ID = "0b6f7f5e-3c1a-4c8e-9a51-6f0c2f3d4e5a"
PR = "https://github.com/mini-app-polis/watcher-cog/pull/7"
RUN_URL = "https://github.com/mini-app-polis/watcher-cog/actions/runs/1"

NOTES = (
    "Done when: it works\nWhere: watcher-cog\nConstraints: None\nEffort: S\n"
    "PR:\nBlocked by: None\n\nBecause the watcher drifted."
)

SECTIONS = [
    AsanaSection("s-ready", "Ready"),
    AsanaSection("s-progress", "In progress"),
    AsanaSection("s-review", "Review"),
    AsanaSection("s-refining", "Refining"),
]


def _run(status: str = "succeeded", **changes) -> AgentRun:
    data = {
        "id": RUN_ID,
        "task_gid": "1",
        "task_id": "MINI-1",
        "repository": "watcher-cog",
        "model": "claude-sonnet-5-5",
        "status": status,
        "cost_usd": 0.84,
        "pr_url": PR if status == "succeeded" else None,
        "actions_run_url": RUN_URL,
        "reason": None if status == "succeeded" else "tests failed",
        "dispatched_at": dt.datetime(2026, 10, 23, 2, 0, tzinfo=dt.UTC),
        "finished_at": dt.datetime(2026, 10, 23, 2, 20, tzinfo=dt.UTC),
    }
    data.update(changes)
    return AgentRun.model_validate(data)


@dataclass
class FakeBoard:
    task: AsanaTaskRecord | None
    moves: list[str] = field(default_factory=list)
    updates: int = 0
    comments_: list[str] = field(default_factory=list)

    def get(self, gid: str) -> AsanaTaskRecord | None:
        return self.task

    def sections(self) -> list[AsanaSection]:
        return SECTIONS

    def move(self, gid: str, section_gid: str) -> None:
        self.moves.append(section_gid)
        assert self.task is not None
        self.task = dataclasses.replace(self.task, section_gid=section_gid)

    def update(self, gid: str, *, name: str, html_notes: str) -> None:
        self.updates += 1
        assert self.task is not None
        text = html_notes.removeprefix("<body>").removesuffix("</body>")
        self.task = dataclasses.replace(self.task, name=name, notes=html.unescape(text))

    def comment(self, gid: str, text: str) -> None:
        self.comments_.append(text)

    def comments(self, gid: str) -> list[str]:
        return list(self.comments_)

    def url(self, gid: str) -> str:
        return f"https://board/{gid}"


def _board(section: str = "s-progress", *, completed: bool = False) -> FakeBoard:
    return FakeBoard(
        AsanaTaskRecord(
            gid="1",
            name="MINI-1 · Fix the watcher",
            notes=NOTES,
            completed=completed,
            section_gid=section,
            section_name="",
        )
    )


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)


def _apply(run: AgentRun, board: FakeBoard) -> None:
    apply_result(run, run_id="m-1", board_factory=lambda: board)


def test_a_success_fills_the_pr_line_and_moves_to_review(
    production, _no_outbound_reports
) -> None:
    board = _board()
    _apply(_run(), board)

    assert board.moves == ["s-review"]
    assert board.task is not None
    parsed = parse(board.task.notes)
    assert parsed.fields["PR"] == PR
    assert parsed.fields["Where"] == "watcher-cog"
    assert parsed.body == "Because the watcher drifted."
    (comment,) = board.comments_
    assert comment.startswith(f"Agent run {RUN_ID} opened a draft pull request")
    assert "$0.84" in comment and "mark this task done" in comment
    assert board.task.completed is False
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["cost_usd"] == "0.8400"


@pytest.mark.parametrize(
    ("status", "words"), [("failed", "failed"), ("capped", "budget limit")]
)
def test_a_run_that_did_not_succeed_leaves_the_task_in_progress(
    production, status: str, words: str
) -> None:
    board = _board()
    _apply(_run(status, cost_usd=2.01 if status == "capped" else 0.3), board)
    assert board.moves == [] and board.updates == 0
    (comment,) = board.comments_
    assert words in comment
    assert "tests failed" in comment
    assert "not dispatched again" in comment


def test_an_unknown_cost_says_so(production) -> None:
    board = _board()
    _apply(_run("failed", cost_usd=None), board)
    assert "Cost: unknown" in board.comments_[0]


def test_a_task_moved_away_is_left_where_it_is(production) -> None:
    board = _board("s-refining")
    _apply(_run(), board)
    assert board.moves == [] and board.updates == 0
    (comment,) = board.comments_
    assert PR in comment and "left where it is" in comment


def test_a_redelivered_result_changes_nothing(production) -> None:
    board = _board()
    _apply(_run(), board)
    _apply(_run(), board)
    assert board.moves == ["s-review"]
    assert board.updates == 1
    assert len(board.comments_) == 1

    failed = _board()
    _apply(_run("failed"), failed)
    _apply(_run("failed"), failed)
    assert len(failed.comments_) == 1


def test_a_done_or_deleted_task_is_not_touched(production) -> None:
    board = _board(completed=True)
    _apply(_run(), board)
    assert board.moves == [] and board.comments_ == []
    gone = FakeBoard(None)
    _apply(_run(), gone)
    assert gone.comments_ == []


def test_nothing_is_written_outside_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)
    board = _board()
    _apply(_run(), board)
    assert board.moves == [] and board.comments_ == []
