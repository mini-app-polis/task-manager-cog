from __future__ import annotations

from datetime import date

import pytest

from task_manager_cog.board import Board, BoardNotConfigured
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


def test_create_places_and_tags_best_effort_and_assigns_an_agent_task_to_the_owner() -> (
    None
):
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
    assert sent.assignee == "me"
    assert sent.tag_gids == ("tag-venue",)
    assert sent.external_id == "voicenote.f"
    assert sent.due_on == date(2026, 10, 20)


@pytest.mark.parametrize("executor", ["cowork", "person"])
def test_create_assigns_a_cowork_or_person_task_to_the_owner(executor: str) -> None:
    client = FakeClient()
    board = Board(client, project_gid="proj", section_gid=None)  # type: ignore[arg-type]
    task = ComposedTask(
        title="t", html_notes="<body>x</body>", tag_names=(executor,), due_on=None
    )
    board.create("TASK-2 · t", task, external_id="x")
    assert client.created[0].assignee == "me"


def test_body_and_assignment_updates_go_through_the_client() -> None:
    calls: list[tuple[str, dict]] = []

    class UpdateClient:
        def update_task(self, task_gid: str, **kwargs) -> None:
            calls.append((task_gid, kwargs))

    board = Board(UpdateClient(), project_gid="p", section_gid=None)  # type: ignore[arg-type]
    board.update_body("t1", "<body>y</body>")
    board.assign("t1")
    assert calls == [
        ("t1", {"html_notes": "<body>y</body>"}),
        ("t1", {"assignee": "me"}),
    ]


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


class MoveClient:
    def __init__(self, sections) -> None:
        self.sections = sections
        self.moved: list[tuple[str, str]] = []
        self.records = []

    def list_sections(self, project_gid: str):
        return self.sections

    def list_project_tasks(self, project_gid: str):
        return self.records

    def move_task_to_section(self, task_gid: str, section_gid: str) -> None:
        self.moved.append((task_gid, section_gid))


def _record(gid: str, section_gid: str):
    from mini_app_polis.asana import AsanaTaskRecord

    return AsanaTaskRecord(
        gid=gid,
        name=f"MINI-{gid} · t",
        notes="",
        completed=False,
        section_gid=section_gid,
        section_name="",
    )


def _hold_sections():
    from mini_app_polis.asana import AsanaSection

    return [AsanaSection("s-ready", "Ready"), AsanaSection("s-hold", "On Hold")]


def test_a_task_on_hold_is_never_moved_out() -> None:
    client = MoveClient(_hold_sections())
    client.records = [_record("1", "s-hold")]
    board = Board(client, project_gid="p", section_gid=None)  # type: ignore[arg-type]
    board.tasks()
    board.move("1", "s-ready")
    assert client.moved == []


def test_nothing_is_moved_onto_hold() -> None:
    client = MoveClient(_hold_sections())
    client.records = [_record("1", "s-ready")]
    board = Board(client, project_gid="p", section_gid=None)  # type: ignore[arg-type]
    board.tasks()
    board.move("1", "s-hold")
    assert client.moved == []


def test_other_moves_go_through() -> None:
    client = MoveClient(_hold_sections())
    client.records = [_record("1", "s-ready")]
    board = Board(client, project_gid="p", section_gid=None)  # type: ignore[arg-type]
    board.tasks()
    board.move("1", "s-other")
    assert client.moved == [("1", "s-other")]


def test_the_on_hold_column_name_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from mini_app_polis.asana import AsanaSection

    monkeypatch.setenv("ON_HOLD_SECTION", "Parked")
    client = MoveClient([AsanaSection("s-park", "Parked")])
    client.records = [_record("1", "s-park")]
    board = Board(client, project_gid="p", section_gid=None)  # type: ignore[arg-type]
    board.tasks()
    board.move("1", "s-ready")
    assert client.moved == []
