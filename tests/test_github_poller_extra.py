"""Additional tests for github_poller coverage gaps: _load_seen, _fetch_release, run_release_poller."""
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


# ── _load_seen ────────────────────────────────────────────────────────────────

def test_load_seen_returns_empty_on_corrupt_file(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)
    (tmp_path / "github_seen_releases.json").write_text("{not valid json{{")
    assert poller._load_seen() == {}


# ── _fetch_release ────────────────────────────────────────────────────────────

def test_fetch_release_returns_none_on_network_error():
    from todo_board.github_poller import _fetch_release
    with mock.patch("urllib.request.urlopen", side_effect=Exception("network error")):
        assert _fetch_release("owner/repo") is None


def test_fetch_release_returns_parsed_json():
    from todo_board.github_poller import _fetch_release

    release_data = {"tag_name": "v1.2.3", "body": "Bug fixes"}
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = json.dumps(release_data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = mock.MagicMock(return_value=False)

    with mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = _fetch_release("owner/repo")

    assert result == release_data


def test_fetch_release_hits_correct_api_url():
    from todo_board.github_poller import _fetch_release

    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = b'{"tag_name": "v1.0"}'
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = mock.MagicMock(return_value=False)

    with mock.patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        _fetch_release("owner/myrepo")

    req = mock_urlopen.call_args[0][0]
    assert "owner/myrepo" in req.full_url
    assert "releases/latest" in req.full_url


# ── run_release_poller ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_release_poller_calls_poll_on_startup(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)

    poll_calls = []

    async def mock_poll():
        poll_calls.append(1)

    async def fast_sleep(_):
        raise asyncio.CancelledError()

    with mock.patch.object(poller, "poll_github_releases", side_effect=mock_poll):
        with mock.patch("asyncio.sleep", side_effect=fast_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poller.run_release_poller()

    assert len(poll_calls) >= 1


@pytest.mark.asyncio
async def test_run_release_poller_swallows_exception_on_first_poll(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)

    async def failing_poll():
        raise RuntimeError("poll exploded")

    async def fast_sleep(_):
        raise asyncio.CancelledError()

    with mock.patch.object(poller, "poll_github_releases", side_effect=failing_poll):
        with mock.patch("asyncio.sleep", side_effect=fast_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poller.run_release_poller()  # must not propagate RuntimeError


@pytest.mark.asyncio
async def test_run_release_poller_continues_after_failed_poll(tmp_path, monkeypatch):
    poller = _setup(tmp_path, monkeypatch)

    call_count = 0

    async def sometimes_failing_poll():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("transient error")

    sleep_calls = 0

    async def fast_sleep(_):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 1:
            raise asyncio.CancelledError()

    with mock.patch.object(poller, "poll_github_releases", side_effect=sometimes_failing_poll):
        with mock.patch("asyncio.sleep", side_effect=fast_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poller.run_release_poller()

    assert call_count >= 1
