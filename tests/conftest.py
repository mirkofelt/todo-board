import json
import os
import time
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TODO_BOARD_PROJECTS_DIR", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "General").mkdir()
    yield tmp_path


@pytest.fixture()
def app(data_dir):
    # Re-import so config picks up the patched env vars
    import importlib
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.server as server

    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(server)

    return server.app


@pytest.fixture()
def seed_todos(data_dir):
    def _seed(todos):
        (data_dir / "todos.json").write_text(json.dumps(todos))
    return _seed


@pytest.fixture()
def read_todos(data_dir):
    def _read():
        path = data_dir / "todos.json"
        if not path.exists():
            return []
        return json.loads(path.read_text())
    return _read
