from __future__ import annotations

import pytest
from mini_app_polis.api.contract import GroomingChild, GroomingProposal

from task_manager_cog.lint import parse
from task_manager_cog.records import fingerprint
from task_manager_cog.split import DRY_RUN_MARKER, external_id

from .test_groom import VAGUE, _proposal
from .test_sweep import SECTIONS, FakeBoard, FakeRecords, _sweep_sending, _task

CHILDREN = [
    GroomingChild(
        title="common-python-utils: the shared contract",
        done_when="It is released.",
        where="common-python-utils",
        constraints="None",
        effort="S",
        body="The shared shape comes first.",
    ),
    GroomingChild(
        title="infra: apply it",
        done_when="The plan is clean.",
        where="infra (cogs.tf)",
        constraints="None",
        effort="M",
        executor="cowork",
        blocked_by_siblings=[0],
        blocked_by="2026-11-02",
        body="Needs the release.",
    ),
]

REPOS_WITH_LIB = frozenset({"infra", "common-python-utils"})


def _split(**overrides) -> GroomingProposal:
    return _proposal(
        title="Ship the contract and apply it",
        done_when=None,
        where=None,
        constraints=None,
        effort=None,
        blocked_by=None,
        body="Two repositories, so two tasks.",
        children=CHILDREN,
        **overrides,
    )


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ASANA_ENABLED", raising=False)


@pytest.fixture
def live(production, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROOM_SPLIT_DRY_RUN", "false")


def _board() -> FakeBoard:
    return FakeBoard([VAGUE], sections_=[*SECTIONS])


def _apply_split(proposal, board, store) -> None:
    from task_manager_cog import groom

    groom.apply_proposal(
        proposal,
        run_id="r",
        board_factory=lambda: board,
        records_factory=lambda: store,
        repos_loader=lambda: REPOS_WITH_LIB,
    )


def test_a_split_creates_its_children_and_rewrites_the_task(
    live, _no_outbound_reports
) -> None:
    board, store = _board(), FakeRecords()
    _apply_split(_split(), board, store)

    # The task had no id: it takes MINI-1, its children the next two.
    assert [name for name, _ in board.created] == [
        "MINI-2 · common-python-utils: the shared contract",
        "MINI-3 · infra: apply it",
    ]
    first, second = (board.get(f"9{i}") for i in (1, 2))
    assert first is not None and second is not None
    assert parse(first.notes).fields["Blocked by"] == "None"
    assert parse(second.notes).fields["Blocked by"] == "MINI-2, 2026-11-02"
    assert parse(second.notes).fields["Effort"] == "M"
    assert second.tag_names == ("cowork",)
    assert "Part of MINI-1 · Ship the contract and apply it." in second.notes

    parent = board.get("7")
    assert parent is not None
    assert parent.name == "MINI-1 · Ship the contract and apply it"
    fields = parse(parent.notes).fields
    assert fields["Done when"] == "MINI-2 and MINI-3 are done."
    assert fields["Blocked by"] == "MINI-2, MINI-3"
    assert fields["Where"] == "common-python-utils, infra"
    assert fields["Constraints"] == "A compound task: no work of its own."
    assert fields["Effort"] == "M"
    assert parent.section_name == "Blocked"

    # Children are recorded as groomed, so the sweep does not send them again.
    for gid in ("91", "92"):
        record = store.stored[gid]
        task = board.get(gid)
        assert task is not None
        assert record.groomed_fingerprint == fingerprint(task)
    assert store.stored["7"].groomed_fingerprint == fingerprint(parent)


def test_with_task_shapes_on_the_split_passes_as_leaves_and_a_compound(
    live, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_TASK_SHAPES", "true")
    board, store = _board(), FakeRecords()
    _apply_split(_split(), board, store)
    assert store.stored["91"].problems == []
    assert store.stored["7"].problems == [
        "Blocked by MINI-2, which is not done",
        "Blocked by MINI-3, which is not done",
    ]


def test_a_dry_run_reports_the_split_and_creates_nothing(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.delenv("GROOM_SPLIT_DRY_RUN", raising=False)
    board, store = _board(), FakeRecords()
    proposal = _split()
    _apply_split(proposal, board, store)
    _apply_split(proposal, board, store)  # redelivered

    assert board.created == []
    assert board.get("7") == VAGUE
    (comment,) = board.comments_["7"]
    assert comment.startswith(DRY_RUN_MARKER)
    assert "0. common-python-utils: the shared contract" in comment
    assert "executor cowork; waits on children 0; blocked by 2026-11-02" in comment
    # Held until it changes: not sent to be groomed (and paid for) again.
    assert store.stored["7"].groomed_fingerprint == fingerprint(VAGUE)


def test_a_dry_run_split_posts_a_run_report(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.delenv("GROOM_SPLIT_DRY_RUN", raising=False)
    named = _task("7", "MINI-140 · Look into the runners", notes=VAGUE.notes)
    board = FakeBoard([named], sections_=[*SECTIONS])
    _apply_split(_split(fingerprint=fingerprint(named)), board, FakeRecords())

    ((args, kwargs),) = _no_outbound_reports
    assert args[:2] == ("apply-grooming", "SUCCESS")
    assert kwargs["notable"] is True
    assert "split proposed (dry run): [MINI-140 children=2]" in kwargs["text"]
    assert kwargs["cost_usd"] == "0.0900"
    assert kwargs["children_proposed"] == 2


def test_a_dry_run_split_is_not_sent_for_grooming_again_until_it_changes(
    production, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.delenv("GROOM_SPLIT_DRY_RUN", raising=False)
    board, store = _board(), FakeRecords()

    _sweep_sending(store, board, monkeypatch, "5")
    (job,) = store.jobs
    _apply_split(_split(fingerprint=job.task.fingerprint), board, store)
    assert board.created == []
    _sweep_sending(store, board, monkeypatch, "5")
    assert len(store.jobs) == 1

    # Edited after the proposal: sent again.
    board._replace("7", notes="ubuntu-latest is moving, and soon")
    _sweep_sending(store, board, monkeypatch, "5")
    assert len(store.jobs) == 2


def test_a_split_over_the_cap_is_not_applied(
    live, monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setenv("GROOM_SPLIT_MAX_CHILDREN", "1")
    board, store = _board(), FakeRecords()
    _apply_split(_split(), board, store)
    assert board.created == []
    (comment,) = board.comments_["7"]
    assert "2 children is over GROOM_SPLIT_MAX_CHILDREN (1)" in comment
    assert store.stored["7"].groomed_fingerprint == fingerprint(VAGUE)


def test_a_redelivered_split_creates_no_second_set(live, _no_outbound_reports) -> None:
    board, store = _board(), FakeRecords()
    proposal = _split()
    _apply_split(proposal, board, store)
    _apply_split(proposal, board, store)
    assert len(board.created) == 2


def test_a_split_cut_short_finishes_without_duplicates(
    live, _no_outbound_reports
) -> None:
    # The first child was created before a failure; the redelivery finds it.
    board, store = _board(), FakeRecords()
    proposal = _split()
    earlier = _task("90", "MINI-5 · common-python-utils: the shared contract")
    board.records.append(earlier)
    board.by_external_id[external_id("7", proposal.fingerprint, 0)] = "90"

    _apply_split(proposal, board, store)
    assert [name for name, _ in board.created] == ["MINI-7 · infra: apply it"]
    second = board.get("91")
    assert second is not None
    assert parse(second.notes).fields["Blocked by"].startswith("MINI-5, ")
    parent = board.get("7")
    assert parent is not None
    assert parse(parent.notes).fields["Done when"] == "MINI-5 and MINI-7 are done."
