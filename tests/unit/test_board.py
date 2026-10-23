from __future__ import annotations

from datetime import date

import pytest

from task_manager_cog.board import ASSIGNEE, Board, BoardNotConfigured
from task_manager_cog.compose import ComposedTask


class FakeClient:
    def __init__(self, failing_tags: tuple[str, ...] = ()) -> None:
        self.failing_tags = failing_tags
        self.created = []

    def find_or_create_tag(self, name: str) -> str:
        if name in self.failing_tags:
            raise RuntimeError("tag lookup failed")
        return f"tag-{name}"

    def create_task(self, task) -> str:
        self.created.append(task)
        return "gid-1"

    def find_task_by_external_id(self, external_id: str):
        return None

    def list_task_names(self, project_gid: str):
        return ["VN-1 · a"]


def test_from_env_needs_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASANA_INBOX_PROJECT_ID", raising=False)
    with pytest.raises(BoardNotConfigured):
        Board.from_env()


def test_from_env_reads_the_section_as_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASANA_INBOX_PROJECT_ID", "proj")
    monkeypatch.setenv("ASANA_INBOX_SECTION_ID", " ")
    board = Board.from_env()
    assert (board.project_gid, board.section_gid) == ("proj", None)


def test_create_places_assigns_and_tags_best_effort() -> None:
    client = FakeClient(failing_tags=("broken",))
    board = Board(client, project_gid="proj", section_gid="col")  # type: ignore[arg-type]
    task = ComposedTask(
        title="t",
        html_notes="<body>x</body>",
        tag_names=("venue", "broken"),
        due_on=date(2026, 10, 20),
    )
    assert board.create("VN-2 · t", task, external_id="voicenote.f") == "gid-1"
    sent = client.created[0]
    assert sent.name == "VN-2 · t"
    assert sent.section_gid == "col"
    assert sent.assignee == ASSIGNEE
    assert sent.tag_gids == ("tag-venue",)
    assert sent.external_id == "voicenote.f"
    assert sent.due_on == date(2026, 10, 20)


def test_reads_pass_through_and_url_names_the_task() -> None:
    board = Board(FakeClient(), project_gid="proj", section_gid=None)  # type: ignore[arg-type]
    assert board.find("x") is None
    assert board.names() == ["VN-1 · a"]
    assert board.url("gid-9") == "https://app.asana.com/0/proj/gid-9"


def test_tag_and_untag_go_through_the_client_by_name() -> None:
    calls: list[tuple[str, str, str]] = []

    class TagClient:
        def add_tag(self, task_gid: str, tag_name: str) -> None:
            calls.append(("add", task_gid, tag_name))

        def remove_tag(self, task_gid: str, tag_name: str) -> None:
            calls.append(("remove", task_gid, tag_name))

    board = Board(TagClient(), project_gid="p", section_gid=None)  # type: ignore[arg-type]
    board.tag("t1", "cowork")
    board.untag("t1", "review")
    assert calls == [("add", "t1", "cowork"), ("remove", "t1", "review")]
