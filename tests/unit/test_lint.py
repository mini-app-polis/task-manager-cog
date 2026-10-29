from __future__ import annotations

import datetime as dt

import pytest

from task_manager_cog.lint import (
    children_of,
    executor_of,
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
Blocked by: TASK-21

Run reports already carry their severity; this reads it.
Links: https://example.test/run?id=1"""


def _lint(
    *,
    name: str = "TASK-144 · Cog-run detector",
    notes: str = READY_NOTES,
    tags: tuple[str, ...] = (),
    completed: dict[str, bool] | None = None,
) -> list[str]:
    return lint(
        name=name,
        notes=notes,
        tags=tags,
        repos=REPOS,
        completed_by_id={"TASK-21": True} if completed is None else completed,
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
        "Constraints: none on the 2nd\nBlocked by: time.\nFrom: TASK-64"
    )
    assert parsed.fields == {
        "Done when": "the drill exits 0",
        "Where": "db-ops",
        "Constraints": "none on the 2nd",
        "Blocked by": "time.",
    }
    assert parsed.body == "Why: never run by cron\nFrom: TASK-64"


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
        ("TASK-12 · Do the thing", "TASK-12"),
        ("infra#4 · Fix it", "infra#4"),
        ("Do the thing", None),
        ("TASK-12 - Do the thing", None),
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
    problems = _lint(completed={"TASK-21": False})
    assert problems == ["Blocked by TASK-21, which is not done"]


_TODAY = dt.date(2026, 10, 23)


def _lint_on(blocked_by: str, completed: dict[str, bool] | None = None) -> list[str]:
    return lint(
        name="TASK-77 · Verify the first monthly db-drill run",
        notes=_with("Blocked by", blocked_by),
        tags=(),
        repos=REPOS,
        completed_by_id={"TASK-21": True} if completed is None else completed,
        today=_TODAY,
    )


def test_a_date_ahead_blocks_until_it_comes() -> None:
    assert _lint_on("2026-11-02") == ["Blocked until 2026-11-02"]


@pytest.mark.parametrize("day", ["2026-10-23", "2026-10-01"])
def test_a_date_that_has_come_no_longer_blocks(day: str) -> None:
    assert _lint_on(day) == []


def test_a_date_beside_a_task_id_blocks_with_it() -> None:
    assert _lint_on("TASK-21, 2026-11-02") == ["Blocked until 2026-11-02"]
    assert _lint_on("TASK-21, 2026-11-02", completed={"TASK-21": False}) == [
        "Blocked by TASK-21, which is not done",
        "Blocked until 2026-11-02",
    ]
    assert _lint_on("TASK-21 and 2026-10-01") == []


@pytest.mark.parametrize("day", ["2026-11-2", "2026-13-01", "2026-02-30", "11/02/2026"])
def test_a_malformed_date_is_a_problem_of_its_own(day: str) -> None:
    assert _lint_on(day) == [f"Blocked by {day}, which is not a date (YYYY-MM-DD)"]
    assert not only_blocked(_lint_on(day))


def test_blocked_by_naming_neither_a_task_nor_a_date_is_not_ready() -> None:
    assert _lint_on("the next drill") == ["Blocked by names no task or date"]


def test_a_date_ahead_counts_as_waiting() -> None:
    assert only_blocked(["Blocked until 2026-11-02"])
    assert only_blocked(
        ["Blocked by TASK-21, which is not done", "Blocked until 2026-11-02"]
    )
    assert not only_blocked(
        ["Blocked until 2026-11-02", "Effort is blank, not S, M or L"]
    )


def test_only_blocked_is_true_only_when_every_problem_is_an_open_blocker() -> None:
    assert only_blocked(["Blocked by TASK-21, which is not done"])
    assert only_blocked(
        [
            "Blocked by TASK-21, which is not done",
            "Blocked by TASK-22, which is not done",
        ]
    )
    assert not only_blocked([])
    assert not only_blocked(["Blocked by TASK-21, which is not on the board"])
    assert not only_blocked(
        ["Blocked by TASK-21, which is not done", "Effort is blank, not S, M or L"]
    )


def test_blocked_by_an_unknown_task_is_not_ready() -> None:
    assert "Blocked by TASK-21, which is not on the board" in _lint(completed={})


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


def test_the_executor_is_read_from_the_tags_by_the_contracts_names() -> None:
    assert executor_of([]) is None
    assert executor_of(["admin", "review"]) is None
    assert executor_of(["admin", "Cowork"]) == "cowork"
    assert executor_of(["person"]) == "person"


def test_an_executor_tag_never_makes_a_task_less_ready() -> None:
    assert _lint(tags=("cowork",)) == []
    assert _lint(tags=("person",)) == []


# ── Shapes: a leaf or a compound task ─────────────────────────────────────


def _shaped(
    notes: str = READY_NOTES,
    tags: tuple[str, ...] = (),
    completed: dict[str, bool] | None = None,
) -> list[str]:
    return lint(
        name="TASK-171 · Grooming splits tasks",
        notes=notes,
        tags=tags,
        repos=REPOS,
        completed_by_id=completed or {"TASK-21": True, "TASK-1": True, "TASK-2": True},
        shapes=True,
    )


def _notes_with(**fields: str) -> str:
    notes = READY_NOTES
    for label, value in fields.items():
        notes = "\n".join(
            f"{label}: {value}".rstrip() if line.startswith(f"{label}:") else line
            for line in notes.split("\n")
        )
    return notes


def test_a_leaf_names_one_repository_and_passes() -> None:
    assert _shaped(_notes_with(Where="task-manager-cog (src/lint.py)")) == []


def test_two_repositories_are_not_a_leaf() -> None:
    problems = _shaped()  # api-kaianolevine-com and task-manager-cog
    assert problems == [
        "Where names more than one repository (api-kaianolevine-com, "
        "task-manager-cog): a leaf task names one, so this is a compound task "
        "over one task each"
    ]
    # Off, the same task is ready.
    assert _lint() == []


def test_two_executor_tags_are_not_a_leaf() -> None:
    notes = _notes_with(Where="infra")
    assert _shaped(notes, tags=("cowork", "Person")) == [
        "tagged for more than one executor (cowork, person)"
    ]
    assert _shaped(notes, tags=("cowork", "admin")) == []


@pytest.mark.parametrize("where", ["", "None"])
def test_only_a_task_for_another_executor_may_name_no_repository(where: str) -> None:
    notes = _notes_with(Where=where)
    assert _shaped(notes, tags=("cowork",)) == []
    assert _shaped(notes) != []


def test_a_compound_task_lists_every_child_in_blocked_by() -> None:
    notes = _notes_with(
        **{
            "Done when": "TASK-1, TASK-2 and TASK-21 are done. Between them: x.",
            "Blocked by": "TASK-1, TASK-2, TASK-21",
        }
    )
    assert _shaped(notes) == []  # spans two repositories: it has no work of its own


def test_a_compound_task_missing_a_child_is_not_ready() -> None:
    notes = _notes_with(
        **{
            "Done when": "TASK-1, TASK-2 and TASK-21 are done.",
            "Blocked by": "TASK-1, TASK-21",
        }
    )
    assert _shaped(notes) == ["compound task's Blocked by does not list TASK-2"]


@pytest.mark.parametrize(
    ("done_when", "children"),
    [
        (
            "TASK-183, TASK-184, TASK-185 and TASK-186 are done. Between them: …",
            ["TASK-183", "TASK-184", "TASK-185", "TASK-186"],
        ),
        ("TASK-173 and TASK-174 are done.", ["TASK-173", "TASK-174"]),
        ("infra#4 is done", ["infra#4"]),
        ("The sweep is done when TASK-1 passes.", []),
        ("A test passes once TASK-1 is done.", []),
        ("", []),
    ],
)
def test_children_are_read_from_the_opening_of_done_when(
    done_when: str, children: list[str]
) -> None:
    assert children_of(done_when) == children


# ── open questions written in the body ────────────────────────────────────


@pytest.mark.parametrize(
    ("section", "head"),
    [
        ("Open decisions:\n- Which queue?", "Open decisions"),
        ("open questions: whether to page on WARN", "Open questions"),
        ("Still open, for the owner:\n1. The retention period.", "Still open"),
        ("STILL OPEN: the alert threshold", "Still open"),
    ],
)
def test_a_body_with_an_open_section_is_not_ready(section: str, head: str) -> None:
    problems = _lint(notes=f"{READY_NOTES}\n\n{section}")
    assert problems == [f"body lists open questions ({head})"]


@pytest.mark.parametrize(
    "section",
    [
        "Open decisions:",
        "Open questions:\n\nThe rest of the body.",
        "Open questions: None",
        "Still open, for the owner:\n- none",
    ],
    ids=["head alone", "head then a blank line", "none inline", "none listed"],
)
def test_an_emptied_open_section_passes(section: str) -> None:
    assert _lint(notes=f"{READY_NOTES}\n\n{section}") == []


def test_a_body_without_an_open_section_passes() -> None:
    notes = f"{READY_NOTES}\n\nStill opening the queue is not a question."
    assert _lint(notes=notes) == []


def test_an_open_section_under_the_original_text_is_not_read() -> None:
    notes = f"{READY_NOTES}\n\nBefore grooming:\nOpen questions:\n- Which queue?"
    assert _lint(notes=notes) == []


def test_an_open_section_above_the_original_text_still_counts() -> None:
    notes = (
        f"{READY_NOTES}\n\nOpen questions:\n- Which queue?\n\n"
        "Before grooming:\nOpen questions:\n- Which queue?"
    )
    assert _lint(notes=notes) == ["body lists open questions (Open questions)"]
