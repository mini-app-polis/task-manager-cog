from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import pytest
from mini_app_polis.api.contract import TaskDraft
from mini_app_polis.asana import AsanaTaskRecord

from task_manager_cog import executor
from task_manager_cog.compose import compose
from task_manager_cog.executor import (
    executor_of_task,
    keep_in_step,
    line_value,
    plan,
    with_line,
    without_line,
)
from task_manager_cog.lint import lint
from task_manager_cog.records import fingerprint

BLOCK = """Done when: the plan is clean
Where: infra
Constraints: None
Effort: S
PR:
Blocked by: None"""

BODY = "Because the plan drifted."


def _task(
    *,
    line: str | None = None,
    tags: tuple[str, ...] = (),
    assignee: str | None = None,
    completed: bool = False,
    gid: str = "1",
) -> AsanaTaskRecord:
    block = BLOCK if line is None else f"{BLOCK}\nExecutor: {line}"
    notes = f"{block}\n\n{BODY}"
    return AsanaTaskRecord(
        gid=gid,
        name=f"TASK-{gid} · plan",
        notes=notes,
        completed=completed,
        section_gid="s-ready",
        section_name="Ready",
        tag_names=tags,
        assignee_gid=assignee,
        html_notes=f"<body>{block}\n\n<strong>Why</strong>\n{BODY}</body>",
    )


@dataclass
class FakeBoard:
    calls: list[tuple] = field(default_factory=list)

    def update_body(self, task_gid: str, html_notes: str) -> None:
        self.calls.append(("body", task_gid, html_notes))

    def tag(self, task_gid: str, tag_name: str) -> None:
        self.calls.append(("tag", task_gid, tag_name))

    def untag(self, task_gid: str, tag_name: str) -> None:
        self.calls.append(("untag", task_gid, tag_name))

    def assign(self, task_gid: str) -> None:
        self.calls.append(("assign", task_gid))

    def unassign(self, task_gid: str) -> None:
        self.calls.append(("unassign", task_gid))


# ── reading the executor ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "tags", "expected"),
    [
        ("cowork", (), "cowork"),
        ("Person", ("cowork",), "person"),  # the line wins over the tag
        ("agent", ("person",), "agent"),
        (None, ("Cowork",), "cowork"),  # no line: the tag
        (None, (), "agent"),  # neither: the agent
        ("robot", ("person",), "person"),  # a line naming no executor
    ],
)
def test_executor_of_task(line, tags, expected) -> None:
    task = _task(line=line, tags=tags)
    assert executor_of_task(task.notes, task.tag_names) == expected


def test_line_value_tells_a_missing_line_from_a_bad_one() -> None:
    assert line_value(_task().notes) is None
    assert line_value(_task(line="robot").notes) == "robot"
    assert line_value(_task(line="").notes) == ""


# ── writing the line ──────────────────────────────────────────────────────


def test_a_missing_line_goes_after_blocked_by_and_keeps_the_formatting() -> None:
    body = with_line(_task().html_notes, "cowork")
    assert body == (
        f"<body>{BLOCK}\nExecutor: cowork\n\n<strong>Why</strong>\n{BODY}</body>"
    )


def test_a_line_goes_after_a_blocked_by_that_runs_on() -> None:
    html_notes = "<body>Done when: x\nBlocked by: TASK-1,\nTASK-2\n\nBody</body>"
    assert with_line(html_notes, "person") == (
        "<body>Done when: x\nBlocked by: TASK-1,\nTASK-2\nExecutor: person\n\nBody</body>"
    )


def test_a_line_goes_at_the_end_of_a_body_that_is_only_the_block() -> None:
    assert with_line("<body>Blocked by: None</body>", "agent") == (
        "<body>Blocked by: None\nExecutor: agent</body>"
    )


def test_an_existing_line_is_rewritten_in_place() -> None:
    html_notes = _task(line="agent").html_notes
    assert with_line(html_notes, "cowork") == html_notes.replace(
        "Executor: agent", "Executor: cowork"
    )


