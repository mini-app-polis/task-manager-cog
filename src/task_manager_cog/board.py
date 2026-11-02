"""The board: the one place this cog reads and writes Asana.

Configuration is read from the environment when a board is built, not at
import. The worker refreshes its settings from SSM on every invocation and
builds a board after that, so a project or section changed in Doppler
applies to the next job without a cold start.

  ASANA_ACCESS_TOKEN      read by the client on every request
  ASANA_WORKSPACE_ID      for resolving tag names
  ASANA_INBOX_PROJECT_ID  the board
  ASANA_INBOX_SECTION_ID  the column new tasks land in; optional
"""

from __future__ import annotations

import os

from mini_app_polis import logger as logger_mod
from mini_app_polis.asana import (
    AsanaClient,
    AsanaSection,
    AsanaStory,
    AsanaTaskInput,
    AsanaTaskRecord,
)

from .compose import ComposedTask
from .executor import OWNER

log = logger_mod.get_logger()

TASK_URL_TEMPLATE = "https://app.asana.com/0/{project_gid}/{task_gid}"


class BoardNotConfigured(RuntimeError):
    """The board's project is not set, so no task can be placed."""


class Board:
    """One Asana project, and the column new tasks go in."""

    def __init__(
        self, client: AsanaClient, *, project_gid: str, section_gid: str | None
    ) -> None:
        self._client = client
        self.project_gid = project_gid
        self.section_gid = section_gid

    @classmethod
    def from_env(cls) -> Board:
        """The board this process is configured for, read from the environment.

        Raises :class:`BoardNotConfigured` when ``ASANA_INBOX_PROJECT_ID`` is
        unset; the section is optional.
        """
        project = (os.environ.get("ASANA_INBOX_PROJECT_ID") or "").strip()
        if not project:
            raise BoardNotConfigured(
                "ASANA_INBOX_PROJECT_ID is not set; there is no board to put "
                "the task on."
            )
        section = (os.environ.get("ASANA_INBOX_SECTION_ID") or "").strip() or None
        return cls(AsanaClient(), project_gid=project, section_gid=section)

    def find(self, external_id: str) -> str | None:
        """The task already carrying ``external_id``, completed ones included.

        Asana can miss a task here that does carry the id, so callers use
        :func:`find_or_scan` rather than this alone.
        """
        return self._client.find_task_by_external_id(external_id)

    def names(self) -> list[str]:
        """Every task name on the board, completed ones included."""
        return self._client.list_task_names(self.project_gid)

    def tag_gids(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """Resolve tag names, skipping any that fail.

        A tag is decoration on a task whose content is already right.
        Failing the job over one would retry it into the dead-letter queue
        for the sake of a label, so a failure is logged and skipped. The
        review signal survives a lost tag in the title fallback.
        """
        gids: list[str] = []
        for name in names:
            try:
                gids.append(self._client.find_or_create_tag(name))
            except Exception as exc:  # noqa: BLE001 — best effort, see above
                log.warning("board: tag %r not applied: %s", name, exc)
        return tuple(gids)

    def create(self, name: str, task: ComposedTask, *, external_id: str) -> str:
        """Create the task and return its gid.

        Every task is assigned to the owner, whoever does the work
        (executor.py).
        """
        return self._client.create_task(
            AsanaTaskInput(
                name=name,
                html_notes=task.html_notes,
                project_gid=self.project_gid,
                section_gid=self.section_gid,
                assignee=OWNER,
                due_on=task.due_on,
                tag_gids=self.tag_gids(task.tag_names),
                external_id=external_id,
            )
        )

    def sections(self) -> list[AsanaSection]:
        """The board's columns, in order."""
        return self._client.list_sections(self.project_gid)

    def tasks(self) -> list[AsanaTaskRecord]:
        """Every task on the board, with its body, column and tags."""
        return self._client.list_project_tasks(self.project_gid)

    def move(self, task_gid: str, section_gid: str) -> None:
        """Move a task to a column."""
        self._client.move_task_to_section(task_gid, section_gid)

    def get(self, task_gid: str) -> AsanaTaskRecord | None:
        """One task, or None when it has been deleted."""
        return self._client.get_task(task_gid, self.project_gid)

    def update(self, task_gid: str, *, name: str, html_notes: str) -> None:
        """Rewrite a task's name and body."""
        self._client.update_task(task_gid, name=name, html_notes=html_notes)

    def update_body(self, task_gid: str, html_notes: str) -> None:
        """Rewrite a task's body, leaving its name alone."""
        self._client.update_task(task_gid, html_notes=html_notes)

    def assign(self, task_gid: str) -> None:
        """Assign a task to the owner."""
        self._client.update_task(task_gid, assignee=OWNER)

    def tag(self, task_gid: str, tag_name: str) -> None:
        """Put a tag on a task."""
        self._client.add_tag(task_gid, tag_name)

    def untag(self, task_gid: str, tag_name: str) -> None:
        """Take a tag off a task."""
        self._client.remove_tag(task_gid, tag_name)

    def comment(self, task_gid: str, text: str) -> None:
        """Post a comment on a task."""
        self._client.add_comment(task_gid, text)

    def comments(self, task_gid: str) -> list[str]:
        """Every comment on a task, oldest first."""
        return self._client.list_comments(task_gid)

    def stories(self, task_gid: str) -> list[AsanaStory]:
        """A task's history, oldest first: comments and column changes."""
        return self._client.list_stories(task_gid)

    def complete(self, task_gid: str) -> None:
        """Mark a task complete."""
        self._client.complete_task(task_gid)

    def url(self, task_gid: str) -> str:
        """A link to the task on this board, for the run report."""
        return TASK_URL_TEMPLATE.format(project_gid=self.project_gid, task_gid=task_gid)


def find_or_scan(board: Board, external_id: str) -> str | None:
    """The task carrying ``external_id``: the direct lookup, then a scan.

    The direct lookup can answer not found for a task that does store the
    id; ids holding characters such as ``/`` and ``#``, which go into the
    request path, have been seen to. The board's task list carries
    each task's stored external id too, completed tasks included, so a
    task the lookup cannot reach is still found while it is on this
    board's project. Exact match only.
    """
    gid = board.find(external_id)
    if gid is not None:
        return gid
    for task in board.tasks():
        if task.external_id == external_id:
            return task.gid
    return None
