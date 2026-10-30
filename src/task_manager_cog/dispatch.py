"""Dispatching an agent for each Ready task it can take.

The sweep ends here. A task in Ready is meant for an agent: a
headless Claude Code run in GitHub Actions, in the repository the task
names, that opens a draft pull request into ``dev``. This module picks the
tasks, keeps the limits, records each run with the API, and starts it with
the agent GitHub App (github.py). The run's result comes back on this cog's
queue and is applied by results.py.

**Which tasks.** In board order, a Ready task is dispatched when:

- it is not the board's template task, nor a detected problem (the sources
  sources.py marks unswept);
- its executor is the agent: a task whose ``Executor:`` line (or, with no
  line, its tag) says cowork or person is not the agent's, and is counted
  as skipped under that executor;
- its Effort is S: larger tasks are left for a person, since they would
  mostly hit the per-task cap;
- its Where names exactly one repository, and that repository has the
  agent's caller workflow with the agent App installed;
- no run is in flight in that repository, nor dispatched to it this sweep;
- the month's spend, with every run in flight counted at the per-task cap,
  leaves room for one more run under the monthly cap.

A task passed over stays in Ready and is looked at again next sweep.

**Time.** Dispatch is the end of the sweep and gets what time the sweep
leaves. A task is not started with less than ``TIME_RESERVE_SECONDS`` left
before the run's deadline: it is passed over as out of time, so the run
stops with its report rather than being cut off between recording a run
and starting it.

**One run each, never retried.** A run is recorded with the API before it
is started, and the record is the lock on its repository. The task then
moves to In progress, and only then is the workflow started, so a task is
never dispatched twice. Nothing dispatches from In progress: a run that
fails, or could not be started, leaves its task there for a person.

**A run that never reports.** A run still in flight after
``AGENT_RUN_TIMEOUT_MINUTES`` is marked failed, its cost unknown, and its
task is told so.

**Settings.** Dispatch is off unless ``AGENT_DISPATCH=true``.
``AGENT_DRY_RUN=true`` reports what would be dispatched, and the most each
could cost, and writes nothing anywhere.

  AGENT_DISPATCH             true to dispatch; off unless set
  AGENT_DRY_RUN              true to report only
  AGENT_MODEL                the model a run uses (default: claude-sonnet-5-5)
  AGENT_TASK_CAP_USD         a run's budget limit (default: 2)
  AGENT_MONTHLY_CAP_USD      the month's limit (default: 20)
  AGENT_RUN_TIMEOUT_MINUTES  when a silent run is failed (default: 90)
  IN_PROGRESS_SECTION        where a dispatched task goes (default: In progress)
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from mini_app_polis import logger as logger_mod
from mini_app_polis.api import KaianoApiError
from mini_app_polis.api.contract import (
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentSpend,
)
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord
from mini_app_polis.deadline import remaining_seconds

from .executor import AGENT, TAGGED, executor_of_task
from .lint import parse, task_id_of, where_repos

log = logger_mod.get_logger()

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_TASK_CAP_USD = 2.0
DEFAULT_MONTHLY_CAP_USD = 20.0
DEFAULT_RUN_TIMEOUT_MINUTES = 90
DEFAULT_IN_PROGRESS_SECTION = "In progress"

#: The branch a run starts from, and its pull request's base.
BASE_BRANCH = "dev"

#: What a workflow_dispatch can carry is bounded; a task this long is for a
#: person anyway.
MAX_TASK_CHARS = 50_000

#: The least time left before the run's deadline to take one more task: its
#: caller check, record, move and start, each a request or two.
TIME_RESERVE_SECONDS = 30.0

#: Why a Ready task was passed over, as the run report counts it.
SKIP_NOT_SMALL = "effort_not_s"
SKIP_WHERE = "where_not_one_repo"
SKIP_IN_FLIGHT = "repo_busy"
SKIP_NO_CALLER = "no_agent_caller"
SKIP_MONTHLY_CAP = "monthly_cap"
SKIP_TOO_LONG = "task_too_long"
SKIP_OUT_OF_TIME = "out_of_time"
#: Followed by the tag: ``executor_cowork``, ``executor_person``.
SKIP_EXECUTOR = "executor_"

#: Each skip reason in plain words, as the run report shows it.
SKIP_LABELS: dict[str, str] = {
    SKIP_NOT_SMALL: "effort larger than S",
    SKIP_WHERE: "Where does not name exactly one repository",
    SKIP_IN_FLIGHT: "repository already has a run in progress",
    SKIP_NO_CALLER: "repository has no agent caller or App installed",
    SKIP_MONTHLY_CAP: "monthly spend limit reached",
    SKIP_TOO_LONG: "task too long to send to the agent",
    SKIP_OUT_OF_TIME: "out of time this run",
    **{f"{SKIP_EXECUTOR}{tag}": f"a {tag} task" for tag in TAGGED},
}


def skip_label(reason: str) -> str:
    """How the run report words a skip reason."""
    return SKIP_LABELS.get(reason, reason.replace("_", " "))


class AgentRuns(Protocol):
    """Where agent runs are recorded: the API, or a fake in a test."""

    def record_agent_run(self, payload: AgentRunCreate) -> AgentRun:
        """Record a run about to be started; a 409 means the repository is busy."""
        ...

    def list_agent_runs(
        self, *, status: str | None = None, repository: str | None = None
    ) -> list[AgentRun]:
        """Runs, narrowed by status and repository."""
        ...

    def agent_spend(self) -> AgentSpend:
        """This month's runs and their known cost."""
        ...

    def fail_agent_run(self, payload: AgentRunFailure) -> AgentRun:
        """Mark a dispatched run failed, its cost unknown."""
        ...


