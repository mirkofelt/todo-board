import subprocess
import sys
from pathlib import Path

_WORKER = Path(__file__).parent / "worker.py"


def project_has_active_worker(project_id, todos: list) -> bool:
    if project_id is None:
        return False
    # Parent tasks in "working" state supervise subtasks but have no active worker process.
    # Exclude them so _spawn_next_pending can start their subtasks.
    parent_ids = {t["parent_id"] for t in todos if t.get("parent_id") is not None}
    return any(
        t.get("project_id") == project_id
        and (
            t.get("status") in ("in_progress", "planning")
            or (t.get("status") == "working" and t["id"] not in parent_ids)
        )
        for t in todos
    )


def spawn_worker(todo_id: int) -> None:
    subprocess.Popen(
        [sys.executable, str(_WORKER), str(todo_id)],
        cwd=str(Path.home()),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
