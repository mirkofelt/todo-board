"""Tests for project management endpoints."""
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport


def _todo(id, text, project_id=1, status="pending"):
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
async def test_get_projects_returns_list(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/projects")
    assert r.status_code == 200
    projects = r.json()
    assert isinstance(projects, list)
    assert len(projects) >= 1


@pytest.mark.asyncio
async def test_get_projects_includes_general(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/projects")
    names = [p["name"] for p in r.json()]
    assert "General" in names


@pytest.mark.asyncio
async def test_add_project_creates_directory(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/projects/add", json={"name": "NewProject"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert (data_dir / "projects" / "NewProject").is_dir()


@pytest.mark.asyncio
async def test_add_project_appears_in_list(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/projects/add", json={"name": "Alpha"})
        r = await client.get("/api/projects")
    names = [p["name"] for p in r.json()]
    assert "Alpha" in names


@pytest.mark.asyncio
async def test_add_project_empty_name_rejected(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/projects/add", json={"name": ""})
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_delete_project_removes_from_list(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/projects/add", json={"name": "ToDelete"})
        projects_before = (await client.get("/api/projects")).json()
        target = next(p for p in projects_before if p["name"] == "ToDelete")
        r = await client.post(f"/api/projects/delete/{target['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_delete_project_unassigns_todos(app, data_dir, seed_todos, read_todos):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/projects/add", json={"name": "Temporary"})
        projects = (await client.get("/api/projects")).json()
        proj = next(p for p in projects if p["name"] == "Temporary")
        pid = proj["id"]

    seed_todos([_todo(1, "Task in project", project_id=pid)])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(f"/api/projects/delete/{pid}")

    todos = read_todos()
    assert todos[0]["project_id"] is None


@pytest.mark.asyncio
async def test_add_project_with_model(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/projects/add", json={"name": "ModelProject", "model": "opus"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
