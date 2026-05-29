"""Background poller: checks GitHub repos for new releases and posts them as news."""
import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

from .config import DATA_DIR, GITHUB_LINKS_FILE
from .storage import load_news, save_news

_SEEN_FILE = DATA_DIR / "github_seen_releases.json"
POLL_INTERVAL = int(os.environ.get("GITHUB_POLL_INTERVAL", str(6 * 3600)))  # 6 h default


def _load_seen() -> dict:
    if not _SEEN_FILE.exists():
        return {}
    try:
        return json.loads(_SEEN_FILE.read_text())
    except Exception:
        return {}


def _save_seen(seen: dict) -> None:
    _SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


def _repo_slug(url: str) -> str | None:
    if "github.com/" in url:
        parts = url.split("github.com/", 1)[-1].rstrip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return None


def _fetch_release(slug: str) -> dict | None:
    url = f"https://api.github.com/repos/{slug}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "todo-board/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


async def poll_github_releases() -> int:
    """Poll all repos for new releases. Returns number of new releases posted."""
    if not GITHUB_LINKS_FILE.exists():
        return 0
    try:
        links: dict = json.loads(GITHUB_LINKS_FILE.read_text())
    except Exception:
        return 0

    seen = _load_seen()
    first_run = not _SEEN_FILE.exists()
    new_count = 0
    seen_changed = False

    for project_name, repo_url in links.items():
        slug = _repo_slug(repo_url)
        if not slug:
            continue

        data = await asyncio.to_thread(_fetch_release, slug)
        if not data:
            continue

        tag = (data.get("tag_name") or "").strip()
        if not tag:
            continue

        prev_tag = seen.get(slug)
        seen[slug] = tag
        seen_changed = True

        # Silently initialize on first run or when a new repo is added to the list
        if first_run or prev_tag is None or prev_tag == tag:
            continue

        # New release — post news
        body = (data.get("body") or "").strip()
        snippet = " ".join(body.split())[:100] if body else ""
        msg = f"{project_name} {tag} released"
        if snippet:
            msg += f": {snippet}"

        release_url = f"{repo_url.rstrip('/')}/releases/tag/{tag}"

        news = load_news()
        new_id = max((n["id"] for n in news), default=0) + 1
        news.insert(0, {
            "id": new_id,
            "type": "info",
            "message": msg[:500],
            "todo_id": None,
            "project_id": None,
            "created": int(time.time()),
            "read": False,
            "url": release_url,
        })
        save_news(news[:200])
        new_count += 1

    if seen_changed:
        _save_seen(seen)

    return new_count


async def run_release_poller() -> None:
    """Loop: initial poll on startup, then every POLL_INTERVAL seconds."""
    try:
        await poll_github_releases()
    except Exception:
        pass

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            await poll_github_releases()
        except Exception:
            pass
