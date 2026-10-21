"""The sweep: every task waiting to be worked, checked against the definition of ready.

A schedule puts one ``task.sweep`` message on this cog's queue every hour,
so a sweep runs in line with task creation and never beside it. A sweep
reads the board once, checks each open task in the grooming columns with
the linter (lint.py), and moves each task that passes to the ready column.
Nothing here calls a model: a task that is already ready costs nothing to
find, and a task that is not is only logged.

Moving is a setting, ``GROOM_AUTO_READY``, off unless set, so the sweep can
run and say what it would move before it moves anything. The columns are
named, not configured by gid, so they read the same in Doppler as on the
board:

  GROOM_SECTIONS    the columns swept (default: Ideas, Refining, Blocked)
  READY_SECTION     the column a ready task moves to (default: Ready)
  GROOM_AUTO_READY  true to move ready tasks; anything else only reports

The board's template task says so in its name ("do not work") and is never
swept.

Run it locally, read-only, to see every task's verdict:

    doppler run -- uv run python -m task_manager_cog.sweep
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from mini_app_polis import logger as logger_mod
from mini_app_polis.asana import AsanaSection, AsanaTaskRecord
from mini_app_polis.environment import Effect, effect_enabled
from mini_app_polis.pipeline_status import run_report

from .board import Board
from .lint import lint, task_id_of
from .registry import load_repos

log = logger_mod.get_logger()

REPO = "task-manager-cog"
FLOW_NAME = "groom-sweep"

DEFAULT_GROOM_SECTIONS: tuple[str, ...] = ("Ideas", "Refining", "Blocked")
DEFAULT_READY_SECTION = "Ready"

#: In the template task's name. A task saying this is never swept.
DO_NOT_WORK = "do not work"


class SweepMisconfigured(RuntimeError):
    """The board has no column by the configured ready name."""


@dataclass(frozen=True)
class Verdict:
    """One task, and every rule it fails. No problems means ready."""

    task: AsanaTaskRecord
    problems: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.problems


@dataclass
class SweepResult:
    verdicts: list[Verdict] = field(default_factory=list)
    moved: list[AsanaTaskRecord] = field(default_factory=list)

    @property
    def ready(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.ready]


def groom_sections() -> tuple[str, ...]:
    raw = os.environ.get("GROOM_SECTIONS") or ""
    names = tuple(n.strip() for n in raw.split(",") if n.strip())
    return names or DEFAULT_GROOM_SECTIONS


def ready_section() -> str:
    return (os.environ.get("READY_SECTION") or "").strip() or DEFAULT_READY_SECTION


def auto_ready() -> bool:
    return (os.environ.get("GROOM_AUTO_READY") or "").strip().lower() == "true"


def assess(
    tasks: list[AsanaTaskRecord],
    *,
    swept: Collection[str],
    repos: Collection[str],
) -> list[Verdict]:
    """A verdict for every open task in a swept column, in board order."""
    swept_lower = {name.lower() for name in swept}
    completed_by_id = {
        task_id: task.completed
        for task in tasks
        if (task_id := task_id_of(task.name)) is not None
    }
    verdicts = []
    for task in tasks:
        if task.completed or DO_NOT_WORK in task.name.lower():
            continue
        if (task.section_name or "").lower() not in swept_lower:
            continue
        problems = lint(
            name=task.name,
            notes=task.notes,
            tags=task.tag_names,
            repos=repos,
            completed_by_id=completed_by_id,
        )
        verdicts.append(Verdict(task=task, problems=tuple(problems)))
    return verdicts


def _section_named(sections: list[AsanaSection], name: str) -> AsanaSection:
    for section in sections:
        if section.name.lower() == name.lower():
            return section
    raise SweepMisconfigured(
        f"the board has no {name!r} column (READY_SECTION); "
        f"it has {', '.join(s.name for s in sections)}"
    )


def sweep(board: Board, *, repos: Collection[str], move: bool) -> SweepResult:
    """Check the board, and move what is ready when ``move`` is set."""
    sections = board.sections()
    ready = _section_named(sections, ready_section())
    result = SweepResult(
        verdicts=assess(board.tasks(), swept=groom_sections(), repos=repos)
    )
    for verdict in result.verdicts:
        if not verdict.ready:
            log.info(
                "sweep: %s not ready: %s",
                verdict.task.name,
                "; ".join(verdict.problems),
            )
            continue
        if move:
            board.move(verdict.task.gid, ready.gid)
            result.moved.append(verdict.task)
            log.info("sweep: %s moved to %s", verdict.task.name, ready.name)
        else:
            log.info("sweep: %s is ready (GROOM_AUTO_READY is off)", verdict.task.name)
    return result


def run_sweep(
    *,
    run_id: str,
    board_factory: Callable[[], Board] | None = None,
    repos_loader: Callable[[], Collection[str]] = load_repos,
) -> None:
    """One scheduled sweep, with its run report. Raises to be redelivered."""
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id) as report:
        if not effect_enabled(Effect.ASANA):
            # One board, and no development copy of it.
            log.info("sweep: board writes are off here; nothing swept")
            report.note("board writes off outside production")
            return

        board = (board_factory or Board.from_env)()
        result = sweep(board, repos=repos_loader(), move=auto_ready())

        report.count("tasks_checked", len(result.verdicts))
        report.count("not_ready", len(result.verdicts) - len(result.ready))
        if not auto_ready():
            report.count("ready_not_moved", len(result.ready))
        for task in result.moved:
            report.updated("task moved to Ready", task.name, link=board.url(task.gid))


def main(argv: list[str] | None = None) -> int:
    """Print every swept task's verdict. Reads only; never moves anything."""
    del argv
    board = Board.from_env()
    result = sweep(board, repos=load_repos(), move=False)
    for verdict in result.verdicts:
        mark = "READY" if verdict.ready else "     "
        print(f"{mark}  [{verdict.task.section_name}] {verdict.task.name}")
        for problem in verdict.problems:
            print(f"         - {problem}")
    print(
        f"\n{len(result.ready)} of {len(result.verdicts)} tasks ready "
        f"in {', '.join(groom_sections())}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
