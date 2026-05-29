"""Tests for /api/cancel: canceling todos and auto-advancing the queue."""
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="in_progress", project_id=1, locked=False):
    return {
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


@pytest.mark.asyncio
async def test_cancel_sets_status_canceled(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Running task")])
    with mock.patch("todo_board.server.spawn_worker"):
        with mock.patch("os.killpg"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post("/api/cancel/1")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    t = todos[0]
    assert t["status"] == "canceled"
    assert t["done"] is False
    assert t.get("progress") is None


@pytest.mark.asyncio
async def test_cancel_advances_next_pending(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Running task", status="in_progress", project_id=1),
        _todo(2, "Pending task", status="pending", project_id=1),
    ])
    with mock.patch("os.killpg"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/cancel/1")
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "in_progress"
    assert 2 in spawned


@pytest.mark.asyncio
async def test_cancel_skips_locked_pending(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Running", status="in_progress", project_id=1),
        _todo(2, "Locked", status="pending", project_id=1, locked=True),
        _todo(3, "Free", status="pending", project_id=1, locked=False),
    ])
    with mock.patch("os.killpg"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/cancel/1")
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "pending"
    assert by_id[3]["status"] == "in_progress"
    assert 3 in spawned


@pytest.mark.asyncio
async def test_cancel_no_next_pending_does_not_spawn(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([_todo(1, "Running task", status="in_progress", project_id=1)])
    with mock.patch("os.killpg"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/cancel/1")
    assert spawned == []


@pytest.mark.asyncio
async def test_cancel_different_project_not_advanced(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Running in project 1", status="in_progress", project_id=1),
        _todo(2, "Pending in project 2", status="pending", project_id=2),
    ])
    with mock.patch("os.killpg"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/cancel/1")
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "pending"
    assert spawned == []


@pytest.mark.asyncio
async def test_cancel_pending_todo(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([_todo(1, "Waiting task", status="pending")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/cancel/1")
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["status"] == "canceled"
