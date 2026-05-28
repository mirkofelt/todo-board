"""Tests for the /api/breakdown endpoint."""
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_breakdown_returns_tasks(app, data_dir):
    with mock.patch("todo_board.breakdown.breakdown_task", return_value=(["Task A", "Task B", "Task C"], "")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/breakdown", json={"text": "Build a feature", "project_id": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["tasks"] == ["Task A", "Task B", "Task C"]


@pytest.mark.asyncio
async def test_breakdown_rejects_empty_text(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/breakdown", json={"text": "", "project_id": 1})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_breakdown_returns_500_when_claude_returns_nothing(app, data_dir):
    with mock.patch("todo_board.breakdown.breakdown_task", return_value=([], "Claude exited with code 1: something went wrong")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/breakdown", json={"text": "Some task", "project_id": 1})
    assert r.status_code == 500
    assert r.json()["ok"] is False
