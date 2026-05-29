"""Tests for /api/lock: locking and unlocking todos."""
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


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


@pytest.mark.asyncio
async def test_lock_pending_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": True})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    assert todos[0]["locked"] is True


@pytest.mark.asyncio
async def test_lock_default_is_true(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["locked"] is True


@pytest.mark.asyncio
async def test_unlock_pending_todo_no_active_worker_spawns(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task", locked=True)])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/lock/1", json={"locked": False})
    assert r.status_code == 200
    mock_spawn.assert_called_once_with(1)
    todos = read_todos()
    assert todos[0]["locked"] is False
    assert todos[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_unlock_pending_todo_with_active_worker_stays_pending(app, seed_todos, read_todos):
    seed_todos([
        _todo(1, "Active", status="in_progress", project_id=5),
        _todo(2, "Locked", status="pending", project_id=5, locked=True),
    ])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/lock/2", json={"locked": False})
    assert r.status_code == 200
    mock_spawn.assert_not_called()
    todos = read_todos()
    unlocked = next(t for t in todos if t["id"] == 2)
    assert unlocked["locked"] is False
    assert unlocked["status"] == "pending"


@pytest.mark.asyncio
async def test_cannot_lock_in_progress_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Running", status="in_progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/lock/1", json={"locked": True})
    assert r.status_code == 409
    assert r.json()["ok"] is False
    todos = read_todos()
    assert not todos[0].get("locked")


@pytest.mark.asyncio
async def test_unlock_non_pending_does_not_spawn(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Done task", status="done", locked=True)])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/lock/1", json={"locked": False})
    mock_spawn.assert_not_called()
