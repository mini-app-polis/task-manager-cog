"""What a sweep remembers about each task: its refining record.

The records live in api-kaianolevine-com (``/v1/tasks/refinings``), so they
outlast the function and the board stays the only thing written to Asana.
A record holds a fingerprint of the task's content as last judged, the
verdict, and whether this cog moved the task to Ready.

**The fingerprint** covers the name, the body and the tags: what a person
edits. Not the column, so moving a task is not a change to its content. A
task whose fingerprint still matches its record is unchanged since it was
last judged, and refining it again would cost a run to learn nothing new.

**Sent back from Ready.** A task this cog moved to Ready that turns up in a
refining column again was moved back by a person. A task in Ready that no
longer passes is moved back by the sweep itself (:func:`sent_back_record`).
Either is counted on the record (``returned_count``): it is the measure of
whether ready meant ready.

**Sent for refining.** ``refined_fingerprint`` is the content last sent to
task-refiner-cog, or that a refining wrote. A task that is not ready is sent
when its content differs from it: once per change, never in a loop, and a
refined task that is waiting costs nothing until someone changes it.

**Stopped by the budget.** A refining run the spent budget stopped is
reported by the refiner, and the record comes back with that content's
fingerprint as ``budget_spent_fingerprint``. While it equals the task's
content, the task is sent again even though it was sent before, so the
content is refined once there is budget. The sweep never writes it. A stop
for older content is ignored.

**Lost.** A refining can be lost for other reasons too: a run that crashes,
or a proposal that never reaches the board. Sending stamps the record with
``refine_sent_at`` and counts the send in ``refine_attempts``; writing the
record of a refining that came back, applied or not, clears both. A send
still outstanding after :data:`REFINE_RETRY_AFTER` is sent again, up to
:data:`REFINE_MAX_ATTEMPTS` sends of the same content. After that the task is
held like any other, and the sweep names it. Sending again what the budget
stopped is not a new attempt, and leaves both as they are.

**Answers.** A refining that has questions posts them as one comment that
starts with :data:`QUESTIONS_PREFIX`. A split that is reported rather than
created (a dry run, or over the cap) is posted as one comment starting with
:data:`SPLIT_REPORT_PREFIX`, and waits on its owner the same way. The
comments after the last such comment are the owner's answers, and count as
the task's content: answering changes the fingerprint, which sends the task
to be refined again, answers included.

**Edited by hand.** A person who edits a task's name, body or tags after
its refining asked questions has written the answer into the task itself.
:func:`edited_since_refining` compares the task with the content the last
refining wrote; answers left only as comments do not count, since a refining
still has to fold them in. An edited task that now passes, or waits only on
blockers, is released from its questions without a paid run (sweep.py).

A record is written only when something in it changed, so a sweep of a quiet
board writes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping, Sequence

from mini_app_polis.api.contract import (
    RefiningOutcome,
    RefiningRecord,
    RefiningRecordWrite,
)
from mini_app_polis.asana import AsanaTaskRecord

from .executor import AGENT, TAGGED, executor_of_task, without_line
from .lint import task_id_of

#: Starts the comment a refining's questions are posted as.
QUESTIONS_PREFIX = "Refining questions:"

#: The other prefix a questions comment may start with. A comment starting
#: with it holds its task the way :data:`QUESTIONS_PREFIX` does.
GROOMING_QUESTIONS_PREFIX = "Grooming questions:"

#: Starts the comment a split reported but not created is posted as.
SPLIT_REPORT_PREFIX = "Proposed split (not created"

#: The comments that ask the owner something; what follows answers them.
ASKING_PREFIXES = (QUESTIONS_PREFIX, GROOMING_QUESTIONS_PREFIX, SPLIT_REPORT_PREFIX)

#: How long a send may go with no refining back before it counts as lost.
REFINE_RETRY_AFTER = dt.timedelta(hours=12)

#: The most times the same content is sent with no refining coming back.
REFINE_MAX_ATTEMPTS = 3


def answers(comments: Sequence[str]) -> list[str]:
    """The comments after the last refining questions or reported split.

    Those are the owner's answers. A task never asked anything has none: its
    comments are not answers to anything, and are not part of its content.
    """
    asked = [i for i, c in enumerate(comments) if c.startswith(ASKING_PREFIXES)]
    if not asked:
        return []
    return [c for c in comments[asked[-1] + 1 :] if c.strip()]


def unanswered(comments: Sequence[str]) -> bool:
    """Whether the last refining questions on a task have nothing after them.

    Whoever posted them, a refiner or a person, and a reported split counts
    as a question: the comments are oldest first, so any comment after the
    last one answers it.
    """
    asked = any(c.startswith(ASKING_PREFIXES) for c in comments)
    return asked and not answers(comments)


def fingerprint(task: AsanaTaskRecord, replies: Sequence[str] = ()) -> str:
    """sha256 of the task's name, body and tags, and any answers.

    Who works the task is counted once, the way a tag was before the
    ``Executor:`` line existed: the line is left out of the body, and the
    task's executor stands in for its executor tags. So the sweep writing
    the line, or tagging a task to match it, changes no fingerprint, and a
    task is not refined again for it.
    """
    executor = executor_of_task(task.notes, task.tag_names)
    tags = {t.lower() for t in task.tag_names if t.lower() not in TAGGED}
    if executor != AGENT:
        tags.add(executor)
    content = "\n\x1f".join(
        (
            task.name,
            without_line(task.notes),
            ",".join(sorted(tags)),
            *replies,
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def changed(
    task: AsanaTaskRecord,
    records: Mapping[str, RefiningRecord],
    replies: Sequence[str] = (),
) -> bool:
    """Whether the task is new, or edited since its record was written."""
    record = records.get(task.gid)
    return record is None or record.fingerprint != fingerprint(task, replies)


def edited_since_refining(
    task: AsanaTaskRecord, previous: RefiningRecord | None
) -> bool:
    """Whether a person changed the task's name, body or tags since the last
    refining wrote it.

    Comments are left out: an answer given only as a comment still needs a
    refining to fold it into the task. The executor line, executor tags and
    assignment are this cog's own and never count (see :func:`fingerprint`).
    """
    return (
        previous is not None
        and previous.refined_fingerprint is not None
        and previous.refined_fingerprint != fingerprint(task)
    )


def _outstanding(previous: RefiningRecord, current: str) -> bool:
    """Whether ``current`` was sent and no refining of it has come back."""
    return (
        previous.refined_fingerprint == current and previous.refine_sent_at is not None
    )


def _lost(previous: RefiningRecord, current: str, now: dt.datetime) -> bool:
    """Whether a send of ``current`` has been outstanding too long."""
    sent_at = previous.refine_sent_at
    return (
        _outstanding(previous, current)
        and sent_at is not None
        and now - sent_at >= REFINE_RETRY_AFTER
    )


def needs_refining(
    task: AsanaTaskRecord,
    previous: RefiningRecord | None,
    replies: Sequence[str] = (),
    now: dt.datetime | None = None,
) -> bool:
    """Whether a task that is not ready should be sent for refining.

    Yes unless its content is what was last sent, or what a refining wrote,
    and no refining of that content was stopped by the budget or lost.
    """
    if previous is None:
        return True
    current = fingerprint(task, replies)
    return (
        previous.refined_fingerprint != current
        or previous.budget_spent_fingerprint == current
        or (
            _lost(previous, current, now or dt.datetime.now(dt.UTC))
            and previous.refine_attempts < REFINE_MAX_ATTEMPTS
        )
    )


def awaiting_refining(
    task: AsanaTaskRecord,
    previous: RefiningRecord | None,
    replies: Sequence[str] = (),
    now: dt.datetime | None = None,
) -> bool:
    """Whether the task's content was sent and its refining may still come back."""
    if previous is None:
        return False
    current = fingerprint(task, replies)
    return _outstanding(previous, current) and not _lost(
        previous, current, now or dt.datetime.now(dt.UTC)
    )


