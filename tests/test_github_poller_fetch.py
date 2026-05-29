"""Tests for github_poller._fetch_release, _load_seen error branch, and run_release_poller."""
import asyncio
import importlib
import json
import unittest.mock as mock
import pytest


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.github_poller as poller
    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(poller)
    return poller


# ── _load_seen error branch ───────────────────────────────────────────────────

def test_load_seen_returns_empty_on_invalid_json(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    poller._SEEN_FILE.write_text("not json {{{{")
    assert poller._load_seen() == {}


# ── _fetch_release ────────────────────────────────────────────────────────────

def test_fetch_release_returns_parsed_json(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    payload = {"tag_name": "v3.0.0", "body": "New stuff"}
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps(payload).encode()
    fake_ctx = mock.MagicMock()
    fake_ctx.__enter__ = mock.Mock(return_value=fake_resp)
    fake_ctx.__exit__ = mock.Mock(return_value=False)

    with mock.patch("urllib.request.urlopen", return_value=fake_ctx):
        result = poller._fetch_release("owner/repo")

    assert result == payload


def test_fetch_release_returns_none_on_network_error(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = poller._fetch_release("owner/repo")
    assert result is None


def test_fetch_release_returns_none_on_http_error(tmp_path, monkeypatch):
    import urllib.error
    poller = _setup(tmp_path, monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="https://api.github.com/repos/owner/repo/releases/latest",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )):
        result = poller._fetch_release("owner/repo")
    assert result is None


def test_fetch_release_builds_correct_api_url(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = b'{"tag_name": "v1.0"}'
        ctx = mock.MagicMock()
        ctx.__enter__ = mock.Mock(return_value=fake_resp)
        ctx.__exit__ = mock.Mock(return_value=False)
        return ctx

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        poller._fetch_release("myorg/myrepo")

    assert "api.github.com/repos/myorg/myrepo/releases/latest" in captured["url"]


# ── run_release_poller ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_release_poller_calls_poll_on_startup(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    called = []

    async def fake_poll():
        called.append(True)
        return 0

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(poller, "poll_github_releases", fake_poll)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await poller.run_release_poller()

    assert len(called) >= 1


@pytest.mark.asyncio
async def test_run_release_poller_survives_poll_exception(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    call_count = [0]

    async def fake_poll():
        call_count[0] += 1
        raise RuntimeError("network down")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(poller, "poll_github_releases", fake_poll)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await poller.run_release_poller()

    assert call_count[0] == 1
