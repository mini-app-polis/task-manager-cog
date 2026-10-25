# ADR-0001: One writer to the task board

## Status

Accepted

## Context

Tasks reach the board from more than one place: voice notes, GitHub issues,
and later others. Each source used to be expected to write to Asana itself.
That spreads three things across every source:

- **The board's shape.** Every task starts with the same six-line template
  block so it can be groomed in place. A copy of that block in each source
  drifts.
- **Ids.** A task's name leads with an id (`VN-12 · …`). On the free tier
  Asana has no id field and no search, so the next id is the highest one on
  the board plus one. Two writers doing that at once can mint the same id.
- **Credentials and failure handling.** Each writer needs the Asana token,
  retries, and a way to avoid creating the same task twice.

## Decision

This cog is the only thing that writes to the board.

- **Sources hand over a draft.** `TaskCreateRequest` in common-python-utils'
  API contract: a source, the source's own key for the work
  (`external_id`, prefixed with the source), an id when the source already
  has one, and a `TaskDraft` (title, where, body, sections, links, labels,
  needs_review, due date). A source does whatever understanding of its input
  is needed (a voice note's model extraction) before it asks; this cog makes
  no model calls.
- **The API is the only producer.** Sources call `POST /v1/tasks`; the API
  validates the request against the same contract and enqueues it on
  `task-manager-jobs.fifo`.
- **One job at a time.** The queue has a single message group, so jobs run
  serially. That is what makes "highest plus one" safe without a counter.
- **One prefix per source, one writer per prefix.** `sources.py` maps each
  source to the prefix this cog mints, or to none for a source that brings
  its own id (an issue's `repo#n`). Ids written by hand use a prefix not in
  that table.
- **Duplicates are stopped by the external id.** Asana stores `external_id`
  on the task and can look a task up by it, completed tasks included. The
  lookup runs before anything is composed or minted, so a request repeated by
  its source, or a job redelivered after it created its task, finds that task
  and stops: the duplicate creates nothing and mints no id.
- **Production only.** There is one board and no development copy, so board
  writes are gated on `Effect.ASANA`; elsewhere the job logs what it would
  have created.

## Consequences

- Adding a source is a contract change (a new `TaskSource` value, and
  whether it brings an id) plus a row in `sources.py`. A test holds the two
  in step.
- A task is created after the source's request is accepted, not during it.
  The source learns that its request was queued; this cog's run report names
  the task.
- Minting reads every task name on the board on each new task. At the
  board's size that is a few paged requests, and it is what lets a completed
  task keep its id.
