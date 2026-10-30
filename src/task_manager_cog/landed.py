"""Landing: posting ``Landed:`` on a Review task whose pull request landed.

Each sweep reads the incomplete tasks in Review whose ``PR:`` line names a
pull request in the organisation
(``https://github.com/<GITHUB_ORG>/<repo>/pull/<n>``), and asks GitHub, as
the agent App with a read-only token, whether it was merged and how CI on
dev went for its merge commit. A task without such a PR line is never read
or touched, and nothing here calls a model.

- Merged, and the latest run of the CI workflow (``LANDED_CI_WORKFLOW``) on
  dev for the merge commit completed with success: a comment starting
  ``Landed:`` is posted, naming the repository, the branch, the merge
  commit and the run. The Review pass (review.py) then closes the task.
- Merged, and that run failed: no comment; listed as failed.
- Merged, and that run is still going: nothing, until a later sweep.
- Merged, and no such run, a run that ended otherwise (cancelled, skipped),
  or a pull request the App cannot read: no comment; listed as unverified.
- Not merged: nothing.

A task with a ``Landed:`` comment since it entered Review is left alone:
either it is closed this sweep, or a later comment reopened it.

  LANDED_CI_WORKFLOW  the name of the workflow whose run on dev confirms a
                      landing (default: CI)
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from mini_app_polis import logger as logger_mod
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from . import github
from .github import PullRequest, WorkflowRun
from .lint import parse
from .review import COMMENT, LANDED_PREFIX, entered_review, review_section

log = logger_mod.get_logger()

DEFAULT_CI_WORKFLOW = "CI"

#: The branch a pull request lands on, and CI is read on.
LANDED_BRANCH = "dev"

#: Conclusions of a completed run that mean CI failed.
FAILED = frozenset({"failure", "timed_out", "startup_failure"})


class LandingBoard(Protocol):
    """What the landing pass needs of the board."""

    def sections(self) -> list[AsanaSection]:
        """The board's columns."""
        ...

    def tasks(self) -> list[AsanaTaskRecord]:
        """Every task on the board."""
        ...

    def stories(self, task_gid: str) -> list[AsanaStory]:
        """A task's history, oldest first."""
        ...

    def comment(self, task_gid: str, text: str) -> None:
        """Post a comment on a task."""
        ...


class Reader(Protocol):
    """What the landing pass reads from GitHub: the agent App, or a fake."""

    def pull_request(self, repo: str, number: int) -> PullRequest | None:
        """A pull request; None when it cannot be read."""
        ...

    def workflow_runs(
        self, repo: str, *, head_sha: str, branch: str
    ) -> list[WorkflowRun] | None:
        """The runs on ``branch`` for ``head_sha``, newest first; None when
        they cannot be read."""
        ...


@dataclass(frozen=True)
class PullRequestRef:
    """A pull request named on a task's PR line."""

    repo: str
    number: int

    @property
    def label(self) -> str:
        """``task-manager-cog#12``."""
        return f"{self.repo}#{self.number}"


@dataclass
class LandingResult:
    """What one landing pass did and found."""

    #: Tasks given a ``Landed:`` comment.
    landed: list[AsanaTaskRecord] = field(default_factory=list)
    #: Merged pull requests whose CI on dev failed, with the run.
    failed: list[tuple[AsanaTaskRecord, PullRequestRef, str]] = field(
        default_factory=list
    )
    #: Merged pull requests whose CI on dev could not be confirmed, and why.
    unverified: list[tuple[AsanaTaskRecord, PullRequestRef, str]] = field(
        default_factory=list
    )
    #: Tasks whose pull request or runs could not be read, and the error.
    errors: list[tuple[AsanaTaskRecord, str]] = field(default_factory=list)


def ci_workflow() -> str:
    """The workflow confirming a landing: ``LANDED_CI_WORKFLOW``, or CI."""
    return (os.environ.get("LANDED_CI_WORKFLOW") or "").strip() or DEFAULT_CI_WORKFLOW


def pull_request_of(notes: str) -> PullRequestRef | None:
    """The organisation's pull request the task's PR line names, if any."""
    line = parse(notes).fields.get("PR", "")
    match = re.search(
        rf"https://github\.com/{re.escape(github.org())}/"
        r"(?P<repo>[A-Za-z0-9._-]+)/pull/(?P<number>\d+)",
        line,
    )
    if match is None:
        return None
    return PullRequestRef(repo=match["repo"], number=int(match["number"]))


def landed_comment(ref: PullRequestRef, pr: PullRequest, run: WorkflowRun) -> str:
    """The ``Landed:`` comment for a merged pull request with green CI."""
    return (
        f"{LANDED_PREFIX} {github.org()}/{ref.repo}, branch {LANDED_BRANCH}, "
        f"merge commit {pr.merge_commit_sha} from pull request {ref.label} "
        f"({pr.html_url}). CI on {LANDED_BRANCH} passed: {ci_workflow()} run "
        f"{run.id} ({run.html_url})."
    )


def _landed_since_entered(
    task: AsanaTaskRecord, stories: list[AsanaStory], review_gid: str
) -> bool:
    entered = entered_review(task, stories, review_gid)
    return any(
        s.kind == COMMENT
        and (s.text or "").lstrip().startswith(LANDED_PREFIX)
        and (entered is None or s.created_at is None or s.created_at >= entered)
        for s in stories
    )


def land(
    board: LandingBoard, reader_factory: Callable[[], Reader]
) -> LandingResult | None:
    """Post ``Landed:`` on each Review task whose pull request landed.

    None when the board has no Review column. ``reader_factory`` is called
    only when a task names a pull request, and raises when GitHub is not
    configured.
    """
    section = next(
        (s for s in board.sections() if s.name.lower() == review_section().lower()),
        None,
    )
    if section is None:
        return None
    result = LandingResult()
    reader: Reader | None = None
    for task in board.tasks():
        if task.completed or task.section_gid != section.gid:
            continue
        ref = pull_request_of(task.notes)
        if ref is None:
            continue
        stories = board.stories(task.gid)
        if _landed_since_entered(task, stories, section.gid):
            continue
        reader = reader or reader_factory()
        try:
            pr = reader.pull_request(ref.repo, ref.number)
            if pr is not None and not pr.merged:
                continue
            if pr is None or not pr.merge_commit_sha:
                result.unverified.append(
                    (task, ref, "the pull request could not be read")
                )
                continue
            runs = reader.workflow_runs(
                ref.repo, head_sha=pr.merge_commit_sha, branch=LANDED_BRANCH
            )
        except github.GitHubError as exc:
            log.warning("landing: %s not checked: %s", task.name, exc)
            result.errors.append((task, str(exc)))
            continue
        run = next((r for r in runs or [] if r.name == ci_workflow()), None)
        if run is None:
            result.unverified.append(
                (task, ref, f"no {ci_workflow()} run on {LANDED_BRANCH} for its merge")
            )
        elif run.status != "completed":
            log.info("landing: %s waits for CI on %s", task.name, LANDED_BRANCH)
        elif run.conclusion == "success":
            board.comment(task.gid, landed_comment(ref, pr, run))
            result.landed.append(task)
            log.info("landing: %s landed; Landed: posted", task.name)
        elif run.conclusion in FAILED:
            result.failed.append((task, ref, run.html_url))
        else:
            result.unverified.append(
                (task, ref, f"{ci_workflow()} on {LANDED_BRANCH} {run.conclusion}")
            )
    return result
