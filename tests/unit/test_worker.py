from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from task_manager_cog import worker
from task_manager_cog.compose import ComposedTask


@dataclass
class FakeBoard:
    """The board, in memory: names, external ids and what was created."""

    existing_names: list[str] = field(default_factory=list)
    by_external_id: dict[str, str] = field(default_factory=dict)
    created: list[tuple[str, ComposedTask, str]] = field(default_factory=list)
    fail_create: bool = False

    def find(self, external_id: str) -> str | None:
        return self.by_external_id.get(external_id)

    def names(self) -> list[str]:
        return list(self.existing_names)

    def create(self, name: str, task: ComposedTask, *, external_id: str) -> str:
        if self.fail_create:
            raise RuntimeError("asana is down")
        gid = f"gid-{len(self.created) + 1}"
        self.created.append((name, task, external_id))
        self.existing_names.append(name)
        self.by_external_id[external_id] = gid
        return gid

    def url(self, gid: str) -> str:
        return f"https://board/{gid}"


def _body(**payload_overrides: Any) -> str:
    payload: dict[str, Any] = {
        "source": "voicenote",
        "external_id": "voicenote.file-1",
        "draft": {"title": "Call the venue", "labels": ["venue"]},
    }
    payload.update(payload_overrides)
    return json.dumps({"type": "task.create", "version": 1, "payload": payload})


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)


def _run(body: str, board: FakeBoard) -> None:
    worker.process_message(body, run_id="m-1", board_factory=lambda: board)


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "body, reason",
    [
        ("not json", "not JSON"),
        ("[1]", "not an object"),
        (json.dumps({"type": "task.create", "version": 2}), "version"),
        (json.dumps({"type": "task.update", "version": 1}), "message type"),
        (
            json.dumps({"type": "task.create", "version": 1, "payload": {}}),
            "not a task request",
        ),
    ],
)
def test_unreadable_messages_are_refused(body: str, reason: str) -> None:
    with pytest.raises(worker.UnprocessableMessage, match=reason):
        worker.parse_message(body)


def test_a_valid_message_parses_to_the_contract_model() -> None:
    request = worker.parse_message(_body())
    assert request.source == "voicenote"
    assert request.draft.title == "Call the venue"


# --- creating ----------------------------------------------------------------


def test_a_minted_source_gets_the_next_id(production: None) -> None:
    board = FakeBoard(existing_names=["VN-3 · old", "MINI-90 · manual"])
    _run(_body(), board)
    name, task, external_id = board.created[0]
    assert name == "VN-4 · Call the venue"
    assert task.tag_names == ("venue",)
    assert external_id == "voicenote.file-1"


def test_a_given_id_source_keeps_its_id_and_mints_nothing(production: None) -> None:
    board = FakeBoard(existing_names=["VN-3 · old"])
    _run(
        _body(
            source="github-issue",
            external_id="github-issue.mini-app-polis/infra#12",
            id="infra#12",
            draft={"title": "Plan fails"},
        ),
        board,
    )
    assert board.created[0][0] == "infra#12 · Plan fails"


def test_asking_twice_makes_one_task(production: None) -> None:
    board = FakeBoard()
    _run(_body(), board)
    _run(_body(), board)
    assert len(board.created) == 1


def test_ids_keep_counting_across_jobs(production: None) -> None:
    board = FakeBoard()
    _run(_body(), board)
    _run(_body(external_id="voicenote.file-2"), board)
    assert [c[0] for c in board.created] == [
        "VN-1 · Call the venue",
        "VN-2 · Call the venue",
    ]


def test_a_found_task_is_reported_without_reading_the_board(
    production: None,
) -> None:
    class NoNames(FakeBoard):
        def names(self) -> list[str]:  # pragma: no cover — must not be reached
            raise AssertionError("minted for a task that already exists")

    board = NoNames(by_external_id={"voicenote.file-1": "gid-old"})
    _run(_body(), board)
    assert board.created == []


def test_outside_production_nothing_touches_the_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)

    def no_board() -> FakeBoard:  # pragma: no cover — must not be reached
        raise AssertionError("built a board outside production")

    worker.process_message(_body(), run_id="m-1", board_factory=no_board)


def test_a_failed_create_raises_for_redelivery(production: None) -> None:
    board = FakeBoard(fail_create=True)
    with pytest.raises(RuntimeError, match="asana is down"):
        _run(_body(), board)


def test_the_run_report_names_the_created_task(
    production: None, _no_outbound_reports: list[tuple]
) -> None:
    _run(_body(), FakeBoard())
    (args, kwargs) = _no_outbound_reports[-1]
    assert "VN-1 · Call the venue" in kwargs["text"]
    assert kwargs["run_id"] == "m-1"


# --- the handler ---------------------------------------------------------------


@pytest.fixture
def no_ssm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "load_secrets", lambda refresh: [])


def _event(*bodies: str) -> dict:
    return {
        "Records": [
            {
                "messageId": f"m-{i}",
                "body": body,
                "attributes": {"ApproximateReceiveCount": "1"},
            }
            for i, body in enumerate(bodies, start=1)
        ]
    }


def test_handler_names_only_the_records_that_failed(
    monkeypatch: pytest.MonkeyPatch, no_ssm: None, production: None
) -> None:
    board = FakeBoard()
    monkeypatch.setattr(worker.Board, "from_env", classmethod(lambda cls: board))
    result = worker.lambda_handler(_event(_body(), "not json"), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m-2"}]}
    assert len(board.created) == 1


def test_handler_reports_an_unreadable_message_once(
    no_ssm: None, _no_outbound_reports: list[tuple]
) -> None:
    worker.lambda_handler(_event("not json"), None)
    assert len(_no_outbound_reports) == 1
    (args, kwargs) = _no_outbound_reports[0]
    assert kwargs["source"] == "queue_consumer"


def test_handler_returns_everything_when_settings_cannot_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(refresh: bool) -> list[str]:
        raise RuntimeError("ssm down")

    monkeypatch.setattr(worker, "load_secrets", boom)
    result = worker.lambda_handler(_event(_body(), _body()), None)
    assert result == {
        "batchItemFailures": [{"itemIdentifier": "m-1"}, {"itemIdentifier": "m-2"}]
    }
