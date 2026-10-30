from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pytest
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from task_manager_cog import review as review_mod
from task_manager_cog.review import review

NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)
SECTIONS = [AsanaSection("s-ready", "Ready"), AsanaSection("s-review", "Review")]


def _hours_ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


def _task(
    gid: str = "1",
    *,
    section: str = "Review",
    completed: bool = False,
    created_hours_ago: float = 100,
) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=f"MINI-{gid} · Title {gid}",
        notes="",
        completed=completed,
        section_gid=f"s-{section.lower()}",
        section_name=section,
        created_at=_hours_ago(created_hours_ago),
    )


def _comment(text: str, hours_ago: float) -> AsanaStory:
    return AsanaStory(
        gid=f"c-{text}-{hours_ago}",
        kind="comment_added",
        created_at=_hours_ago(hours_ago),
        text=text,
    )


def _entered(hours_ago: float) -> AsanaStory:
    return AsanaStory(
        gid=f"m-{hours_ago}",
        kind="section_changed",
        created_at=_hours_ago(hours_ago),
        section_gid="s-review",
        section_name="Review",
    )


@dataclass
class FakeAsanaBoard:
    records: list[AsanaTaskRecord]
    history: dict[str, list[AsanaStory]] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    moves: list[str] = field(default_factory=list)

    def sections(self) -> list[AsanaSection]:
        return SECTIONS

    def tasks(self) -> list[AsanaTaskRecord]:
        return self.records

    def stories(self, task_gid: str) -> list[AsanaStory]:
        return self.history.get(task_gid, [])

    def complete(self, task_gid: str) -> None:
        self.completed.append(task_gid)


def test_a_latest_landed_comment_closes_the_task() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(10),
                _comment("Handed off: PR 4", 9),
                _comment("Landed: repo, dev, abc123, CI green", 1),
            ]
        },
    )
    result = review(board, now=NOW)
    assert board.completed == ["1"]
    assert [t.gid for t in result.closed] == ["1"]
    assert result.missing_handoff == []
    assert result.unverified == []
    assert board.moves == []


def test_a_comment_after_landed_leaves_the_task_open() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(10),
                _comment("Landed: repo, dev, abc123, CI green", 3),
                _comment("Wait, CI was rerun", 1),
            ]
        },
    )
    result = review(board, now=NOW)
    assert board.completed == []
    assert result.closed == []


def test_a_task_with_no_comment_is_missing_a_handoff_at_once() -> None:
    board = FakeAsanaBoard([_task()], {"1": [_entered(0.5)]})
    result = review(board, now=NOW)
    assert [t.gid for t in result.missing_handoff] == ["1"]
    assert result.unverified == []
    assert board.completed == []


def test_a_comment_from_before_review_is_not_a_handoff() -> None:
    board = FakeAsanaBoard([_task()], {"1": [_comment("old note", 50), _entered(2)]})
    result = review(board, now=NOW)
    assert [t.gid for t in result.missing_handoff] == ["1"]


def test_a_handoff_posted_just_before_the_move_counts() -> None:
    board = FakeAsanaBoard(
        [_task()], {"1": [_comment("Handed off: PR 4", 2.1), _entered(2)]}
    )
    result = review(board, now=NOW)
    assert result.missing_handoff == []


def test_a_handoff_followed_by_another_note_before_the_move_does_not_count() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _comment("Handed off: PR 4", 3),
                _comment("one more thing", 2.5),
                _entered(2),
            ]
        },
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.missing_handoff] == ["1"]


def test_a_task_without_landed_is_unverified_after_24_hours() -> None:
    board = FakeAsanaBoard(
        [_task()], {"1": [_entered(25), _comment("Handed off: PR 4", 24.5)]}
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.unverified] == ["1"]
    assert result.missing_handoff == []
    assert board.completed == []


def test_a_task_is_not_unverified_before_24_hours() -> None:
    board = FakeAsanaBoard(
        [_task()], {"1": [_entered(23), _comment("Handed off: PR 4", 22)]}
    )
    result = review(board, now=NOW)
    assert result.unverified == []
    assert result.missing_handoff == []


def test_a_task_never_moved_enters_review_at_its_creation() -> None:
    board = FakeAsanaBoard(
        [_task(created_hours_ago=30)], {"1": [_comment("Handed off: PR 4", 29)]}
    )
    assert [t.gid for t in review(board, now=NOW).unverified] == ["1"]


def test_the_latest_move_into_review_counts() -> None:
    board = FakeAsanaBoard(
        [_task()], {"1": [_entered(80), _entered(2), _comment("Handed off", 1)]}
    )
    assert review(board, now=NOW).unverified == []


def test_the_threshold_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    board = FakeAsanaBoard(
        [_task()], {"1": [_entered(5), _comment("Handed off: PR 4", 4)]}
    )
    assert review(board, now=NOW).unverified == []
    monkeypatch.setenv("REVIEW_UNVERIFIED_HOURS", "4")
    assert [t.gid for t in review(board, now=NOW).unverified] == ["1"]


def test_only_incomplete_tasks_in_review_are_read() -> None:
    board = FakeAsanaBoard(
        [_task("1", completed=True), _task("2", section="Ready")],
        {"1": [_comment("Landed: x", 1)], "2": [_comment("Landed: x", 1)]},
    )
    result = review(board, now=NOW)
    assert board.completed == []
    assert result.missing_handoff == []
    assert result.unverified == []


def test_a_board_without_a_review_column_is_skipped() -> None:
    board = FakeAsanaBoard([_task()])
    board.sections = lambda: [AsanaSection("s-ready", "Ready")]  # type: ignore[method-assign]
    assert review(board, now=NOW) is None


def test_the_label_is_the_id_and_title() -> None:
    task = _task("7")
    assert review_mod.task_label(task) == task.name
