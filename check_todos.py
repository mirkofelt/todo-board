"""Reads pending todos from todos.json and prints them for heartbeat processing.

Also detects stalled in_progress todos (no status change in >25min) and marks them
as context_limit. On the next run, context_limit todos are re-queued by spawning
a fresh worker subprocess.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

DATA_FILE = Path(__file__).parent / "todos.json"

CONTEXT_LIMIT_THRESHOLD = 25 * 60  # seconds — if in_progress longer than this, assume context limit


def load() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text())


def save(todos: list[dict]):
    DATA_FILE.write_text(json.dumps(todos, ensure_ascii=False, indent=2))


def get_pending() -> list[dict]:
    return [t for t in load() if not t.get("done")]


def detect_and_fix_stalled():
    """Mark in_progress todos as context_limit if status hasn't changed in >25 min."""
    todos = load()
    now = int(time.time())
    changed = False
    for t in todos:
        if t.get("status") == "in_progress":
            updated_at = t.get("status_updated_at", t.get("created", now))
            if now - updated_at > CONTEXT_LIMIT_THRESHOLD:
                t["status"] = "context_limit"
                t["status_updated_at"] = now
                changed = True
    if changed:
        save(todos)
    return todos


def _project_has_active_worker(project_id, todos: list) -> bool:
    if project_id is None:
        return False
    return any(
        t.get("project_id") == project_id and t.get("status") == "in_progress"
        for t in todos
    )


def _spawn_worker(todo_id: int) -> None:
    worker = DATA_FILE.parent / "todo_worker.py"
    subprocess.Popen(
        [sys.executable, str(worker), str(todo_id)],
        cwd=str(Path.home()),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    todos = detect_and_fix_stalled()
    non_done = [t for t in todos if t.get("status") not in ("done",) and not t.get("done")]

    if not non_done:
        print("No pending todos.")
    else:
        # Collect and reset context_limit todos back to pending for retry
        context_limit_ids = {t["id"] for t in non_done if t.get("status") == "context_limit"}
        if context_limit_ids:
            print(f"{len(context_limit_ids)} todo(s) hit context limit — retrying:")
            for t in non_done:
                if t["id"] in context_limit_ids:
                    print(f"  [{t['id']}] {t['text'][:80]}")
                    for td in todos:
                        if td["id"] == t["id"]:
                            td["status"] = "pending"
                            td["status_updated_at"] = int(time.time())
            save(todos)

        # Spawn workers: one active worker per project at a time.
        # Non-retry pending todos were already spawned by app.py on creation;
        # skip ones younger than 30s to avoid racing with workers not yet started.
        now = int(time.time())
        pending = [t for t in todos if t.get("status") == "pending" and not t.get("done")]
        spawned_projects: set = set()

        for t in sorted(pending, key=lambda x: x.get("created", 0)):
            pid = t.get("project_id")
            is_retry = t["id"] in context_limit_ids
            age = now - t.get("created", now)

            if not is_retry and age < 30:
                continue

            if pid is None:
                _spawn_worker(t["id"])
                continue

            if pid not in spawned_projects and not _project_has_active_worker(pid, todos):
                spawned_projects.add(pid)
                _spawn_worker(t["id"])

        active = [t for t in non_done if t.get("status") not in ("context_limit", "done")]
        if active:
            print(f"{len(active)} pending todo(s):")
            for t in active:
                status = t.get("status", "pending")
                print(f"  [{t['id']}] [{status}] {t['text'][:80]}")
