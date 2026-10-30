from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pytest
from mini_app_polis.asana import AsanaSection, AsanaStory, AsanaTaskRecord

from task_manager_cog import landed
from task_manager_cog.github import GitHubError, PullRequest, WorkflowRun
from task_manager_cog.landed import PullRequestRef, land, pull_request_of
from task_manager_cog.sweep import run_sweep
from tests.unit.test_sweep import REPOS, FakeBoard, FakeRecords

NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)
SECTIONS = [
    AsanaSection("s-progress", "In progress"),
    AsanaSection("s-review", "Review"),
]
PR_URL = "https://github.com/mini-app-polis/infra/pull/7"
SHA = "a" * 40


def _notes(pr: str = PR_URL) -> str:
    return f"""Done when: the plan is clean
Where: infra
Constraints: None
Effort: S
PR: {pr}
Blocked by:
Executor: agent

Because the plan drifted."""


def _task(
    gid: str = "1", *, notes: str | None = None, section: str = "Review"
) -> AsanaTaskRecord:
    return AsanaTaskRecord(
        gid=gid,
        name=f"MINI-{gid} · Title {gid}",
        notes=_notes() if notes is None else notes,
        completed=False,
        section_gid=f"s-{section.lower().replace(' ', '')}",
        section_name=section,
        created_at=NOW - dt.timedelta(hours=10),
    )


def _comment(text: str, hours_ago: float) -> AsanaStory:
    return AsanaStory(
        gid=f"c-{text}-{hours_ago}",
        kind="comment_added",
        created_at=NOW - dt.timedelta(hours=hours_ago),
        text=text,
    )


@dataclass
class FakeLandingBoard:
    records: list[AsanaTaskRecord]
    history: dict[str, list[AsanaStory]] = field(default_factory=dict)
    posted: list[tuple[str, str]] = field(default_factory=list)

    def sections(self) -> list[AsanaSection]:
        return SECTIONS

    def tasks(self) -> list[AsanaTaskRecord]:
        return self.records

    def stories(self, task_gid: str) -> list[AsanaStory]:
        return self.history.get(task_gid, [])

    def comment(self, task_gid: str, text: str) -> None:
        self.posted.append((task_gid, text))


def _run(conclusion: str | None, *, status: str = "completed", name: str = "CI"):
    return WorkflowRun(
        id=99,
        name=name,
        status=status,
        conclusion=conclusion,
        html_url="https://github.com/mini-app-polis/infra/actions/runs/99",
    )


@dataclass
class FakeReader:
    pr: PullRequest | None = field(
        default_factory=lambda: PullRequest(
            html_url=PR_URL, merged=True, merge_commit_sha=SHA
        )
    )
    runs: list[WorkflowRun] | None = field(default_factory=lambda: [_run("success")])
    asked: list[tuple] = field(default_factory=list)

    def pull_request(self, repo: str, number: int) -> PullRequest | None:
        self.asked.append(("pr", repo, number))
        return self.pr

    def workflow_runs(
        self, repo: str, *, head_sha: str, branch: str
    ) -> list[WorkflowRun] | None:
        self.asked.append(("runs", repo, head_sha, branch))
        return self.runs


def _never() -> FakeReader:
    raise AssertionError("GitHub is not read")


def test_merged_with_green_ci_on_dev_posts_landed() -> None:
    board, reader = FakeLandingBoard([_task()]), FakeReader()
    result = land(board, lambda: reader)
    assert result is not None and [t.gid for t in result.landed] == ["1"]
    ((gid, text),) = board.posted
    assert gid == "1"
    assert text.startswith("Landed:")
    for part in ("mini-app-polis/infra", "branch dev", SHA, "infra#7", "run 99"):
        assert part in text
    assert reader.asked == [("pr", "infra", 7), ("runs", "infra", SHA, "dev")]


def test_merged_with_red_ci_on_dev_posts_nothing_and_is_failed() -> None:
    board = FakeLandingBoard([_task()])
    result = land(board, lambda: FakeReader(runs=[_run("failure")]))
    assert result is not None and board.posted == []
    ((task, ref, run_url),) = result.failed
    assert task.gid == "1" and ref == PullRequestRef("infra", 7)
    assert run_url.endswith("/runs/99")


def test_not_merged_is_nothing() -> None:
    board = FakeLandingBoard([_task()])
    reader = FakeReader(pr=PullRequest(html_url=PR_URL, merged=False))
    result = land(board, lambda: reader)
    assert result is not None
    assert board.posted == [] and not result.failed and not result.unverified
    assert reader.asked == [("pr", "infra", 7)]


@pytest.mark.parametrize(
    "pr", ["", "https://github.com/someone-else/infra/pull/7", "draft coming"]
)
def test_a_task_without_the_orgs_pull_request_is_never_touched(pr: str) -> None:
    board = FakeLandingBoard([_task(notes=_notes(pr))])
    result = land(board, _never)
    assert result is not None and board.posted == []


def test_a_task_with_no_template_is_never_touched() -> None:
    board = FakeLandingBoard([_task(notes="just a note")])
    assert land(board, _never) is not None and board.posted == []


def test_already_landed_is_not_posted_again() -> None:
    board = FakeLandingBoard(
        [_task()], {"1": [_comment("Handed off: PR 7", 5), _comment("Landed: x", 1)]}
    )
    land(board, _never)
    assert board.posted == []


def test_a_comment_after_landed_means_reopened_and_nothing_is_posted() -> None:
    board = FakeLandingBoard(
        [_task()], {"1": [_comment("Landed: x", 3), _comment("not yet", 1)]}
    )
    land(board, _never)
    assert board.posted == []


