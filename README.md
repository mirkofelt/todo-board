# todo-board

A lightweight task queue and web UI for managing AI agent work items. Tasks are dispatched to
worker subprocesses (Claude CLI) and their status tracked in real time.

## Features

- Dark-themed web UI with live polling (no page reload needed)
- Tasks grouped by project, with status badges: `pending`, `in_progress`, `done`, `blocked`, `failed`, `context_limit`
- Auto-spawns one Claude worker per project (no concurrent workers on the same project)
- Stalled workers detected after 25 min and re-queued automatically
- Editable global requirements shown to every worker
- Status line for live worker progress
- Auto-reloads the UI when `app.py` changes on disk

## Requirements

- Python 3.11+
- `claude` CLI in PATH (or set `CLAUDE_BIN`)

```bash
pip install fastapi uvicorn
# or
pip install -e .
```

## Running

```bash
python app.py
```

Open [http://localhost:7842](http://localhost:7842).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TODO_BOARD_PORT` | `7842` | Port for the web server |
| `TODO_BOARD_URL` | `http://localhost:7842` | Base URL used by workers to call back |
| `CLAUDE_BIN` | `claude` (auto-detected) | Path to the Claude CLI binary |
| `MEMORY_FILE` | _(none)_ | Optional path to a markdown file injected as context into each worker prompt |
| `CLAUDE_WORK_DIR` | `$HOME` | Working directory for Claude worker subprocesses |

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/todos` | List all todos |
| `POST` | `/api/add` | Add a todo `{text, project_id?}` |
| `POST` | `/api/status/:id` | Set status `{status}` |
| `POST` | `/api/done/:id` | Mark done |
| `POST` | `/api/delete/:id` | Delete (blocked if `in_progress`) |
| `POST` | `/api/note/:id` | Set note `{note}` |
| `GET/POST` | `/api/statusline` | Read / write status line text |
| `GET/POST` | `/api/requirements` | Read / write global requirements |
| `GET` | `/api/projects` | List projects |
| `POST` | `/api/projects/add` | Add project `{name}` |
| `POST` | `/api/projects/delete/:id` | Delete project |
| `GET` | `/api/version` | Returns `app.py` mtime — used for auto-reload |

## Worker lifecycle

When a new todo is added, `todo_worker.py` is spawned as a subprocess. It:
1. Sets status → `in_progress`
2. Calls `claude -p <prompt>` with the task text and optional memory context
3. Sets status → `done` or `failed` based on output
4. Clears the status line

The heartbeat script (`check_todos.py`) can be run on a cron to detect and re-queue
stalled workers.

## License

MIT
