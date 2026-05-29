"""Tests for breakdown.py session helpers: _load_sessions, _save_sessions."""
import importlib
import json
import unittest.mock as mock
import pytest


def _reload_breakdown(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.breakdown as b
    importlib.reload(b)
    return b


# ── _load_sessions ────────────────────────────────────────────────────────────

def test_breakdown_load_sessions_missing_file(tmp_path, monkeypatch):
    b = _reload_breakdown(tmp_path, monkeypatch)
    assert b._load_sessions() == {}


def test_breakdown_load_sessions_valid_json(tmp_path, monkeypatch):
    b = _reload_breakdown(tmp_path, monkeypatch)
    data = {"1": "sess-abc", "none": "sess-xyz"}
    b._SESSIONS_FILE.write_text(json.dumps(data))
    assert b._load_sessions() == data


def test_breakdown_load_sessions_invalid_json(tmp_path, monkeypatch):
    b = _reload_breakdown(tmp_path, monkeypatch)
    b._SESSIONS_FILE.write_text("{{invalid}}")
    assert b._load_sessions() == {}


# ── _save_sessions ────────────────────────────────────────────────────────────

def test_breakdown_save_sessions_writes_correct_json(tmp_path, monkeypatch):
    b = _reload_breakdown(tmp_path, monkeypatch)
    data = {"5": "sess-new-session"}
    b._save_sessions(data)
    written = json.loads(b._SESSIONS_FILE.read_text())
    assert written == data


def test_breakdown_save_sessions_roundtrip(tmp_path, monkeypatch):
    b = _reload_breakdown(tmp_path, monkeypatch)
    original = {"1": "abc", "2": "def"}
    b._save_sessions(original)
    assert b._load_sessions() == original


def test_breakdown_save_sessions_silently_ignores_write_error(tmp_path, monkeypatch):
    b = _reload_breakdown(tmp_path, monkeypatch)
    with mock.patch("pathlib.Path.write_text", side_effect=OSError("no space")):
        b._save_sessions({"k": "v"})  # must not raise
