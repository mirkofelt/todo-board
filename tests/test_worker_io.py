"""Tests for worker.py I/O helper functions: _api, _load_sessions, _save_sessions."""
import importlib
import json
import unittest.mock as mock
import urllib.request
import pytest


def _reload_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as w
    importlib.reload(w)
    return w


# ── _load_sessions ────────────────────────────────────────────────────────────

def test_load_sessions_returns_empty_when_file_missing(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    assert w._load_sessions() == {}


def test_load_sessions_returns_data_from_file(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    data = {"1": "sess-abc", "2": "sess-xyz"}
    w.SESSIONS_FILE.write_text(json.dumps(data))
    assert w._load_sessions() == data


def test_load_sessions_returns_empty_on_invalid_json(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    w.SESSIONS_FILE.write_text("not valid json {{{")
    assert w._load_sessions() == {}


# ── _save_sessions ────────────────────────────────────────────────────────────

def test_save_sessions_writes_json(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    data = {"3": "sess-new"}
    w._save_sessions(data)
    written = json.loads(w.SESSIONS_FILE.read_text())
    assert written == data


def test_save_sessions_overwrites_existing(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    w.SESSIONS_FILE.write_text(json.dumps({"old": "value"}))
    w._save_sessions({"new": "value"})
    assert json.loads(w.SESSIONS_FILE.read_text()) == {"new": "value"}


def test_save_sessions_does_not_raise_on_write_error(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    with mock.patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        w._save_sessions({"key": "val"})  # must not raise


# ── _api ──────────────────────────────────────────────────────────────────────

def test_api_posts_json_to_correct_url(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.method
        captured["body"] = json.loads(req.data)
        return mock.MagicMock().__enter__.return_value

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        w._api("/api/status/5", {"status": "done"})

    assert captured["url"].endswith("/api/status/5")
    assert captured["method"] == "POST"
    assert captured["body"] == {"status": "done"}


def test_api_uses_board_url_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_URL", "http://custom-host:9999")
    w = _reload_worker(tmp_path, monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return mock.MagicMock().__enter__.return_value

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        w._api("/api/ping", {})

    assert captured["url"].startswith("http://custom-host:9999")


def test_api_does_not_raise_on_network_error(tmp_path, monkeypatch):
    w = _reload_worker(tmp_path, monkeypatch)
    with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        w._api("/api/status/1", {"status": "in_progress"})  # must not raise
