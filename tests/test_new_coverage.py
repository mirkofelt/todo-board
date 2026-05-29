"""Tests targeting coverage gaps in storage, plugin_runner, heartbeat, spawner, and server."""
import importlib
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport
from unittest import mock


def _todo(id, text, status="pending", project_id=1, **kwargs):
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


def _reload_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TODO_BOARD_PROJECTS_DIR", str(tmp_path / "projects"))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)
    return storage


def _run_recovery(data_dir, seed, monkeypatch, seed_news=None):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    (data_dir / "todos.json").write_text(json.dumps(seed))
    if seed_news is not None:
        (data_dir / "news.json").write_text(json.dumps(seed_news))

    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    server._recover_orphaned_todos()

    todos = json.loads((data_dir / "todos.json").read_text())
    return todos, spawned


# ── storage: load_sessions when file missing ──────────────────────────────────

def test_load_sessions_returns_empty_when_file_missing(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    result = storage.load_sessions()
    assert result == {}


# ── storage: load/save github_links ──────────────────────────────────────────

def test_load_github_links_returns_empty_when_file_missing(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    result = storage.load_github_links()
    assert result == {}


def test_load_github_links_reads_file_when_exists(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    links = {"my-repo": "owner/my-repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    result = storage.load_github_links()
    assert result == links


def test_save_and_load_github_links_roundtrip(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    links = {"todo-board": "user/todo-board", "sensor": "user/sensor"}
    storage.save_github_links(links)
    result = storage.load_github_links()
    assert result == links


# ── storage: load/save plugins ────────────────────────────────────────────────

def test_load_plugins_returns_empty_when_file_missing(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    result = storage.load_plugins()
    assert result == {}


def test_load_plugins_reads_file_when_exists(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    plugins = {"myplugin": {"name": "My Plugin", "command": ["echo"]}}
    (tmp_path / "plugins.json").write_text(json.dumps(plugins))
    result = storage.load_plugins()
    assert result == plugins


# ── storage: load/save crypto_state ──────────────────────────────────────────

def test_load_crypto_state_returns_empty_when_file_missing(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    result = storage.load_crypto_state()
    assert result == {}


def test_load_crypto_state_reads_file_when_exists(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    state = {"symbol": "BTC-USD", "price": 50000.0, "last_updated": 12345}
    (tmp_path / "crypto_state.json").write_text(json.dumps(state))
    result = storage.load_crypto_state()
    assert result == state


def test_save_and_load_crypto_state_roundtrip(tmp_path, monkeypatch):
    storage = _reload_storage(tmp_path, monkeypatch)
    state = {"symbol": "ETH-USD", "price": 3000.0, "error": None}
    storage.save_crypto_state(state)
    result = storage.load_crypto_state()
    assert result == state


# ── plugin_runner: exception path ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_plugin_handles_subprocess_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.plugin_runner as runner

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(runner)

    with mock.patch("asyncio.create_subprocess_exec", side_effect=OSError("exec failed")):
        await runner.run_plugin("bad", {"name": "Bad", "path": str(tmp_path), "command": ["bad-cmd"]})

    states = storage.load_plugin_states()
    assert states["bad"]["status"] == "failed"
    assert "exec failed" in states["bad"]["result"]


@pytest.mark.asyncio
async def test_run_plugin_exception_removes_from_running(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.plugin_runner as runner

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(runner)

    with mock.patch("asyncio.create_subprocess_exec", side_effect=Exception("fail")):
        await runner.run_plugin("p", {"name": "P", "path": ".", "command": []})

    assert not runner.is_running("p")


# ── spawner: project_has_active_worker with None project_id ──────────────────

def test_project_has_active_worker_none_project_id():
    import importlib
    import todo_board.spawner as spawner
    importlib.reload(spawner)
    todos = [{"id": 1, "project_id": None, "status": "in_progress"}]
    assert spawner.project_has_active_worker(None, todos) is False


# ── heartbeat: session_limit_ready block ──────────────────────────────────────

def test_heartbeat_main_resets_session_limit_ready(data_dir, monkeypatch):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    # Use an old created timestamp so the task is also eligible for spawning.
    old_ts = int(time.time()) - 3600
    past_reset = int(time.time()) - 100
    seed = [_todo(1, "Interrupted", status="session_limit", project_id=1,
                  created=old_ts, session_limit_reset_at=past_reset)]
    (data_dir / "todos.json").write_text(json.dumps(seed))

    spawned = []
    monkeypatch.setattr("todo_board.heartbeat.spawn_worker", lambda tid: spawned.append(tid))
    monkeypatch.setattr("todo_board.heartbeat.project_has_active_worker", lambda pid, todos: False)
    hb.main()

    todos = json.loads((data_dir / "todos.json").read_text())
    assert todos[0]["status"] == "in_progress"
    assert 1 in spawned


def test_heartbeat_main_skips_session_limit_not_yet_ready(data_dir, monkeypatch):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    future = int(time.time()) + 3600
    seed = [_todo(1, "Interrupted", status="session_limit", project_id=1,
                  session_limit_reset_at=future)]
    (data_dir / "todos.json").write_text(json.dumps(seed))

    spawned = []
    monkeypatch.setattr("todo_board.heartbeat.spawn_worker", lambda tid: spawned.append(tid))
    monkeypatch.setattr("todo_board.heartbeat.project_has_active_worker", lambda pid, todos: False)
    hb.main()

    todos = json.loads((data_dir / "todos.json").read_text())
    assert todos[0]["status"] == "session_limit"
    assert spawned == []


# ── heartbeat: pending todo with no project_id (pid is None) ─────────────────

def test_heartbeat_main_spawns_todo_with_no_project(data_dir, monkeypatch):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    old_ts = int(time.time()) - 60
    seed = [_todo(1, "No-project task", status="pending", project_id=None, created=old_ts)]
    (data_dir / "todos.json").write_text(json.dumps(seed))

    spawned = []
    monkeypatch.setattr("todo_board.heartbeat.spawn_worker", lambda tid: spawned.append(tid))
    monkeypatch.setattr("todo_board.heartbeat.project_has_active_worker", lambda pid, todos: False)
    hb.main()

    assert 1 in spawned


# ── server: _recover_orphaned_todos: orphaned subtask cancelation ─────────────

def test_recover_cancels_pending_subtasks_of_deleted_parents(data_dir, monkeypatch):
    """Pending sub-tasks whose parent no longer exists are canceled at startup."""
    seed = [
        _todo(2, "Orphan sub-task", status="pending", parent_id=999),
    ]
    todos, _ = _run_recovery(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "canceled"


# ── server: _recover_orphaned_todos: planned task auto-complete ───────────────

def test_recover_completes_planned_task_when_all_subs_done(data_dir, monkeypatch):
    """A 'planned' parent whose all sub-tasks have terminated is auto-completed."""
    seed = [
        _todo(1, "Plan", status="planned", project_id=1),
        _todo(2, "Sub A", status="done", done=True, parent_id=1, project_id=1),
        _todo(3, "Sub B", status="failed", parent_id=1, project_id=1),
    ]
    todos, _ = _run_recovery(data_dir, seed, monkeypatch)
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "done"
    assert by_id[1]["done"] is True


def test_recover_does_not_complete_planned_task_with_active_subs(data_dir, monkeypatch):
    """A 'planned' parent with a pending sub-task should not be auto-completed."""
    seed = [
        _todo(1, "Plan", status="planned", project_id=1),
        _todo(2, "Sub A", status="done", done=True, parent_id=1, project_id=1),
        _todo(3, "Sub B", status="pending", parent_id=1, project_id=1),
    ]
    todos, _ = _run_recovery(data_dir, seed, monkeypatch)
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "planned"


# ── server: _recover_orphaned_todos: prev_task_id dependency ─────────────────

def test_recover_does_not_spawn_when_prev_task_not_done(data_dir, monkeypatch):
    """A pending todo with an unfinished prev_task_id must not be spawned."""
    seed = [
        _todo(1, "Blocker", status="pending", project_id=1),
        _todo(2, "Blocked", status="pending", project_id=1, prev_task_id=1),
    ]
    todos, spawned = _run_recovery(data_dir, seed, monkeypatch)
    by_id = {t["id"]: t for t in todos}
    assert 2 not in spawned
    assert by_id[2]["status"] == "pending"


# ── server: add_todo with model field ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_todo_stores_model_field(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "Task with model", "project_id": 1, "model": "claude-opus-4-7"})
    assert r.json()["ok"] is True
    todos = read_todos()
    assert todos[0].get("model") == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_add_todo_no_model_field_when_empty(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "Task without model", "project_id": 1, "model": ""})
    assert r.json()["ok"] is True
    todos = read_todos()
    assert "model" not in todos[0]


# ── server: set_status with session_limit_reset_at ───────────────────────────

@pytest.mark.asyncio
async def test_set_status_stores_session_limit_reset_at(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([_todo(1, "Task", status="in_progress")])
    future = int(time.time()) + 3600
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/status/1", json={"status": "session_limit", "session_limit_reset_at": future})
    assert r.json()["ok"] is True
    todos = read_todos()
    assert todos[0]["status"] == "session_limit"
    assert todos[0]["session_limit_reset_at"] == future


# ── server: parent auto-complete when all sub-tasks finish ───────────────────

@pytest.mark.asyncio
async def test_status_done_autocompletes_parent_when_all_subs_done(app, seed_todos, read_todos, monkeypatch):
    """Marking the last pending sub-task done should auto-complete its planned parent."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([
        _todo(1, "Parent Plan", status="planned", project_id=1),
        _todo(2, "Sub A", status="done", done=True, parent_id=1, project_id=1),
        _todo(3, "Sub B", status="in_progress", parent_id=1, project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/status/3", json={"status": "done"})
    assert r.json()["ok"] is True
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "done"
    assert by_id[1]["done"] is True


@pytest.mark.asyncio
async def test_status_failed_autocompletes_parent_when_all_subs_terminal(app, seed_todos, read_todos, monkeypatch):
    """When the last sub fails, the planned parent should still be auto-completed."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([
        _todo(1, "Parent Plan", status="planned", project_id=1),
        _todo(2, "Sub A", status="done", done=True, parent_id=1, project_id=1),
        _todo(3, "Sub B", status="in_progress", parent_id=1, project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/status/3", json={"status": "failed"})
    assert r.json()["ok"] is True
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "done"


@pytest.mark.asyncio
async def test_status_done_does_not_autocomplete_parent_with_active_subs(app, seed_todos, read_todos, monkeypatch):
    """Parent stays 'planned' while another sub-task is still running."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([
        _todo(1, "Parent Plan", status="planned", project_id=1),
        _todo(2, "Sub A", status="in_progress", parent_id=1, project_id=1),
        _todo(3, "Sub B", status="in_progress", parent_id=1, project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/status/2", json={"status": "done"})
    assert r.json()["ok"] is True
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "planned"


# ── server: answer_question when project has active worker ───────────────────

@pytest.mark.asyncio
async def test_answer_question_queues_pending_when_project_busy(app, seed_todos, read_todos, monkeypatch):
    """When all questions are answered but another worker is running, go to pending."""
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([
        _todo(1, "Waiting task", status="waiting", project_id=1,
              questions=[{"question": "Q?", "options": [], "answer": None}],
              question_idx=0),
        _todo(2, "Active task", status="in_progress", project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/questions/1/answer", json={"answer": "yes"})
    assert r.json()["ok"] is True
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "pending"
