"""Tests for heartbeat: stall detection and main retry/spawn logic."""
import importlib
import json
import time
import pytest


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
        "retry_count": 0,
    }
    t.update(kwargs)
    return t


def _run_detect(data_dir, seed, monkeypatch, fake_now=None):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    (data_dir / "todos.json").write_text(json.dumps(seed))

    if fake_now is not None:
        monkeypatch.setattr("todo_board.heartbeat.time", _FakeTime(fake_now))

    return hb.detect_and_fix_stalled()


def _run_main(data_dir, seed, monkeypatch, fake_now=None):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    (data_dir / "todos.json").write_text(json.dumps(seed))

    spawned = []
    monkeypatch.setattr("todo_board.heartbeat.spawn_worker", lambda tid: spawned.append(tid))
    monkeypatch.setattr("todo_board.heartbeat.project_has_active_worker", lambda pid, todos: False)

    if fake_now is not None:
        monkeypatch.setattr("todo_board.heartbeat.time", _FakeTime(fake_now))

    hb.main()

    todos = json.loads((data_dir / "todos.json").read_text())
    return todos, spawned


class _FakeTime:
    def __init__(self, now):
        self._now = now

    def time(self):
        return float(self._now)


THRESHOLD = 25 * 60  # must match config


# ── detect_and_fix_stalled ────────────────────────────────────────────────────

def test_stalled_in_progress_becomes_context_limit(data_dir, monkeypatch):
    old_ts = int(time.time()) - THRESHOLD - 60
    seed = [_todo(1, "Task", status="in_progress", status_updated_at=old_ts)]
    todos = _run_detect(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "context_limit"


def test_fresh_in_progress_unchanged(data_dir, monkeypatch):
    seed = [_todo(1, "Task", status="in_progress")]
    todos = _run_detect(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "in_progress"


def test_pending_todo_not_marked_stalled(data_dir, monkeypatch):
    old_ts = int(time.time()) - THRESHOLD - 60
    seed = [_todo(1, "Task", status="pending", status_updated_at=old_ts)]
    todos = _run_detect(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "pending"


def test_done_todo_not_marked_stalled(data_dir, monkeypatch):
    old_ts = int(time.time()) - THRESHOLD - 60
    seed = [_todo(1, "Task", status="done", done=True, status_updated_at=old_ts)]
    todos = _run_detect(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "done"


def test_stalled_uses_created_when_no_status_updated_at(data_dir, monkeypatch):
    old_ts = int(time.time()) - THRESHOLD - 60
    seed = [_todo(1, "Task", status="in_progress", created=old_ts, status_updated_at=old_ts)]
    del seed[0]["status_updated_at"]
    todos = _run_detect(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "context_limit"


def test_detect_returns_all_todos(data_dir, monkeypatch):
    seed = [
        _todo(1, "A", status="in_progress"),
        _todo(2, "B", status="pending"),
    ]
    todos = _run_detect(data_dir, seed, monkeypatch)
    assert len(todos) == 2


def test_no_change_when_nothing_stalled(data_dir, monkeypatch):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    seed = [_todo(1, "Task", status="in_progress")]
    (data_dir / "todos.json").write_text(json.dumps(seed))

    saves = []
    monkeypatch.setattr("todo_board.heartbeat.save_todos", lambda t: saves.append(t))
    hb.detect_and_fix_stalled()
    assert saves == []


# ── main: context_limit retry logic ──────────────────────────────────────────

def test_context_limit_below_max_retries_becomes_pending(data_dir, monkeypatch):
    seed = [_todo(1, "Task", status="context_limit", retry_count=0)]
    todos, _ = _run_main(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "in_progress"  # was reset to pending, then spawned
    assert todos[0]["retry_count"] == 1


def test_context_limit_at_max_retries_becomes_failed(data_dir, monkeypatch):
    seed = [_todo(1, "Task", status="context_limit", retry_count=2)]
    todos, spawned = _run_main(data_dir, seed, monkeypatch)
    assert todos[0]["status"] == "failed"
    assert "Exceeded max retries" in todos[0]["note"]
    assert spawned == []


def test_context_limit_retry_count_incremented(data_dir, monkeypatch):
    seed = [_todo(1, "Task", status="context_limit", retry_count=1)]
    todos, _ = _run_main(data_dir, seed, monkeypatch)
    assert todos[0]["retry_count"] == 2


# ── main: pending spawn logic ─────────────────────────────────────────────────

def test_old_pending_todo_spawns_worker(data_dir, monkeypatch):
    old_ts = int(time.time()) - 60
    seed = [_todo(1, "Task", status="pending", created=old_ts)]
    _, spawned = _run_main(data_dir, seed, monkeypatch)
    assert 1 in spawned


def test_fresh_pending_todo_not_spawned(data_dir, monkeypatch):
    seed = [_todo(1, "Task", status="pending")]
    _, spawned = _run_main(data_dir, seed, monkeypatch)
    assert spawned == []


def test_retry_todo_spawned_regardless_of_age(data_dir, monkeypatch):
    # A context_limit todo reset to pending → is a retry → spawned even if brand-new
    seed = [_todo(1, "Task", status="context_limit", retry_count=0)]
    _, spawned = _run_main(data_dir, seed, monkeypatch)
    assert 1 in spawned


def test_done_todos_ignored_by_main(data_dir, monkeypatch):
    seed = [_todo(1, "Task", status="done", done=True)]
    todos, spawned = _run_main(data_dir, seed, monkeypatch)
    assert spawned == []


def test_two_pending_same_project_only_one_spawned(data_dir, monkeypatch):
    old_ts = int(time.time()) - 60
    seed = [
        _todo(1, "First", status="pending", created=old_ts - 10, project_id=1),
        _todo(2, "Second", status="pending", created=old_ts, project_id=1),
    ]
    _, spawned = _run_main(data_dir, seed, monkeypatch)
    assert len(spawned) == 1


def test_two_pending_different_projects_both_spawned(data_dir, monkeypatch):
    old_ts = int(time.time()) - 60
    seed = [
        _todo(1, "Task A", status="pending", created=old_ts, project_id=1),
        _todo(2, "Task B", status="pending", created=old_ts, project_id=2),
    ]
    _, spawned = _run_main(data_dir, seed, monkeypatch)
    assert set(spawned) == {1, 2}


def test_project_with_active_worker_not_spawned(data_dir, monkeypatch):
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.heartbeat as hb

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(hb)

    old_ts = int(time.time()) - 60
    seed = [_todo(1, "Task", status="pending", created=old_ts, project_id=42)]
    (data_dir / "todos.json").write_text(json.dumps(seed))

    spawned = []
    monkeypatch.setattr("todo_board.heartbeat.spawn_worker", lambda tid: spawned.append(tid))
    monkeypatch.setattr("todo_board.heartbeat.project_has_active_worker", lambda pid, todos: True)

    hb.main()
    assert spawned == []


def test_no_pending_todos_prints_and_exits(data_dir, monkeypatch, capsys):
    seed = [_todo(1, "Task", status="done", done=True)]
    _run_main(data_dir, seed, monkeypatch)
    out = capsys.readouterr().out
    assert "No pending todos" in out


def test_empty_todos_prints_and_exits(data_dir, monkeypatch, capsys):
    _run_main(data_dir, [], monkeypatch)
    out = capsys.readouterr().out
    assert "No pending todos" in out
