"""Unit tests for storage.py: load_projects auto-discovery and accumulate_stats."""
import importlib
import json
import pytest


def _reload(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TODO_BOARD_PROJECTS_DIR", str(tmp_path / "projects"))
    import todo_board.config as cfg
    import todo_board.storage as storage
    importlib.reload(cfg)
    importlib.reload(storage)
    return storage


# ── load_projects: auto-discovery ─────────────────────────────────────────────

def test_load_projects_returns_default_when_no_files(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    # No projects.json, no PROJECTS_DIR → returns DEFAULT_PROJECTS
    projects = storage.load_projects()
    assert any(p["name"] == "General" for p in projects)


def test_load_projects_auto_discovers_new_directory(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "NewProject").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    projects = storage.load_projects()
    names = [p["name"] for p in projects]
    assert "NewProject" in names


def test_load_projects_auto_discovered_project_gets_unique_id(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "Alpha").mkdir()
    (projects_dir / "Beta").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    projects = storage.load_projects()
    ids = [p["id"] for p in projects]
    assert len(ids) == len(set(ids))


def test_load_projects_persists_auto_discovered_ids(tmp_path, monkeypatch):
    """Auto-discovered project IDs must be stable across calls."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "AutoProject").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    first = {p["name"]: p["id"] for p in storage.load_projects()}
    second = {p["name"]: p["id"] for p in storage.load_projects()}
    assert first["AutoProject"] == second["AutoProject"]


def test_load_projects_excludes_uuid_dirs(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "550e8400-e29b-41d4-a716-446655440000").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    names = [p["name"] for p in storage.load_projects()]
    assert not any(len(n) == 36 and n.count("-") == 4 for n in names)


def test_load_projects_excludes_memory_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "memory").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    names = [p["name"] for p in storage.load_projects()]
    assert "memory" not in names


def test_load_projects_excludes_dot_prefix_dirs(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / ".hidden").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    names = [p["name"] for p in storage.load_projects()]
    assert ".hidden" not in names


def test_load_projects_excludes_underscore_prefix_dirs(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "__pycache__").mkdir()
    storage = _reload(tmp_path, monkeypatch)
    names = [p["name"] for p in storage.load_projects()]
    assert "__pycache__" not in names


def test_load_projects_skips_files_in_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "somefile.txt").write_text("not a dir")
    storage = _reload(tmp_path, monkeypatch)
    names = [p["name"] for p in storage.load_projects()]
    assert "somefile.txt" not in names


def test_load_projects_no_projects_dir_returns_stored(tmp_path, monkeypatch):
    """When PROJECTS_DIR doesn't exist, load_projects returns the stored list."""
    stored = [{"id": 99, "name": "Existing"}]
    (tmp_path / "projects.json").write_text(json.dumps(stored))
    # Don't create tmp_path / "projects" directory
    storage = _reload(tmp_path, monkeypatch)
    projects = storage.load_projects()
    assert any(p["name"] == "Existing" for p in projects)


# ── accumulate_stats ──────────────────────────────────────────────────────────

def test_accumulate_stats_adds_tokens(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    todos = [{"tokens": {"input": 100, "output": 50, "cache_creation": 0, "cache_read": 0}, "duration_secs": 10}]
    storage.accumulate_stats(todos)
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 100
    assert stats["total_output_tokens"] == 50
    assert stats["total_duration_secs"] == 10


def test_accumulate_stats_is_cumulative(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    storage.accumulate_stats([{"tokens": {"input": 100, "output": 50, "cache_creation": 0, "cache_read": 0}, "duration_secs": 10}])
    storage.accumulate_stats([{"tokens": {"input": 200, "output": 80, "cache_creation": 0, "cache_read": 0}, "duration_secs": 20}])
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 300
    assert stats["total_output_tokens"] == 130
    assert stats["total_duration_secs"] == 30


def test_accumulate_stats_accumulates_cache_tokens(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    todos = [{"tokens": {"input": 0, "output": 0, "cache_creation": 500, "cache_read": 1000}, "duration_secs": 0}]
    storage.accumulate_stats(todos)
    stats = storage.load_stats()
    assert stats["total_cache_creation_tokens"] == 500
    assert stats["total_cache_read_tokens"] == 1000


def test_accumulate_stats_skips_todos_without_tokens(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    storage.accumulate_stats([{"duration_secs": 5}])
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 0
    assert stats["total_duration_secs"] == 5


def test_accumulate_stats_skips_todos_without_duration(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    storage.accumulate_stats([{"tokens": {"input": 50, "output": 25, "cache_creation": 0, "cache_read": 0}}])
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 50
    assert stats["total_duration_secs"] == 0


def test_accumulate_stats_handles_empty_list(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    storage.accumulate_stats([])
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 0


def test_load_stats_returns_defaults_when_no_file(tmp_path, monkeypatch):
    storage = _reload(tmp_path, monkeypatch)
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 0
    assert stats["total_output_tokens"] == 0
    assert stats["total_duration_secs"] == 0
    assert stats["total_cache_creation_tokens"] == 0
    assert stats["total_cache_read_tokens"] == 0


def test_load_stats_merges_defaults_with_stored(tmp_path, monkeypatch):
    """Stored stats with missing keys still get defaults for missing fields."""
    (tmp_path / "stats.json").write_text(json.dumps({"total_input_tokens": 42}))
    storage = _reload(tmp_path, monkeypatch)
    stats = storage.load_stats()
    assert stats["total_input_tokens"] == 42
    assert stats["total_output_tokens"] == 0
    assert stats["total_cache_creation_tokens"] == 0
