"""What still works under the grooming names while every cog moves to refining.

Grooming was renamed refining. Comments and task bodies already on the board
say grooming, the groomer still running hands its proposals back as
``task.groom-proposal``, and a setting may still be set under its GROOM_
name. These tests go with those names.
"""

from __future__ import annotations

import json

from task_manager_cog import refine_settings_from_groom
from task_manager_cog.lint import open_section
from task_manager_cog.records import answers, unanswered
from task_manager_cog.worker import parse_message

FINGERPRINT = "a" * 64


def test_old_grooming_questions_still_hold_their_task() -> None:
    comments = ["Grooming questions:\n1. Which repo?"]
    assert unanswered(comments)
    assert answers([*comments, "infra"]) == ["infra"]


def test_text_before_grooming_is_not_read_as_open_questions() -> None:
    body = "Done when: x\n\nBefore grooming:\nOpen questions: which repo?"
    assert open_section(body) is None


def test_a_groomers_proposal_is_applied_like_a_refiners() -> None:
    proposal = {
        "task_gid": "1",
        "fingerprint": FINGERPRINT,
        "title": "A task",
        "model": "a-model",
        "cost_usd": 0.1,
    }
    old = parse_message(
        json.dumps({"type": "task.groom-proposal", "version": 1, "payload": proposal})
    )
    new = parse_message(
        json.dumps({"type": "task.refine-proposal", "version": 1, "payload": proposal})
    )
    assert old == new


def test_a_sweep_asked_to_groom_refines() -> None:
    job = parse_message(
        json.dumps({"type": "task.sweep", "version": 1, "payload": {"groom": True}})
    )
    assert job.refine is True


def test_a_groom_setting_stands_in_for_its_refine_name() -> None:
    environ = {
        "GROOM_TIMES": "04:15",
        "GROOM_DISPATCH": "true",
        "REFINE_DISPATCH": "false",
    }
    refine_settings_from_groom(environ)
    assert environ["REFINE_TIMES"] == "04:15"
    # A REFINE_ name already set wins.
    assert environ["REFINE_DISPATCH"] == "false"
