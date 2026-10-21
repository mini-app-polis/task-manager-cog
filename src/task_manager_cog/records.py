"""What a sweep remembers about each task: its grooming record.

The records live in api-kaianolevine-com (``/v1/tasks/groomings``), so they
outlast the function and the board stays the only thing written to Asana.
A record holds a fingerprint of the task's content as last judged, the
verdict, and whether this cog moved the task to Ready.

**The fingerprint** covers the name, the body and the tags: what a person
edits. Not the column, so moving a task is not a change to its content. A
task whose fingerprint still matches its record is unchanged since it was
last judged, and grooming it again would cost a run to learn nothing new.

**Sent back from Ready.** A task this cog moved to Ready that turns up in a
grooming column again was moved back by a person. That is counted on the
record (``returned_count``): it is the measure of whether ready meant ready.

A record is written only when something in it changed, so a sweep of a quiet
board writes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping

from mini_app_polis.api.contract import (
    GroomingOutcome,
    GroomingRecord,
    GroomingRecordWrite,
)
from mini_app_polis.asana import AsanaTaskRecord

from .lint import task_id_of


def fingerprint(task: AsanaTaskRecord) -> str:
    """sha256 of the task's name, body and tags."""
    content = "\n\x1f".join(
        (task.name, task.notes, ",".join(sorted(t.lower() for t in task.tag_names)))
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def changed(task: AsanaTaskRecord, records: Mapping[str, GroomingRecord]) -> bool:
    """Whether the task is new, or edited since its record was written."""
    record = records.get(task.gid)
    return record is None or record.fingerprint != fingerprint(task)


def next_record(
    task: AsanaTaskRecord,
    problems: tuple[str, ...],
    previous: GroomingRecord | None,
    *,
    moved_at: dt.datetime | None,
) -> GroomingRecordWrite | None:
    """The record this sweep leaves for a swept task, or None if unchanged.

    ``moved_at`` is when this sweep moved the task to Ready, if it did. A
    swept task is in a grooming column, so a previous record that says this
    cog moved it to Ready means a person has moved it back.
    """
    current = fingerprint(task)
    returned = previous is not None and previous.moved_to_ready_at is not None
    returned_count = (previous.returned_count if previous else 0) + int(returned)

    outcome: GroomingOutcome
    if not problems:
        outcome = "ready"
    elif (
        previous is not None
        and previous.outcome == "questions"
        and previous.fingerprint == current
    ):
        # Still waiting on the answer to a question; nothing has changed.
        outcome = "questions"
    else:
        outcome = "not_ready"

    record = GroomingRecordWrite(
        task_gid=task.gid,
        task_id=task_id_of(task.name),
        fingerprint=current,
        outcome=outcome,
        problems=list(problems),
        moved_to_ready_at=moved_at,
        returned_count=returned_count,
    )
    if previous is not None and _same(record, previous):
        return None
    return record


def _same(record: GroomingRecordWrite, previous: GroomingRecord) -> bool:
    fields = set(GroomingRecordWrite.model_fields)
    return record.model_dump(include=fields) == previous.model_dump(include=fields)
