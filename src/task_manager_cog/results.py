"""Applying an agent run's result to the board.

The agent's workflow posts how its run ended to the API, which records it
and puts it on this cog's queue:

    {"type": "task.agent-result", "version": 1, "payload": <AgentRun>}

and it is applied here, in line with every other write to the board.

**Succeeded.** The run opened a draft pull request into ``dev``. Its link
goes on the task's PR line, the task moves to Review (``REVIEW_SECTION``),
and a comment starting ``Handed off:`` names the repository and the pull
request and says what it cost. A person reviews and merges the draft, and
the task closes when a ``Landed:`` comment confirms green CI on dev: nothing
here ever completes a task, because the agent's report is not proof the
work is done.

**Failed or capped.** The task stays in In progress, and a comment says why,
what it cost and where the run is. Nothing dispatches from In progress, so
the task waits for a person and the run is never repeated.

**Needs grooming.** The run found the task unclear or undetermined, changed
nothing and asked its questions. The task goes back to Refining
(``REFINING_SECTION``) with the questions as one comment starting
``Grooming questions:``, and its grooming record says it has questions. It
is not a retry: the task reaches Ready again only through grooming, once a
grooming that reads the owner's answers comes back with none. The record
keeps when the task was moved to Ready, so the next sweep counts it as sent
back from Ready: the task was not as ready as it looked.

**Moved meanwhile.** A task a person has moved out of In progress since its
run started is left where it is, with the comment.

**Once.** Every comment names the run, so a redelivered result finds its own
comment and posts no second one, and a step already done is not redone. A
run that needs grooming posts its comment last, so a redelivery after it
changes nothing.

  REVIEW_SECTION       default Review
  IN_PROGRESS_SECTION  default In progress
  REFINING_SECTION     default Refining
"""

from __future__ import annotations

import os
from collections.abc import Callable

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import AgentRun, GroomingRecordWrite
from mini_app_polis.asana import AsanaTaskRecord
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import RunReport, run_report

from ._version import __version__
from .board import Board
from .compose import TEMPLATE_FIELDS
from .dispatch import in_progress_section
from .groom import (
    DEFAULT_REFINING_SECTION,
    _field_lines,
    _section_setting,
    groomed_html,
    questions_comment,
)
from .lint import parse, task_id_of
from .records import fingerprint
from .sweep import OPEN_QUESTIONS, Records, _api, _section_named

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "apply-agent-result"

DEFAULT_REVIEW_SECTION = "Review"

#: Starts the comment that hands a task's work off into Review.
HANDED_OFF_PREFIX = "Handed off:"


def review_section() -> str:
    """Where a task with a draft pull request goes: ``REVIEW_SECTION``."""
    return (os.environ.get("REVIEW_SECTION") or "").strip() or DEFAULT_REVIEW_SECTION


def marker(run: AgentRun) -> str:
    """How every comment about a run starts."""
    return f"Agent run {run.id}"


def _cost(run: AgentRun) -> str:
    return f"${run.cost_usd:.2f}" if run.cost_usd is not None else "unknown"


def _ours(run: AgentRun, comments: list[str]) -> bool:
    """Whether a comment about this run is already on the task."""
    return any(marker(run) in c for c in comments)


def grooming_comment(run: AgentRun, *, moved_away: bool = False) -> str:
    """The comment a run that needs grooming leaves: its questions, first."""
    where = f" ({run.actions_run_url})" if run.actions_run_url else ""
    text = (
        f"{questions_comment(run.questions)}\n\n"
        f"Asked by {marker(run)}{where}, which changed nothing. "
        f"Cost: {_cost(run)}. The task is back in grooming, not retried."
    )
    if moved_away:
        text += (
            "\nThe task had been moved out of In progress, so it was left where it is."
        )
    return text


def result_comment(run: AgentRun, *, moved_away: bool = False) -> str:
    """The comment a run's result leaves on its task.

    A succeeded run's starts with ``Handed off:``.
    """
    where = f"\nRun: {run.actions_run_url}" if run.actions_run_url else ""
    if run.status == "succeeded":
        text = (
            f"{HANDED_OFF_PREFIX} {marker(run)} opened a draft pull request into "
            f"dev in {run.repository}: {run.pr_url}\n"
            "The files and commit message are in the pull request; it is not "
            f"merged yet.\nCost: {_cost(run)}.{where}\n"
            "Review and merge it. Whoever confirms green CI on dev posts a "
            'comment starting "Landed:" with the repo, branch, commit and CI '
            "result, and the sweep marks this task done."
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
    records_factory: Callable[[], Records] = _api,
) -> None:
    """Apply one run's result to its task, once. Raises to be redelivered."""
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id, version=__version__) as report:
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
        if run.status == "needs_grooming":
            _back_to_grooming(run, task, board, records_factory, report)
            return

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

        if not _ours(run, board.comments(task.gid)):
            board.comment(task.gid, result_comment(run, moved_away=moved_away))

        label = run.task_id or task.name
        if moved_away:
            report.note("task was moved out of In progress; left where it is", label)
        elif succeeded:
            report.updated(
                "draft PR opened; task in Review", task.name, link=run.pr_url
            )
        else:
            report.issue(f"agent run {run.status}", label, detail=run.reason or "")
        log.info("results: run %s (%s) applied to %s", run.id, run.status, task.name)


def _back_to_grooming(
    run: AgentRun,
    task: AsanaTaskRecord,
    board: Board,
    records_factory: Callable[[], Records],
    report: RunReport,
) -> None:
    """Send a task whose run needs grooming back to Refining, once."""
    label = run.task_id or task.name
    if _ours(run, board.comments(task.gid)):
        report.note("result already applied", label)
        return

    store = records_factory()
    previous = {r.task_gid: r for r in store.list_groomings()}.get(task.gid)
    current = fingerprint(task)
    # Groomed as it reads now: only the owner's answers send it again.
    store.record_grooming(
        GroomingRecordWrite(
            task_gid=task.gid,
            task_id=task_id_of(task.name),
            fingerprint=current,
            outcome="questions",
            problems=[OPEN_QUESTIONS],
            moved_to_ready_at=previous.moved_to_ready_at if previous else None,
            returned_count=previous.returned_count if previous else 0,
            groomed_fingerprint=current,
        )
    )

    sections = board.sections()
    in_progress = _section_named(sections, in_progress_section())
    moved_away = task.section_gid != in_progress.gid
    if not moved_away:
        refining = _section_named(
            sections, _section_setting("REFINING_SECTION", DEFAULT_REFINING_SECTION)
        )
        board.move(task.gid, refining.gid)
    board.comment(task.gid, grooming_comment(run, moved_away=moved_away))

    report.count("questions", len(run.questions))
    if moved_away:
        report.note("task was moved out of In progress; left where it is", label)
    else:
        report.updated(
            "task sent back to grooming", task.name, link=board.url(task.gid)
        )
    log.info("results: run %s needs grooming; %s back to Refining", run.id, task.name)
