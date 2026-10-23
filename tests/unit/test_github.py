from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from task_manager_cog import github

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()

API = "https://api.github.com"
REPO = "watcher-cog"


def _decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_the_app_token_is_signed_with_the_apps_key() -> None:
    token = github.app_jwt("Iv1.abc", PEM, now=1_800_000_000)
    header, claims, signature = token.split(".")
    assert json.loads(_decode(header)) == {"alg": "RS256", "typ": "JWT"}
    body = json.loads(_decode(claims))
    assert body["iss"] == "Iv1.abc"
    assert body["iat"] == 1_800_000_000 - 60
    assert body["exp"] - body["iat"] <= 600
    KEY.public_key().verify(
        _decode(signature),
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_missing_credentials_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_APP_CLIENT_ID", raising=False)
    monkeypatch.delenv("AGENT_APP_PRIVATE_KEY", raising=False)
    with pytest.raises(github.GitHubNotConfigured, match="AGENT_APP_CLIENT_ID"):
        github.AgentApp.from_env()


def test_a_key_with_escaped_newlines_still_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_APP_CLIENT_ID", "Iv1.abc")
    monkeypatch.setenv("AGENT_APP_PRIVATE_KEY", PEM.replace("\n", "\\n"))
    app = github.AgentApp.from_env()
    assert github.app_jwt("Iv1.abc", app._private_key)


def _app() -> github.AgentApp:
    app = github.AgentApp(client_id="Iv1.abc", private_key=PEM, http=httpx.Client())
    app._sleep = lambda _seconds: None
    return app


def _installed(router: respx.MockRouter) -> respx.Route:
    router.get(f"{API}/repos/mini-app-polis/{REPO}/installation").respond(
        200, json={"id": 42}
    )
    return router.post(f"{API}/app/installations/42/access_tokens").respond(
        201, json={"token": "ghs_repo"}
    )


@respx.mock
def test_the_token_is_for_one_repository_with_actions_only() -> None:
    minted = _installed(respx.mock)
    respx.get(f"{API}/repos/mini-app-polis/{REPO}/actions/workflows/agent.yml").respond(
        200, json={"state": "active"}
    )
    assert _app().has_caller(REPO)
    asked = json.loads(minted.calls.last.request.content)
    assert asked == {"repositories": [REPO], "permissions": {"actions": "write"}}


@respx.mock
def test_a_repository_without_the_app_or_the_caller_is_not_a_target() -> None:
    respx.get(f"{API}/repos/mini-app-polis/infra/installation").respond(404)
    assert not _app().has_caller("infra")

    _installed(respx.mock)
    respx.get(f"{API}/repos/mini-app-polis/{REPO}/actions/workflows/agent.yml").respond(
        404
    )
    assert not _app().has_caller(REPO)


@respx.mock
def test_a_disabled_caller_is_not_a_target() -> None:
    _installed(respx.mock)
    respx.get(f"{API}/repos/mini-app-polis/{REPO}/actions/workflows/agent.yml").respond(
        200, json={"state": "disabled_manually"}
    )
    assert not _app().has_caller(REPO)


@respx.mock
def test_dispatch_starts_the_caller_on_the_ref_with_its_inputs() -> None:
    _installed(respx.mock)
    started = respx.post(
        f"{API}/repos/mini-app-polis/{REPO}/actions/workflows/agent.yml/dispatches"
    ).respond(204)
    _app().dispatch(REPO, ref="dev", inputs={"run_id": "r"})
    request = started.calls.last.request
    assert request.headers["Authorization"] == "Bearer ghs_repo"
    assert json.loads(request.content) == {"ref": "dev", "inputs": {"run_id": "r"}}


@respx.mock
def test_a_refused_dispatch_raises() -> None:
    _installed(respx.mock)
    respx.post(
        f"{API}/repos/mini-app-polis/{REPO}/actions/workflows/agent.yml/dispatches"
    ).respond(422, text="Unexpected inputs provided")
    with pytest.raises(github.GitHubError, match="422"):
        _app().dispatch(REPO, ref="dev", inputs={"run_id": "r"})


@respx.mock
def test_dispatch_to_a_repository_without_the_app_raises() -> None:
    respx.get(f"{API}/repos/mini-app-polis/infra/installation").respond(404)
    with pytest.raises(github.GitHubError, match="not installed"):
        _app().dispatch("infra", ref="dev", inputs={})


@respx.mock
def test_a_transient_failure_is_tried_again() -> None:
    respx.get(f"{API}/repos/mini-app-polis/{REPO}/installation").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"id": 42})]
    )
    respx.post(f"{API}/app/installations/42/access_tokens").respond(
        201, json={"token": "ghs_repo"}
    )
    respx.get(f"{API}/repos/mini-app-polis/{REPO}/actions/workflows/agent.yml").respond(
        200, json={"state": "active"}
    )
    assert _app().has_caller(REPO)


@respx.mock
def test_github_unreachable_after_every_attempt_raises() -> None:
    route = respx.get(f"{API}/repos/mini-app-polis/{REPO}/installation").mock(
        side_effect=httpx.ConnectError("down")
    )
    with pytest.raises(github.GitHubError, match="down"):
        _app().has_caller(REPO)
    assert route.call_count == github.ATTEMPTS


@respx.mock
def test_an_unexpected_answer_raises() -> None:
    respx.get(f"{API}/repos/mini-app-polis/{REPO}/installation").respond(
        200, json={"no": "id"}
    )
    with pytest.raises(github.GitHubError, match="unexpected answer"):
        _app().has_caller(REPO)
