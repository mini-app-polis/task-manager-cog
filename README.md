# task-manager-cog

[![CI](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/task-manager-cog?label=version)](https://github.com/mini-app-polis/task-manager-cog/releases)

Puts tasks on the board. Every source of tasks (voice notes, GitHub issues)
hands this cog a draft of the work, and it adds what every task has in common:
the board's template block, an id, tags, the column and the assignee. Every
hour it also sweeps the board, moves each task that meets the definition
of ready to Ready, and dispatches an agent for each Ready task it can take.
It is the only thing that writes to the board. See
[ADR-0001](docs/decisions/ADR-0001-one-writer-to-the-board.md).

## Flow

```
source ──POST /v1/tasks──► api-kaianolevine-com ──► task-manager queue
                                                          │ one job at a time
                                                          ▼
                                             task-manager-cog (Lambda)
                                               1. look up the external id
                                               2. compose the task
                                               3. mint or take the id
                                               4. create it on the board
                                               5. run report names the task

schedule (30 min) ──task.sweep──► task-manager queue ──► task-manager-cog
                                               1. read the board and the registry
                                               2. lint every open task in
                                                  Ideas, Refining, Blocked
                                                  and Ready
                                               3. move each ready task to Ready,
                                                  each task waiting only on
                                                  blockers to Blocked, and each
                                                  task failing in Ready back
                                                  (GROOM_AUTO_READY)
                                               4. send tasks that are not ready
                                                  and have changed to
                                                  task-groomer-cog (GROOM_DISPATCH)
                                               5. write the grooming records
                                                  that changed (the API)
                                               6. dispatch an agent for each
                                                  Ready task it can take
                                                  (AGENT_DISPATCH)

task-groomer-cog ──proposal──► API ──task.groom-proposal──► task-manager queue
                                               apply it (groom.py): name, template,
                                               body; then Ready, or Refining with
                                               any questions as a comment

agent workflow ──result──► API ──task.agent-result──► task-manager queue
                                               apply it (results.py): PR line and
                                               Review, or a comment and In progress

source ──update──► API ──task.update──► task-manager queue
                                               apply it (update.py): find the task
                                               by external id, comment, and move
                                               it to the named column
```

### Updating a task

A `task.update` (`mini_app_polis.api.contract.TaskUpdateRequest`) names a
task by the `external_id` it was created under, with a comment and,
optionally, a column by name. `update.py` posts the comment and moves the
task there. `Review` is the board's Review column (`REVIEW_SECTION`); any
other name is matched against the board's columns, ignoring case.

- A task not on the board is reported in the run, not retried.
- A completed task, or one already in the named column, is left alone.
- A task in In progress gets the comment and is not moved.
- A column the board does not have still gets the comment, and the run
  reports the name as an issue.
- A comment whose text is already on the task is not posted again, so a
  redelivered update changes nothing. Nothing here completes a task.

## The definition of ready

A task is ready when someone holding only the task and its repositories could
finish it without asking. Every task is held to the bar an agent needs. The
linter (`lint.py`) checks what can be checked without a model:

| Part | Ready when |
|---|---|
| Title | It starts with an id (`MINI-12 · …`) and says something. It is not `[Needs review]`. |
| Done when | Filled in, with no `TBD` or open question. |
| Where | Names repositories in the ecosystem registry (`ecosystem.yaml`), optionally with paths. |
| Constraints | Filled in. `None` is an answer; blank is not. |
| Effort | `S`, `M` or `L`. An `L` task is ready: its implementation creates its subtasks. |
| PR | Blank. It belongs to whoever works the task. |
| Blocked by | Blank, `None`, or only tasks that are done and dates (`YYYY-MM-DD`, a UTC day) that have come. A task can wait on a date alone, beside task ids, or both. Before the date it is not ready (`Blocked until <date>`) and is not sent for grooming; it moves to Ready on the first sweep on or after it. A date written any other way is a problem of its own. |
| Executor | Not checked yet: the sweep writes the line on every task first (see Who does a task). |
| Tags | No `review` tag. |
| Body | Says why, and links what is needed. No open-questions section (a line starting with one of `OPEN_SECTION_HEADS` in `lint.py`, such as `Open questions:`) lists anything: empty or remove it once it is decided. The author's original text, from the `Before grooming:` line on, is not checked. |
| Shape | With `GROOM_TASK_SHAPES=true`: a **leaf** (Where names at most one repository, and one executor: its `Executor:` line, or at most one executor tag; only a `cowork` or `person` task may name none) or a **compound** task (Done when opens with its children's ids, "… are done", and Blocked by lists every one of them). Off until splitting is in place, so the existing board is not judged against it first. |
| Grooming questions | None unanswered. A task whose last grooming asked questions waits until a grooming answers them, even if its template is complete. A task that passes everything else is also held while its last `Grooming questions:` comment or reported split, from anyone, has no comment after it; it waits for its owner and is not sent for grooming (`waiting_on_owner` in the run report). Comments are read only for such a task. |

A task whose only problems are blockers (a task in Blocked by that is not
done, or a date still ahead) is never sent for grooming. With
`GROOM_AUTO_READY` on, the sweep moves it from the column it is in to
Blocked (`BLOCKED_SECTION`), and names it in the run report; with it off,
the report counts it (`blocked_not_moved`). From Blocked it moves to Ready
on the first sweep after its blockers are done and its date has come. A
task with any other problem stays where it is and is groomed.

Ready is checked too, so a task put there by hand, or edited after it got
there, is held to the same bar. One that passes is left alone, and its
record is not rewritten. With `GROOM_AUTO_READY` on, one that fails moves
to Refining (`REFINING_SECTION`) with one comment starting
`Sent back from Ready:` that lists its problems, or to Blocked when its
blockers are its only problem. Either counts as sent back from Ready, on
its record and in the run report (`sent_back_from_ready`). A task put back
in Ready unchanged is moved again, but gets no second comment. Moving it
back does not send it for grooming: it waits until a person changes it.
With `GROOM_AUTO_READY` off, the report counts it
(`failing_in_ready_not_moved`). Either way, the agent dispatch at the end of
the sweep never takes it.

The sweep never touches a task whose name says "do not work" (the board's
template), nor a task from a source `sources.py` marks unswept: the problems
the fleet detected (`CI-`, `RUN-`, `CONF-`, `API-`). Those wait for a person,
neither groomed nor moved to Ready, until fixing them automatically is
decided on.

Each swept task's verdict is kept as a grooming record in
api-kaianolevine-com (`/v1/tasks/groomings`), written only when it changed.
A record's fingerprint covers the task's name, body and tags, so a later
sweep can tell an edited task from one still waiting. A task this cog moved
to Ready that a person moves back, or that the sweep sends back from Ready,
is counted on its record, and named in the run report: that count is how to
tell whether ready meant ready.

Each sweep's run report has three sections. **Moved** has one line per task
moved or sent, saying where it went (`~ Ready: MINI-431 · …`). **Board**
counts what the sweep found, one count a line: tasks checked, not ready,
waiting on blockers, held without questions, and the rest, with the share
of tasks moved to Ready over the last 30 days that came back out
(`returned from Ready, 30 days: 3 of 40 (8%)`, from
`GET /v1/tasks/groomings/return-rate`; when that read fails the line is
left out and the report notes it). **Agent** has the dispatch's spend this
month, what it dispatched, and what it skipped by reason, one a line.

## Who does a task

A task's executor is its `Executor:` line, the last line of the template
block, after `Blocked by` (`executor.py`). The values are `agent` and the
shared contract's executors (`mini_app_polis.api.contract.GroomingExecutor`):

| Executor | Who | Tag | Assigned |
|---|---|---|---|
| `agent` | a headless run with only the task and the repositories it names | none | Kaiano |
| `cowork` | a working session with Kaiano: his repositories, the board, Railway, his machine, his decisions | `cowork` | Kaiano |
| `person` | Kaiano alone: consoles behind his login, accounts, physical checks | `person` | Kaiano |

The line is the source of truth, because anyone who writes a task's body
can set it: a working session, the groomer, a source. The tag and the
assignment follow from it, and this cog is their only writer. Every sweep,
before it judges the board, brings up to `STEP_LIMIT` open tasks into step:

- a task with no line gets one, written from its tag (no tag is `agent`),
  into its rich body so its formatting survives;
- the executor tag is added or removed to match the line;
- every task is assigned to Kaiano, whoever does the work: he owns every
  task on the board.

Each change is named in the run report (`executor kept in step`; with
`GROOM_AUTO_READY` off, `executor out of step`, and nothing is written).
None of it counts as a change to the task: the grooming fingerprint leaves
the line out and counts the executor once, so a task is not groomed again
for it. A line that names no executor is left for the writer to fix.

An executor never blocks: a cowork or person task is held to the definition
of ready like any other and moves to Ready when it passes. Dispatch takes
only `agent` tasks, and counts each other task it passes over in the run
report (`agent_skipped_executor_cowork`, `agent_skipped_executor_person`).
The groomer proposes an executor for a task the agent cannot do, and
grooming writes it into the line; an executor a task already has is never
replaced. A task this cog creates gets its line from its source's labels,
and is assigned to Kaiano whatever its executor.

## Grooming

A task that is not ready is sent to task-groomer-cog when its content
differs from what was last sent for grooming (the record's
`groomed_fingerprint`): at most `GROOM_DISPATCH_LIMIT` a sweep (default 5),
and only when `GROOM_DISPATCH=true`, because each one is a paid run. A job
the API refuses is sent again next sweep. With `GROOM_TIMES` set (times of
day, comma-separated `HH:MM`, e.g. `04:15,05:15`, read in `SWEEP_TIMEZONE`,
default UTC), only the sweep starting within half an hour after one of them
sends any; the others still lint and move tasks. `AGENT_TIMES` does the same
for agent dispatch.

A grooming run the spent budget stopped is reported by the groomer, and the
task's record comes back with that content's fingerprint
(`budget_spent_fingerprint`). While it equals the task's content, the task
is sent again even though it was sent before, within the same limit and at
most once a sweep, so content the budget stopped is groomed once there is
budget. A stop for older content is ignored.

A grooming lost any other way (a run that crashes, a proposal that never
reaches the board) is sent again too. Sending stamps the record with
`groom_sent_at` and counts the send in `groom_attempts`; the record written
when a grooming comes back, applied or not, clears both. While a send is
outstanding the task is counted as `awaiting_grooming`. Once it has been
outstanding for 12 hours it is sent again, up to 3 sends of the same content;
after that the task is held and named like any other held task.

The groomer's proposal is applied here (`groom.py`), and only to the content
it was made for:

- The name becomes `<id> · <title>`. A task without an id takes the next
  `MINI-` id.
- The template block is filled from the proposal, keeping the task's own
  value for any part the proposal left out, and its PR line. The
  proposal's body follows, then the task's original text under
  `Before grooming:`, added the first time only.
- The `review` tag comes off.
- A proposed executor goes into the `Executor:` line (and on as a tag),
  unless the task already has one.
- With questions, they are posted as one comment starting
  `Grooming questions:` and the task moves to Refining. **Answer in a
  comment:** the answers become part of the task's content, so the next
  sweep sends it to be groomed again, answers included.
- Without, it moves to Ready if it now passes and `GROOM_AUTO_READY` is on,
  and to Refining otherwise. A task that still has problems gets one
  comment starting `Grooming left this task not ready:` listing them, and
  the run report names them.

A groomed task is not sent again until someone changes it, so a task
waiting on you costs nothing.

A task that is not ready, has no grooming questions out, is not waiting
only on blockers, and is not sent because it is unchanged since it was last
groomed is held with nobody asked: nothing happens to it until a person
changes it. Each sweep counts these (`held_without_questions`). The sweep
that first finds a task held names it, with its link, as a notice (`task
newly held with nobody asked`): it is seen rather than silent, but a
standing backlog does not turn every sweep into a WARN. A task that stays
held is counted, not named again, until it changes. The sweep does nothing
to it: it is not moved, sent or commented on.

### Splits

A proposal with children (from a groomer with splitting on) divides its
task, and `split.py` applies it:

- Each child is created as a leaf in the template, with the next `MINI-`
  id, its executor tag, and Blocked by naming the earlier children it waits
  on by id, then any task or date the proposal gave it. It is recorded as
  groomed, so the sweep moves it to Ready if it passes rather than sending
  it to be groomed again.
- The task becomes their compound task: Done when "`<children>` are done",
  Blocked by every child, Where the children's repositories, Constraints
  "A compound task: no work of its own.", Effort its largest child's. It
  waits in Blocked (`BLOCKED_SECTION`) until they are done.
- **Dry run first.** Unless `GROOM_SPLIT_DRY_RUN=false`, nothing is
  created: the children are posted on the task as one comment starting
  `Proposed split (not created` and named in the run report.
- **A cap.** A split with more children than `GROOM_SPLIT_MAX_CHILDREN`
  (default 25) is reported the same way and not applied.
- **Waits are named.** A child whose title, Done when or body says it
  waits ("waits on", "wait on", "after the … child", in any case) but whose
  proposal names no earlier child and no existing task or date for it to be
  blocked by makes the split inconsistent: created, it would reach Ready
  ahead of what it waits for. Such a split is reported the same way, naming
  each child and what it says, with an issue in the run report, and not
  applied. The check reads the proposal only.
- **A reported split waits on you**, as grooming questions do. **Answer in
  a comment** — approve it, or correct it — and the next sweep sends the
  task to be groomed again with your answer; with dry run off and the split
  under the cap, that grooming creates it. Until then the task is held and
  costs nothing.
- **Questions first.** A proposal with children and questions is not
  split: its questions are posted as one `Grooming questions:` comment,
  with the proposed children listed under them (and any child that says it
  waits but names no blocker), and the task moves to Refining as it reads.
  **Answer in a comment**, and the next sweep sends it to be groomed again
  with your answers; that grooming may then split it.
- **Once.** Each child's external id is its parent, the content the split
  was made for, and its position, so a redelivered proposal creates no
  second set, even one cut short partway.

The shape check (`GROOM_TASK_SHAPES`) and the groomer's splitting
(`GROOMER_SPLIT` in task-groomer-cog) are turned on together with this.

## Agent dispatch

An untagged task in Ready is meant for an agent: a headless Claude Code run in
GitHub Actions, in the repository the task names, that opens a **draft**
pull request into `dev`. The fleet's automerge skips drafts, so a person
always reviews and merges what an agent wrote. `dispatch.py` picks the
tasks and `github.py` starts the runs; the workflow itself is the agent
caller in each target repository, from the shared `.github` repository.

Dispatch is triggered by the sweep, not by Asana webhooks: Asana
delivers a webhook at most once and recommends polling beside it, and this
cog has no public endpoint for the webhook handshake, so the sweep is the
one trigger.

After grooming, the sweep takes Ready tasks in board order that:

- are not the template task nor a detected problem (`CI-`, `RUN-`, `CONF-`,
  `API-`);
- are `agent` tasks (their `Executor:` line, or with no line, no executor
  tag);
- are Effort `S`: larger tasks are left for a person;
- name exactly one repository in Where, and that repository has the agent
  caller (`AGENT_WORKFLOW`) with the agent GitHub App installed. The
  repositories with a caller are the ones the agent may change;
- target a repository with no run in flight, and none dispatched to it this
  sweep;
- fit the month: the month's spend, with each run in flight counted at
  `AGENT_TASK_CAP_USD`, plus one more run, stays within
  `AGENT_MONTHLY_CAP_USD`.

Each dispatch records the run in api-kaianolevine-com
(`/v1/tasks/agent-runs`), whose record is the lock on the repository, moves
the task to In progress, then starts the caller with a token for that one
repository that can only start workflows. A run is never retried: one that
fails, hits its budget, cannot be started, or never reports (after
`AGENT_RUN_TIMEOUT_MINUTES`) leaves its task in In progress with a comment,
and nothing dispatches from In progress.

The run's result comes back through the API, and `results.py` applies it. A
run that succeeded puts its pull request on the task's PR line and moves
the task to Review; one that needs grooming goes back to Refining (below).
**Nothing here ever marks a task done:** a person merges the draft,
confirms the change in dev, and completes the task.

The trigger is the sweep, not Asana webhooks. Asana delivers webhook
events at most once and recommends polling as a fallback, and this cog has
no public endpoint for the webhook handshake, so a poll is needed either
way; an hour's wait does not matter for this work.

`AGENT_DRY_RUN=true` reports what would be dispatched, and the most each
could cost, without writing anything.

### Back to grooming

Implementation that finds a task unclear or undetermined sends it back to
grooming rather than guessing, including partway through. It is not a
retry: the task reaches Ready again only through grooming.

- **An agent run** changes nothing and ends as `needs_grooming` with its
  questions. `results.py` moves the task from In progress to Refining, posts
  the questions as one comment starting `Grooming questions:`, and records
  the task as having questions. It is not dispatched or groomed again until
  you answer in a comment; the next grooming reads your answers. The task
  counts once as sent back from Ready.
- **A cowork session** that finds a task unclear midway does the same by
  hand: move the task to Refining, post the questions as one comment
  starting `Grooming questions:` (numbered, one per line), and add a
  comment saying what is already done and where (repository, branch, files,
  whether anything is committed). The next sweep reads the task as waiting
  on your answers.

The other way round, a cowork session that grooms a task by hand records
it, so its open questions are cleared and the sweep does not send it to
the paid groomer:

```bash
doppler run --config prd -- uv run python -m task_manager_cog.groomed 1219828105613597
```

It lints each task as the sweep does and writes its grooming record with
the task's current content as groomed; it never writes the board.
`--awaiting-owner` records each task as waiting on its owner instead, as if
its grooming had asked questions: it is held, and the comments after its
last `Grooming questions:` comment or reported split are read as answers.
`--dry-run` prints what it would record. A gid that is not an open task
is refused.

## Sources

| Source         | Id                  | Minted here |
|----------------|---------------------|-------------|
| `voicenote`    | `VN-<n>`            | yes         |
| `github-issue` | `<repo>#<n>`        | no          |
| `manual`       | `MINI-<n>`          | yes         |

A `manual` task is one filed by hand from a working session, through
common-python-utils' `file-task` command as the `claude-session` machine.
Its key is `manual.<key>`, so filing it again creates nothing new, and who
works it comes from the draft's labels into its `Executor:` line.

`src/task_manager_cog/sources.py` is the table; the request contract in
common-python-utils (`mini_app_polis.api.contract.TaskCreateRequest`) is the
shape a source sends.

## Layout

```
src/task_manager_cog/
  worker.py     Lambda entrypoint: read the message, create the task once
  compose.py    draft → title, body and tags
  ids.py        reading ids in use, minting the next, naming the task
  sources.py    which sources mint and with what prefix
  board.py      the Asana project, and the only Asana calls
  sweep.py      the sweep: lint the board, move what is ready
  split.py      applying a split: the children, then the compound task
  groomed.py    recording tasks groomed by hand in a working session
  lint.py       the definition of ready
  registry.py   the fleet's repositories, from ecosystem.yaml
  records.py    grooming records: fingerprints, verdicts, sent back from Ready
  groom.py      applying a groomer's proposal to the board
  dispatch.py   dispatching an agent for each Ready task it can take
  github.py     the agent GitHub App: is there a caller, start it
  results.py    applying an agent run's result to the board
  update.py     applying an update to a task: a comment, and a column
```

## Running locally (DOC-013)

**Prerequisites:** Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and the
[Doppler CLI](https://docs.doppler.com/docs/install-cli). Secrets come from
this repo's own Doppler `dev` config; nothing reads a `.env` file, and local runs
never use `prd`. `.env.example` lists the names read.

```bash
git clone git@github.com:mini-app-polis/task-manager-cog.git
cd task-manager-cog
doppler setup                              # reads doppler.yaml: mini-app-polis-ecosystem / dev
uv sync --all-extras
uv run pre-commit install
uv run pre-commit run --all-files
```

**Run one message** through the worker, as the queue would deliver it. Outside
production board writes are off, so this logs the task it would have created
and touches nothing:

```bash
cat > /tmp/task-message.json <<'JSON'
{"type": "task.create", "version": 1, "payload": {"source": "voicenote", "external_id": "voicenote.local-test", "draft": {"title": "Local test", "labels": ["test"]}}}
JSON
doppler run -- uv run python -c 'import sys; from task_manager_cog.worker import process_message; process_message(open(sys.argv[1]).read(), run_id="local")' /tmp/task-message.json
```

Setting `ASANA_ENABLED=true` in your shell makes that run create the task on
the real board.

**See every task's verdict** without moving anything. The board exists
only in production, so this reads it with the `prd` config, and never
writes:

```bash
doppler run --config prd -- uv run python -m task_manager_cog.sweep
```

## Running tests

Two layers, run in one pytest invocation: `tests/unit/` and
`tests/integration/`. The integration suite invokes `lambda_handler` end to
end on SQS events, with Asana, the ecosystem registry and
api-kaianolevine-com stubbed at the HTTP boundary by respx, so it needs
nothing provisioned. It covers redelivery (the same message twice makes one
task) and a bad record in a batch (named in `batchItemFailures` while the
rest completes).

```bash
uv run pytest                              # with coverage, as CI runs it
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

## Deployment

A release on `main` deploys the Lambda through the shared `lambda-deploy`
workflow. The function, queue, dead-letter queue, alarms and the half-hourly
schedule are declared in `mini-app-polis/infra`, which also supplies the
repository variables `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION` and
`AWS_FUNCTION_NAME`. The schedule puts
`{"type": "task.sweep", "version": 1}` on the queue.
