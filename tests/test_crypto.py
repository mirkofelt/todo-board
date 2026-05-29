"""Tests for /api/crypto/* endpoints and crypto state storage."""
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport


# ── /api/crypto/data ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crypto_data_empty_when_no_state(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/data")
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_crypto_data_returns_persisted_state(app, data_dir):
    state = {
        "symbol": "BTC-USD",
        "last_updated": int(time.time()),
        "price": 65000.0,
        "wave_label": "3",
        "scenarios": [],
        "upcoming_events": [],
        "news": {"signal": "bullish", "score": 1.2, "count": 5, "top_headlines": []},
        "error": None,
    }
    (data_dir / "crypto_state.json").write_text(json.dumps(state))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/data")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "BTC-USD"
    assert data["price"] == 65000.0
    assert data["wave_label"] == "3"


# ── /api/crypto/refresh ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crypto_refresh_starts_background_task(app, data_dir, monkeypatch):
    refreshed = []

    async def _mock_run(symbol="BTC-USD"):
        refreshed.append(symbol)
        return {"symbol": symbol, "last_updated": int(time.time()), "price": 50000.0}

    monkeypatch.setattr("todo_board.server._run_crypto_refresh", _mock_run)

    import todo_board.server as server
    server._crypto_refreshing = False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/crypto/refresh", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_crypto_refresh_rejects_concurrent_refresh(app, data_dir, monkeypatch):
    import todo_board.server as server
    server._crypto_refreshing = True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/crypto/refresh", json={})
    assert r.status_code == 409
    assert r.json()["ok"] is False

    server._crypto_refreshing = False


@pytest.mark.asyncio
async def test_crypto_refresh_accepts_symbol_param(app, data_dir, monkeypatch):
    called_with = []

    async def _mock_run(symbol="BTC-USD"):
        called_with.append(symbol)
        return {}

    monkeypatch.setattr("todo_board.server._run_crypto_refresh", _mock_run)

    import todo_board.server as server
    server._crypto_refreshing = False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/crypto/refresh", json={"symbol": "ETH-USD"})
    assert r.status_code == 200


# ── /api/crypto/chart ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crypto_chart_404_when_no_state(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/chart")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_crypto_chart_404_when_chart_path_missing(app, data_dir):
    state = {"symbol": "BTC-USD", "chart_path": "/tmp/nonexistent_chart.png"}
    (data_dir / "crypto_state.json").write_text(json.dumps(state))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/chart")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_crypto_chart_serves_png_file(app, data_dir, tmp_path):
    chart_file = tmp_path / "chart.png"
    chart_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)  # minimal PNG header

    state = {"symbol": "BTC-USD", "chart_path": str(chart_file)}
    (data_dir / "crypto_state.json").write_text(json.dumps(state))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/chart")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


# ── /api/state includes crypto_mtime ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_includes_crypto_mtime(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/state")
    assert r.status_code == 200
    assert "crypto_mtime" in r.json()


@pytest.mark.asyncio
async def test_state_crypto_mtime_updates_after_save(app, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/state")
        mtime_before = r1.json()["crypto_mtime"]

    state = {"symbol": "BTC-USD", "last_updated": int(time.time()), "price": 60000.0}
    (data_dir / "crypto_state.json").write_text(json.dumps(state))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/api/state")
    assert r2.json()["crypto_mtime"] > mtime_before
