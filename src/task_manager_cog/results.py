"""Applying an agent run's result to the board.

The agent's workflow posts how its run ended to the API, which records it
and puts it on this cog's queue:

    {"type": "task.agent-result", "version": 1, "payload": <AgentRun>}

and it is applied here, in line with every other write to the board.

**Succeeded.** The run opened a draft pull request into ``dev``. Its link
goes on the task's PR line, the task moves to Review (``REVIEW_SECTION``),
and a comment says what it cost. A person reviews and merges the draft,
confirms the change in dev, and marks the task done: nothing here ever
completes a task, because the agent's report is not proof the work is done.

**Failed or capped.** The task stays in In progress, and a comment says why,
what it cost and where the run is. Nothing dispatches from In progress, so
the task waits for a person and the run is never repeated.

**Moved meanwhile.** A task a person has moved out of In progress since its
run started is left where it is, with the comment.

**Once.** Every comment names the run, so a redelivered result finds its own
comment and posts no second one, and a step already done is not redone.

  REVIEW_SECTION       default Review
  IN_PROGRESS_SECTION  default In progress
"""

from __future__ import annotations

import os
from collections.abc import Callable

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import AgentRun
from mini_app_polis.asana import AsanaTaskRecord
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import run_report

from .board import Board
from .compose import TEMPLATE_FIELDS
from .dispatch import in_progress_section
from .groom import _field_lines, groomed_html
from .lint import parse
from .sweep import _section_named

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "apply-agent-result"

DEFAULT_REVIEW_SECTION = "Review"


def review_section() -> str:
    """Where a task with a draft pull request goes: ``REVIEW_SECTION``."""
    return (os.environ.get("REVIEW_SECTION") or "").strip() or DEFAULT_REVIEW_SECTION


def marker(run: AgentRun) -> str:
    """How every comment about a run starts."""
    return f"Agent run {run.id}"


def _cost(run: AgentRun) -> str:
    return f"${run.cost_usd:.2f}" if run.cost_usd is not None else "unknown"


def result_comment(run: AgentRun, *, moved_away: bool = False) -> str:
    """The comment a run's result leaves on its task."""
    where = f"\nRun: {run.actions_run_url}" if run.actions_run_url else ""
    if run.status == "succeeded":
        text = (
            f"{marker(run)} opened a draft pull request into dev: {run.pr_url}\n"
            f"Cost: {_cost(run)}.{where}\n"
            "Review and merge it, confirm the change in dev, then mark this "
            "task done."
        )
    else:
        how = "stopped at its budget limit" if run.status == "capped" else "failed"
        text = (
            f"{marker(run)} {how}: {run.reason or 'no reason given'}\n"
            f"Cost: {_cost(run)}.{where}\n"
            "No pull request was opened. The task is left for a person; it "
            "is not dispatched again."
        )
    if moved_away:
        text += (
            "\nThe task had been moved out of In progress, so it was left where it is."
        )
    return text


def with_pr(task: AsanaTaskRecord, pr_url: str) -> tuple[str, str]:
    """The task's template block with its PR line filled, and its body."""
    current = parse(task.notes)
    block: list[str] = []
    for label in TEMPLATE_FIELDS:
        value = pr_url if label == "PR" else current.fields.get(label, "")
        first, *rest = _field_lines(value) if value else [""]
        block.append(f"{label}: {first}".rstrip())
        block.extend(rest)
    return "\n".join(block), current.body


def apply_result(
    run: AgentRun,
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
) -> None:
    """Apply one run's result to its task, once. Raises to be redelivered."""
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id) as report:
        if run.cost_usd is not None:
            report.count("cost_usd", f"{run.cost_usd:.4f}")
        report.count("outcome", run.status)

        if not effect_enabled(Effect.ASANA):
            log.info("results: board writes are off here; not applied")
            report.note("board writes off outside production")
            return

        board = (board_factory or Board.from_env)()
        task = board.get(run.task_gid)
        if task is None or task.completed:
            report.note("task gone or done", run.task_id or run.task_gid)
            return

        sections = board.sections()
        in_progress = _section_named(sections, in_progress_section())
        review = _section_named(sections, review_section())
        succeeded = run.status == "succeeded" and bool(run.pr_url)
        pr_line = parse(task.notes).fields.get("PR", "").strip()
        ours_in_review = (
            succeeded and task.section_gid == review.gid and pr_line == run.pr_url
        )
        moved_away = task.section_gid != in_progress.gid and not ours_in_review

        if succeeded and not moved_away:
            if pr_line != run.pr_url:
                block, body = with_pr(task, run.pr_url or "")
                board.update(
                    task.gid, name=task.name, html_notes=groomed_html(block, body)
                )
            if task.section_gid != review.gid:
                board.move(task.gid, review.gid)

        if not any(c.startswith(marker(run)) for c in board.comments(task.gid)):
            board.comment(task.gid, result_comment(run, moved_away=moved_away))

        label = run.task_id or task.name
        if moved_away:
            report.note("task was moved out of In progress; left where it is", label)
        elif succeeded:
            report.updated(
                "draft PR opened; task in Review", task.name, link=board.url(task.gid)
            )
        else:
            report.issue(f"agent run {run.status}", label, detail=run.reason or "")
        log.info("results: run %s (%s) applied to %s", run.id, run.status, task.name)
