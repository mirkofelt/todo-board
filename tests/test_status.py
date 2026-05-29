"""Tests for /api/status: status updates, auto-advance, token/duration storage."""
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


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


@pytest.mark.asyncio
async def test_status_done_marks_done_flag(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/status/1", json={"status": "done"})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["done"] is True
    assert todos[0]["status"] == "done"


@pytest.mark.asyncio
async def test_status_failed_sets_status(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/status/1", json={"status": "failed"})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["status"] == "failed"
    assert todos[0]["done"] is False


@pytest.mark.asyncio
async def test_status_stores_tokens(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    tokens = {"input": 100, "output": 50, "cache_creation": 200, "cache_read": 300}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done", "tokens": tokens})
    todos = read_todos()
    assert todos[0]["tokens"] == tokens


@pytest.mark.asyncio
async def test_status_stores_duration(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done", "duration_secs": 42})
    todos = read_todos()
    assert todos[0]["duration_secs"] == 42


@pytest.mark.asyncio
async def test_status_stores_result_on_done(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done", "result": "All done!"})
    todos = read_todos()
    assert todos[0]["result"] == "All done!"


@pytest.mark.asyncio
async def test_status_result_not_stored_on_non_done(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "failed", "result": "Should be ignored"})
    todos = read_todos()
    assert "result" not in todos[0]


@pytest.mark.asyncio
async def test_status_done_advances_next_pending(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Done task", status="in_progress", project_id=1),
        _todo(2, "Next pending", status="pending", project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done"})
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "in_progress"
    assert 2 in spawned


@pytest.mark.asyncio
async def test_status_failed_advances_next_pending(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Failed task", status="in_progress", project_id=1),
        _todo(2, "Waiting", status="pending", project_id=1),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "failed"})
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "in_progress"
    assert 2 in spawned


@pytest.mark.asyncio
async def test_status_done_clears_progress(app, seed_todos, read_todos):
    t = _todo(1, "Task", progress="Working…")
    seed_todos([t])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done"})
    todos = read_todos()
    assert todos[0].get("progress") is None


@pytest.mark.asyncio
async def test_status_done_different_project_not_advanced(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([
        _todo(1, "Done in P1", status="in_progress", project_id=1),
        _todo(2, "Pending in P2", status="pending", project_id=2),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done"})
    todos = read_todos()
    by_id = {t["id"]: t for t in todos}
    assert by_id[2]["status"] == "pending"
    assert spawned == []


@pytest.mark.asyncio
async def test_status_result_truncated_to_3000_chars(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    long_result = "x" * 5000
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/status/1", json={"status": "done", "result": long_result})
    todos = read_todos()
    assert len(todos[0]["result"]) == 3000
