from __future__ import annotations

import httpx
import pytest

from task_manager_cog import registry

DOC = """services:
- id: watcher-cog
  type: trigger-cog
- id: infra
- type: no-id
patterns: {}
"""


def _client(status: int, text: str) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=text))
    )


def test_the_registry_lists_service_ids() -> None:
    assert registry.load_repos(client=_client(200, DOC)) == {"watcher-cog", "infra"}


def test_the_url_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=DOC)

    monkeypatch.setenv("ECOSYSTEM_YAML_URL", "https://registry.test/eco.yaml")
    registry.load_repos(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == ["https://registry.test/eco.yaml"]


@pytest.mark.parametrize(
    ("status", "text"),
    [(500, "down"), (200, "patterns: {}"), (200, "services: []"), (200, ": : :")],
)
def test_an_unreadable_registry_stops_the_sweep(status: int, text: str) -> None:
    with pytest.raises(registry.RegistryUnavailable):
        registry.load_repos(client=_client(status, text), sleep=lambda _: None)


def test_a_transient_failure_is_read_again() -> None:
    answers = iter([httpx.Response(503), httpx.Response(200, text=DOC)])
    client = httpx.Client(transport=httpx.MockTransport(lambda _: next(answers)))
    waits: list[float] = []
    assert registry.load_repos(client=client, sleep=waits.append) == {
        "watcher-cog",
        "infra",
    }
    assert waits == [2.0]


def test_an_unreachable_registry_is_tried_attempts_times() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("down", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(registry.RegistryUnavailable):
        registry.load_repos(client=client, sleep=lambda _: None)
    assert len(calls) == registry.ATTEMPTS


def test_a_refusal_is_not_read_again() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(registry.RegistryUnavailable):
        registry.load_repos(client=client, sleep=lambda _: None)
    assert len(calls) == 1
