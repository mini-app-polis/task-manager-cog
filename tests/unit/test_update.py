from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from mini_app_polis.api.contract import TaskUpdateRequest
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord

from task_manager_cog.update import (
    apply_update,
    is_rotation_prefix,
    rotated_at,
    section_name,
)

EXTERNAL_ID = "ci.watcher-cog.main"
COMMENT = "CI on watcher-cog main is green again after 3 failed runs."

SECTIONS = [
    AsanaSection("s-ideas", "Ideas"),
    AsanaSection("s-ready", "Ready"),
    AsanaSection("s-progress", "In progress"),
    AsanaSection("s-review", "Review"),
    AsanaSection("s-checked", "Checked"),
]


@dataclass
class FakeBoard:
    task: AsanaTaskRecord | None
    found: str | None = "1"
    moves: list[str] = field(default_factory=list)
    comments_: list[str] = field(default_factory=list)
    looked_up: list[str] = field(default_factory=list)

    def find(self, external_id: str) -> str | None:
        self.looked_up.append(external_id)
        return self.found

    def get(self, gid: str) -> AsanaTaskRecord | None:
        return self.task

    def sections(self) -> list[AsanaSection]:
        return SECTIONS

    def move(self, gid: str, section_gid: str) -> None:
        self.moves.append(section_gid)
        assert self.task is not None
        self.task = dataclasses.replace(self.task, section_gid=section_gid)

    def comment(self, gid: str, text: str) -> None:
        self.comments_.append(text)

    def comments(self, gid: str) -> list[str]:
        return list(self.comments_)

    def url(self, gid: str) -> str:
        return f"https://board/{gid}"

    def tasks(self) -> list[AsanaTaskRecord]:
        return [self.task] if self.task is not None else []


def _board(section: str = "s-ideas", *, completed: bool = False) -> FakeBoard:
    return FakeBoard(
        AsanaTaskRecord(
            gid="1",
            name="CI-4 · watcher-cog main is failing",
            notes="",
            completed=completed,
            section_gid=section,
            section_name="",
        )
    )


def _request(section: str | None = "Review", **changes) -> TaskUpdateRequest:
    data = {"external_id": EXTERNAL_ID, "comment": COMMENT, "section": section}
    data.update(changes)
    return TaskUpdateRequest.model_validate(data)


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)
    monkeypatch.delenv("REVIEW_SECTION", raising=False)
    monkeypatch.delenv("IN_PROGRESS_SECTION", raising=False)


def _apply(request: TaskUpdateRequest, board: FakeBoard) -> None:
    apply_update(request, run_id="m-1", board_factory=lambda: board)


def test_a_recovery_moves_the_task_to_review_with_one_comment(production) -> None:
    board = _board()
    _apply(_request(), board)
    assert board.looked_up == [EXTERNAL_ID]
    assert board.moves == ["s-review"]
    assert board.comments_ == [COMMENT]
    assert board.task is not None and board.task.completed is False


def test_a_task_in_progress_gets_the_comment_and_stays(production) -> None:
    board = _board("s-progress")
    _apply(_request(), board)
    assert board.moves == []
    assert board.comments_ == [COMMENT]


def test_a_redelivered_update_changes_nothing(production) -> None:
    board = _board()
    _apply(_request(), board)
    _apply(_request(), board)
    assert board.moves == ["s-review"]
    assert board.comments_ == [COMMENT]

    held = _board("s-progress")
    _apply(_request(), held)
    _apply(_request(), held)
    assert held.moves == [] and held.comments_ == [COMMENT]


def test_a_comment_already_posted_is_not_posted_again_but_the_move_is_made(
    production,
) -> None:
    # A delivery whose comment landed and whose move failed comes back.
    board = _board()
    board.comments_.append(COMMENT + "\n")
    _apply(_request(), board)
    assert board.moves == ["s-review"]
    assert len(board.comments_) == 1


def test_a_task_not_on_the_board_is_reported_not_raised(
    production, _no_outbound_reports
) -> None:
    board = FakeBoard(None, found=None)
    _apply(_request(), board)
    assert board.moves == [] and board.comments_ == []
    ((_, kwargs),) = _no_outbound_reports
    assert "no task on the board" in str(kwargs)

    gone = FakeBoard(None)
    _apply(_request(), gone)
    assert gone.moves == [] and gone.comments_ == []


