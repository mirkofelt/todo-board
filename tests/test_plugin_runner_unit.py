"""Unit tests for plugin_runner: run_plugin."""
import asyncio
import importlib
import pytest
from unittest import mock


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.plugin_runner as runner
    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(runner)
    return runner, storage


def _mock_proc(stdout: bytes = b"output", stderr: bytes = b"", returncode: int = 0):
    proc = mock.MagicMock()
    proc.communicate = mock.AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


# ── run_plugin ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_plugin_success_sets_done_status(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"Plugin output\n", b"", 0))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("test-plugin", {
            "name": "Test Plugin",
            "path": str(tmp_path),
            "command": ["echo", "hello"],
        })

    states = storage.load_plugin_states()
    assert states["test-plugin"]["status"] == "done"


@pytest.mark.asyncio
async def test_run_plugin_failure_sets_failed_status(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"", b"error message", 1))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("bad-plugin", {
            "name": "Bad Plugin",
            "path": str(tmp_path),
            "command": ["false"],
        })

    states = storage.load_plugin_states()
    assert states["bad-plugin"]["status"] == "failed"


@pytest.mark.asyncio
async def test_run_plugin_stores_result(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"my result output\n", b"", 0))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("p", {"name": "P", "path": str(tmp_path), "command": ["echo"]})

    states = storage.load_plugin_states()
    assert "my result output" in states["p"]["result"]


@pytest.mark.asyncio
async def test_run_plugin_removes_from_running_after_success(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"done", b"", 0))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("p", {"name": "P", "path": str(tmp_path), "command": ["echo"]})

    assert not runner.is_running("p")


@pytest.mark.asyncio
async def test_run_plugin_removes_from_running_after_failure(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"", b"", 1))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("p", {"name": "P", "path": str(tmp_path), "command": ["false"]})

    assert not runner.is_running("p")


@pytest.mark.asyncio
async def test_run_plugin_noop_when_already_running(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._running.add("my-plugin")

    called = []
    mock_exec = mock.AsyncMock(side_effect=lambda *a, **kw: called.append(1))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("my-plugin", {"name": "X", "path": ".", "command": ["echo"]})

    assert called == []


@pytest.mark.asyncio
async def test_run_plugin_filters_progress_lines_from_result(tmp_path, monkeypatch):
    """Lines starting with '[' or '  →' are stripped from the final result."""
    runner, storage = _setup(tmp_path, monkeypatch)
    output = "[step 1/3]\n  → doing work\nActual result line\n".encode()
    mock_exec = mock.AsyncMock(return_value=_mock_proc(output, b"", 0))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("p", {"name": "P", "path": str(tmp_path), "command": ["echo"]})

    states = storage.load_plugin_states()
    assert "[step 1/3]" not in states["p"]["result"]
    assert "Actual result line" in states["p"]["result"]


@pytest.mark.asyncio
async def test_run_plugin_falls_back_to_stderr_when_no_stdout(tmp_path, monkeypatch):
    """When stdout is empty, stderr is used as the result."""
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"", b"diagnostic info", 0))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("p", {"name": "P", "path": str(tmp_path), "command": ["echo"]})

    states = storage.load_plugin_states()
    assert "diagnostic info" in states["p"]["result"]