def test_a_task_with_no_blocked_by_has_nowhere_for_the_line() -> None:
    assert with_line("<body>just some text</body>", "agent") is None


def test_without_line_gives_back_the_body_as_it_was() -> None:
    assert without_line(_task(line="cowork").notes) == _task().notes
    assert without_line(_task().notes) == _task().notes


# ── planning and applying ─────────────────────────────────────────────────


def test_a_tagged_task_with_no_line_is_backfilled_and_assigned() -> None:
    change = plan(_task(tags=("cowork",)))
    assert change.executor == "cowork"
    assert change.body is not None and "\nExecutor: cowork\n" in change.body
    assert change.assigned
    assert not change.tagged
    assert change.describe() == "Executor: cowork written, assigned to the owner"


def test_an_untagged_task_with_no_line_is_the_agents_and_unassigned() -> None:
    change = plan(_task(assignee="u1"))
    assert change.executor == "agent"
    assert change.body is not None and "\nExecutor: agent\n" in change.body
    assert change.unassigned
    assert change.describe() == "Executor: agent written, unassigned"


def test_a_line_that_contradicts_the_tag_moves_the_tag() -> None:
    change = plan(_task(line="person", tags=("cowork",), assignee="u1"))
    assert change.executor == "person"
    assert change.body is None
    assert change.tagged == ["person"]
    assert change.untagged == ["cowork"]
    assert not change.assigned


def test_an_agent_line_takes_an_executor_tag_off() -> None:
    change = plan(_task(line="agent", tags=("cowork", "review")))
    assert change.untagged == ["cowork"]
    assert change.tagged == []


def test_a_task_in_step_needs_nothing() -> None:
    assert not plan(_task(line="cowork", tags=("cowork",), assignee="u1")).any
    assert not plan(_task(line="agent")).any


def test_a_bad_line_is_left_for_the_linter() -> None:
    change = plan(_task(line="robot"))
    assert change.body is None
    assert change.executor == "agent"


def test_a_task_with_no_template_is_not_given_a_line() -> None:
    task = dataclasses.replace(_task(), html_notes="<body>loose text</body>")
    assert plan(task).body is None


def test_keep_in_step_writes_what_it_planned() -> None:
    board = FakeBoard()
    tasks = [
        _task(gid="1", tags=("person",)),
        _task(gid="2", line="agent", tags=("cowork",), assignee="u1"),
        _task(gid="3", line="cowork", tags=("cowork",), assignee="u1"),  # in step
        _task(gid="4", tags=("cowork",), completed=True),  # done: left alone
    ]
    changes = keep_in_step(board, tasks, write=True)

    assert [c.task.gid for c in changes] == ["1", "2"]
    kinds = [(c[0], c[1]) for c in board.calls]
    assert kinds == [
        ("body", "1"),
        ("assign", "1"),
        ("untag", "2"),
        ("unassign", "2"),
    ]


def test_keep_in_step_with_writing_off_only_reports() -> None:
    board = FakeBoard()
    changes = keep_in_step(board, [_task(tags=("person",))], write=False)
    assert len(changes) == 1
    assert board.calls == []


def test_keep_in_step_stops_at_its_limit() -> None:
    board = FakeBoard()
    tasks = [_task(gid=str(i)) for i in range(5)]
    changes = keep_in_step(board, tasks, write=True, limit=2)
    assert [c.task.gid for c in changes] == ["0", "1"]


# ── the rest of the cog reading the line ──────────────────────────────────


def test_writing_the_line_or_matching_the_tag_changes_no_fingerprint() -> None:
    before = _task(tags=("cowork",))
    after = _task(line="cowork", tags=("cowork",))
    assert fingerprint(before) == fingerprint(after)
    untagged = _task(line="cowork")
    assert fingerprint(untagged) == fingerprint(before)
    agent = _task()
    assert fingerprint(_task(line="agent")) == fingerprint(agent)