def _send_stamp(
    previous: RefiningRecord | None, current: str, now: dt.datetime
) -> tuple[dt.datetime | None, int]:
    """``refine_sent_at`` and ``refine_attempts`` for a send of ``current``."""
    if previous is None or previous.refined_fingerprint != current:
        return now, 1
    if previous.budget_spent_fingerprint == current:
        # Sent again for the budget, not because it was lost.
        return previous.refine_sent_at, previous.refine_attempts
    if previous.refine_sent_at is None:
        return now, 1
    return now, previous.refine_attempts + 1


def next_record(
    task: AsanaTaskRecord,
    problems: tuple[str, ...],
    previous: RefiningRecord | None,
    *,
    moved_at: dt.datetime | None,
    replies: Sequence[str] = (),
    sent: bool = False,
    now: dt.datetime | None = None,
) -> RefiningRecordWrite | None:
    """The record this sweep leaves for a swept task, or None if unchanged.

    ``moved_at`` is when this sweep moved the task to Ready, if it did. A
    swept task is in a refining column, so a previous record that says this
    cog moved it to Ready means a person has moved it back. ``sent`` says
    this sweep sent the task for refining, so its content is now what was
    last sent, and the send is stamped until a refining comes back.
    """
    current = fingerprint(task, replies)
    if sent:
        sent_at, attempts = _send_stamp(
            previous, current, now or dt.datetime.now(dt.UTC)
        )
    elif previous is not None:
        sent_at, attempts = previous.refine_sent_at, previous.refine_attempts
    else:
        sent_at, attempts = None, 0
    returned = previous is not None and previous.moved_to_ready_at is not None
    returned_count = (previous.returned_count if previous else 0) + int(returned)

    outcome: RefiningOutcome
    if not problems:
        outcome = "ready"
    elif previous is not None and previous.outcome == "questions":
        # Questions stay out until a refining answers them: the answers are
        # read, and are part of the content, only while they are.
        outcome = "questions"
    else:
        outcome = "not_ready"

    record = RefiningRecordWrite(
        task_gid=task.gid,
        task_id=task_id_of(task.name),
        fingerprint=current,
        outcome=outcome,
        problems=list(problems),
        moved_to_ready_at=moved_at,
        returned_count=returned_count,
        refined_fingerprint=(
            current if sent else (previous.refined_fingerprint if previous else None)
        ),
        refine_sent_at=sent_at,
        refine_attempts=attempts,
    )
    if previous is not None and _same(record, previous):
        return None
    return record


def sent_back_record(
    task: AsanaTaskRecord,
    problems: tuple[str, ...],
    previous: RefiningRecord | None,
    *,
    replies: Sequence[str] = (),
) -> RefiningRecordWrite:
    """The record for a task the sweep moved out of Ready because it failed.

    It counts once as sent back from Ready, and stops saying this cog moved
    it there, so the next sweep does not count it again. Its content is
    recorded as refined: moving it back does not send it for refining, and a
    person changing it does.
    """
    current = fingerprint(task, replies)
    outcome: RefiningOutcome = (
        "questions"
        if previous is not None and previous.outcome == "questions"
        else "not_ready"
    )
    return RefiningRecordWrite(
        task_gid=task.gid,
        task_id=task_id_of(task.name),
        fingerprint=current,
        outcome=outcome,
        problems=list(problems),
        moved_to_ready_at=None,
        returned_count=(previous.returned_count if previous else 0) + 1,
        refined_fingerprint=current,
    )


def _same(record: RefiningRecordWrite, previous: RefiningRecord) -> bool:
    fields = set(RefiningRecordWrite.model_fields)
    return record.model_dump(include=fields) == previous.model_dump(include=fields)
