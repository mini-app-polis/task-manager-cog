"""The fleet's repositories, as the ecosystem registry lists them.

``Where`` on a ready task names repositories, and a name is only useful if
it is one of the fleet's. The registry is ecosystem-standards'
``ecosystem.yaml``; its ``services[].id`` are the repository names. It is
read once per sweep, so a repository registered there is accepted on the
next one.

  ECOSYSTEM_YAML_URL  where to read it; defaults to the file on main
"""

from __future__ import annotations

import os

import httpx
import yaml

DEFAULT_URL = (
    "https://raw.githubusercontent.com/mini-app-polis/ecosystem-standards/"
    "main/ecosystem.yaml"
)

TIMEOUT_SECONDS = 20.0


class RegistryUnavailable(RuntimeError):
    """The registry could not be read, so no task's Where can be judged."""


def parse_repos(text: str) -> frozenset[str]:
    """The repository names in a registry document."""
    document = yaml.safe_load(text) or {}
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, list):
        raise RegistryUnavailable("the registry has no services list")
    return frozenset(
        str(service["id"])
        for service in services
        if isinstance(service, dict) and service.get("id")
    )


def load_repos(*, client: httpx.Client | None = None) -> frozenset[str]:
    """Read the registry. Raises :class:`RegistryUnavailable` on any failure.

    A sweep that cannot read it stops rather than judging every task's
    Where against an empty list.
    """
    url = (os.environ.get("ECOSYSTEM_YAML_URL") or "").strip() or DEFAULT_URL
    try:
        http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        response = http.get(url)
        response.raise_for_status()
        repos = parse_repos(response.text)
    except RegistryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure means the same
        raise RegistryUnavailable(f"could not read {url}: {exc}") from exc
    if not repos:
        raise RegistryUnavailable(f"{url} lists no repositories")
    return repos
