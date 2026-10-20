from __future__ import annotations

import pytest

from task_manager_cog.ids import next_id, task_name


def test_first_id_is_one() -> None:
    assert next_id("VN", []) == "VN-1"


def test_next_id_is_the_highest_plus_one_not_the_count() -> None:
    names = ["VN-2 · b", "VN-10 · j", "VN-3 · c"]
    assert next_id("VN", names) == "VN-11"


@pytest.mark.parametrize(
    "name",
    [
        "MINI-40 · manual",  # another prefix
        "VNX-40 · lookalike",
        "VN-40x · not an id",
        "about VN-40",  # not leading
        "plain title",
    ],
)
def test_only_this_prefixs_ids_count(name: str) -> None:
    assert next_id("VN", ["VN-1 · a", name]) == "VN-2"


def test_a_hand_edited_separator_still_holds_its_id() -> None:
    assert next_id("VN", ["VN-7 - edited", "VN-3"]) == "VN-8"


def test_task_name() -> None:
    assert task_name("VN-4", "Call the venue") == "VN-4 · Call the venue"