def test_a_landed_before_it_entered_review_does_not_hold_it_back() -> None:
    moved_in = AsanaStory(
        gid="m",
        kind="section_changed",
        created_at=NOW - dt.timedelta(hours=2),
        section_gid="s-review",
        section_name="Review",
    )
    board = FakeLandingBoard([_task()], {"1": [_comment("Landed: x", 5), moved_in]})
    land(board, FakeReader)
    assert len(board.posted) == 1


def test_only_incomplete_review_tasks_are_read() -> None:
    board = FakeLandingBoard([_task(section="In progress")])
    assert land(board, _never) is not None and board.posted == []


def test_ci_still_running_waits() -> None:
    board = FakeLandingBoard([_task()])
    result = land(board, lambda: FakeReader(runs=[_run(None, status="in_progress")]))
    assert result is not None
    assert board.posted == [] and not result.failed and not result.unverified


@pytest.mark.parametrize(
    "runs",
    [[], None, [_run("success", name="security")], [_run("cancelled")]],
)
def test_no_ci_run_to_confirm_is_unverified(runs: list[WorkflowRun] | None) -> None:
    board = FakeLandingBoard([_task()])
    result = land(board, lambda: FakeReader(runs=runs))
    assert result is not None and board.posted == []
    assert [t.gid for t, _, _ in result.unverified] == ["1"]


def test_an_unreadable_pull_request_is_unverified() -> None:
    board = FakeLandingBoard([_task()])
    result = land(board, lambda: FakeReader(pr=None))
    assert result is not None and board.posted == [] and len(result.unverified) == 1


def test_the_ci_workflow_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANDED_CI_WORKFLOW", "Build")
    board = FakeLandingBoard([_task()])
    land(board, lambda: FakeReader(runs=[_run("success", name="Build")]))
    assert len(board.posted) == 1
    monkeypatch.delenv("LANDED_CI_WORKFLOW")
    assert landed.ci_workflow() == "CI"


def test_a_github_error_is_kept_and_the_rest_go_on() -> None:
    class Failing(FakeReader):
        def pull_request(self, repo: str, number: int) -> PullRequest | None:
            if number == 7:
                raise GitHubError("pull request infra#7: 500")
            return super().pull_request(repo, number)

    other = _task("2", notes=_notes(PR_URL.replace("/7", "/8")))
    board = FakeLandingBoard([_task(), other])
    result = land(board, Failing)
    assert result is not None
    assert [t.gid for t, _ in result.errors] == ["1"]
    assert [gid for gid, _ in board.posted] == ["2"]


def test_no_review_column_skips_the_pass() -> None:
    class NoReview(FakeLandingBoard):
        def sections(self) -> list[AsanaSection]:
            return SECTIONS[:1]

    assert land(NoReview([_task()]), _never) is None


def test_the_pr_line_is_read_from_the_template() -> None:
    assert pull_request_of(_notes()) == PullRequestRef("infra", 7)
    assert pull_request_of(_notes(f"{PR_URL} (draft)")) == PullRequestRef("infra", 7)
    assert pull_request_of(_notes("")) is None


@dataclass
class ReviewingBoard(FakeBoard):
    """The sweep's fake board, with task histories built from its comments."""

    completed: list[str] = field(default_factory=list)

    def stories(self, task_gid: str) -> list[AsanaStory]:
        return [
            AsanaStory(gid=f"c{i}", kind="comment_added", created_at=None, text=text)
            for i, text in enumerate(self.comments_.get(task_gid, []))
        ]

    def complete(self, task_gid: str) -> None:
        self.completed.append(task_gid)


def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)


def test_the_sweep_posts_landed_and_closes_the_task_in_one_sweep(
    monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    _production(monkeypatch)
    board = ReviewingBoard([_task()])
    board.sections_.append(AsanaSection("s-review", "Review"))
    board.comments_["1"] = ["Handed off: PR 7"]
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=FakeRecords,
        reader_factory=FakeReader,
    )
    assert board.comments_["1"][-1].startswith("Landed:")
    assert board.completed == ["1"]
    ((_, kwargs),) = _no_outbound_reports
    assert kwargs["review_landed"] == 1 and kwargs["review_closed"] == 1


def test_the_sweep_names_a_merged_pull_request_with_red_ci(
    monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    _production(monkeypatch)
    board = ReviewingBoard([_task()])
    board.sections_.append(AsanaSection("s-review", "Review"))
    board.comments_["1"] = ["Handed off: PR 7"]
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=FakeRecords,
        reader_factory=lambda: FakeReader(runs=[_run("failure")]),
    )
    assert board.comments_["1"] == ["Handed off: PR 7"] and board.completed == []
    ((_, kwargs),) = _no_outbound_reports
    assert "merged, CI on dev failed" in kwargs["text"]
    assert "infra#7" in kwargs["text"]


def test_the_sweep_reports_github_not_configured(
    monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    _production(monkeypatch)

    def unconfigured() -> FakeReader:
        raise RuntimeError("AGENT_APP_CLIENT_ID not set")

    board = ReviewingBoard([_task()])
    board.sections_.append(AsanaSection("s-review", "Review"))
    board.comments_["1"] = ["Handed off: PR 7"]
    run_sweep(
        run_id="r-1",
        board_factory=lambda: board,
        repos_loader=lambda: REPOS,
        records_factory=FakeRecords,
        reader_factory=unconfigured,
    )
    ((_, kwargs),) = _no_outbound_reports
    assert "landings not checked" in kwargs["text"]
