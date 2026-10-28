"""Integration harness: the Lambda handler, end to end, with the world stubbed.

What runs for real: ``worker.lambda_handler`` and the shared batch loop
under it, message parsing against the shared contract, composing and
minting, the board, the sweep and the linter, the registry parser, and the
shared ``AsanaClient`` and ``KaianoApiClient`` with their request and
response handling.

What is stubbed, all at the HTTP boundary with respx (TEST-007): Asana, the
ecosystem registry and api-kaianolevine-com. Every request must match a
route; an unmatched one fails the test, so nothing here reaches a real
service. Settings come from the environment with no Parameter Store map
declared, so ``load_secrets`` has nothing to load. Run reports are caught
by the autouse fixture in tests/conftest.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import respx

from .harness import (
    API_BASE,
    API_KEY,
    INBOX_SECTION,
    PROJECT_GID,
    REGISTRY_URL,
    REGISTRY_YAML,
    SECTIONS,
    WORKSPACE_GID,
    FakeApi,
    FakeAsana,
    LambdaContext,
)

#: Settings a developer's shell or Doppler might carry that would change
#: what a run does. Each is cleared, so a test sets only what it means to.
_CLEARED = (
    "ASANA_ENABLED",
    "KAIANO_API_KEY",
    "KAIANO_API_BASE_URL_DEV",
    "SSM_PREFIX",
    "SSM_PARAMETERS",
    "SSM_OPTIONAL_PARAMETERS",
    "GROOM_SECTIONS",
    "READY_SECTION",
    "REFINING_SECTION",
    "BLOCKED_SECTION",
    "IN_PROGRESS_SECTION",
    "REVIEW_SECTION",
    "GROOM_AUTO_READY",
    "GROOM_DISPATCH",
    "GROOM_DISPATCH_LIMIT",
    "GROOM_TIMES",
    "AGENT_TIMES",
    "SWEEP_TIMEZONE",
    "GROOM_TASK_SHAPES",
    "AGENT_DISPATCH",
    "AGENT_DRY_RUN",
    "SENTRY_DSN_COGS",
)


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _CLEARED:
        monkeypatch.delenv(name, raising=False)
    # Production, so board writes are on and the client reads
    # KAIANO_API_BASE_URL, not the _DEV one.
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ASANA_ACCESS_TOKEN", "asana-test-token")
    monkeypatch.setenv("ASANA_WORKSPACE_ID", WORKSPACE_GID)
    monkeypatch.setenv("ASANA_INBOX_PROJECT_ID", PROJECT_GID)
    monkeypatch.setenv("ASANA_INBOX_SECTION_ID", SECTIONS[INBOX_SECTION])
    monkeypatch.setenv("KAIANO_API_BASE_URL", API_BASE)
    monkeypatch.setenv("TASK_MANAGER_COG_API_KEY", API_KEY)
    monkeypatch.setenv("ECOSYSTEM_YAML_URL", REGISTRY_URL)
    # No waiting out a retry backoff in a test.
    monkeypatch.setattr(
        "mini_app_polis.api.client._sleep", lambda _s: None, raising=False
    )


@pytest.fixture
def world() -> Iterator[tuple[FakeAsana, FakeApi]]:
    """Every service the handler reaches, stubbed and recorded."""
    asana, api = FakeAsana(), FakeApi()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.route(host="app.asana.com").mock(side_effect=asana)
        router.get(REGISTRY_URL).respond(200, text=REGISTRY_YAML)
        router.get(f"{API_BASE}/v1/tasks/groomings").mock(
            side_effect=api.list_groomings
        )
        router.post(f"{API_BASE}/v1/tasks/groomings").mock(
            side_effect=api.record_grooming
        )
        yield asana, api


@pytest.fixture
def handler() -> Callable[..., dict]:
    """``lambda_handler``, imported as Lambda would import it."""
    from task_manager_cog.worker import lambda_handler

    def invoke(event: dict, context: Any | None = None) -> dict:
        return lambda_handler(
            event, context if context is not None else LambdaContext()
        )

    return invoke
