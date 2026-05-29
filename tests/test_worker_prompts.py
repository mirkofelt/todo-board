"""Tests for worker.py prompt-building and helper functions."""
import pytest
from todo_board.worker import (
    _build_system_prompt,
    _build_task_prompt,
    _build_cold_cmd,
    _build_resume_cmd,
    _session_key,
    CLAUDE_BIN,
    CLAUDE_MAX_TURNS,
)


# ── _session_key ──────────────────────────────────────────────────────────────

def test_session_key_with_project_id():
    assert _session_key(42) == "42"


def test_session_key_with_none():
    assert _session_key(None) == "none"


def test_session_key_with_zero():
    assert _session_key(0) == "0"


# ── _build_system_prompt ──────────────────────────────────────────────────────

def test_system_prompt_with_both():
    result = _build_system_prompt("be careful", "project context here")
    assert "Project Context (Memory)" in result
    assert "project context here" in result
    assert "Rules" in result
    assert "be careful" in result


def test_system_prompt_rules_only():
    result = _build_system_prompt("my rules", "")
    assert "Rules" in result
    assert "my rules" in result
    assert "Memory" not in result


def test_system_prompt_memory_only():
    result = _build_system_prompt("", "some memory")
    assert "Memory" in result
    assert "some memory" in result
    assert "Rules" not in result


def test_system_prompt_empty():
    result = _build_system_prompt("", "")
    assert result == ""


# ── _build_task_prompt ────────────────────────────────────────────────────────

def test_task_prompt_basic():
    result = _build_task_prompt(5, "General", "Do the thing")
    assert "Todo #5" in result
    assert "General" in result
    assert "Do the thing" in result


def test_task_prompt_subtask_uses_display_id():
    result = _build_task_prompt(92, "General", "Do subtask", display_id="91-1")
    assert "Todo #91-1" in result
    assert "Todo #92" not in result


def test_task_prompt_no_display_id_uses_todo_id():
    result = _build_task_prompt(42, "General", "task", display_id="")
    assert "Todo #42" in result


def test_task_prompt_contains_asking_for_clarification_section():
    result = _build_task_prompt(1, "General", "task")
    assert "WAITING_FOR_ANSWERS" in result
    assert "QUESTION:" in result


def test_task_prompt_contains_status_section():
    result = _build_task_prompt(1, "General", "task")
    assert "STATUS:" in result


def test_task_prompt_contains_failed_instruction():
    result = _build_task_prompt(1, "General", "task")
    assert "FAILED:" in result


def test_task_prompt_with_prev_result():
    result = _build_task_prompt(2, "General", "task", prev_result="prior output")
    assert "Previous Task Output" in result
    assert "prior output" in result


def test_task_prompt_without_prev_result():
    result = _build_task_prompt(2, "General", "task", prev_result="")
    assert "Previous Task Output" not in result


def test_task_prompt_with_answered_questions():
    questions = [
        {"question": "Use TypeScript?", "answer": "yes"},
        {"question": "Add tests?", "answer": "no"},
    ]
    result = _build_task_prompt(3, "General", "task", answered_questions=questions)
    assert "Clarifications" in result
    assert "Use TypeScript?" in result
    assert "yes" in result
    assert "Add tests?" in result
    assert "no" in result


def test_task_prompt_without_answered_questions():
    result = _build_task_prompt(3, "General", "task", answered_questions=None)
    assert "Clarifications" not in result


def test_task_prompt_empty_answered_questions():
    result = _build_task_prompt(3, "General", "task", answered_questions=[])
    assert "Clarifications" not in result


# ── _build_cold_cmd ───────────────────────────────────────────────────────────

def test_cold_cmd_basic_structure():
    cmd = _build_cold_cmd("task prompt", "", "sonnet")
    assert CLAUDE_BIN in cmd
    assert "-p" in cmd
    assert "task prompt" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--model" in cmd
    assert "sonnet" in cmd
    assert "--max-turns" in cmd
    assert CLAUDE_MAX_TURNS in cmd


def test_cold_cmd_with_system_prompt():
    cmd = _build_cold_cmd("task", "system context", "opus")
    assert "--append-system-prompt" in cmd
    assert "system context" in cmd


def test_cold_cmd_without_system_prompt():
    cmd = _build_cold_cmd("task", "", "sonnet")
    assert "--append-system-prompt" not in cmd


def test_cold_cmd_uses_stream_json_output():
    cmd = _build_cold_cmd("task", "", "sonnet")
    assert "--output-format" in cmd
    idx = cmd.index("--output-format")
    assert cmd[idx + 1] == "stream-json"


def test_cold_cmd_with_budget(monkeypatch):
    monkeypatch.setattr("todo_board.worker.CLAUDE_MAX_BUDGET_USD", "1.50")
    cmd = _build_cold_cmd("task", "", "sonnet")
    assert "--max-budget-usd" in cmd
    assert "1.50" in cmd


def test_cold_cmd_without_budget(monkeypatch):
    monkeypatch.setattr("todo_board.worker.CLAUDE_MAX_BUDGET_USD", "")
    cmd = _build_cold_cmd("task", "", "sonnet")
    assert "--max-budget-usd" not in cmd


# ── _build_resume_cmd ─────────────────────────────────────────────────────────

def test_resume_cmd_basic_structure():
    cmd = _build_resume_cmd("sess-abc123", "continue task", "sonnet")
    assert CLAUDE_BIN in cmd
    assert "--resume" in cmd
    assert "sess-abc123" in cmd
    assert "continue task" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_resume_cmd_uses_stream_json_output():
    cmd = _build_resume_cmd("sess-abc", "task", "sonnet")
    assert "--output-format" in cmd
    idx = cmd.index("--output-format")
    assert cmd[idx + 1] == "stream-json"


def test_resume_cmd_with_budget(monkeypatch):
    monkeypatch.setattr("todo_board.worker.CLAUDE_MAX_BUDGET_USD", "2.00")
    cmd = _build_resume_cmd("sess-x", "task", "sonnet")
    assert "--max-budget-usd" in cmd
    assert "2.00" in cmd


def test_resume_cmd_without_budget(monkeypatch):
    monkeypatch.setattr("todo_board.worker.CLAUDE_MAX_BUDGET_USD", "")
    cmd = _build_resume_cmd("sess-x", "task", "sonnet")
    assert "--max-budget-usd" not in cmd
