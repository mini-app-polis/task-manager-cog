"""Fakes and event builders for the integration suite. See conftest.py.

Every external service is stubbed at the HTTP boundary with respx (TEST-007):
Asana, the ecosystem registry and api-kaianolevine-com. The fakes keep state
the way the real services do, so a message delivered twice meets the board
its first delivery left behind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

ASANA_BASE = "https://app.asana.com/api/1.0"
API_BASE = "https://api.task-manager.test"
API_KEY = "task-manager-cog-test-key"
REGISTRY_URL = "https://registry.test/ecosystem.yaml"

WORKSPACE_GID = "1000"
PROJECT_GID = "2000"

#: The board's columns, in board order: name -> gid.
SECTIONS: dict[str, str] = {
    "Ideas": "3001",
    "Refining": "3002",
    "Blocked": "3003",
    "Ready": "3004",
    "In progress": "3005",
    "Review": "3006",
}

#: The column new tasks land in.
INBOX_SECTION = "Ideas"

REGISTRY_YAML = """\
services:
  - id: infra
    description: Terraform for the fleet.
  - id: task-manager-cog
    description: Puts tasks on the board.
"""

#: A task body that meets the definition of ready.
READY_NOTES = """Done when: the plan is clean
Where: infra
Constraints: None
Effort: S
PR:
Blocked by:
Executor: agent

Because the plan drifted."""


# ── SQS and Lambda ───────────────────────────────────────────────────────


def create_body(
    *,
    source: str = "voicenote",
    external_id: str = "voicenote.file-1",
    title: str = "Call the venue",
    labels: tuple[str, ...] = ("venue",),
    **payload: Any,
) -> str:
    """A ``task.create`` message as the API enqueues it."""
    request: dict[str, Any] = {
        "source": source,
        "external_id": external_id,
        "draft": {"title": title, "labels": list(labels)},
    }
    request.update(payload)
    return json.dumps({"type": "task.create", "version": 1, "payload": request})


def update_body(
    *,
    external_id: str = "ci.infra.main",
    comment: str = "CI on infra main is green again.",
    section: str | None = "Review",
) -> str:
    """A ``task.update`` message as the API enqueues it."""
    request = {"external_id": external_id, "comment": comment, "section": section}
    return json.dumps({"type": "task.update", "version": 1, "payload": request})


def sweep_body() -> str:
    """A ``task.sweep`` message as the schedule enqueues it."""
    return json.dumps({"type": "task.sweep", "version": 1})


def sqs_record(body: str, message_id: str, *, receive_count: int = 1) -> dict:
    """One record as the SQS event source mapping delivers it to Lambda."""
    return {
        "messageId": message_id,
        "receiptHandle": f"rh-{message_id}",
        "body": body,
        "attributes": {
            "ApproximateReceiveCount": str(receive_count),
            "SentTimestamp": "1759680000000",
            "SenderId": "AROAEXAMPLE:api-kaianolevine-com",
            "ApproximateFirstReceiveTimestamp": "1759680000001",
            "MessageGroupId": "board",
            "MessageDeduplicationId": f"dedup-{message_id}",
            "SequenceNumber": "18849496460467696128",
        },
        "messageAttributes": {},
        "md5OfBody": "ignored",
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:000000000000:task-manager-jobs.fifo",
        "awsRegion": "us-east-1",
    }


def sqs_event(*records: dict) -> dict:
    return {"Records": list(records)}


def failures(result: dict) -> list[str]:
    """The message ids a handler's result names for redelivery."""
    return [item["itemIdentifier"] for item in result["batchItemFailures"]]


@dataclass
class LambdaContext:
    """Enough of the Lambda context for the deadline to arm."""

    remaining_ms: int = 300_000

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_ms


# ── Asana ────────────────────────────────────────────────────────────────


