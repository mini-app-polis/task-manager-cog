"""Starting an agent run: the agent GitHub App, and only what dispatch needs.

The agent's workflow is a caller in each repository the agent may change
(``.github/workflows/<AGENT_WORKFLOW>``), so a repository without one is not
a target. Dispatch asks GitHub two things, as the agent App: whether the
repository has the caller, and to start it. Each request uses a token
minted for that one repository with only Actions access, so this cog can
start a workflow and nothing else; pushing and opening the pull request
happen in the workflow, with a token of the workflow's own.

  AGENT_APP_CLIENT_ID    the agent App's client ID
  AGENT_APP_PRIVATE_KEY  its private key, PEM
  AGENT_WORKFLOW         the caller's file name (default: agent.yml)
  GITHUB_ORG             the organisation (default: mini-app-polis)
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import BaseModel, Field, ValidationError

API = "https://api.github.com"
TIMEOUT_SECONDS = 20.0
DEFAULT_ORG = "mini-app-polis"
DEFAULT_WORKFLOW = "agent.yml"

#: The App token's lifetime GitHub allows is ten minutes; this stays under
#: it, backdated a minute for clock drift.
_JWT_SECONDS = 540

#: Requests to GitHub before one fails. A blip at GitHub would otherwise
#: leave a Ready task for the next sweep, an hour later.
ATTEMPTS = 3

#: Answers worth asking again: rate limits and GitHub's own failures.
_TRANSIENT = frozenset({429, 500, 502, 503, 504})


class Installation(BaseModel):
    """The agent App's installation on a repository, as far as it is read."""

    id: int = Field(description="The installation's id, to mint its token.")


class InstallationToken(BaseModel):
    """A token minted for one installation."""

    token: str = Field(description="The installation token, valid for an hour.")


class Workflow(BaseModel):
    """A repository's workflow, as far as it is read."""

    state: str = Field(description="active, or why the workflow is disabled.")


_M = TypeVar("_M", bound=BaseModel)


class GitHubNotConfigured(RuntimeError):
    """The agent App's credentials are not set."""


class GitHubError(RuntimeError):
    """GitHub refused or failed a request."""


def org() -> str:
    """The organisation the agent works in: ``GITHUB_ORG``."""
    return (os.environ.get("GITHUB_ORG") or "").strip() or DEFAULT_ORG


def workflow() -> str:
    """The caller's file name: ``AGENT_WORKFLOW``."""
    return (os.environ.get("AGENT_WORKFLOW") or "").strip() or DEFAULT_WORKFLOW


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def app_jwt(client_id: str, private_key_pem: str, *, now: float | None = None) -> str:
    """The App's own token (RS256), to ask for an installation token."""
    issued = int(now if now is not None else time.time()) - 60
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(
        json.dumps(
            {"iat": issued, "exp": issued + _JWT_SECONDS, "iss": client_id}
        ).encode()
    )
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubNotConfigured("AGENT_APP_PRIVATE_KEY is not an RSA key")
    signature = key.sign(
        f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{header}.{claims}.{_b64(signature)}"


class AgentApp:
    """The agent App, as far as dispatch uses it."""

    def __init__(
        self,
        *,
        client_id: str,
        private_key: str,
        http: httpx.Client | None = None,
    ) -> None:
        # no-retry: builds the client only; every request retries in _request.
        self._client_id = client_id
        self._private_key = private_key
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_env(cls) -> AgentApp:
        """The App this process is configured with. Raises GitHubNotConfigured."""
        client_id = (os.environ.get("AGENT_APP_CLIENT_ID") or "").strip()
        # A key pasted with escaped newlines still reads as PEM.
        key = (os.environ.get("AGENT_APP_PRIVATE_KEY") or "").strip()
        key = key.replace("\\n", "\n")
        missing = [
            name
            for name, value in (
                ("AGENT_APP_CLIENT_ID", client_id),
                ("AGENT_APP_PRIVATE_KEY", key),
            )
            if not value
        ]
        if missing:
            raise GitHubNotConfigured(f"{', '.join(missing)} not set")
        return cls(client_id=client_id, private_key=key)

    def _request(
        self, method: str, path: str, *, token: str, body: Any = None
    ) -> httpx.Response:
        """One request, tried again on a transient failure, up to ATTEMPTS."""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = self._http.request(
                    method,
                    f"{API}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=body,
                )
            except httpx.TransportError as exc:
                if attempt == ATTEMPTS:
                    raise GitHubError(f"{method} {path}: {exc}") from exc
                self._sleep(2.0**attempt)
                continue
            if response.status_code in _TRANSIENT and attempt < ATTEMPTS:
                self._sleep(2.0**attempt)
                continue
            return response
        raise GitHubError(f"{method} {path}: no answer")  # pragma: no cover

    @staticmethod
    def _read(model: type[_M], response: httpx.Response, what: str) -> _M:
        """The response's body as ``model``. Raises GitHubError."""
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise GitHubError(f"{what}: unexpected answer: {exc}") from exc

    def _token(self, repo: str) -> str | None:
        """A token for ``repo`` with Actions access only, or None when the
        App is not installed there."""
        jwt = app_jwt(self._client_id, self._private_key)
        found = self._request("GET", f"/repos/{org()}/{repo}/installation", token=jwt)
        if found.status_code == 404:
            return None
        if found.status_code != 200:
            raise GitHubError(f"installation for {repo}: {found.status_code}")
        installation = self._read(Installation, found, f"installation for {repo}").id
        minted = self._request(
            "POST",
            f"/app/installations/{installation}/access_tokens",
            token=jwt,
            body={"repositories": [repo], "permissions": {"actions": "write"}},
        )
        if minted.status_code != 201:
            raise GitHubError(f"token for {repo}: {minted.status_code}")
        return self._read(InstallationToken, minted, f"token for {repo}").token

    def has_caller(self, repo: str) -> bool:
        """Whether ``repo`` has the agent caller, and the App is installed there."""
        token = self._token(repo)
        if token is None:
            return False
        found = self._request(
            "GET", f"/repos/{org()}/{repo}/actions/workflows/{workflow()}", token=token
        )
        if found.status_code == 404:
            return False
        if found.status_code != 200:
            raise GitHubError(f"caller in {repo}: {found.status_code}")
        return self._read(Workflow, found, f"caller in {repo}").state == "active"

    def dispatch(self, repo: str, *, ref: str, inputs: dict[str, str]) -> None:
        """Start the caller in ``repo``. Raises GitHubError."""
        token = self._token(repo)
        if token is None:
            raise GitHubError(f"the agent App is not installed on {repo}")
        started = self._request(
            "POST",
            f"/repos/{org()}/{repo}/actions/workflows/{workflow()}/dispatches",
            token=token,
            body={"ref": ref, "inputs": inputs},
        )
        if started.status_code not in (200, 204):
            raise GitHubError(
                f"dispatch in {repo}: {started.status_code} {started.text[:200]}"
            )
