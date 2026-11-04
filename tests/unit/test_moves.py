from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from task_manager_cog.moves import apply_moves

NOW = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.UTC)
SECTIONS = [
    AsanaSection("s-ready", "Ready"),
    AsanaSection("s-progress", "In progress"),
    AsanaSection("s-review", "Review"),
    AsanaSection("s-hold", "On Hold"),
]
NAMES = {s.gid: s.name for s in SECTIONS}


def _ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


def _task(gid: str, section: str, *, completed: bool = False) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=f"MINI-{gid} · Task {gid}",
        notes="",
        completed=completed,
        section_gid=section,
        section_name=NAMES[section],
        created_at=_ago(100),
    )


def _comment(text: str, hours_ago: float) -> AsanaStory:
    return AsanaStory(
        gid=f"c-{text}-{hours_ago}",
        kind="comment_added",
        created_at=_ago(hours_ago),
        text=text,
    )


def _entered(section: str, hours_ago: float) -> AsanaStory:
    return AsanaStory(
        gid=f"m-{section}-{hours_ago}",
        kind="section_changed",
        created_at=_ago(hours_ago),
        section_gid=section,
        section_name=NAMES[section],
    )


@dataclass
class FakeBoard:
    records: list[AsanaTaskRecord]
    history: dict[str, list[AsanaStory]] = field(default_factory=dict)
    moves: list[tuple[str, str]] = field(default_factory=list)

    def sections(self) -> list[AsanaSection]:
        return SECTIONS

    def tasks(self) -> list[AsanaTaskRecord]:
        return self.records

    def stories(self, task_gid: str) -> list[AsanaStory]:
        return self.history.get(task_gid, [])

    def move(self, task_gid: str, section_gid: str) -> None:
        self.moves.append((task_gid, section_gid))


def test_started_moves_a_ready_task_to_in_progress() -> None:
    board = FakeBoard(
        [_task("1", "s-ready")],
        {"1": [_entered("s-ready", 10), _comment("Started: cowork session", 1)]},
    )
    result = apply_moves(board)
    assert board.moves == [("1", "s-progress")]
    assert result is not None
    assert [(t.gid, c) for t, c in result.moved] == [("1", "In progress")]


def test_handed_off_moves_an_in_progress_task_to_review() -> None:
    board = FakeBoard(
        [_task("1", "s-progress")],
        {"1": [_entered("s-progress", 10), _comment("Handed off: all done", 1)]},
    )
    apply_moves(board)
    assert board.moves == [("1", "s-review")]


def test_started_and_handed_off_between_sweeps_go_straight_to_review() -> None:
    board = FakeBoard(
        [_task("1", "s-ready")],
        {
            "1": [
                _entered("s-ready", 10),
                _comment("Started: picked up", 2),
                _comment("Handed off: done", 1),
            ]
        },
    )
    apply_moves(board)
    assert board.moves == [("1", "s-review")]


def test_a_comment_from_before_the_task_entered_its_column_moves_nothing() -> None:
    # Started: moved it to In progress already; the comment predates that move.
    board = FakeBoard(
        [_task("1", "s-progress")],
        {
            "1": [
                _comment("Started: picked up", 5),
                _entered("s-progress", 4),
                _comment("Started: picked up again", 3),
            ]
        },
    )
    result = apply_moves(board)
    assert board.moves == []
    assert result is not None and result.moved == []


def test_handed_off_in_ready_without_started_is_the_wrong_column() -> None:
    board = FakeBoard(
        [_task("1", "s-ready")],
        {"1": [_entered("s-ready", 10), _comment("Handed off: done", 1)]},
    )
    result = apply_moves(board)
    assert board.moves == []
    assert result is not None
    assert [t.gid for t in result.wrong_column] == ["1"]


def test_tasks_on_hold_completed_or_elsewhere_are_never_moved() -> None:
    board = FakeBoard(
        [
            _task("1", "s-hold"),
            _task("2", "s-ready", completed=True),
            _task("3", "s-review"),
        ],
        {
            "1": [_comment("Started: x", 1)],
            "2": [_comment("Started: x", 1)],
            "3": [_comment("Handed off: x", 1)],
        },
    )
    apply_moves(board)
    assert board.moves == []


def test_a_board_without_the_columns_skips_the_pass() -> None:
    board = FakeBoard([_task("1", "s-ready")])
    board.sections = lambda: [AsanaSection("s-ready", "Ready")]  # type: ignore[method-assign]
    assert apply_moves(board) is None
