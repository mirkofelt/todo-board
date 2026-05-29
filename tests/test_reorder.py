"""Tests for /api/reorder: changing todo order.

The ids list is priority-ordered: ids[0] runs first, ids[-1] runs last.
Workers pick tasks via reversed(todos), so ids[0] ends up at the HIGHEST array index.
"""
import time
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
async def test_reorder_changes_positions(app, seed_todos, read_todos):
    # Start: [1, 2, 3]. Reorder so id=3 runs first, id=1 runs second.
    # ids=[3, 1] → id=3 gets highest index, id=1 gets lower index.
    # Expected result array: [1, 2, 3] → [1, 2, 3] unchanged (3 was already last)
    # To actually see a change: start [1, 2, 3], reorder [1, 3] (1 first, 3 second)
    # → id=1 gets index 2, id=3 gets index 0 → final: [3, 2, 1]
    seed_todos([_todo(1, "A"), _todo(2, "B"), _todo(3, "C")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/reorder", json={"ids": [1, 3]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    todos = read_todos()
    ids = [t["id"] for t in todos]
    # id=1 should run first → gets highest array index
    assert ids.index(1) > ids.index(3)


@pytest.mark.asyncio
async def test_reorder_empty_ids_is_noop(app, seed_todos, read_todos):
    seed_todos([_todo(1, "A"), _todo(2, "B")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/reorder", json={"ids": []})
    assert r.status_code == 200
    todos = read_todos()
    assert [t["id"] for t in todos] == [1, 2]


@pytest.mark.asyncio
async def test_reorder_missing_ids_field(app, seed_todos, read_todos):
    seed_todos([_todo(1, "A"), _todo(2, "B")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/reorder", json={})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_reorder_single_item_noop(app, seed_todos, read_todos):
    seed_todos([_todo(1, "A"), _todo(2, "B"), _todo(3, "C")])
    original = [t["id"] for t in read_todos()]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/reorder", json={"ids": [2]})
    todos = read_todos()
    assert [t["id"] for t in todos] == original


@pytest.mark.asyncio
async def test_reorder_full_priority_order(app, seed_todos, read_todos):
    # Start: [1, 2, 3]. Reorder as [3, 2, 1] (3 first, 2 second, 1 third).
    # ids[0]=3 → highest index; ids[1]=2 → middle; ids[2]=1 → lowest index.
    # positions of {1,2,3} = [0,1,2], reversed = [2,1,0]
    # result[2]=3, result[1]=2, result[0]=1 → final array = [1, 2, 3] (no change since already in order)
    # Better test: reorder [2, 1, 3] from [1, 2, 3]
    # positions = [0,1,2], reversed=[2,1,0]
    # result[2]=2, result[1]=1, result[0]=3 → final: [3, 1, 2]
    seed_todos([_todo(1, "A"), _todo(2, "B"), _todo(3, "C")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/reorder", json={"ids": [2, 1, 3]})
    todos = read_todos()
    ids = [t["id"] for t in todos]
    assert ids == [3, 1, 2]


@pytest.mark.asyncio
async def test_reorder_preserves_unaffected_todos(app, seed_todos, read_todos):
    # Reorder only ids 1 and 3; id=2 should stay at index 1.
    seed_todos([_todo(1, "A"), _todo(2, "B"), _todo(3, "C")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/reorder", json={"ids": [1, 3]})
    todos = read_todos()
    # id=2 should remain at position 1
    assert todos[1]["id"] == 2
