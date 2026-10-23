# task-manager-cog

[![CI](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/task-manager-cog?label=version)](https://github.com/mini-app-polis/task-manager-cog/releases)

Puts tasks on the board. Every source of tasks (voice notes, GitHub issues)
hands this cog a draft of the work, and it adds what every task has in common:
the board's template block, an id, tags, the column and the assignee. Every
hour it also sweeps the board, moves each task that meets the definition
of ready to Ready, and dispatches an agent for each Ready task it can take.
It is the only thing that writes to the board. See
[ADR-001](docs/decisions/ADR-001-one-writer-to-the-board.md).

## Flow

```
source ──POST /v1/tasks──► api-kaianolevine-com ──► task-manager-jobs.fifo
                                                          │ one job at a time
                                                          ▼
                                             task-manager-cog (Lambda)
                                               1. look up the external id
                                               2. compose the task
                                               3. mint or take the id
                                               4. create it on the board
                                               5. run report names the task

schedule (hourly) ──task.sweep──► task-manager-jobs.fifo ──► task-manager-cog
                                               1. read the board and the registry
                                               2. lint every open task in
                                                  Ideas, Refining and Blocked
                                               3. move each ready task to Ready
                                                  (GROOM_AUTO_READY)
                                               4. send tasks that are not ready
                                                  and have changed to
                                                  task-groomer-cog (GROOM_DISPATCH)
                                               5. write the grooming records
                                                  that changed (the API)
                                               6. dispatch an agent for each
                                                  Ready task it can take
                                                  (AGENT_DISPATCH)

task-groomer-cog ──proposal──► API ──task.groom-proposal──► task-manager-jobs.fifo
                                               apply it (groom.py): name, template,
                                               body; then Ready, or Refining with
                                               any questions as a comment

agent workflow ──result──► API ──task.agent-result──► task-manager-jobs.fifo
                                               apply it (results.py): PR line and
                                               Review, or a comment and In progress
```

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
| Tags | No `review` tag. |
| Body | Says why, and links what is needed. |
| Shape | With `GROOM_TASK_SHAPES=true`: a **leaf** (Where names at most one repository, and at most one executor tag; only a `cowork` or `person` task may name none) or a **compound** task (Done when opens with its children's ids, "… are done", and Blocked by lists every one of them). Off until splitting is in place, so the existing board is not judged against it first. |
| Grooming questions | None unanswered. A task whose last grooming asked questions waits until a grooming answers them, even if its template is complete. |

The sweep never touches a task whose name says "do not work" (the board's
template), nor a task from a source `sources.py` marks unswept: the problems
the fleet detected (`CI-`, `RUN-`, `CONF-`, `API-`). Those wait for a person,
neither groomed nor moved to Ready, until fixing them automatically is
decided on.

Each swept task's verdict is kept as a grooming record in
api-kaianolevine-com (`/v1/tasks/groomings`), written only when it changed.
A record's fingerprint covers the task's name, body and tags, so a later
sweep can tell an edited task from one still waiting. A task this cog moved
to Ready that a person moves back is counted on its record, and named in
the run report: that count is how to tell whether ready meant ready.

## Who does a task

A task's executor is a tag on it, named in the shared contract
(`mini_app_polis.api.contract.GroomingExecutor`):

| Tag | Executor |
|---|---|
| none | the agent: a headless run with only the task and the repositories it names |
| `cowork` | a working session with Kaiano: his repositories, the board, Railway, his machine, his decisions |
| `person` | Kaiano alone: consoles behind his login, accounts, physical checks |

A tag never blocks: a tagged task is held to the definition of ready like
any other and moves to Ready when it passes. Dispatch takes only untagged
tasks, and counts each tagged task it passes over in the run report
(`agent_skipped_executor_cowork`, `agent_skipped_executor_person`). The
groomer proposes an executor for an untagged task the agent cannot do, and
grooming applies it as a tag; a tag a task already has is never replaced.

## Grooming

A task that is not ready is sent to task-groomer-cog when its content
differs from what was last sent for grooming (the record's
`groomed_fingerprint`): at most `GROOM_DISPATCH_LIMIT` a sweep (default 5),
and only when `GROOM_DISPATCH=true`, because each one is a paid run. A job
the API refuses is sent again next sweep.

The groomer's proposal is applied here (`groom.py`), and only to the content
it was made for:

- The name becomes `<id> · <title>`. A task without an id takes the next
  `MINI-` id.
- The template block is filled from the proposal, keeping the task's own
  value for any part the proposal left out, and its PR line. The
  proposal's body follows, then the task's original text under
  `Before grooming:`, added the first time only.
- The `review` tag comes off.
- A proposed executor goes on as a tag, unless the task already has one.
- With questions, they are posted as one comment starting
  `Grooming questions:` and the task moves to Refining. **Answer in a
  comment:** the answers become part of the task's content, so the next
  sweep sends it to be groomed again, answers included.
- Without, it moves to Ready if it now passes and `GROOM_AUTO_READY` is on,
  and to Refining otherwise.

A groomed task is not sent again until someone changes it, so a task
waiting on you costs nothing.

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
  created: the children are posted on the task as one comment and named in
  the run report. The task is held until it changes, so once dry run is
  off, edit a task that was reported to have it split for real.
- **A cap.** A split with more children than `GROOM_SPLIT_MAX_CHILDREN`
  (default 5) is reported the same way and not applied.
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

After grooming, the sweep takes Ready tasks in board order that:

- are not the template task nor a detected problem (`CI-`, `RUN-`, `CONF-`,
  `API-`);
- have no executor tag (`cowork`, `person`);
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

The trigger is the hourly sweep, not Asana webhooks. Asana delivers webhook
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

## Sources

| Source         | Id                  | Minted here |
|----------------|---------------------|-------------|
| `voicenote`    | `VN-<n>`            | yes         |
| `github-issue` | `<repo>#<n>`        | no          |

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
  sweep.py      the hourly sweep: lint the board, move what is ready
  split.py      applying a split: the children, then the compound task
  lint.py       the definition of ready
  registry.py   the fleet's repositories, from ecosystem.yaml
  records.py    grooming records: fingerprints, verdicts, sent back from Ready
  groom.py      applying a groomer's proposal to the board
  dispatch.py   dispatching an agent for each Ready task it can take
  github.py     the agent GitHub App: is there a caller, start it
  results.py    applying an agent run's result to the board
  _deadline.py  stop a run before the function timeout
```

## Running locally (DOC-013)

**Prerequisites:** Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and the
[Doppler CLI](https://docs.doppler.com/docs/install-cli). Secrets come from
Doppler's shared `dev` config; nothing reads a `.env` file, and local runs
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

**See every task's verdict** without moving anything. This reads the board
the `dev` config points at, and never writes:

```bash
doppler run -- uv run python -m task_manager_cog.sweep
```

## Running tests

```bash
uv run pytest                              # with coverage, as CI runs it
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

## Deployment

A release on `main` deploys the Lambda through the shared `lambda-deploy`
workflow. The function, queue, dead-letter queue and alarms are
`module "task_manager"` in `mini-app-polis/infra`; the repository variables
`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION` and `AWS_FUNCTION_NAME` come from that
stack's `cogs` output. The hourly sweep is the module's `schedules` entry,
which puts `{"type": "task.sweep", "version": 1}` on the queue.
