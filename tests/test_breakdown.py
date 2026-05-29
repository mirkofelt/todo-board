"""Tests for the /api/breakdown endpoint and _parse_tasks helper."""
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport
from todo_board.breakdown import _parse_tasks


# ── _parse_tasks unit tests ───────────────────────────────────────────────────

def test_parse_tasks_bare_json_array():
    assert _parse_tasks('["A", "B", "C"]') == ["A", "B", "C"]


def test_parse_tasks_in_fenced_code_block():
    text = '```json\n["Step 1", "Step 2"]\n```'
    assert _parse_tasks(text) == ["Step 1", "Step 2"]


def test_parse_tasks_fenced_without_language():
    text = '```\n["Do X", "Do Y"]\n```'
    assert _parse_tasks(text) == ["Do X", "Do Y"]


def test_parse_tasks_with_prose_before():
    text = 'Here are the tasks:\n["Task A", "Task B", "Task C"]'
    assert _parse_tasks(text) == ["Task A", "Task B", "Task C"]


def test_parse_tasks_filters_empty_strings():
    assert _parse_tasks('["Step 1", "", "Step 2", "  "]') == ["Step 1", "Step 2"]


def test_parse_tasks_invalid_json_returns_empty():
    assert _parse_tasks("not json at all") == []


def test_parse_tasks_empty_string_returns_empty():
    assert _parse_tasks("") == []


def test_parse_tasks_non_list_json_returns_empty():
    assert _parse_tasks('{"key": "value"}') == []


def test_parse_tasks_coerces_non_string_items():
    result = _parse_tasks('[1, "two", 3]')
    assert result == ["1", "two", "3"]


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
