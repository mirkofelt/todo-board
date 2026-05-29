"""Tests for startup recovery: orphaned in_progress todos are reset and re-queued."""
import time
import pytest


def _todo(id, text, status="in_progress", project_id=1, locked=False, **kwargs):
    t = {
        "id": id,
        "text": text,
        "done": False,
        "status": status,
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
        "locked": locked,
    }
    t.update(kwargs)
    return t


def _run_recovery(data_dir, seed, monkeypatch):
    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    (data_dir / "todos.json").write_text(__import__("json").dumps(seed))

    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    server._recover_orphaned_todos()

    todos = __import__("json").loads((data_dir / "todos.json").read_text())
    return todos, spawned


def test_orphaned_in_progress_reset_to_pending(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [_todo(1, "Orphan", status="in_progress")],
        monkeypatch,
    )
    # No PID file exists → worker is dead → reset to pending, then respawned
    assert todos[0]["status"] == "in_progress"  # re-queued
    assert 1 in spawned


def test_in_progress_with_pid_file_still_reset(data_dir, tmp_path, monkeypatch):
    """PID files are unreliable after a reboot (PIDs get reused), so we reset unconditionally."""
    import os
    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    # Even with a PID file pointing at a live process, the task is reset and respawned
    pid_file = data_dir / "worker_1.pid"
    pid_file.write_text(str(os.getpid()))
    (data_dir / "todos.json").write_text(__import__("json").dumps([_todo(1, "InProgress")]))

    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    server._recover_orphaned_todos()

    todos = __import__("json").loads((data_dir / "todos.json").read_text())
    assert todos[0]["status"] == "in_progress"  # reset to pending, then re-queued
    assert 1 in spawned
    assert not pid_file.exists()


def test_waiting_todos_not_touched(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [_todo(1, "Waiting", status="waiting")],
        monkeypatch,
    )
    assert todos[0]["status"] == "waiting"
    assert spawned == []


def test_done_todos_not_touched(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [_todo(1, "Done", status="done", done=True)],
        monkeypatch,
    )
    assert todos[0]["status"] == "done"
    assert spawned == []


def test_pending_todo_spawned_when_no_active_worker(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [_todo(1, "Pending", status="pending")],
        monkeypatch,
    )
    assert todos[0]["status"] == "in_progress"
    assert 1 in spawned


def test_locked_pending_not_spawned(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [_todo(1, "Locked", status="pending", locked=True)],
        monkeypatch,
    )
    assert todos[0]["status"] == "pending"
    assert spawned == []


def test_one_worker_per_project(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [
            _todo(1, "First", status="pending", project_id=1),
            _todo(2, "Second", status="pending", project_id=1),
        ],
        monkeypatch,
    )
    by_id = {t["id"]: t for t in todos}
    # Only one should be in_progress; the other stays pending
    in_progress = [t for t in todos if t["status"] == "in_progress"]
    pending = [t for t in todos if t["status"] == "pending"]
    assert len(in_progress) == 1
    assert len(pending) == 1
    assert len(spawned) == 1


def test_separate_projects_each_get_worker(data_dir, monkeypatch):
    todos, spawned = _run_recovery(
        data_dir,
        [
            _todo(1, "P1 task", status="pending", project_id=1),
            _todo(2, "P2 task", status="pending", project_id=2),
        ],
        monkeypatch,
    )
    by_id = {t["id"]: t for t in todos}
    assert by_id[1]["status"] == "in_progress"
    assert by_id[2]["status"] == "in_progress"
    assert set(spawned) == {1, 2}
