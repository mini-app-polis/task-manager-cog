from __future__ import annotations

import datetime as dt

import pytest

from task_manager_cog.lint import (
    lint,
    only_blocked,
    parse,
    task_id_of,
    where_repos,
)

REPOS = frozenset({"api-kaianolevine-com", "task-manager-cog", "infra"})

READY_NOTES = """Done when: A failing run report opens a streak, and two in a row make one task.
Where: api-kaianolevine-com (the notify route), task-manager-cog
Constraints: None
Effort: M
PR:
Blocked by: MINI-21

Run reports already carry their severity; this reads it.
Links: https://example.test/run?id=1"""


def _lint(
    *,
    name: str = "MINI-144 · Cog-run detector",
    notes: str = READY_NOTES,
    tags: tuple[str, ...] = (),
    completed: dict[str, bool] | None = None,
) -> list[str]:
    return lint(
        name=name,
        notes=notes,
        tags=tags,
        repos=REPOS,
        completed_by_id={"MINI-21": True} if completed is None else completed,
    )


def _with(field: str, value: str) -> str:
    lines = [
        f"{field}: {value}".rstrip() if line.startswith(f"{field}:") else line
        for line in READY_NOTES.split("\n")
    ]
    return "\n".join(lines)


def test_a_complete_task_is_ready() -> None:
    assert _lint() == []


def test_parse_splits_the_block_from_the_body() -> None:
    parsed = parse(READY_NOTES)
    assert parsed.fields["Effort"] == "M"
    assert parsed.fields["PR"] == ""
    assert parsed.body.startswith("Run reports already")


def test_a_line_without_a_label_continues_the_field_above() -> None:
    parsed = parse("Done when:\n- one\n- two\nWhere: infra\n\nbody")
    assert parsed.fields["Done when"] == "- one\n- two"
    assert parsed.fields["Where"] == "infra"
    assert parsed.body == "body"


def test_a_label_the_template_lacks_is_body_and_the_fields_around_it_still_read() -> (
    None
):
    parsed = parse(
        "Done when: the drill exits 0\nWhere: db-ops\nWhy: never run by cron\n"
        "Constraints: none on the 2nd\nBlocked by: time.\nFrom: MINI-64"
    )
    assert parsed.fields == {
        "Done when": "the drill exits 0",
        "Where": "db-ops",
        "Constraints": "none on the 2nd",
        "Blocked by": "time.",
    }
    assert parsed.body == "Why: never run by cron\nFrom: MINI-64"


def test_a_continuation_line_is_not_mistaken_for_a_label() -> None:
    parsed = parse("Done when:\nthe report says: done\nWhere: infra\n\nbody")
    assert parsed.fields["Done when"] == "the report says: done"


def test_text_before_any_label_is_all_body() -> None:
    parsed = parse("Just an idea\nDone when: x")
    assert parsed.fields == {}
    assert parsed.body == "Just an idea\nDone when: x"


def test_a_task_with_no_template_fails_every_field() -> None:
    problems = _lint(notes="maybe look at this")
    assert any(p.startswith("template is missing Done when") for p in problems)
    assert "Done when is blank" in problems
    assert "Effort is blank, not S, M or L" in problems


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("MINI-12 · Do the thing", "MINI-12"),
        ("infra#4 · Fix it", "infra#4"),
        ("Do the thing", None),
        ("MINI-12 - Do the thing", None),
    ],
)
def test_task_id_of(name: str, expected: str | None) -> None:
    assert task_id_of(name) == expected


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("Replace retag music", "title has no id"),
        ("VN-3 · [Needs review]", "title says nothing"),
    ],
)
def test_the_title_needs_an_id_and_a_summary(name: str, problem: str) -> None:
    assert problem in _lint(name=name)


def test_a_review_tag_is_not_ready() -> None:
    assert "tagged review" in _lint(tags=("Review",))


@pytest.mark.parametrize("value", ["", "TBD", "it works?", "maybe - ?"])
def test_done_when_must_be_decided(value: str) -> None:
    problems = _lint(notes=_with("Done when", value))
    assert any(p.startswith("Done when is") for p in problems)


def test_a_question_mark_in_a_url_is_not_a_question() -> None:
    assert _lint(notes=_with("Done when", "GET https://x.test/a?b=1 answers 200")) == []


def test_where_must_name_registered_repositories() -> None:
    problems = _lint(notes=_with("Where", "new groomer cog repo, infra"))
    assert "Where names no repository called new" in problems


