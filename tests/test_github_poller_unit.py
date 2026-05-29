"""Unit tests for github_poller.poll_github_releases."""
import importlib
import json
import pytest


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.config as cfg
    import todo_board.storage as storage
    import todo_board.github_poller as poller
    importlib.reload(cfg)
    importlib.reload(storage)
    importlib.reload(poller)
    return poller, storage


@pytest.mark.asyncio
async def test_poll_returns_zero_when_no_links_file(tmp_path, monkeypatch):
    poller, _ = _setup(tmp_path, monkeypatch)
    count = await poller.poll_github_releases()
    assert count == 0


@pytest.mark.asyncio
async def test_poll_first_run_initializes_seen_silently(tmp_path, monkeypatch):
    """On first run, no news is posted but the seen file is created."""
    poller, storage = _setup(tmp_path, monkeypatch)
    links = {"my-app": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: {"tag_name": "v1.0.0", "body": ""})

    count = await poller.poll_github_releases()

    assert count == 0
    assert len(storage.load_news()) == 0
    seen = json.loads((tmp_path / "github_seen_releases.json").read_text())
    assert seen.get("owner/repo") == "v1.0.0"


@pytest.mark.asyncio
async def test_poll_same_version_posts_no_news(tmp_path, monkeypatch):
    """When the seen version matches the current release, no news is posted."""
    poller, storage = _setup(tmp_path, monkeypatch)
    links = {"my-app": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({"owner/repo": "v1.0.0"}))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: {"tag_name": "v1.0.0", "body": ""})

    count = await poller.poll_github_releases()

    assert count == 0
    assert len(storage.load_news()) == 0


@pytest.mark.asyncio
async def test_poll_new_version_posts_news(tmp_path, monkeypatch):
    """A new release tag triggers a news item."""
    poller, storage = _setup(tmp_path, monkeypatch)
    links = {"my-app": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({"owner/repo": "v1.0.0"}))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: {"tag_name": "v2.0.0", "body": "Big update"})

    count = await poller.poll_github_releases()

    assert count == 1
    news = storage.load_news()
    assert len(news) == 1
    assert "v2.0.0" in news[0]["message"]
    assert "my-app" in news[0]["message"]
    assert news[0]["type"] == "info"
    assert not news[0]["read"]


@pytest.mark.asyncio
async def test_poll_news_includes_body_snippet(tmp_path, monkeypatch):
    """Release body is included in the news message."""
    poller, storage = _setup(tmp_path, monkeypatch)
    links = {"proj": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({"owner/repo": "v1.0.0"}))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: {"tag_name": "v1.1.0", "body": "Fixed important bug"})

    await poller.poll_github_releases()

    news = storage.load_news()
    assert "Fixed important bug" in news[0]["message"]


@pytest.mark.asyncio
async def test_poll_updates_seen_on_new_version(tmp_path, monkeypatch):
    """Seen file is updated to the new tag after detection."""
    poller, _ = _setup(tmp_path, monkeypatch)
    links = {"my-app": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({"owner/repo": "v1.0.0"}))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: {"tag_name": "v2.0.0", "body": ""})

    await poller.poll_github_releases()

    seen = json.loads((tmp_path / "github_seen_releases.json").read_text())
    assert seen["owner/repo"] == "v2.0.0"


@pytest.mark.asyncio
async def test_poll_skips_non_github_links(tmp_path, monkeypatch):
    """Non-GitHub URLs produce no news and no error."""
    poller, _ = _setup(tmp_path, monkeypatch)
    links = {"my-app": "https://gitlab.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))

    called = []
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: called.append(slug) or None)

    count = await poller.poll_github_releases()

    assert count == 0
    assert called == []


@pytest.mark.asyncio
async def test_poll_skips_repo_when_fetch_returns_none(tmp_path, monkeypatch):
    """If _fetch_release returns None the repo is silently skipped."""
    poller, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({"owner/repo": "v1.0.0"}))
    links = {"my-app": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: None)

    count = await poller.poll_github_releases()
    assert count == 0


@pytest.mark.asyncio
async def test_poll_skips_release_with_no_tag(tmp_path, monkeypatch):
    """Releases without a tag name are ignored."""
    poller, storage = _setup(tmp_path, monkeypatch)
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({"owner/repo": "v1.0.0"}))
    links = {"my-app": "https://github.com/owner/repo"}
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    monkeypatch.setattr(poller, "_fetch_release", lambda slug: {"tag_name": "", "body": ""})

    count = await poller.poll_github_releases()
    assert count == 0
    assert len(storage.load_news()) == 0


@pytest.mark.asyncio
async def test_poll_handles_invalid_links_json(tmp_path, monkeypatch):
    """A corrupt links file does not crash — returns 0."""
    poller, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "github_links.json").write_text("not valid json{{{")

    count = await poller.poll_github_releases()
    assert count == 0


@pytest.mark.asyncio
async def test_poll_multiple_repos_independently(tmp_path, monkeypatch):
    """Each repo is checked independently; one new release = count 1."""
    poller, storage = _setup(tmp_path, monkeypatch)
    links = {
        "app-a": "https://github.com/owner/repo-a",
        "app-b": "https://github.com/owner/repo-b",
    }
    (tmp_path / "github_links.json").write_text(json.dumps(links))
    (tmp_path / "github_seen_releases.json").write_text(json.dumps({
        "owner/repo-a": "v1.0.0",
        "owner/repo-b": "v1.0.0",
    }))

    def fake_fetch(slug):
        if slug == "owner/repo-a":
            return {"tag_name": "v2.0.0", "body": ""}
        return {"tag_name": "v1.0.0", "body": ""}

    monkeypatch.setattr(poller, "_fetch_release", fake_fetch)

    count = await poller.poll_github_releases()
    assert count == 1
    assert len(storage.load_news()) == 1
    assert "app-a" in storage.load_news()[0]["message"]
