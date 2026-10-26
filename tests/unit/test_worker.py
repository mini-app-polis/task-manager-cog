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
        (json.dumps({"type": "task.delete", "version": 1}), "message type"),
        (
            json.dumps({"type": "task.create", "version": 1, "payload": {}}),
            "not a valid task.create",
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


def test_a_sweep_message_is_a_sweep() -> None:
    body = json.dumps({"type": "task.sweep", "version": 1})
    assert worker.parse_message(body) == worker.SweepJob()
    assert worker._source_label(body) == "sweep"


def test_a_proposal_message_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[str] = []
    monkeypatch.setattr(
        worker,
        "apply_proposal",
        lambda proposal, *, run_id, board_factory: applied.append(proposal.task_gid),
    )
    body = json.dumps(
        {
            "type": "task.groom-proposal",
            "version": 1,
            "payload": {
                "task_gid": "7",
                "fingerprint": "a" * 64,
                "title": "Pin runners",
                "model": "claude-sonnet-5-5",
                "cost_usd": 0.1,
            },
        }
    )
    assert worker._source_label(body) == "grooming"
    worker.process_message(body, run_id="m-3")
    assert applied == ["7"]


def test_a_malformed_proposal_is_refused() -> None:
    body = json.dumps({"type": "task.groom-proposal", "version": 1, "payload": {}})
    with pytest.raises(
        worker.UnprocessableMessage, match="not a valid task.groom-proposal"
    ):
        worker.parse_message(body)


def test_a_sweep_message_runs_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[str] = []
    monkeypatch.setattr(
        worker, "run_sweep", lambda *, run_id, board_factory: runs.append(run_id)
    )
    worker.process_message(
        json.dumps({"type": "task.sweep", "version": 1}), run_id="m-9"
    )
    assert runs == ["m-9"]


# --- creating ----------------------------------------------------------------


def test_a_minted_source_gets_the_next_id(production: None) -> None:
    board = FakeBoard(existing_names=["VN-3 · old", "MINI-90 · manual"])
    _run(_body(), board)
    name, task, external_id = board.created[0]
    assert name == "VN-4 · Call the venue"
    assert task.tag_names == ("venue",)
    assert external_id == "voicenote.file-1"


def test_a_due_secret_rotation_gets_the_next_rot_id(production: None) -> None:
    board = FakeBoard(existing_names=["ROT-2 · old", "MINI-90 · manual"])
    _run(
        _body(
            source="secret-rotation",
            external_id="secret-rotation.prd.BREVO_API_KEY.2026-10",
            draft={"title": "Rotate BREVO_API_KEY in prd", "labels": ["person"]},
        ),
        board,
    )
    name, task, external_id = board.created[0]
    assert name == "ROT-3 · Rotate BREVO_API_KEY in prd"
    assert task.tag_names == ("person",)
    assert external_id == "secret-rotation.prd.BREVO_API_KEY.2026-10"


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


def test_the_package_loads_its_secrets_before_any_module_reads_them() -> None:
    """Sentry starts at the worker's import, so SSM must already be loaded."""
    import inspect

    import task_manager_cog

    source = inspect.getsource(task_manager_cog)
    assert "load_secrets()" in source


# --- agent results -------------------------------------------------------------


def _agent_result(**changes: Any) -> str:
    payload: dict[str, Any] = {
        "id": "0b6f7f5e-3c1a-4c8e-9a51-6f0c2f3d4e5a",
        "task_gid": "1",
        "task_id": "MINI-1",
        "repository": "watcher-cog",
        "model": "claude-sonnet-5-5",
        "status": "failed",
        "dispatched_at": "2026-10-23T02:00:00Z",
    }
    payload.update(changes)
    return json.dumps({"type": "task.agent-result", "version": 1, "payload": payload})


def test_an_agent_result_is_read_as_an_agent_run() -> None:
    run = worker.parse_message(_agent_result())
    assert run.id == "0b6f7f5e-3c1a-4c8e-9a51-6f0c2f3d4e5a"
    assert worker._source_label(_agent_result()) == "agent-result"


def test_an_unreadable_agent_result_is_refused() -> None:
    with pytest.raises(
        worker.UnprocessableMessage, match="not a valid task.agent-result"
    ):
        worker.parse_message(_agent_result(status="maybe"))


def test_an_agent_result_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[str] = []
    monkeypatch.setattr(
        worker, "apply_result", lambda run, **_: applied.append(run.task_gid)
    )
    worker.process_message(_agent_result(), run_id="m-1")
    assert applied == ["1"]


# --- updates -------------------------------------------------------------------


def _update(**changes: Any) -> str:
    payload: dict[str, Any] = {
        "external_id": "ci.watcher-cog.main",
        "comment": "CI is green again.",
        "section": "Review",
    }
    payload.update(changes)
    return json.dumps({"type": "task.update", "version": 1, "payload": payload})


def test_an_update_is_read_as_an_update_request() -> None:
    request = worker.parse_message(_update())
    assert isinstance(request, worker.TaskUpdateRequest)
    assert request.external_id == "ci.watcher-cog.main"
    assert request.section == "Review"
    assert worker._source_label(_update()) == "update"


def test_an_unreadable_update_is_refused() -> None:
    with pytest.raises(worker.UnprocessableMessage, match="not a valid task.update"):
        worker.parse_message(_update(comment=""))


def test_an_update_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[str] = []
    monkeypatch.setattr(
        worker, "apply_update", lambda request, **_: applied.append(request.comment)
    )
    worker.process_message(_update(), run_id="m-1")
    assert applied == ["CI is green again."]