@pytest.mark.parametrize(
    ("section", "completed"), [("s-review", False), ("s-ideas", True)]
)
def test_a_task_in_review_or_done_is_left_alone(
    production, section: str, completed: bool
) -> None:
    board = _board(section, completed=completed)
    _apply(_request(), board)
    assert board.moves == [] and board.comments_ == []
    assert board.task is not None and board.task.completed is completed


def test_without_a_section_the_task_only_gets_the_comment(production) -> None:
    board = _board()
    _apply(_request(section=None), board)
    assert board.moves == [] and board.comments_ == [COMMENT]


def test_review_is_the_configured_review_column(
    production, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REVIEW_SECTION", "Checked")
    assert section_name("Review") == "Checked"
    assert section_name("Ready") == "Ready"
    board = _board()
    _apply(_request(), board)
    assert board.moves == ["s-checked"]


def test_another_column_is_found_by_name_ignoring_case(production) -> None:
    board = _board()
    _apply(_request(section="ready"), board)
    assert board.moves == ["s-ready"]


def test_an_unknown_column_still_gets_the_comment_and_is_reported(
    production, _no_outbound_reports
) -> None:
    board = _board()
    _apply(_request(section="Shipped"), board)
    assert board.moves == []
    assert board.comments_ == [COMMENT]
    ((_, kwargs),) = _no_outbound_reports
    assert "Shipped" in str(kwargs)


def test_a_failed_board_call_raises_for_redelivery(production) -> None:
    board = _board()

    def fail(gid: str, text: str) -> None:
        raise RuntimeError("asana is down")

    board.comment = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="asana is down"):
        _apply(_request(), board)
    assert board.moves == []


def test_nothing_is_written_outside_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)
    board = _board()
    _apply(_request(), board)
    assert board.looked_up == [] and board.moves == [] and board.comments_ == []


# A secret's rotation, named by key prefix.

ROT_PREFIX = "secret-rotation.prd.BREVO_API_KEY."
ROT_COMMENT = (
    "Rotated 2026-10-30T12:00:00Z; verification ok\n"
    "Rotated by hand; the new key is in Doppler."
)
CREATED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@dataclass
class RotationBoard(FakeBoard):
    """A board holding several tasks, looked up by key prefix."""

    all_tasks: list[AsanaTaskRecord] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    commented: list[tuple[str, str]] = field(default_factory=list)

    def find(self, external_id: str) -> str | None:
        raise AssertionError("a prefix is not looked up as an exact key")

    def tasks(self) -> list[AsanaTaskRecord]:
        return list(self.all_tasks)

    def move(self, gid: str, section_gid: str) -> None:
        self.moved.append((gid, section_gid))
        self.all_tasks = [
            dataclasses.replace(t, section_gid=section_gid) if t.gid == gid else t
            for t in self.all_tasks
        ]

    def comment(self, gid: str, text: str) -> None:
        self.commented.append((gid, text))

    def comments(self, gid: str) -> list[str]:
        return [text for g, text in self.commented if g == gid]


def _rot_task(
    gid: str,
    key: str,
    *,
    section: str = "s-ideas",
    completed: bool = False,
    created_at: datetime | None = CREATED,
) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=f"ROT-{gid} · rotate BREVO_API_KEY",
        notes="",
        completed=completed,
        section_gid=section,
        section_name="",
        external_id=key,
        created_at=created_at,
    )


def _rot_board(*tasks: AsanaTaskRecord) -> RotationBoard:
    return RotationBoard(None, all_tasks=list(tasks))


def _rot_request(comment: str = ROT_COMMENT, section: str | None = None):
    return _request(section=section, external_id=ROT_PREFIX, comment=comment)


def test_only_secret_rotation_keys_ending_in_a_dot_are_prefixes() -> None:
    assert is_rotation_prefix(ROT_PREFIX)
    assert not is_rotation_prefix("secret-rotation.prd.BREVO_API_KEY.2026-10")
    assert not is_rotation_prefix("ci.watcher-cog.")


