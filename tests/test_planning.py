"""Tests for the planning phase: _parse_plan, _build_plan_prompt, and worker planning flow."""
import json
import time
import pytest
from unittest import mock
from pathlib import Path


# ── _parse_plan ───────────────────────────────────────────────────────────────

def test_parse_plan_direct():
    from todo_board.worker import _parse_plan
    is_direct, subtasks = _parse_plan(["PLAN: direct"])
    assert is_direct is True
    assert subtasks == []


def test_parse_plan_direct_case_insensitive():
    from todo_board.worker import _parse_plan
    is_direct, subtasks = _parse_plan(["plan: Direct"])
    assert is_direct is True
    assert subtasks == []


def test_parse_plan_subtasks():
    from todo_board.worker import _parse_plan
    lines = [
        "SUBTASK: Research the API",
        "SUBTASK: Implement the feature",
        "SUBTASK: Write tests",
    ]
    is_direct, subtasks = _parse_plan(lines)
    assert is_direct is False
    assert subtasks == ["Research the API", "Implement the feature", "Write tests"]


def test_parse_plan_empty_defaults_to_direct():
    from todo_board.worker import _parse_plan
    is_direct, subtasks = _parse_plan([])
    assert is_direct is True
    assert subtasks == []


def test_parse_plan_no_markers_defaults_to_direct():
    from todo_board.worker import _parse_plan
    is_direct, subtasks = _parse_plan(["Some random output", "That has no markers"])
    assert is_direct is True
    assert subtasks == []


def test_parse_plan_subtask_strips_whitespace():
    from todo_board.worker import _parse_plan
    _, subtasks = _parse_plan(["  SUBTASK:   Do something important  "])
    assert subtasks == ["Do something important"]


def test_parse_plan_skips_blank_subtasks():
    from todo_board.worker import _parse_plan
    _, subtasks = _parse_plan(["SUBTASK: ", "SUBTASK: Valid task"])
    assert subtasks == ["Valid task"]


def test_parse_plan_mixed_content_extracts_subtasks():
    from todo_board.worker import _parse_plan
    lines = [
        "I'll split this into sub-tasks:",
        "SUBTASK: Step one",
        "SUBTASK: Step two",
        "That's my plan.",
    ]
    is_direct, subtasks = _parse_plan(lines)
    assert is_direct is False
    assert subtasks == ["Step one", "Step two"]


# ── _build_plan_prompt ────────────────────────────────────────────────────────

def test_build_plan_prompt_contains_task():
    from todo_board.worker import _build_plan_prompt
    prompt = _build_plan_prompt(42, "MyProject", "Do something complex")
    assert "42" in prompt
    assert "MyProject" in prompt
    assert "Do something complex" in prompt
    assert "PLAN: direct" in prompt
    assert "SUBTASK:" in prompt


# ── spawner: planning counts as active worker ─────────────────────────────────

def test_project_has_active_worker_includes_planning():
    from todo_board.spawner import project_has_active_worker
    todos = [{"id": 1, "project_id": 5, "status": "planning"}]
    assert project_has_active_worker(5, todos) is True


def test_project_has_active_worker_excludes_pending():
    from todo_board.spawner import project_has_active_worker
    todos = [{"id": 1, "project_id": 5, "status": "pending"}]
    assert project_has_active_worker(5, todos) is False


# ── planning phase integration: subtask creation path ────────────────────────

def _make_todo(id, text="Do the thing", project_id=1, parent_id=None):
    return {
        "id": id, "text": text, "done": False, "status": "in_progress",
        "created": int(time.time()), "project_id": project_id,
        "note": None, "status_updated_at": int(time.time()),
        "parent_id": parent_id,
    }


def test_planning_skipped_for_subtasks(data_dir, monkeypatch):
    """Sub-tasks (parent_id set) skip the planning phase and go straight to working."""
    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.worker as worker

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(worker)

    todo = _make_todo(10, parent_id=5)
    todos = [todo]
    (data_dir / "todos.json").write_text(json.dumps(todos))

    api_calls = []

    def fake_api(path, data):
        api_calls.append((path, data))
        if path == "/api/status/10" and data.get("status") == "working":
            return {}
        return {}

    invoke_results = (0, ["PLAN: direct"], "done output", "success", {}, "sess1", "")

    monkeypatch.setattr(worker, "_api", fake_api)
    monkeypatch.setattr(worker, "_invoke_claude", lambda cmd, tid: invoke_results)

    # Should not call planning API
    planning_status_calls = [(p, d) for p, d in api_calls if d.get("status") == "planning"]
    assert planning_status_calls == []
