from __future__ import annotations

from datetime import date

from mini_app_polis.api.contract import TaskDraft

from task_manager_cog.compose import EMPTY_TITLE_FALLBACK, REVIEW_TAG, compose


def test_every_task_starts_with_the_full_template_block() -> None:
    html = compose(TaskDraft(title="t")).html_notes
    assert html.startswith(
        "<body>Done when:\nWhere:\nConstraints:\nEffort:\nPR:\nBlocked by:"
    )


def test_where_fills_its_line_only() -> None:
    html = compose(TaskDraft(title="t", where="deejaytools")).html_notes
    assert "\nWhere: deejaytools\nConstraints:\n" in html


def test_body_sections_and_links_follow_the_template_in_order() -> None:
    draft = TaskDraft(
        title="t",
        body="Book the hall",
        sections=[{"label": "Peers", "text": "Amy"}],
        links=[{"text": "Listen", "url": "https://drive.example/f"}],
    )
    html = compose(draft).html_notes
    assert html.index("Blocked by:") < html.index("Book the hall")
    assert html.index("Book the hall") < html.index("<strong>Peers</strong>\nAmy")
    assert (
        '<strong>Links</strong>\n<a href="https://drive.example/f">Listen</a>' in html
    )


def test_everything_from_the_draft_is_escaped() -> None:
    draft = TaskDraft(
        title="t",
        where="a<b",
        body="x & y < z",
        sections=[{"label": "<L>", "text": "<script>"}],
    )
    html = compose(draft).html_notes
    assert "Where: a&lt;b" in html
    assert "x &amp; y &lt; z" in html
    assert "<strong>&lt;L&gt;</strong>\n&lt;script&gt;" in html


def test_labels_become_tags_once_each() -> None:
    task = compose(TaskDraft(title="t", labels=["Venue", "venue", "Music"]))
    assert task.tag_names == ("venue", "music")


def test_review_replaces_the_labels() -> None:
    task = compose(TaskDraft(title="t", labels=["venue"], needs_review=True))
    assert task.tag_names == (REVIEW_TAG,)


def test_a_blank_title_lands_for_review() -> None:
    task = compose(TaskDraft(title="   ", labels=["venue"]))
    assert task.title == EMPTY_TITLE_FALLBACK
    assert task.tag_names == (REVIEW_TAG,)


def test_due_date_is_carried() -> None:
    assert compose(TaskDraft(title="t", due_on=date(2026, 10, 20))).due_on == date(
        2026, 10, 20
    )
