"""Tests for inline task editing: lock, edit, cancel-edit."""
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="pending", locked=False, project_id=1):
    return {
        "id": id,
        "text": text,
        "done": status == "done",
        "status": status,
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
        "locked": locked,
    }


@pytest.mark.asyncio
async def test_lock_pending_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Write tests")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": True})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    assert todos[0]["locked"] is True


@pytest.mark.asyncio
async def test_unlock_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Write tests", locked=True)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": False})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["locked"] is False


@pytest.mark.asyncio
async def test_cannot_lock_in_progress_todo(app, seed_todos):
    seed_todos([_todo(1, "Running task", status="in_progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": True})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_edit_updates_text_and_unlocks(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Old text", locked=True)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/edit/1", json={"text": "New text"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    assert todos[0]["text"] == "New text"
    assert not todos[0].get("locked")


@pytest.mark.asyncio
async def test_edit_rejects_empty_text(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Original")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/edit/1", json={"text": "  "})
    assert r.status_code == 400
    todos = read_todos()
    assert todos[0]["text"] == "Original"


@pytest.mark.asyncio
async def test_cannot_edit_in_progress_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Running task", status="in_progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/edit/1", json={"text": "Changed"})
    assert r.status_code == 409
    todos = read_todos()
    assert todos[0]["text"] == "Running task"


@pytest.mark.asyncio
async def test_locked_todo_skipped_by_auto_advance(app, seed_todos, read_todos):
    """When a task finishes, locked pending tasks must not be auto-started."""
    seed_todos([
        _todo(1, "Done task", status="done"),
        _todo(2, "Locked pending", status="pending", locked=True),
        _todo(3, "Unlocked pending", status="pending", locked=False),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Mark todo 1 as done — triggers auto-advance check
        r = await client.post("/api/status/1", json={"status": "done"})
    assert r.status_code == 200
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    # Locked task must remain pending
    assert by_id[2]["status"] == "pending"
    # The unlocked pending task should be picked up
    assert by_id[3]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_edit_whitespace_trimmed(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Original")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/edit/1", json={"text": "  Trimmed  "})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["text"] == "Trimmed"


@pytest.mark.asyncio
async def test_todos_endpoint_exposes_locked_field(app, seed_todos):
    seed_todos([_todo(1, "Check me", locked=True)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/todos")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["locked"] is True


@pytest.mark.asyncio
async def test_unlock_pending_spawns_worker(app, seed_todos, read_todos, monkeypatch):
    """Unlocking a pending todo immediately sets it in_progress when no worker is active."""
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([_todo(1, "Paused task", status="pending", locked=True)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": False})
    assert r.status_code == 200
    assert spawned == [1]
    todos = read_todos()
    assert todos[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_unlock_pending_no_spawn_if_worker_active(app, seed_todos, read_todos, monkeypatch):
    """Unlocking a pending todo does not spawn when another task is already in_progress."""
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Running task", status="in_progress", project_id=1),
        _todo(2, "Paused task", status="pending", locked=True, project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/2", json={"locked": False})
    assert r.status_code == 200
    assert spawned == []
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "pending"


@pytest.mark.asyncio
async def test_lock_does_not_spawn(app, seed_todos, read_todos, monkeypatch):
    """Locking a pending todo never spawns a worker."""
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([_todo(1, "Free task", status="pending", locked=False)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": True})
    assert r.status_code == 200
    assert spawned == []
