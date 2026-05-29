"""Tests for GitHub links, plugins, and version endpoints."""
import json
import pytest
from httpx import AsyncClient, ASGITransport


# ── /api/version ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_version_returns_numeric_mtime(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/version")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert isinstance(data["version"], (int, float))
    assert data["version"] > 0


# ── /api/github-links ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_links_empty_by_default(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/github-links")
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_set_and_get_github_links(app, data_dir):
    links = {"sensor-dashboard": "owner/sensor-dashboard", "todo-board": "owner/todo-board"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/github-links", json=links)
        assert r.json()["ok"] is True
        r = await client.get("/api/github-links")
    assert r.json() == links


@pytest.mark.asyncio
async def test_set_github_links_rejects_non_dict(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/github-links", json=["not", "a", "dict"])
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_github_links_overwritten_on_second_post(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/github-links", json={"old": "old/repo"})
        await client.post("/api/github-links", json={"new": "new/repo"})
        r = await client.get("/api/github-links")
    assert r.json() == {"new": "new/repo"}


# ── /api/plugins ──────────────────────────────────────────────────────────────

def _seed_plugins(data_dir, plugins: dict):
    (data_dir / "plugins.json").write_text(json.dumps(plugins))


@pytest.mark.asyncio
async def test_get_plugins_empty_when_no_definitions(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/plugins")
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_get_plugins_returns_all_definitions(app, data_dir):
    _seed_plugins(data_dir, {
        "my-plugin": {"name": "My Plugin", "description": "Does things", "command": ["echo", "hi"]},
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/plugins")
    assert r.status_code == 200
    data = r.json()
    assert "my-plugin" in data
    assert data["my-plugin"]["name"] == "My Plugin"
    assert data["my-plugin"]["description"] == "Does things"


@pytest.mark.asyncio
async def test_get_plugin_status_idle_by_default(app, data_dir):
    _seed_plugins(data_dir, {
        "checker": {"name": "Checker", "description": "", "command": ["echo", "ok"]},
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/plugins")
    assert r.json()["checker"]["status"] == "idle"


@pytest.mark.asyncio
async def test_get_plugin_shows_last_run_state(app, data_dir):
    _seed_plugins(data_dir, {
        "reporter": {"name": "Reporter", "description": "", "command": ["echo", "done"]},
    })
    (data_dir / "plugin_states.json").write_text(json.dumps({
        "reporter": {"status": "done", "last_run_at": 1000000, "result": "All good"},
    }))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/plugins")
    plugin = r.json()["reporter"]
    assert plugin["status"] == "done"
    assert plugin["last_run_at"] == 1000000
    assert plugin["result"] == "All good"


@pytest.mark.asyncio
async def test_run_plugin_not_found_returns_404(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/plugins/nonexistent/run")
    assert r.status_code == 404
    assert r.json()["ok"] is False


@pytest.mark.asyncio
async def test_run_plugin_schedules_task(app, data_dir, monkeypatch):
    _seed_plugins(data_dir, {
        "myplugin": {"name": "My Plugin", "command": ["echo", "hello"]},
    })

    async def noop(name, plugin):
        pass

    monkeypatch.setattr("todo_board.server.run_plugin", noop)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/plugins/myplugin/run")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_run_plugin_already_running_returns_409(app, data_dir, monkeypatch):
    _seed_plugins(data_dir, {
        "slow-plugin": {"name": "Slow", "command": ["sleep", "999"]},
    })
    import todo_board.plugin_runner as pr
    monkeypatch.setattr(pr, "_running", {"slow-plugin"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/plugins/slow-plugin/run")
    assert r.status_code == 409
    assert r.json()["ok"] is False


# ── /api/poll-releases ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_releases_returns_count(app, data_dir, monkeypatch):
    async def mock_poll():
        return 2

    monkeypatch.setattr("todo_board.server.poll_github_releases", mock_poll)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/poll-releases")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["new_releases"] == 2


@pytest.mark.asyncio
async def test_poll_releases_zero_when_no_new(app, data_dir, monkeypatch):
    async def mock_poll():
        return 0

    monkeypatch.setattr("todo_board.server.poll_github_releases", mock_poll)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/poll-releases")
    assert r.json()["new_releases"] == 0
