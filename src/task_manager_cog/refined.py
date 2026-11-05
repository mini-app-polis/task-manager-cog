"""Record tasks refined by hand, in a working session, as refined.

A task whose last refining asked questions is sent to the paid refiner as
soon as its content changes, so a session that refines a task by hand needs
a way to say it did. This records each task as it now reads: linted as the
sweep lints it, with its problems, and with its content as both what was
judged and what was refined. Its open questions are cleared, and the sweep
does not send it to be refined again until it changes.

It writes refining records only, never the board, and makes no paid call.
It reads the board and the API with the same settings as the sweep:

    doppler run --config prd -- uv run python -m task_manager_cog.refined <gid> ...

``--awaiting-owner`` records each task as waiting on its owner instead, as
if its refining had asked questions: it is held, and the comments after its
last ``Refining questions:`` comment or reported split are read as answers,
so an answer sends it to be refined again. Use it for a task a session
leaves with a question for its owner.

``--dry-run`` prints what it would record and writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Collection, Sequence

from mini_app_polis.api.contract import RefiningOutcome, RefiningRecordWrite

from .board import Board
from .lint import lint, task_id_of
from .records import fingerprint
from .registry import load_repos
from .sweep import Records, _api, task_shapes


def record_refined(
    gids: Sequence[str],
    *,
    board: Board,
    store: Records,
    repos: Collection[str],
    write: bool,
    awaiting_owner: bool = False,
    out: Callable[[str], None] = print,
) -> int:
    """Record each task as refined. Returns the number of gids refused.

    With ``awaiting_owner``, each is recorded as having questions instead.
    """
    previous = {r.task_gid: r for r in store.list_refinings()}
    tasks = board.tasks()
    open_tasks = {t.gid: t for t in tasks if not t.completed}
    completed_by_id = {
        tid: t.completed for t in tasks if (tid := task_id_of(t.name)) is not None
    }
    refused = 0
    for gid in gids:
        task = open_tasks.get(gid)
        if task is None:
            out(f"{gid}: not an open task on the board; nothing recorded")
            refused += 1
            continue
        problems = lint(
            name=task.name,
            notes=task.notes,
            tags=task.tag_names,
            repos=repos,
            completed_by_id=completed_by_id,
            shapes=task_shapes(),
        )
        current = fingerprint(task)
        prior = previous.get(gid)
        outcome: RefiningOutcome = "ready" if not problems else "not_ready"
        if awaiting_owner:
            outcome = "questions"
        record = RefiningRecordWrite(
            task_gid=gid,
            task_id=task_id_of(task.name),
            fingerprint=current,
            outcome=outcome,
            problems=problems,
            returned_count=prior.returned_count if prior else 0,
            refined_fingerprint=current,
        )
        if write:
            store.record_refining(record)
        what = "waiting on its owner" if awaiting_owner else "refined"
        verb = f"recorded as {what}" if write else f"would record as {what}"
        out(f"{task.name}\n  {verb}; problems: {'; '.join(problems) or 'none'}")
    return refused


def main(argv: list[str] | None = None) -> int:
    """Record the tasks named by gid as refined by hand."""
    parser = argparse.ArgumentParser(prog="python -m task_manager_cog.refined")
    parser.add_argument("gids", nargs="+", help="the tasks' gids on the board")
    parser.add_argument(
        "--awaiting-owner",
        action="store_true",
        help="record each as waiting on its owner's answers",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print only; write nothing"
    )
    args = parser.parse_args(argv)
    refused = record_refined(
        args.gids,
        board=Board.from_env(),
        store=_api(),
        repos=load_repos(),
        write=not args.dry_run,
        awaiting_owner=args.awaiting_owner,
    )
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