def test_a_new_executor_changes_the_fingerprint() -> None:
    assert fingerprint(_task(line="person")) != fingerprint(_task(line="cowork"))


def test_compose_writes_the_line_from_the_sources_labels() -> None:
    person = compose(TaskDraft(title="Rotate it", labels=["person"]))
    agent = compose(TaskDraft(title="Fix it"))
    assert "\nExecutor: person" in person.html_notes
    assert "\nExecutor: agent" in agent.html_notes


def test_the_linter_does_not_yet_require_the_line() -> None:
    task = _task()
    assert (
        lint(
            name=task.name,
            notes=task.notes,
            tags=(),
            repos={"infra"},
            completed_by_id={},
            shapes=True,
        )
        == []
    )


def test_with_a_line_two_executor_tags_are_not_a_shape_problem() -> None:
    task = _task(line="cowork", tags=("cowork", "person"))
    problems = lint(
        name=task.name,
        notes=task.notes,
        tags=task.tag_names,
        repos={"infra"},
        completed_by_id={},
        shapes=True,
    )
    assert not any("more than one executor" in p for p in problems)


def test_a_cowork_line_lets_a_leaf_name_no_repository() -> None:
    notes = _task(line="cowork").notes.replace("Where: infra", "Where: None")
    problems = lint(
        name="TASK-1 · plan",
        notes=notes,
        tags=(),
        repos={"infra"},
        completed_by_id={},
        shapes=True,
    )
    assert problems == []


def test_the_executors_come_from_the_contract() -> None:
    assert executor.EXECUTORS == ("agent", "cowork", "person")


def _proposal(task: AsanaTaskRecord, **overrides):
    from mini_app_polis.api.contract import GroomingProposal

    base = {
        "task_gid": task.gid,
        "fingerprint": fingerprint(task),
        "title": "plan",
        "done_when": "the plan is clean",
        "where": "infra",
        "constraints": "None",
        "effort": "S",
        "blocked_by": "None",
        "body": "Because.",
        "questions": [],
        "model": "m",
        "cost_usd": 0.01,
    }
    base.update(overrides)
    return GroomingProposal(**base)


@pytest.mark.parametrize(
    ("line", "tags", "proposed", "expected"),
    [
        (None, (), None, "agent"),
        (None, (), "cowork", "cowork"),
        (None, ("person",), "cowork", "person"),  # the task's own wins
        ("cowork", (), "person", "cowork"),
    ],
)
def test_grooming_writes_the_executor_line(line, tags, proposed, expected) -> None:
    from task_manager_cog.groom import groomed_text

    task = _task(line=line, tags=tags)
    block, _ = groomed_text(task, _proposal(task, executor=proposed))
    assert block.splitlines()[-1] == f"Executor: {expected}"


def test_a_split_child_carries_its_executor_line() -> None:
    from mini_app_polis.api.contract import GroomingChild

    from task_manager_cog.split import child_notes

    child = GroomingChild(
        title="Set the key",
        done_when="the key is set",
        where="infra",
        constraints="None",
        effort="S",
        body="Because.",
        executor="person",
    )
    block, _ = child_notes(child, ["TASK-9"], "TASK-8", "parent")
    assert block.splitlines()[-1] == "Executor: person"
    agent = child.model_copy(update={"executor": None})
    assert child_notes(agent, ["TASK-9"], "TASK-8", "parent")[0].endswith(
        "Executor: agent"
    )


def test_dispatch_skips_a_task_whose_line_says_cowork_even_untagged() -> None:
    from task_manager_cog.dispatch import DispatchResult, candidates

    result = DispatchResult()
    picked = candidates(
        [_task(gid="1", line="cowork"), _task(gid="2", line="agent", tags=("cowork",))],
        ready_gid="s-ready",
        unswept_prefixes=frozenset(),
        template_marker="do not work",
        result=result,
    )
    assert [c.task.gid for c in picked] == ["2"]
    assert result.skipped == {"executor_cowork": 1}
