"""Tests for the resume endpoint on context_limit interrupted tasks."""
import json
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="context_limit", project_id=1):
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
async def test_resume_context_limit_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Interrupted task")])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/resume/1")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    assert todos[0]["status"] == "in_progress"
    mock_spawn.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_resume_sets_progress_message(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Interrupted task")])
    with mock.patch("todo_board.server.spawn_worker"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/resume/1")
    todos = read_todos()
    assert todos[0].get("progress") == "Resuming after context limit…"


@pytest.mark.asyncio
async def test_resume_rejects_non_context_limit(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Pending task", status="pending")])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/resume/1")
    assert r.status_code == 409
    mock_spawn.assert_not_called()
    todos = read_todos()
    assert todos[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_resume_rejects_in_progress(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Active task", status="in_progress")])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/resume/1")
    assert r.status_code == 409
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_resume_not_found(app, seed_todos):
    seed_todos([])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/resume/999")
    assert r.status_code == 404
    mock_spawn.assert_not_called()