@dataclass
class FakeAsana:
    """One workspace with one board, behind Asana's REST API.

    Answers every request to ``app.asana.com`` from its own state. Tasks
    keep their ``external`` gid, so the idempotency lookup sees what an
    earlier delivery created. ``fail`` queues an answer for the next
    request to a route; ``commit_then_fail`` makes a create land and then
    answer as if it had not, as a timed-out response would.
    """

    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    comments: dict[str, list[str]] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    moves: list[tuple[str, str]] = field(default_factory=list)
    auth: set[str] = field(default_factory=set)
    _faults: dict[str, list[int]] = field(default_factory=dict)
    _commit_then_fail: list[int] = field(default_factory=list)
    _next_gid: int = 5000

    # ── setup ─────────────────────────────────────────────────────────

    def add_task(
        self,
        name: str,
        *,
        notes: str = READY_NOTES,
        section: str = INBOX_SECTION,
        completed: bool = False,
        external_id: str | None = None,
    ) -> str:
        """Put a task on the board as if someone had made it there."""
        gid = self._gid()
        self.tasks[gid] = {
            "gid": gid,
            "name": name,
            "notes": notes,
            "completed": completed,
            "section_gid": SECTIONS[section],
            "tag_gids": [],
            "external": external_id,
        }
        return gid

    def fail(self, route: str, status: int, *, times: int = 1) -> None:
        """Answer the next ``times`` requests to ``route`` with ``status``."""
        self._faults.setdefault(route, []).extend([status] * times)

    def commit_then_fail(self, status: int = 504) -> None:
        """Make the next task create land on the board, then answer ``status``."""
        self._commit_then_fail.append(status)

    # ── reading the state ─────────────────────────────────────────────

    def section_of(self, gid: str) -> str:
        section_gid = self.tasks[gid]["section_gid"]
        return next(name for name, sg in SECTIONS.items() if sg == section_gid)

    def names(self) -> list[str]:
        return [task["name"] for task in self.tasks.values()]

    def tag_names_of(self, gid: str) -> list[str]:
        by_gid = {tag_gid: name for name, tag_gid in self.tags.items()}
        return [by_gid[t] for t in self.tasks[gid]["tag_gids"]]

    # ── the API ───────────────────────────────────────────────────────

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("Authorization", ""))
        path = unquote(request.url.path.removeprefix("/api/1.0"))
        method = request.method
        for pattern, route, handler in self._routes():
            match = re.fullmatch(pattern, path)
            if match is None or route.split(" ", 1)[0] != method:
                continue
            pending = self._faults.get(route)
            if pending:
                return _asana_error(pending.pop(0))
            return handler(request, **match.groupdict())
        return _asana_error(404, f"no fake for {method} {path}")

    def _routes(self) -> list[tuple[str, str, Any]]:
        return [
            (r"/tasks/external:(?P<external_id>.+)", "GET external", self._find),
            (r"/tasks/(?P<task>\d+)", "GET task", self._get_task),
            (r"/tasks/(?P<task>\d+)", "PUT task", self._update_task),
            (r"/projects/(?P<project>\d+)/tasks", "GET tasks", self._list_tasks),
            (r"/projects/(?P<project>\d+)/sections", "GET sections", self._sections),
            (r"/tags", "GET tags", self._list_tags),
            (r"/tags", "POST tags", self._create_tag),
            (r"/tasks", "POST tasks", self._create_task),
            (r"/sections/(?P<section>\d+)/addTask", "POST addTask", self._move),
            (r"/tasks/(?P<task>\d+)/stories", "GET stories", self._stories),
            (r"/tasks/(?P<task>\d+)/stories", "POST stories", self._comment),
        ]

    def _find(self, request: httpx.Request, external_id: str) -> httpx.Response:
        for task in self.tasks.values():
            if task["external"] == external_id:
                return _data({"gid": task["gid"]})
        return _asana_error(404, "Unknown object")

    def _get_task(self, request: httpx.Request, task: str) -> httpx.Response:
        if task not in self.tasks:
            return _asana_error(404, "Unknown object")
        return _data(self._record(self.tasks[task]))

    def _update_task(self, request: httpx.Request, task: str) -> httpx.Response:
        if task not in self.tasks:
            return _asana_error(404, "Unknown object")
        data = json.loads(request.content)["data"]
        if "assignee" in data:
            self.tasks[task]["assignee"] = data["assignee"]
        if "html_notes" in data:
            self.tasks[task]["notes"] = data["html_notes"]
        return _data(self._record(self.tasks[task]))

    def _list_tasks(self, request: httpx.Request, project: str) -> httpx.Response:
        assert project == PROJECT_GID, f"listed project {project}"
        return _data([self._record(task) for task in self.tasks.values()])

    def _sections(self, request: httpx.Request, project: str) -> httpx.Response:
        assert project == PROJECT_GID, f"listed sections of project {project}"
        return _data([{"gid": gid, "name": name} for name, gid in SECTIONS.items()])

    def _list_tags(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("workspace") == WORKSPACE_GID
        return _data([{"gid": gid, "name": name} for name, gid in self.tags.items()])

    def _create_tag(self, request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)["data"]
        assert data["workspace"] == WORKSPACE_GID
        gid = self._gid()
        self.tags[data["name"]] = gid
        return _data({"gid": gid, "name": data["name"]}, status=201)

    def _create_task(self, request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)["data"]
        external = (data.get("external") or {}).get("gid")
        if external and any(t["external"] == external for t in self.tasks.values()):
            # Asana refuses a second task with the same external gid.
            return _asana_error(400, "external.gid: already in use")
        if data.get("memberships"):
            (membership,) = data["memberships"]
            assert data["workspace"] == WORKSPACE_GID
            assert membership["project"] == PROJECT_GID
            section_gid = membership["section"]
        else:
            assert data["projects"] == [PROJECT_GID]
            section_gid = next(iter(SECTIONS.values()))
        gid = self._gid()
        self.tasks[gid] = {
            "gid": gid,
            "name": data["name"],
            "notes": data["html_notes"],
            "completed": False,
            "section_gid": section_gid,
            "tag_gids": list(data.get("tags") or []),
            "external": external,
        }
        self.created.append(data)
        if self._commit_then_fail:
            return _asana_error(self._commit_then_fail.pop(0), "timed out")
        return _data({"gid": gid}, status=201)

    def _move(self, request: httpx.Request, section: str) -> httpx.Response:
        task_gid = json.loads(request.content)["data"]["task"]
        assert section in SECTIONS.values(), f"moved to unknown section {section}"
        self.tasks[task_gid]["section_gid"] = section
        self.moves.append((task_gid, section))
        return _data({})

    def _stories(self, request: httpx.Request, task: str) -> httpx.Response:
        return _data(
            [
                {"resource_subtype": "comment_added", "text": text}
                for text in self.comments.get(task, [])
            ]
        )

    def _comment(self, request: httpx.Request, task: str) -> httpx.Response:
        text = json.loads(request.content)["data"]["text"]
        self.comments.setdefault(task, []).append(text)
        return _data({"resource_subtype": "comment_added", "text": text}, status=201)

    def _record(self, task: dict[str, Any]) -> dict[str, Any]:
        by_gid = {gid: name for name, gid in self.tags.items()}
        section_name = next(n for n, g in SECTIONS.items() if g == task["section_gid"])
        return {
            "gid": task["gid"],
            "name": task["name"],
            "notes": task["notes"],
            "completed": task["completed"],
            "memberships": [
                {
                    "project": {"gid": PROJECT_GID},
                    "section": {"gid": task["section_gid"], "name": section_name},
                }
            ],
            "tags": [{"gid": t, "name": by_gid.get(t, t)} for t in task["tag_gids"]],
            "assignee": {"gid": task["assignee"]} if task.get("assignee") else None,
        }

    def _gid(self) -> str:
        self._next_gid += 1
        return str(self._next_gid)


def _data(data: Any, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"data": data})


def _asana_error(status: int, message: str = "injected failure") -> httpx.Response:
    return httpx.Response(status, json={"errors": [{"message": message}]})


# ── api-kaianolevine-com ─────────────────────────────────────────────────


@dataclass
class FakeApi:
    """The grooming-record routes, keyed by task gid as the API keys them."""

    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    writes: list[dict[str, Any]] = field(default_factory=list)
    auth: set[str] = field(default_factory=set)

    @staticmethod
    def _ok(data: Any) -> httpx.Response:
        count = len(data) if isinstance(data, list) else 1
        return httpx.Response(
            200,
            json={
                "data": data,
                "meta": {"count": count, "total": count, "version": "t"},
            },
        )

    def list_groomings(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("Authorization", ""))
        return self._ok(list(self.records.values()))

    def record_grooming(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("Authorization", ""))
        body = json.loads(request.content)
        self.writes.append(body)
        stored = {
            "moved_to_ready_at": None,
            "returned_count": 0,
            "groomed_fingerprint": None,
            "task_id": None,
            "problems": [],
            **body,
            "updated_at": "2026-10-24T12:00:00Z",
        }
        self.records[body["task_gid"]] = stored
        return self._ok(stored)
