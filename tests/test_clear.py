"""Tests for /api/delete-done: clear removes done and canceled tasks."""
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="pending", done=None):
    return {
        "id": id,
        "text": text,
        "done": (status == "done") if done is None else done,
        "status": status,
        "created": int(time.time()),
        "project_id": 1,
        "note": None,
        "status_updated_at": int(time.time()),
    }


@pytest.mark.asyncio
async def test_clear_removes_done_tasks(app, seed_todos, read_todos):
    seed_todos([
        _todo(1, "Finished task", status="done"),
        _todo(2, "Still pending"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete-done")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    ids = [t["id"] for t in todos]
    assert 1 not in ids
    assert 2 in ids


@pytest.mark.asyncio
async def test_clear_removes_canceled_tasks(app, seed_todos, read_todos):
    seed_todos([
        _todo(1, "Canceled task", status="canceled"),
        _todo(2, "Pending task"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete-done")
    assert r.status_code == 200
    todos = read_todos()
    ids = [t["id"] for t in todos]
    assert 1 not in ids
    assert 2 in ids


@pytest.mark.asyncio
async def test_clear_removes_both_done_and_canceled(app, seed_todos, read_todos):
    seed_todos([
        _todo(1, "Done task", status="done"),
        _todo(2, "Canceled task", status="canceled"),
        _todo(3, "Pending task"),
        _todo(4, "In-progress task", status="in_progress"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete-done")
    assert r.status_code == 200
    todos = read_todos()
    ids = [t["id"] for t in todos]
    assert 1 not in ids
    assert 2 not in ids
    assert 3 in ids
    assert 4 in ids


@pytest.mark.asyncio
async def test_clear_on_empty_list(app, seed_todos, read_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete-done")
    assert r.status_code == 200
    assert read_todos() == []


@pytest.mark.asyncio
async def test_clear_preserves_in_progress(app, seed_todos, read_todos):
    seed_todos([
        _todo(1, "Running", status="in_progress"),
        _todo(2, "Done", status="done"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete-done")
    assert r.status_code == 200
    todos = read_todos()
    ids = [t["id"] for t in todos]
    assert 1 in ids
    assert 2 not in ids
