from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pytest
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from task_manager_cog import review as review_mod
from task_manager_cog.review import review

NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)
SECTIONS = [
    AsanaSection("s-ready", "Ready"),
    AsanaSection("s-in progress", "In progress"),
    AsanaSection("s-review", "Review"),
    AsanaSection("s-complete", "Complete"),
]
#: Two Done when items, as sentences.
NOTES = (
    "Done when: The widget renders. Its test passes.\n"
    "Where: task-manager-cog\n"
    "PR: https://github.com/miniappolis/task-manager-cog/pull/4\n"
    "Executor: agent"
)
HANDOFF = "Handed off: PR 4\n- renders on dev, src/widget.py\n- test_widget passes"


def _hours_ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


def _task(
    gid: str = "1",
    *,
    section: str = "Review",
    completed: bool = False,
    created_hours_ago: float = 100,
    notes: str = NOTES,
) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=f"MINI-{gid} · Title {gid}",
        notes=notes,
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


def _marked_complete(at: dt.datetime) -> AsanaStory:
    return AsanaStory(gid=f"done-{at}", kind="marked_complete", created_at=at)


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
    moves: list[tuple[str, str]] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    posted: list[tuple[str, str]] = field(default_factory=list)
    #: Every write, in order, to check what happens before what.
    writes: list[str] = field(default_factory=list)

    def sections(self) -> list[AsanaSection]:
        return SECTIONS

    def tasks(self) -> list[AsanaTaskRecord]:
        return self.records

    def stories(self, task_gid: str) -> list[AsanaStory]:
        return self.history.get(task_gid, [])

    def complete(self, task_gid: str) -> None:
        self.completed.append(task_gid)
        self.writes.append(f"complete {task_gid}")

    def reopen(self, task_gid: str) -> None:
        self.reopened.append(task_gid)
        self.writes.append(f"reopen {task_gid}")

    def move(self, task_gid: str, section_gid: str) -> None:
        self.moves.append((task_gid, section_gid))
        self.writes.append(f"move {task_gid} {section_gid}")

    def comment(self, task_gid: str, text: str) -> None:
        self.posted.append((task_gid, text))
        self.writes.append(f"comment {task_gid}")


def test_a_task_that_passes_moves_to_complete_then_is_marked_complete() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(10),
                _comment(HANDOFF, 9),
                _comment("Landed: repo, dev, abc123, CI green", 1),
            ]
        },
    )
    result = review(board, now=NOW)
    assert board.writes == ["move 1 s-complete", "complete 1"]
    assert [t.gid for t in result.closed] == ["1"]
    assert result.rejected == []
    assert result.missing_handoff == []
    assert result.unverified == []
    assert board.posted == []


def test_a_handoff_short_of_the_done_when_is_rejected_once() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(10),
                _comment("Handed off: PR 4\n- renders on dev", 9),
                _comment("Landed: repo, dev, abc123, CI green", 1),
            ]
        },
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.rejected] == ["1"]
    assert result.closed == []
    assert board.completed == []
    assert board.writes == ["comment 1", "move 1 s-in progress"]
    ((_, text),) = board.posted
    assert text.startswith("Rejected:")
    assert "Not answered: Its test passes." in text
    assert "The widget renders" not in text


def test_a_handoff_with_no_list_answers_nothing() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(10),
                _comment("Handed off: all done", 9),
                _comment("Landed: x", 1),
            ]
        },
    )
    review(board, now=NOW)
    ((_, text),) = board.posted
    assert text.count("Not answered:") == 2


def test_an_empty_answer_does_not_count() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(10),
                _comment("Handed off:\n- \n- test passes", 9),
                _comment("Landed: x", 1),
            ]
        },
    )
    review(board, now=NOW)
    ((_, text),) = board.posted
    assert "Not answered: The widget renders." in text
    assert "Its test passes" not in text


def test_a_task_with_no_pull_request_passes_on_evidence_for_each_item() -> None:
    notes = (
        "Done when:\n- The folder is set in Doppler\n- The backfill ran\n"
        "Where: deejay-cog\nPR:\nExecutor: person"
    )
    handoff = (
        "Handed off: no PR\n"
        "1. Set by Kaiano in Doppler prd, 2026-11-04\n"
        "2. Run 1234567 on deejay-cog, https://example.com/runs/1234567"
    )
    board = FakeAsanaBoard(
        [_task(notes=notes)],
        {"1": [_entered(10), _comment(handoff, 9), _comment("Landed: run 1234567", 1)]},
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.closed] == ["1"]
    assert board.writes == ["move 1 s-complete", "complete 1"]


def test_a_task_with_no_pull_request_missing_evidence_is_rejected() -> None:
    notes = (
        "Done when:\n- The folder is set in Doppler\n- The backfill ran\n"
        "Where: deejay-cog\nPR:\nExecutor: person"
    )
    board = FakeAsanaBoard(
        [_task(notes=notes)],
        {
            "1": [
                _entered(10),
                _comment("Handed off:\n- Set in Doppler prd", 9),
                _comment("Landed: done", 1),
            ]
        },
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.rejected] == ["1"]
    assert "Not answered: The backfill ran" in board.posted[0][1]


