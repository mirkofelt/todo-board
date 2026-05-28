"""Tests for continuous ID numbering across restarts and deletions."""
import pytest
from httpx import AsyncClient, ASGITransport


async def _add(client, text="task", project_id=1):
    r = await client.post("/api/add", json={"text": text, "project_id": project_id})
    assert r.status_code == 200
    return r.json()["id"]


@pytest.mark.asyncio
async def test_ids_do_not_reset_after_delete_all(app, seed_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        id1 = await _add(client, "first")
        id2 = await _add(client, "second")
        assert id2 == id1 + 1

        await client.post(f"/api/delete/{id1}")
        await client.post(f"/api/delete/{id2}")

        id3 = await _add(client, "third")
        assert id3 > id2, f"Expected id3 > {id2}, got {id3}"


@pytest.mark.asyncio
async def test_ids_continue_after_delete_done(app, seed_todos):
    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        id1 = await _add(client, "task a")
        await client.post(f"/api/done/{id1}")
        await client.post("/api/delete-done")

        id2 = await _add(client, "task b")
        assert id2 > id1, f"Expected id2 > {id1}, got {id2}"


@pytest.mark.asyncio
async def test_counter_survives_restart(app, data_dir, seed_todos):
    """Simulate restart by reloading modules with the same data_dir."""
    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    seed_todos([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        id1 = await _add(client, "before restart")

    # Reload modules (simulates server restart — env vars still set by data_dir fixture)
    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as client:
        id2 = await _add(client, "after restart")

    assert id2 > id1, f"Expected id2 > {id1}, got {id2}"
