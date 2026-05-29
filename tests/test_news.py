"""Tests for news endpoints: create, list, mark-read, clear."""
import time
import pytest
from httpx import AsyncClient, ASGITransport


def _news(id, message, msg_type="info", todo_id=None, project_id=None, read=False):
    return {
        "id": id,
        "type": msg_type,
        "message": message,
        "todo_id": todo_id,
        "project_id": project_id,
        "created": int(time.time()),
        "read": read,
    }


def seed_news(data_dir, items):
    import json
    (data_dir / "news.json").write_text(json.dumps(items))


@pytest.mark.asyncio
async def test_create_news_entry(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/news", json={"message": "Task completed", "type": "info"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["id"], int)


@pytest.mark.asyncio
async def test_get_news_returns_list(app, data_dir):
    seed_news(data_dir, [_news(1, "First"), _news(2, "Second")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/news")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_create_news_rejects_empty_message(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/news", json={"message": "", "type": "info"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_create_news_normalizes_invalid_type(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/news", json={"message": "Hello", "type": "invalid_type"})
    assert r.status_code == 200
    # Should fall back to "info"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/news")
    assert r.json()[0]["type"] == "info"


@pytest.mark.asyncio
async def test_create_news_stores_todo_id(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/news", json={"message": "Done", "type": "info", "todo_id": 42})
        r = await client.get("/api/news")
    items = r.json()
    assert items[0]["todo_id"] == 42


@pytest.mark.asyncio
async def test_mark_news_read_by_ids(app, data_dir):
    seed_news(data_dir, [_news(1, "A", read=False), _news(2, "B", read=False)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/news/mark-read", json={"ids": [1]})
        assert r.json()["ok"] is True
        items = (await client.get("/api/news")).json()
    by_id = {n["id"]: n for n in items}
    assert by_id[1]["read"] is True
    assert by_id[2]["read"] is False


@pytest.mark.asyncio
async def test_mark_all_news_read(app, data_dir):
    seed_news(data_dir, [_news(1, "A", read=False), _news(2, "B", read=False)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/news/mark-read", json={})
        items = (await client.get("/api/news")).json()
    assert all(n["read"] for n in items)


@pytest.mark.asyncio
async def test_clear_news(app, data_dir):
    seed_news(data_dir, [_news(1, "Old"), _news(2, "Also old")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/news/clear")
        assert r.json()["ok"] is True
        items = (await client.get("/api/news")).json()
    assert items == []


@pytest.mark.asyncio
async def test_news_unread_count_in_state(app, data_dir, seed_todos):
    seed_todos([])
    seed_news(data_dir, [_news(1, "A", read=False), _news(2, "B", read=True)])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/state")
    assert r.json()["news_unread"] == 1


@pytest.mark.asyncio
async def test_delete_done_also_removes_news(app, data_dir, seed_todos, read_todos):
    import json
    import time as t
    seed_todos([{"id": 5, "text": "Task", "done": True, "status": "done",
                 "created": int(t.time()), "project_id": 1, "note": None,
                 "status_updated_at": int(t.time())}])
    seed_news(data_dir, [_news(1, "Task done", todo_id=5), _news(2, "Other")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/delete-done")
        items = (await client.get("/api/news")).json()
    ids = [n["id"] for n in items]
    assert 1 not in ids
    assert 2 in ids