class Starter(Protocol):
    """What starts a run: the agent App, or a fake in a test."""

    def has_caller(self, repo: str) -> bool:
        """Whether ``repo`` has the agent caller and the App installed."""
        ...

    def dispatch(self, repo: str, *, ref: str, inputs: dict[str, str]) -> None:
        """Start the caller in ``repo`` on ``ref``."""
        ...


class DispatchBoard(Protocol):
    """The board, as far as dispatch writes it."""

    def move(self, task_gid: str, section_gid: str) -> None:
        """Move a task to a column."""
        ...

    def comment(self, task_gid: str, text: str) -> None:
        """Post a comment on a task."""
        ...


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() == "true"


def _number(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    return float(raw) if raw else default


def enabled() -> bool:
    """Whether to dispatch: ``AGENT_DISPATCH``."""
    return _flag("AGENT_DISPATCH")


def dry_run() -> bool:
    """Whether to report only: ``AGENT_DRY_RUN``."""
    return _flag("AGENT_DRY_RUN")


def model() -> str:
    """The model a run uses: ``AGENT_MODEL``."""
    return (os.environ.get("AGENT_MODEL") or "").strip() or DEFAULT_MODEL


def task_cap() -> float:
    """A run's budget limit, in USD: ``AGENT_TASK_CAP_USD``."""
    return _number("AGENT_TASK_CAP_USD", DEFAULT_TASK_CAP_USD)


def monthly_cap() -> float:
    """The month's limit, in USD: ``AGENT_MONTHLY_CAP_USD``."""
    return _number("AGENT_MONTHLY_CAP_USD", DEFAULT_MONTHLY_CAP_USD)


def run_timeout() -> dt.timedelta:
    """When a run that has not reported is failed: ``AGENT_RUN_TIMEOUT_MINUTES``."""
    return dt.timedelta(
        minutes=_number("AGENT_RUN_TIMEOUT_MINUTES", DEFAULT_RUN_TIMEOUT_MINUTES)
    )


def in_progress_section() -> str:
    """Where a dispatched task goes: ``IN_PROGRESS_SECTION``."""
    return (
        os.environ.get("IN_PROGRESS_SECTION") or ""
    ).strip() or DEFAULT_IN_PROGRESS_SECTION


@dataclass(frozen=True)
class Candidate:
    """A Ready task the agent may take, and the one repository it names."""

    task: AsanaTaskRecord
    repo: str


@dataclass
class DispatchResult:
    """What one dispatch pass did, or would do."""

    dispatched: list[tuple[Candidate, AgentRun]] = field(default_factory=list)
    would_dispatch: list[Candidate] = field(default_factory=list)
    not_started: list[Candidate] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    stale: list[AgentRun] = field(default_factory=list)
    spend_usd: float = 0.0

    def skip(self, reason: str) -> None:
        """Count one Ready task passed over, by reason."""
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def candidates(
    tasks: Sequence[AsanaTaskRecord],
    *,
    ready_gid: str,
    unswept_prefixes: frozenset[str],
    template_marker: str,
    result: DispatchResult,
) -> list[Candidate]:
    """The Ready tasks the agent may take, in board order.

    Counts each task passed over, by reason, on ``result``. Whether its
    repository is free or has the caller is decided later, per task.
    """
    picked = []
    for task in tasks:
        if task.completed or task.section_gid != ready_gid:
            continue
        if template_marker in task.name.lower():
            continue
        task_id = task_id_of(task.name) or ""
        if task_id.split("-", 1)[0] in unswept_prefixes:
            continue
        executor = executor_of_task(task.notes, task.tag_names)
        if executor != AGENT:
            result.skip(f"{SKIP_EXECUTOR}{executor}")
            continue
        fields = parse(task.notes).fields
        if fields.get("Effort", "").strip().upper() != "S":
            result.skip(SKIP_NOT_SMALL)
            continue
        repos = list(dict.fromkeys(where_repos(fields.get("Where", ""))))
        if len(repos) != 1:
            result.skip(SKIP_WHERE)
            continue
        if len(task.name) + len(task.notes) > MAX_TASK_CHARS:
            result.skip(SKIP_TOO_LONG)
            continue
        picked.append(Candidate(task=task, repo=repos[0]))
    return picked


def _inputs(candidate: Candidate, run: AgentRun) -> dict[str, str]:
    """What the caller workflow is started with. Every input is a string."""
    task = candidate.task
    return {
        "run_id": run.id,
        "task_id": task_id_of(task.name) or task.gid,
        "task": f"{task.name}\n\n{task.notes}",
        "model": run.model,
        "budget_usd": f"{task_cap():.2f}",
    }


def _run_comment(run: AgentRun, text: str) -> str:
    return f"Agent run {run.id}: {text}"


def fail_stale(
    runs: Sequence[AgentRun],
    *,
    store: AgentRuns,
    board: DispatchBoard,
    now: dt.datetime,
    write: bool,
) -> list[AgentRun]:
    """Fail every run in flight past the timeout. Returns those it failed.

    With ``write`` off it only says which it would fail.
    """
    timeout = run_timeout()
    stale = [r for r in runs if now - r.dispatched_at > timeout]
    if not write:
        return stale
    minutes = int(timeout.total_seconds() // 60)
    for run in stale:
        reason = f"no result after {minutes} minutes"
        store.fail_agent_run(AgentRunFailure(run_id=run.id, reason=reason))
        board.comment(
            run.task_gid,
            _run_comment(
                run,
                f"failed: {reason}, so its cost is unknown. The task stays "
                "here for a person; it is not dispatched again.",
            ),
        )
        log.info("dispatch: run %s in %s failed as stale", run.id, run.repository)
    return stale


def dispatch(
    tasks: Sequence[AsanaTaskRecord],
    sections: Sequence[AsanaSection],
    *,
    ready_gid: str,
    board: DispatchBoard,
    store: AgentRuns,
    starter: Starter,
    unswept_prefixes: frozenset[str],
    template_marker: str,
    write: bool,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    time_left: Callable[[], float | None] = remaining_seconds,
) -> DispatchResult:
    """Dispatch what can be dispatched; with ``write`` off, only report it.

    ``time_left`` is the seconds before the run's deadline, or None when
    there is none.
    """
    result = DispatchResult()
    in_progress = next(
        (s for s in sections if s.name.lower() == in_progress_section().lower()),
        None,
    )
    if in_progress is None:
        raise RuntimeError(
            f"the board has no {in_progress_section()!r} column (IN_PROGRESS_SECTION)"
        )

    in_flight = store.list_agent_runs(status="dispatched")
    result.stale = fail_stale(
        in_flight, store=store, board=board, now=now(), write=write
    )
    stale_ids = {r.id for r in result.stale}
    busy = {r.repository for r in in_flight if r.id not in stale_ids}

    spend = store.agent_spend()
    result.spend_usd = spend.cost_usd
    # A run in flight has no cost yet; count it at the most it can cost.
    committed = spend.cost_usd + task_cap() * len(busy)
    cap = monthly_cap()

    callers: dict[str, bool] = {}
    for candidate in candidates(
        tasks,
        ready_gid=ready_gid,
        unswept_prefixes=unswept_prefixes,
        template_marker=template_marker,
        result=result,
    ):
        repo = candidate.repo
        left = time_left()
        if left is not None and left < TIME_RESERVE_SECONDS:
            result.skip(SKIP_OUT_OF_TIME)
            continue
        if repo in busy:
            result.skip(SKIP_IN_FLIGHT)
            continue
        if committed + task_cap() > cap:
            result.skip(SKIP_MONTHLY_CAP)
            continue
        if repo not in callers:
            callers[repo] = starter.has_caller(repo)
        if not callers[repo]:
            result.skip(SKIP_NO_CALLER)
            continue

        busy.add(repo)
        committed += task_cap()
        if not write:
            result.would_dispatch.append(candidate)
            log.info("dispatch: would dispatch %s to %s", candidate.task.name, repo)
            continue

        task = candidate.task
        try:
            run = store.record_agent_run(
                AgentRunCreate(
                    task_gid=task.gid,
                    task_id=task_id_of(task.name),
                    repository=repo,
                    model=model(),
                )
            )
        except KaianoApiError as exc:
            if exc.status_code != 409:
                raise
            # A run the list did not show yet: the repository is busy.
            result.skip(SKIP_IN_FLIGHT)
            continue
        board.move(task.gid, in_progress.gid)
        try:
            starter.dispatch(repo, ref=BASE_BRANCH, inputs=_inputs(candidate, run))
        except Exception as exc:  # noqa: BLE001 — recorded, never retried
            reason = f"could not be started: {exc}"[:2000]
            store.fail_agent_run(AgentRunFailure(run_id=run.id, reason=reason))
            board.comment(
                task.gid,
                _run_comment(
                    run,
                    f"{reason}. The task stays here for a person; it is not "
                    "dispatched again.",
                ),
            )
            result.not_started.append(candidate)
            log.warning("dispatch: %s not started in %s: %s", task.name, repo, exc)
            continue
        result.dispatched.append((candidate, run))
        log.info("dispatch: %s dispatched to %s as run %s", task.name, repo, run.id)
    return result
