"""Tests for the question/answer workflow (waiting status)."""
import json
import time
import unittest.mock as mock
import pytest
from httpx import AsyncClient, ASGITransport

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _todo(id, text, status="in_progress", project_id=1, **kwargs):
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


# ── /api/questions/{todo_id} ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_questions_sets_waiting(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Do something", status="in_progress")])
    questions = [
        {"question": "Which approach?", "options": ["A", "B"], "answer": None},
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/questions/1", json={"questions": questions})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["status"] == "waiting"
    assert todos[0]["question_idx"] == 0
    assert len(todos[0]["questions"]) == 1


@pytest.mark.asyncio
async def test_post_questions_rejects_empty(app, seed_todos):
    seed_todos([_todo(1, "task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/questions/1", json={"questions": []})
    assert r.status_code == 400


# ── /api/questions/{todo_id}/answer ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_advances_index(app, seed_todos, read_todos):
    seed_todos([_todo(1, "task", status="waiting", questions=[
        {"question": "Q1?", "options": [], "answer": None},
        {"question": "Q2?", "options": [], "answer": None},
    ], question_idx=0)])
    with mock.patch("todo_board.server.spawn_worker"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/questions/1/answer", json={"answer": "yes"})
    assert r.status_code == 200
    data = r.json()
    assert data["all_answered"] is False
    todos = read_todos()
    assert todos[0]["question_idx"] == 1
    assert todos[0]["questions"][0]["answer"] == "yes"
    assert todos[0]["status"] == "waiting"


@pytest.mark.asyncio
async def test_answer_last_question_spawns_worker(app, seed_todos, read_todos):
    seed_todos([_todo(1, "task", status="waiting", questions=[
        {"question": "Only Q?", "options": [], "answer": None},
    ], question_idx=0)])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/questions/1/answer", json={"answer": "go"})
    assert r.status_code == 200
    assert r.json()["all_answered"] is True
    mock_spawn.assert_called_once_with(1)
    todos = read_todos()
    assert todos[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_answer_last_question_queues_pending_when_project_busy(app, seed_todos, read_todos):
    seed_todos([
        _todo(1, "active", status="in_progress", project_id=2),
        _todo(2, "waiting", status="waiting", project_id=2, questions=[
            {"question": "Q?", "options": [], "answer": None},
        ], question_idx=0),
    ])
    with mock.patch("todo_board.server.spawn_worker") as mock_spawn:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/questions/2/answer", json={"answer": "ok"})
    assert r.status_code == 200
    mock_spawn.assert_not_called()
    todos = read_todos()
    waiting_todo = next(t for t in todos if t["id"] == 2)
    assert waiting_todo["status"] == "pending"


@pytest.mark.asyncio
async def test_answer_rejects_non_waiting_task(app, seed_todos):
    seed_todos([_todo(1, "task", status="pending")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/questions/1/answer", json={"answer": "x"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_answer_rejects_empty_answer(app, seed_todos):
    seed_todos([_todo(1, "task", status="waiting", questions=[
        {"question": "Q?", "options": [], "answer": None},
    ], question_idx=0)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/questions/1/answer", json={"answer": ""})
    assert r.status_code == 400


# ── Worker _parse_questions ───────────────────────────────────────────────────

def test_parse_questions_basic():
    from todo_board.worker import _parse_questions
    lines = [
        "QUESTION: Which database?",
        "OPTION: PostgreSQL",
        "OPTION: SQLite",
        "WAITING_FOR_ANSWERS",
    ]
    qs = _parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["question"] == "Which database?"
    assert qs[0]["options"] == ["PostgreSQL", "SQLite"]
    assert qs[0]["answer"] is None


def test_parse_questions_multiple():
    from todo_board.worker import _parse_questions
    lines = [
        "QUESTION: First question?",
        "OPTION: Yes",
        "OPTION: No",
        "QUESTION: Second question?",
        "OPTION: Option A",
        "WAITING_FOR_ANSWERS",
    ]
    qs = _parse_questions(lines)
    assert len(qs) == 2
    assert qs[0]["question"] == "First question?"
    assert qs[0]["options"] == ["Yes", "No"]
    assert qs[1]["question"] == "Second question?"
    assert qs[1]["options"] == ["Option A"]


def test_parse_questions_max_four_options():
    from todo_board.worker import _parse_questions
    lines = [
        "QUESTION: Pick one?",
        "OPTION: A",
        "OPTION: B",
        "OPTION: C",
        "OPTION: D",
        "OPTION: E",  # 5th — should be ignored
        "WAITING_FOR_ANSWERS",
    ]
    qs = _parse_questions(lines)
    assert len(qs[0]["options"]) == 4


def test_parse_questions_no_options():
    from todo_board.worker import _parse_questions
    lines = ["QUESTION: Open question?", "WAITING_FOR_ANSWERS"]
    qs = _parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["options"] == []


def test_parse_questions_empty():
    from todo_board.worker import _parse_questions
    assert _parse_questions([]) == []
    assert _parse_questions(["WAITING_FOR_ANSWERS"]) == []
