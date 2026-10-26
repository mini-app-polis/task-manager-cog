from __future__ import annotations

import typing

from mini_app_polis.api.contract import GIVEN_ID_SOURCES, TaskSource

from task_manager_cog.sources import SOURCES, UNSWEPT_PREFIXES


def test_every_contract_source_has_handling_here() -> None:
    assert set(SOURCES) == set(typing.get_args(TaskSource))


def test_sources_that_bring_an_id_are_exactly_those_without_a_prefix() -> None:
    assert {s.name for s in SOURCES.values() if s.prefix is None} == set(
        GIVEN_ID_SOURCES
    )


def test_no_two_sources_share_a_prefix() -> None:
    prefixes = [s.prefix for s in SOURCES.values() if s.prefix is not None]
    assert len(prefixes) == len(set(prefixes))


def test_keys_match_names() -> None:
    assert all(key == source.name for key, source in SOURCES.items())


def test_detected_problems_and_due_rotations_are_left_for_a_person() -> None:
    assert {"CI", "RUN", "CONF", "API", "ROT"} == UNSWEPT_PREFIXES
    assert SOURCES["voicenote"].swept
