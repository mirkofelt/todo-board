"""Tests for shutdown recovery: in_progress todos are SIGTERMed and reset to pending."""
import json
import os
import time
import pytest


def _todo(id, text, status="in_progress", project_id=1, progress=None, **kwargs):
    t = {
        "id": id,
        "text": text,
        "done": False,
        "status": status,
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
        "progress": progress,
    }
    t.update(kwargs)
    return t


def _run_shutdown(data_dir, seed, monkeypatch, killed=None):
    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    (data_dir / "todos.json").write_text(json.dumps(seed))

    if killed is None:
        killed = []
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr("os.getpgid", lambda pid: pid)
    server._prepare_for_restart()

    todos = json.loads((data_dir / "todos.json").read_text())
    return todos, killed


def test_in_progress_reset_to_pending(data_dir, monkeypatch):
    todos, _ = _run_shutdown(
        data_dir,
        [_todo(1, "Running", status="in_progress")],
        monkeypatch,
    )
    assert todos[0]["status"] == "pending"
    assert todos[0]["progress"] is None


def test_in_progress_with_pid_file_sigtermed(data_dir, monkeypatch):
    pid_file = data_dir / "worker_1.pid"
    pid_file.write_text("12345")

    todos, killed = _run_shutdown(
        data_dir,
        [_todo(1, "Running")],
        monkeypatch,
    )
    assert todos[0]["status"] == "pending"
    assert any(sig == 15 for _, sig in killed)  # SIGTERM == 15
    assert not pid_file.exists()


def test_stale_pid_file_removed(data_dir, monkeypatch):
    pid_file = data_dir / "worker_5.pid"
    pid_file.write_text("99999")

    todos, _ = _run_shutdown(
        data_dir,
        [_todo(5, "Task")],
        monkeypatch,
    )
    assert todos[0]["status"] == "pending"
    assert not pid_file.exists()


def test_pending_todo_not_touched(data_dir, monkeypatch):
    todos, killed = _run_shutdown(
        data_dir,
        [_todo(1, "Waiting", status="pending")],
        monkeypatch,
    )
    assert todos[0]["status"] == "pending"
    assert killed == []


def test_done_todo_not_touched(data_dir, monkeypatch):
    todos, killed = _run_shutdown(
        data_dir,
        [_todo(1, "Done", status="done", done=True)],
        monkeypatch,
    )
    assert todos[0]["status"] == "done"
    assert killed == []


def test_waiting_todo_not_touched(data_dir, monkeypatch):
    todos, killed = _run_shutdown(
        data_dir,
        [_todo(1, "Blocked", status="waiting")],
        monkeypatch,
    )
    assert todos[0]["status"] == "waiting"
    assert killed == []


def test_multiple_in_progress_all_reset(data_dir, monkeypatch):
    todos, _ = _run_shutdown(
        data_dir,
        [
            _todo(1, "First", status="in_progress", project_id=1),
            _todo(2, "Second", status="in_progress", project_id=2),
        ],
        monkeypatch,
    )
    assert all(t["status"] == "pending" for t in todos)


def test_progress_cleared(data_dir, monkeypatch):
    todos, _ = _run_shutdown(
        data_dir,
        [_todo(1, "Task", progress="Working on step 3...")],
        monkeypatch,
    )
    assert todos[0]["progress"] is None


def test_no_file_write_when_nothing_in_progress(data_dir, monkeypatch):
    seed = [_todo(1, "Done", status="done", done=True)]
    (data_dir / "todos.json").write_text(json.dumps(seed))

    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    mtime_before = (data_dir / "todos.json").stat().st_mtime
    monkeypatch.setattr("os.killpg", lambda pgid, sig: None)
    monkeypatch.setattr("os.getpgid", lambda pid: pid)
    server._prepare_for_restart()
    mtime_after = (data_dir / "todos.json").stat().st_mtime

    assert mtime_before == mtime_after
