"""Unit tests for config helpers and worker prompt builders."""
import pytest
from pathlib import Path


# ── is_project_dir ────────────────────────────────────────────────────────────

def test_is_project_dir_regular_directory(tmp_path):
    from todo_board.config import is_project_dir
    d = tmp_path / "my-project"
    d.mkdir()
    assert is_project_dir(d) is True


def test_is_project_dir_file_returns_false(tmp_path):
    from todo_board.config import is_project_dir
    f = tmp_path / "somefile.txt"
    f.write_text("x")
    assert is_project_dir(f) is False


def test_is_project_dir_dot_prefix_excluded(tmp_path):
    from todo_board.config import is_project_dir
    d = tmp_path / ".hidden"
    d.mkdir()
    assert is_project_dir(d) is False


def test_is_project_dir_underscore_prefix_excluded(tmp_path):
    from todo_board.config import is_project_dir
    d = tmp_path / "__pycache__"
    d.mkdir()
    assert is_project_dir(d) is False


def test_is_project_dir_memory_excluded(tmp_path):
    from todo_board.config import is_project_dir
    d = tmp_path / "memory"
    d.mkdir()
    assert is_project_dir(d) is False


def test_is_project_dir_uuid_excluded(tmp_path):
    from todo_board.config import is_project_dir
    d = tmp_path / "550e8400-e29b-41d4-a716-446655440000"
    d.mkdir()
    assert is_project_dir(d) is False


# ── worker._build_system_prompt ───────────────────────────────────────────────

def test_build_system_prompt_both_parts():
    from todo_board.worker import _build_system_prompt
    result = _build_system_prompt(rules="- be careful", memory="project context here")
    assert "## Rules" in result
    assert "be careful" in result
    assert "## Project Context" in result
    assert "project context here" in result


def test_build_system_prompt_empty_rules():
    from todo_board.worker import _build_system_prompt
    result = _build_system_prompt(rules="", memory="some context")
    assert "## Rules" not in result
    assert "some context" in result


def test_build_system_prompt_empty_memory():
    from todo_board.worker import _build_system_prompt
    result = _build_system_prompt(rules="- rule 1", memory="")
    assert "## Rules" in result
    assert "## Project Context" not in result


def test_build_system_prompt_both_empty_returns_empty():
    from todo_board.worker import _build_system_prompt
    assert _build_system_prompt(rules="", memory="") == ""


# ── worker._build_task_prompt ─────────────────────────────────────────────────

def test_build_task_prompt_contains_task():
    from todo_board.worker import _build_task_prompt
    result = _build_task_prompt(42, "General", "Fix the login bug")
    assert "Todo #42" in result
    assert "General" in result
    assert "Fix the login bug" in result


def test_build_task_prompt_no_prev_result_by_default():
    from todo_board.worker import _build_task_prompt
    result = _build_task_prompt(1, "MyProject", "Do something")
    assert "Previous Task Output" not in result


def test_build_task_prompt_includes_prev_result():
    from todo_board.worker import _build_task_prompt
    result = _build_task_prompt(1, "MyProject", "Do something", prev_result="prior output here")
    assert "Previous Task Output" in result
    assert "prior output here" in result


def test_build_task_prompt_includes_answered_questions():
    from todo_board.worker import _build_task_prompt
    qs = [{"question": "What color?", "answer": "Blue", "options": []}]
    result = _build_task_prompt(1, "MyProject", "Do something", answered_questions=qs)
    assert "Clarifications" in result
    assert "What color?" in result
    assert "Blue" in result


def test_build_task_prompt_no_clarifications_without_answered_questions():
    from todo_board.worker import _build_task_prompt
    result = _build_task_prompt(1, "MyProject", "Do something")
    assert "Clarifications" not in result


def test_build_task_prompt_contains_waiting_for_answers_instructions():
    from todo_board.worker import _build_task_prompt
    result = _build_task_prompt(1, "MyProject", "Do something")
    assert "WAITING_FOR_ANSWERS" in result


# ── github_poller._repo_slug ─────────────────────────────────────────────────

def test_repo_slug_standard_url():
    from todo_board.github_poller import _repo_slug
    assert _repo_slug("https://github.com/owner/repo") == "owner/repo"


def test_repo_slug_with_trailing_slash():
    from todo_board.github_poller import _repo_slug
    assert _repo_slug("https://github.com/owner/repo/") == "owner/repo"


def test_repo_slug_with_subpath():
    from todo_board.github_poller import _repo_slug
    assert _repo_slug("https://github.com/owner/repo/tree/main") == "owner/repo"


def test_repo_slug_non_github_url_returns_none():
    from todo_board.github_poller import _repo_slug
    assert _repo_slug("https://gitlab.com/owner/repo") is None


def test_repo_slug_incomplete_path_returns_none():
    from todo_board.github_poller import _repo_slug
    assert _repo_slug("https://github.com/owner") is None


def test_repo_slug_empty_string_returns_none():
    from todo_board.github_poller import _repo_slug
    assert _repo_slug("") is None
