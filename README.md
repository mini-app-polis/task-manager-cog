# task-manager-cog

[![CI](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/task-manager-cog?label=version)](https://github.com/mini-app-polis/task-manager-cog/releases)

Puts tasks on the board. Every source of tasks (voice notes, GitHub issues)
hands this cog a draft of the work, and it adds what every task has in common:
the board's template block, an id, tags, the column and the assignee. Every
hour it also sweeps the board, and moves each task that meets the definition
of ready to Ready. It is the only thing that writes to the board. See
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
                                               4. write the grooming records
                                                  that changed (the API)
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
| Blocked by | Blank, `None`, or only tasks that are done. |
| Tags | No `review` tag. |
| Body | Says why, and links what is needed. |

The sweep never touches a task whose name says "do not work" (the board's
template).

Each swept task's verdict is kept as a grooming record in
api-kaianolevine-com (`/v1/tasks/groomings`), written only when it changed.
A record's fingerprint covers the task's name, body and tags, so a later
sweep can tell an edited task from one still waiting. A task this cog moved
to Ready that a person moves back is counted on its record, and named in
the run report: that count is how to tell whether ready meant ready.

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
  lint.py       the definition of ready
  registry.py   the fleet's repositories, from ecosystem.yaml
  records.py    grooming records: fingerprints, verdicts, sent back from Ready
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
