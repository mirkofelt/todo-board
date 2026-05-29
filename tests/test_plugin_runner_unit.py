"""Unit tests for plugin_runner: _post_news and run_plugin."""
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


# ── _post_news ─────────────────────────────────────────────────────────────────

def test_post_news_success_creates_info_entry(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("MyPlugin", "done", "Processed 5 records successfully")
    news = storage.load_news()
    assert len(news) == 1
    assert news[0]["type"] == "info"
    assert "MyPlugin" in news[0]["message"]


def test_post_news_failure_creates_error_entry(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("MyPlugin", "failed", "connection refused")
    news = storage.load_news()
    assert len(news) == 1
    assert news[0]["type"] == "error"
    assert "MyPlugin" in news[0]["message"]


def test_post_news_strips_asterisks_from_first_line(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("Checker", "done", "**Summary** of results\nMore detail")
    news = storage.load_news()
    assert "**" not in news[0]["message"]
    assert "Summary" in news[0]["message"]


def test_post_news_uses_first_non_empty_line(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("Checker", "done", "\n\nFirst real line\nSecond line")
    news = storage.load_news()
    assert "First real line" in news[0]["message"]
    assert "Second line" not in news[0]["message"]


def test_post_news_failure_includes_result_snippet(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("BadPlugin", "failed", "timeout after 30s")
    news = storage.load_news()
    assert "timeout after 30s" in news[0]["message"]


def test_post_news_marks_entry_unread(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("X", "done", "all good")
    news = storage.load_news()
    assert news[0]["read"] is False


def test_post_news_assigns_sequential_id(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    runner._post_news("X", "done", "first")
    runner._post_news("Y", "done", "second")
    news = storage.load_news()
    ids = {n["id"] for n in news}
    assert len(ids) == 2


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
async def test_run_plugin_posts_news_on_success(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"All done!\n", b"", 0))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("notifier", {"name": "Notifier", "path": str(tmp_path), "command": ["echo"]})

    news = storage.load_news()
    assert len(news) == 1
    assert news[0]["type"] == "info"


@pytest.mark.asyncio
async def test_run_plugin_posts_error_news_on_failure(tmp_path, monkeypatch):
    runner, storage = _setup(tmp_path, monkeypatch)
    mock_exec = mock.AsyncMock(return_value=_mock_proc(b"", b"crash", 1))

    with mock.patch("asyncio.create_subprocess_exec", mock_exec):
        await runner.run_plugin("crasher", {"name": "Crasher", "path": str(tmp_path), "command": ["false"]})

    news = storage.load_news()
    assert news[0]["type"] == "error"


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
