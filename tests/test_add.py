"""Tests for /api/add: creating new todos."""
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="pending", project_id=1):
    return {
        "id": id,
        "text": text,
        "done": False,
        "status": status,
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
    }


@pytest.mark.asyncio
async def test_add_creates_todo(app, seed_todos, read_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "Do something", "project_id": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["id"], int)
    todos = read_todos()
    assert len(todos) == 1
    assert todos[0]["text"] == "Do something"
    assert todos[0]["project_id"] == 1


@pytest.mark.asyncio
async def test_add_empty_text_rejected(app, seed_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "", "project_id": 1})
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_add_whitespace_text_rejected(app, seed_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "   ", "project_id": 1})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_add_spawns_worker_when_no_active(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "First task", "project_id": 1})
    assert r.status_code == 200
    assert len(spawned) == 1
    todos = read_todos()
    assert todos[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_add_queues_pending_when_project_has_active_worker(app, seed_todos, read_todos, monkeypatch):
    spawned = []
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: spawned.append(tid))
    seed_todos([_todo(1, "Running task", status="in_progress", project_id=1)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/add", json={"text": "Second task", "project_id": 1})
    assert r.status_code == 200
    assert spawned == []
    todos = read_todos()
    new_todo = next(t for t in todos if t["text"] == "Second task")
    assert new_todo["status"] == "pending"


@pytest.mark.asyncio
async def test_add_stores_model(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/add", json={"text": "Task", "project_id": 1, "model": "opus"})
    todos = read_todos()
    assert todos[0].get("model") == "opus"


@pytest.mark.asyncio
async def test_add_without_model_omits_field(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/add", json={"text": "Task", "project_id": 1})
    todos = read_todos()
    assert "model" not in todos[0]


@pytest.mark.asyncio
async def test_add_stores_prev_task_id(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([_todo(5, "Parent", status="done")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/add", json={"text": "Child", "project_id": 1, "prev_task_id": 5})
    todos = read_todos()
    new_todo = next(t for t in todos if t["text"] == "Child")
    assert new_todo.get("prev_task_id") == 5


@pytest.mark.asyncio
async def test_add_ids_are_monotonically_increasing(app, seed_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    ids = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for i in range(3):
            r = await client.post("/api/add", json={"text": f"Task {i}", "project_id": 1})
            ids.append(r.json()["id"])
    assert ids == sorted(ids)
    assert len(set(ids)) == 3


@pytest.mark.asyncio
async def test_add_trims_whitespace(app, seed_todos, read_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/add", json={"text": "  Trimmed  ", "project_id": 1})
    todos = read_todos()
    assert todos[0]["text"] == "Trimmed"
