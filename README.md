# task-manager-cog

[![CI](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/task-manager-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/task-manager-cog?label=version)](https://github.com/mini-app-polis/task-manager-cog/releases)

Puts tasks on the board. Every source of tasks (voice notes, GitHub issues)
hands this cog a draft of the work, and it adds what every task has in common:
the board's template block, an id, tags, the column and the assignee. It is
the only thing that writes to the board. See
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
```

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
  _deadline.py  stop a run before the function timeout
```

## Development

Secrets come from Doppler (`doppler.yaml` pins the shared `dev` config);
`.env.example` lists the names read. Board writes are off outside production.

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

## Deployment

A release on `main` deploys the Lambda through the shared `lambda-deploy`
workflow. The function, queue, dead-letter queue and alarms are
`module "task_manager"` in `mini-app-polis/infra`; the repository variables
`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION` and `AWS_FUNCTION_NAME` come from that
stack's `cogs` output.
