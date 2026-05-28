"""Heartbeat script — detects stalled workers and re-queues interrupted todos.

Run on a cron or via the entry point to:
  1. Mark in_progress todos as context_limit if stalled >25 min
  2. Reset context_limit todos to pending
  3. Spawn workers for pending todos (one per project at a time)

Usage: python -m todo_board.heartbeat
"""
import time

from .config import CONTEXT_LIMIT_THRESHOLD
from .spawner import project_has_active_worker, spawn_worker
from .storage import load_todos, save_todos


def detect_and_fix_stalled() -> list:
    """Mark in_progress todos as context_limit if stalled beyond threshold."""
    todos = load_todos()
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
        save_todos(todos)
    return todos


def main() -> None:
    todos = detect_and_fix_stalled()
    non_done = [t for t in todos if t.get("status") not in ("done",) and not t.get("done")]

    if not non_done:
        print("No pending todos.")
        return

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
        save_todos(todos)

    now = int(time.time())
    pending = [t for t in todos if t.get("status") == "pending" and not t.get("done")]
    spawned_projects: set = set()

    to_spawn = []
    for t in sorted(pending, key=lambda x: x.get("created", 0)):
        pid = t.get("project_id")
        is_retry = t["id"] in context_limit_ids
        age = now - t.get("created", now)

        if not is_retry and age < 30:
            continue

        if pid is None:
            to_spawn.append(t)
            continue

        if pid not in spawned_projects and not project_has_active_worker(pid, todos):
            spawned_projects.add(pid)
            to_spawn.append(t)

    if to_spawn:
        for t in to_spawn:
            t["status"] = "in_progress"
            t["status_updated_at"] = int(time.time())
        save_todos(todos)
        for t in to_spawn:
            spawn_worker(t["id"])

    active = [t for t in non_done if t.get("status") not in ("context_limit", "done")]
    if active:
        print(f"{len(active)} pending todo(s):")
        for t in active:
            status = t.get("status", "pending")
            print(f"  [{t['id']}] [{status}] {t['text'][:80]}")


if __name__ == "__main__":
    main()
