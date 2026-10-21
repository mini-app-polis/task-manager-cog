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
    AsanaTaskInput,
    AsanaTaskRecord,
)

from .compose import ComposedTask

log = logger_mod.get_logger()

#: Every task this cog creates is the operator's own work.
ASSIGNEE = "me"

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
        """The task already carrying ``external_id``, completed ones included."""
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
        """Create the task and return its gid."""
        return self._client.create_task(
            AsanaTaskInput(
                name=name,
                html_notes=task.html_notes,
                project_gid=self.project_gid,
                section_gid=self.section_gid,
                assignee=ASSIGNEE,
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

    def url(self, task_gid: str) -> str:
        """A link to the task on this board, for the run report."""
        return TASK_URL_TEMPLATE.format(project_gid=self.project_gid, task_gid=task_gid)