def test_the_rotated_line_is_parsed_from_the_first_line_only() -> None:
    assert rotated_at(ROT_COMMENT) == datetime(2026, 10, 30, 12, tzinfo=UTC)
    assert rotated_at("Rotated 2026-10-30T12:00:00; verification unchecked") == (
        datetime(2026, 10, 30, 12, tzinfo=UTC)
    )
    assert rotated_at("Rotated 2026-10-30T12:00:00+00:00; verification failed")
    assert rotated_at("Rotated 2026-10-30T12:00:00Z; verification maybe") is None
    assert rotated_at("Rotated yesterday; verification ok") is None
    assert rotated_at("Done.\nRotated 2026-10-30T12:00:00Z; verification ok") is None
    assert rotated_at("") is None


def test_a_newer_rotation_comments_on_the_open_task_and_moves_it_to_review(
    production,
) -> None:
    board = _rot_board(
        _rot_task("1", "secret-rotation.prd.BREVO_API_KEY.never", completed=True),
        _rot_task("2", "secret-rotation.prd.BREVO_API_KEY.after-2026-10-01"),
        _rot_task("3", "secret-rotation.prd.OTHER_KEY.never"),
    )
    _apply(_rot_request(), board)
    assert board.commented == [("2", ROT_COMMENT)]
    assert board.moved == [("2", "s-review")]


def test_a_rotation_goes_to_review_whatever_column_is_named(production) -> None:
    board = _rot_board(_rot_task("2", ROT_PREFIX + "never", section="s-progress"))
    _apply(_rot_request(section="Ready"), board)
    assert board.moved == [("2", "s-review")]


def test_a_redelivered_rotation_changes_nothing(production) -> None:
    board = _rot_board(_rot_task("2", ROT_PREFIX + "never"))
    _apply(_rot_request(), board)
    _apply(_rot_request(), board)
    assert board.commented == [("2", ROT_COMMENT)]
    assert board.moved == [("2", "s-review")]


@pytest.mark.parametrize(
    ("tasks", "comment", "reason"),
    [
        ([], ROT_COMMENT, "no open rotation task"),
        (
            [_rot_task("1", ROT_PREFIX + "never", completed=True)],
            ROT_COMMENT,
            "no open rotation task",
        ),
        (
            [_rot_task("2", ROT_PREFIX + "never")],
            "Rotated the key today.",
            "rotation comment does not parse",
        ),
        (
            [_rot_task("2", ROT_PREFIX + "never")],
            "Rotated 2026-10-01T09:00:00Z; verification ok",
            "rotation not after the task",
        ),
        (
            [_rot_task("2", ROT_PREFIX + "never", created_at=None)],
            ROT_COMMENT,
            "rotation not after the task",
        ),
        (
            [_rot_task("2", ROT_PREFIX + "never", section="s-review")],
            ROT_COMMENT,
            "already in Review",
        ),
    ],
)
def test_a_rotation_that_does_not_apply_is_a_noted_no_op(
    production, _no_outbound_reports, tasks, comment: str, reason: str
) -> None:
    board = _rot_board(*tasks)
    _apply(_rot_request(comment), board)
    assert board.commented == [] and board.moved == []
    ((_, kwargs),) = _no_outbound_reports
    assert reason in str(kwargs)
    assert "no task on the board" not in str(kwargs)


def test_two_open_tasks_for_one_secret_are_left_alone_and_reported(
    production, _no_outbound_reports
) -> None:
    board = _rot_board(
        _rot_task("2", ROT_PREFIX + "never"),
        _rot_task("4", ROT_PREFIX + "failed-2026-10-20"),
    )
    _apply(_rot_request(), board)
    assert board.commented == [] and board.moved == []
    ((_, kwargs),) = _no_outbound_reports
    assert "more than one open rotation task" in str(kwargs)


def test_an_exact_rotation_key_keeps_the_exact_lookup(production) -> None:
    board = _board()
    _apply(_request(external_id=ROT_PREFIX + "never"), board)
    assert board.looked_up == [ROT_PREFIX + "never"]
    assert board.moves == ["s-review"]
