"""Tests for cumulative stats accumulation when tasks are deleted."""
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="done", tokens=None, duration_secs=None):
    t = {
        "id": id,
        "text": text,
        "done": status == "done",
        "status": status,
        "created": int(time.time()),
        "project_id": 1,
        "note": None,
        "status_updated_at": int(time.time()),
    }
    if tokens is not None:
        t["tokens"] = tokens
    if duration_secs is not None:
        t["duration_secs"] = duration_secs
    return t


def read_stats(data_dir):
    path = data_dir / "stats.json"
    if not path.exists():
        return {"total_input_tokens": 0, "total_output_tokens": 0, "total_duration_secs": 0}
    return json.loads(path.read_text())


@pytest.mark.asyncio
async def test_clear_done_accumulates_tokens(app, seed_todos, data_dir):
    seed_todos([
        _todo(1, "Task A", status="done", tokens={"input": 100, "output": 50}, duration_secs=30),
        _todo(2, "Task B", status="pending"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete-done")
    assert r.status_code == 200
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 100
    assert stats["total_output_tokens"] == 50
    assert stats["total_duration_secs"] == 30


@pytest.mark.asyncio
async def test_clear_done_accumulates_multiple_tasks(app, seed_todos, data_dir):
    seed_todos([
        _todo(1, "Task A", status="done", tokens={"input": 200, "output": 80}, duration_secs=60),
        _todo(2, "Task B", status="canceled", tokens={"input": 50, "output": 20}, duration_secs=10),
        _todo(3, "Task C", status="pending"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 250
    assert stats["total_output_tokens"] == 100
    assert stats["total_duration_secs"] == 70


@pytest.mark.asyncio
async def test_stats_accumulate_across_multiple_clears(app, seed_todos, data_dir):
    seed_todos([_todo(1, "First", status="done", tokens={"input": 100, "output": 40}, duration_secs=20)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
        seed_todos([_todo(2, "Second", status="done", tokens={"input": 200, "output": 60}, duration_secs=45)])
        await client.post("/api/delete-done")
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 300
    assert stats["total_output_tokens"] == 100
    assert stats["total_duration_secs"] == 65


@pytest.mark.asyncio
async def test_delete_single_done_task_accumulates_stats(app, seed_todos, data_dir):
    seed_todos([_todo(1, "Done task", status="done", tokens={"input": 150, "output": 75}, duration_secs=40)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete/1")
    assert r.status_code == 200
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 150
    assert stats["total_output_tokens"] == 75
    assert stats["total_duration_secs"] == 40


@pytest.mark.asyncio
async def test_delete_pending_task_does_not_accumulate_stats(app, seed_todos, data_dir):
    seed_todos([_todo(1, "Pending task", status="pending", tokens={"input": 0, "output": 0}, duration_secs=0)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete/1")
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 0
    assert stats["total_output_tokens"] == 0
    assert stats["total_duration_secs"] == 0


@pytest.mark.asyncio
async def test_stats_endpoint_returns_cumulative(app, seed_todos, data_dir):
    seed_todos([_todo(1, "Task", status="done", tokens={"input": 500, "output": 200}, duration_secs=90)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
        r = await client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total_input_tokens"] == 500
    assert data["total_output_tokens"] == 200
    assert data["total_duration_secs"] == 90


@pytest.mark.asyncio
async def test_stats_endpoint_returns_zeros_initially(app, seed_todos, data_dir):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total_input_tokens"] == 0
    assert data["total_output_tokens"] == 0
    assert data["total_duration_secs"] == 0


@pytest.mark.asyncio
async def test_tasks_without_tokens_accumulate_duration_only(app, seed_todos, data_dir):
    seed_todos([_todo(1, "No tokens", status="done", duration_secs=120)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 0
    assert stats["total_output_tokens"] == 0
    assert stats["total_duration_secs"] == 120


@pytest.mark.asyncio
async def test_cache_tokens_accumulated(app, seed_todos, data_dir):
    """cache_creation and cache_read fields are accumulated into stats."""
    seed_todos([
        _todo(1, "Task A", status="done", tokens={
            "input": 100, "output": 50,
            "cache_creation": 200, "cache_read": 300,
        }),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 100
    assert stats["total_output_tokens"] == 50
    assert stats["total_cache_creation_tokens"] == 200
    assert stats["total_cache_read_tokens"] == 300


@pytest.mark.asyncio
async def test_cache_tokens_sum_across_tasks(app, seed_todos, data_dir):
    """Cache token counts add up correctly across multiple tasks."""
    seed_todos([
        _todo(1, "Task A", status="done", tokens={
            "input": 100, "output": 40, "cache_creation": 500, "cache_read": 1000,
        }),
        _todo(2, "Task B", status="done", tokens={
            "input": 200, "output": 60, "cache_creation": 0, "cache_read": 800,
        }),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
    stats = read_stats(data_dir)
    assert stats["total_cache_creation_tokens"] == 500
    assert stats["total_cache_read_tokens"] == 1800


@pytest.mark.asyncio
async def test_cache_tokens_default_zero_without_cache_fields(app, seed_todos, data_dir):
    """Old-style tokens dict (no cache fields) doesn't break accumulation."""
    seed_todos([
        _todo(1, "Old task", status="done", tokens={"input": 100, "output": 50}),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
    stats = read_stats(data_dir)
    assert stats["total_input_tokens"] == 100
    assert stats.get("total_cache_creation_tokens", 0) == 0
    assert stats.get("total_cache_read_tokens", 0) == 0


@pytest.mark.asyncio
async def test_stats_endpoint_includes_cache_fields(app, seed_todos, data_dir):
    """/api/stats response includes cache token fields."""
    seed_todos([
        _todo(1, "Task", status="done", tokens={
            "input": 50, "output": 20, "cache_creation": 100, "cache_read": 250,
        }),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
        r = await client.get("/api/stats")
    data = r.json()
    assert data["total_cache_creation_tokens"] == 100
    assert data["total_cache_read_tokens"] == 250
