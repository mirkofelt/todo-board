"""Tests for misc endpoints: progress, note, done, delete, statusline, state, requirements."""
import time
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, status="pending", project_id=1, **kwargs):
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


# ── /api/progress ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_progress(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task", status="in_progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/progress/1", json={"text": "Running step 2"})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["progress"] == "Running step 2"


@pytest.mark.asyncio
async def test_clear_progress_with_empty_text(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task", status="in_progress", progress="Old progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/progress/1", json={"text": ""})
    todos = read_todos()
    assert todos[0].get("progress") is None


@pytest.mark.asyncio
async def test_progress_truncated_to_150_chars(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task", status="in_progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/progress/1", json={"text": "x" * 200})
    todos = read_todos()
    assert len(todos[0]["progress"]) == 150


# ── /api/note ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_note(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/note/1", json={"note": "Important context"})
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["note"] == "Important context"


@pytest.mark.asyncio
async def test_clear_note_with_empty_string(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task", note="Old note")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/note/1", json={"note": ""})
    todos = read_todos()
    assert todos[0]["note"] is None


# ── /api/done ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_done(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/done/1")
    assert r.status_code == 200
    todos = read_todos()
    assert todos[0]["done"] is True
    assert todos[0]["status"] == "done"


# ── /api/delete ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_pending_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Task"), _todo(2, "Other")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete/1")
    assert r.status_code == 200
    todos = read_todos()
    assert len(todos) == 1
    assert todos[0]["id"] == 2


@pytest.mark.asyncio
async def test_cannot_delete_in_progress_todo(app, seed_todos, read_todos):
    seed_todos([_todo(1, "Running task", status="in_progress")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/delete/1")
    assert r.status_code == 409
    assert r.json()["ok"] is False
    todos = read_todos()
    assert len(todos) == 1


# ── /api/statusline ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_statusline_initially_empty(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/statusline")
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == ""


@pytest.mark.asyncio
async def test_set_and_get_statusline(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/statusline", json={"text": "Agent running: task 3"})
        assert r.json()["ok"] is True
        r = await client.get("/api/statusline")
    data = r.json()
    assert data["text"] == "Agent running: task 3"
    assert data["updated_at"] > 0


@pytest.mark.asyncio
async def test_clear_statusline(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/statusline", json={"text": "Something"})
        await client.post("/api/statusline", json={"text": ""})
        r = await client.get("/api/statusline")
    assert r.json()["text"] == ""


# ── /api/requirements ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_requirements_returns_default_when_missing(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/requirements")
    assert r.status_code == 200
    assert len(r.text) > 0


@pytest.mark.asyncio
async def test_set_and_get_requirements(app, data_dir):
    rules = "- Always write tests\n- No TODOs in code"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/requirements", content=rules.encode())
        assert r.json()["ok"] is True
        r = await client.get("/api/requirements")
    assert r.text == rules


# ── /api/state ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_returns_mtime_fields(app, seed_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/state")
    assert r.status_code == 200
    data = r.json()
    assert "mtime" in data
    assert "plugin_states_mtime" in data
    assert "news_mtime" not in data
    assert "news_unread" not in data


@pytest.mark.asyncio
async def test_state_mtime_changes_after_add(app, seed_todos, monkeypatch):
    monkeypatch.setattr("todo_board.server.spawn_worker", lambda tid: None)
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/state")
        mtime_before = r1.json()["mtime"]
        await client.post("/api/add", json={"text": "New task", "project_id": 1})
        r2 = await client.get("/api/state")
        mtime_after = r2.json()["mtime"]
    assert mtime_after >= mtime_before