def test_a_task_completed_by_hand_that_passes_moves_to_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Marked complete after the check shipped, so it is checked.
    monkeypatch.setenv("COMPLETE_CHECK_SINCE", "2026-10-01T00:00:00Z")
    board = FakeAsanaBoard(
        [_task(completed=True)],
        {
            "1": [
                _entered(10),
                _comment(HANDOFF, 9),
                _comment("Landed: x", 2),
                _marked_complete(_hours_ago(1)),
            ]
        },
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.closed] == ["1"]
    assert board.writes == ["move 1 s-complete"]


def test_a_task_completed_by_hand_that_fails_is_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Marked complete after the check shipped, so it is checked.
    monkeypatch.setenv("COMPLETE_CHECK_SINCE", "2026-10-01T00:00:00Z")
    board = FakeAsanaBoard(
        [_task(completed=True)],
        {"1": [_entered(10), _comment(HANDOFF, 9), _marked_complete(_hours_ago(1))]},
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.rejected] == ["1"]
    assert board.writes == ["comment 1", "reopen 1", "move 1 s-in progress"]
    assert "No Landed: comment" in board.posted[0][1]


def test_tasks_complete_before_the_check_shipped_move_to_complete_once() -> None:
    old = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    board = FakeAsanaBoard(
        [_task("1", completed=True), _task("2", completed=True, section="Complete")],
        {"1": [_entered(900), _comment("Landed: x", 899), _marked_complete(old)]},
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.moved_complete] == ["1"]
    assert board.writes == ["move 1 s-complete"]
    assert result.closed == [] and result.rejected == []


def test_the_one_time_cutoff_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLETE_CHECK_SINCE", "2026-10-30T13:00:00Z")
    board = FakeAsanaBoard(
        [_task(completed=True)],
        {"1": [_entered(10), _marked_complete(_hours_ago(1))]},
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.moved_complete] == ["1"]
    assert board.posted == []


def test_done_when_items_are_list_items_or_sentences() -> None:
    assert review_mod.done_when_items("A is done. B runs! C? d") == [
        "A is done.",
        "B runs!",
        "C? d",
    ]
    assert review_mod.done_when_items("- one\n  more of one\n* two") == [
        "one more of one",
        "two",
    ]


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


def test_an_unverified_task_is_flagged_once_with_a_comment() -> None:
    board = FakeAsanaBoard(
        [_task()], {"1": [_entered(25), _comment("Handed off: PR 4", 24.5)]}
    )
    review(board, now=NOW)
    assert [gid for gid, _ in board.posted] == ["1"]
    assert board.posted[0][1].startswith("Unverified:")


def test_a_task_already_flagged_is_counted_not_flagged_again() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(30),
                _comment("Handed off: PR 4", 29),
                _comment("Unverified: in Review for more than 24 hours", 5),
            ]
        },
    )
    result = review(board, now=NOW)
    assert result.unverified == []
    assert [t.gid for t in result.awaiting_landed] == ["1"]
    assert board.posted == [] and board.completed == []


def test_a_comment_after_the_flag_starts_it_over() -> None:
    board = FakeAsanaBoard(
        [_task()],
        {
            "1": [
                _entered(30),
                _comment("Handed off: PR 4", 29),
                _comment("Unverified: in Review for more than 24 hours", 5),
                _comment("still waiting on the deploy", 1),
            ]
        },
    )
    result = review(board, now=NOW)
    assert [t.gid for t in result.unverified] == ["1"]
    assert result.awaiting_landed == []
    assert len(board.posted) == 1


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


def test_only_tasks_in_review_are_read() -> None:
    board = FakeAsanaBoard(
        [_task("2", section="Ready"), _task("3", section="Complete", completed=True)],
        {"2": [_comment("Landed: x", 1)], "3": [_comment("Landed: x", 1)]},
    )
    result = review(board, now=NOW)
    assert board.writes == []
    assert result.missing_handoff == []
    assert result.unverified == []


@pytest.mark.parametrize("missing", ["Review", "Complete", "In progress"])
def test_a_board_without_a_column_the_check_needs_is_skipped(missing: str) -> None:
    board = FakeAsanaBoard([_task()])
    board.sections = lambda: [s for s in SECTIONS if s.name != missing]  # type: ignore[method-assign]
    assert review(board, now=NOW) is None


def test_the_complete_column_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLETE_SECTION", "Done")
    board = FakeAsanaBoard([_task()])
    assert review(board, now=NOW) is None


def test_the_label_is_the_id_and_title() -> None:
    task = _task("7")
    assert review_mod.task_label(task) == task.name


def test_a_bad_cutoff_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPLETE_CHECK_SINCE", "soon")
    assert review_mod.complete_check_since() == review_mod.DEFAULT_COMPLETE_CHECK_SINCE
