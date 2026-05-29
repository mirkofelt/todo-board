"""Tests targeting specific coverage gaps identified by pytest-cov."""
import importlib
import json
import os
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="in_progress", project_id=1, **kwargs):
    t = {
        "id": id,
        "text": text,
        "done": False,
        "status": status,
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
    }
    t.update(kwargs)
    return t


# ── GET / ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_index_returns_html(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()


# ── startup recovery: stale pid file with dead process ────────────────────────

def test_recover_with_dead_pid_resets_to_pending(data_dir, monkeypatch):
    """Pid file exists but os.kill raises ProcessLookupError → treated as dead."""
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    pid_file = data_dir / "worker_1.pid"
    pid_file.write_text("99999999")  # pid that doesn't exist
    (data_dir / "todos.json").write_text(json.dumps([_todo(1, "Orphan")]))

    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    server._recover_orphaned_todos()

    todos = json.loads((data_dir / "todos.json").read_text())
    assert todos[0]["status"] == "in_progress"  # reset then re-spawned
    assert 1 in spawned
    assert not pid_file.exists()


# ── shutdown recovery: killpg raises ─────────────────────────────────────────

def test_prepare_for_restart_handles_dead_process(data_dir, monkeypatch):
    """If os.killpg raises OSError (dead process), shutdown still resets the todo."""
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    pid_file = data_dir / "worker_1.pid"
    pid_file.write_text("99999999")
    (data_dir / "todos.json").write_text(json.dumps([_todo(1, "Running")]))

    with mock.patch("os.killpg", side_effect=OSError("no such process")):
        server._prepare_for_restart()

    todos = json.loads((data_dir / "todos.json").read_text())
    assert todos[0]["status"] == "pending"
    assert not pid_file.exists()


# ── context_limit with another active worker in same project ──────────────────

@pytest.mark.asyncio
async def test_context_limit_queues_pending_when_project_busy(app, seed_todos, read_todos):
    """When context_limit arrives and another worker is already running in the same
    project, the task is set to pending (not re-spawned immediately)."""
    seed_todos([
        _todo(1, "Stalled", status="in_progress", project_id=1, retry_count=0),
        _todo(2, "Running", status="in_progress", project_id=1),
    ])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/status/1", json={"status": "context_limit"})
    assert r.status_code == 200
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "pending"
    assert by_id[1].get("progress") is None
    mock_spawn.assert_not_called()


# ── cancel with existing pid file ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_kills_process_via_pid_file(app, seed_todos, read_todos, data_dir, monkeypatch):
    """When a pid file exists for the todo, cancel reads it and calls os.killpg."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([_todo(1, "Running task", status="in_progress")])

    pid_file = data_dir / "worker_1.pid"
    pid_file.write_text("12345")

    with mock.patch("os.killpg") as mock_kill:
        with mock.patch("os.getpgid", return_value=12345):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post("/api/cancel/1")

    assert r.status_code == 200
    mock_kill.assert_called_once_with(12345, mock.ANY)
    assert not pid_file.exists()
    todos = read_todos()
    assert todos[0]["status"] == "canceled"


@pytest.mark.asyncio
async def test_cancel_handles_dead_process_in_pid_file(app, seed_todos, read_todos, data_dir, monkeypatch):
    """If the process in the pid file is already dead, cancel still completes cleanly."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([_todo(1, "Running task", status="in_progress")])

    pid_file = data_dir / "worker_1.pid"
    pid_file.write_text("99999999")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/cancel/1")

    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["status"] == "canceled"


@pytest.mark.asyncio
async def test_cancel_handles_git_reset_exception(app, seed_todos, read_todos, monkeypatch):
    """If git reset raises an exception, cancel still completes cleanly."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([_todo(1, "Running task", status="in_progress")])

    with mock.patch("subprocess.run", side_effect=Exception("git not available")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/cancel/1")

    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["status"] == "canceled"


# ── answer_question: edge cases ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_question_todo_not_found(app, seed_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/questions/999/answer", json={"answer": "yes"})
    assert r.status_code == 404
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_answer_question_no_pending_question(app, seed_todos):
    """All questions already answered (idx >= len): returns 409."""
    seed_todos([_todo(1, "task", status="waiting", questions=[
        {"question": "Q?", "options": [], "answer": "done"},
    ], question_idx=1)])  # idx already past the end
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/questions/1/answer", json={"answer": "extra"})
    assert r.status_code == 409
    assert r.json()["ok"] is False


# ── storage: sessions and plugin persistence ──────────────────────────────────

def test_load_sessions_returns_data_when_file_exists(data_dir, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(data_dir))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)

    sessions = {"proj_1": "session-abc"}
    (data_dir / "sessions.json").write_text(json.dumps(sessions))
    result = storage.load_sessions()
    assert result == sessions


def test_save_sessions_persists_to_file(data_dir, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(data_dir))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)

    sessions = {"proj_2": "session-xyz"}
    storage.save_sessions(sessions)
    written = json.loads((data_dir / "sessions.json").read_text())
    assert written == sessions


def test_save_plugins_persists_to_file(data_dir, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(data_dir))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)

    plugins = {"my_plugin": {"name": "My Plugin", "command": "echo hi"}}
    storage.save_plugins(plugins)
    written = json.loads((data_dir / "plugins.json").read_text())
    assert written == plugins


def test_save_plugin_states_persists_to_file(data_dir, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(data_dir))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)

    states = {"my_plugin": {"status": "done", "last_run_at": 12345}}
    storage.save_plugin_states(states)
    written = json.loads((data_dir / "plugin_states.json").read_text())
    assert written == states


def test_load_projects_without_projects_dir(tmp_path, monkeypatch):
    """When PROJECTS_DIR doesn't exist, load_projects returns stored list."""
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TODO_BOARD_PROJECTS_DIR", str(tmp_path / "nonexistent_projects"))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)

    projects = [{"id": 1, "name": "General"}]
    (tmp_path / "projects.json").write_text(json.dumps(projects))
    result = storage.load_projects()
    assert result == projects