def test_where_reads_the_head_of_each_item() -> None:
    assert where_repos(
        "api-kaianolevine-com (a, b, c), task-manager-cog/src/x.py; infra: modules"
    ) == ["api-kaianolevine-com", "task-manager-cog", "infra"]


def test_constraints_may_say_none_but_not_be_blank() -> None:
    assert "Constraints is blank (say None if there are none)" in _lint(
        notes=_with("Constraints", "")
    )


@pytest.mark.parametrize("effort", ["S", "m", "L"])
def test_every_effort_size_is_ready_including_large(effort: str) -> None:
    assert _lint(notes=_with("Effort", effort)) == []


def test_effort_outside_the_scale_is_not_ready() -> None:
    assert "Effort is XL, not S, M or L" in _lint(notes=_with("Effort", "XL"))


def test_pr_belongs_to_whoever_works_it() -> None:
    assert "PR is filled in before the work started" in _lint(
        notes=_with("PR", "https://github.com/x/y/pull/1")
    )


def test_blocked_by_an_open_task_is_not_ready() -> None:
    problems = _lint(completed={"MINI-21": False})
    assert problems == ["Blocked by MINI-21, which is not done"]


_TODAY = dt.date(2026, 10, 23)


def _lint_on(blocked_by: str, completed: dict[str, bool] | None = None) -> list[str]:
    return lint(
        name="MINI-77 · Verify the first monthly db-drill run",
        notes=_with("Blocked by", blocked_by),
        tags=(),
        repos=REPOS,
        completed_by_id={"MINI-21": True} if completed is None else completed,
        today=_TODAY,
    )


def test_a_date_ahead_blocks_until_it_comes() -> None:
    assert _lint_on("2026-11-02") == ["Blocked until 2026-11-02"]


@pytest.mark.parametrize("day", ["2026-10-23", "2026-10-01"])
def test_a_date_that_has_come_no_longer_blocks(day: str) -> None:
    assert _lint_on(day) == []


def test_a_date_beside_a_task_id_blocks_with_it() -> None:
    assert _lint_on("MINI-21, 2026-11-02") == ["Blocked until 2026-11-02"]
    assert _lint_on("MINI-21, 2026-11-02", completed={"MINI-21": False}) == [
        "Blocked by MINI-21, which is not done",
        "Blocked until 2026-11-02",
    ]
    assert _lint_on("MINI-21 and 2026-10-01") == []


@pytest.mark.parametrize("day", ["2026-11-2", "2026-13-01", "2026-02-30", "11/02/2026"])
def test_a_malformed_date_is_a_problem_of_its_own(day: str) -> None:
    assert _lint_on(day) == [f"Blocked by {day}, which is not a date (YYYY-MM-DD)"]
    assert not only_blocked(_lint_on(day))


def test_blocked_by_naming_neither_a_task_nor_a_date_is_not_ready() -> None:
    assert _lint_on("the next drill") == ["Blocked by names no task or date"]


def test_a_date_ahead_counts_as_waiting() -> None:
    assert only_blocked(["Blocked until 2026-11-02"])
    assert only_blocked(
        ["Blocked by MINI-21, which is not done", "Blocked until 2026-11-02"]
    )
    assert not only_blocked(
        ["Blocked until 2026-11-02", "Effort is blank, not S, M or L"]
    )


def test_only_blocked_is_true_only_when_every_problem_is_an_open_blocker() -> None:
    assert only_blocked(["Blocked by MINI-21, which is not done"])
    assert only_blocked(
        [
            "Blocked by MINI-21, which is not done",
            "Blocked by MINI-22, which is not done",
        ]
    )
    assert not only_blocked([])
    assert not only_blocked(["Blocked by MINI-21, which is not on the board"])
    assert not only_blocked(
        ["Blocked by MINI-21, which is not done", "Effort is blank, not S, M or L"]
    )


def test_blocked_by_an_unknown_task_is_not_ready() -> None:
    assert "Blocked by MINI-21, which is not on the board" in _lint(completed={})


@pytest.mark.parametrize("value", ["", "None", "-", "\u2014"])
def test_blocked_by_nothing_is_ready(value: str) -> None:
    assert _lint(notes=_with("Blocked by", value), completed={}) == []


def test_blocked_by_text_with_no_task_is_not_ready() -> None:
    assert "Blocked by names no task or date" in _lint(
        notes=_with("Blocked by", "the Railway incident")
    )


def test_a_task_needs_a_body() -> None:
    notes = READY_NOTES.split("\n\n")[0]
    assert _lint(notes=notes) == ["no body saying why, with links to what is needed"]
