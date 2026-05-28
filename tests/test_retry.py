"""Tests for retry cap behavior on context_limit status updates."""
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="in_progress", retry_count=0, project_id=1):
    return {
        "id": id,
        "text": text,
        "done": False,
        "status": status,
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
        "retry_count": retry_count,
    }


@pytest.mark.asyncio
async def test_context_limit_first_retry(app, seed_todos, read_todos):
    """First context_limit hit: retry_count becomes 1, re-spawns."""
    seed_todos([_todo(1, "Task", retry_count=0)])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/status/1", json={"status": "context_limit"})
    assert r.status_code == 200
    todos = read_todos()
    t = todos[0]
    assert t["status"] == "in_progress"
    assert t["retry_count"] == 1
    mock_spawn.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_context_limit_second_retry(app, seed_todos, read_todos):
    """Second context_limit hit (retry_count=1 → 2): still retries."""
    seed_todos([_todo(1, "Task", retry_count=1)])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/status/1", json={"status": "context_limit"})
    assert r.status_code == 200
    todos = read_todos()
    t = todos[0]
    assert t["status"] == "in_progress"
    assert t["retry_count"] == 2
    mock_spawn.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_context_limit_exceeds_max_retries(app, seed_todos, read_todos):
    """After MAX_RETRIES (default 2) hits: marks failed, no respawn."""
    seed_todos([_todo(1, "Task", retry_count=2)])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/status/1", json={"status": "context_limit"})
    assert r.status_code == 200
    todos = read_todos()
    t = todos[0]
    assert t["status"] == "failed"
    assert t["retry_count"] == 3
    assert "max retries" in (t.get("note") or "").lower()
    assert "2" in (t.get("note") or "")
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_context_limit_progress_message_on_retry(app, seed_todos, read_todos):
    """Progress field shows retry number while retrying."""
    seed_todos([_todo(1, "Task", retry_count=0)])
    with mock.patch("todo_board.server.spawn_worker"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/status/1", json={"status": "context_limit"})
    todos = read_todos()
    progress = todos[0].get("progress") or ""
    assert "1" in progress
    assert "2" in progress  # shows N/MAX format


@pytest.mark.asyncio
async def test_context_limit_failed_clears_progress(app, seed_todos, read_todos):
    """When finally failed, progress is cleared."""
    t = _todo(1, "Task", retry_count=2)
    t["progress"] = "Retry 2/2 after context limit…"
    seed_todos([t])
    with mock.patch("todo_board.server.spawn_worker"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/status/1", json={"status": "context_limit"})
    todos = read_todos()
    assert todos[0].get("progress") is None
