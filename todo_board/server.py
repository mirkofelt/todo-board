import os
import signal
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .config import DATA_DIR, PROJECTS_DIR, TODOS_FILE
from .spawner import project_has_active_worker, spawn_worker
from .storage import (
    accumulate_stats,
    load_projects,
    load_rules,
    load_stats,
    load_statusline,
    load_todos,
    save_projects,
    save_rules,
    save_statusline,
    save_todos,
)

_TEMPLATE = Path(__file__).parent / "templates" / "index.html"

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_TEMPLATE.read_text())


@app.get("/api/todos")
def get_todos():
    return JSONResponse(load_todos())


@app.post("/api/add")
async def add_todo(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False}, status_code=400)
    todos = load_todos()
    new_id = max((t["id"] for t in todos), default=0) + 1
    project_id = body.get("project_id")
    active = project_has_active_worker(project_id, todos)
    will_spawn = not active
    todos.insert(0, {
        "id": new_id,
        "text": text,
        "done": False,
        "status": "in_progress" if will_spawn else "pending",
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
    })
    save_todos(todos)
    if will_spawn:
        spawn_worker(new_id)
    return {"ok": True, "id": new_id}


@app.post("/api/status/{todo_id}")
async def set_status(todo_id: int, request: Request):
    body = await request.json()
    status = body.get("status", "pending")
    duration_secs = body.get("duration_secs")
    tokens = body.get("tokens")
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["status"] = status
            t["status_updated_at"] = int(time.time())
            t["done"] = status == "done"
            if status in ("done", "failed", "blocked", "pending"):
                t["progress"] = None
            if duration_secs is not None:
                t["duration_secs"] = int(duration_secs)
            if tokens:
                t["tokens"] = tokens
            break
    save_todos(todos)
    # context_limit: immediately re-queue the same todo (fresh worker, fresh context)
    if status == "context_limit":
        stalled = next((t for t in todos if t["id"] == todo_id), None)
        if stalled:
            stalled["status"] = "in_progress"
            stalled["status_updated_at"] = int(time.time())
            stalled["progress"] = "Retrying after context limit…"
            save_todos(todos)
            spawn_worker(todo_id)
    # When a worker finishes, start the next pending todo in the same project
    elif status in ("done", "failed", "canceled"):
        finished = next((t for t in todos if t["id"] == todo_id), None)
        if finished and finished.get("project_id"):
            pid = finished["project_id"]
            next_todo = next(
                (t for t in reversed(todos)
                 if t.get("project_id") == pid and t.get("status") == "pending" and not t.get("locked")),
                None,
            )
            if next_todo:
                next_todo["status"] = "in_progress"
                next_todo["status_updated_at"] = int(time.time())
                save_todos(todos)
                spawn_worker(next_todo["id"])
    return {"ok": True}


@app.post("/api/progress/{todo_id}")
async def set_progress(todo_id: int, request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()[:150] or None
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["progress"] = text
            break
    save_todos(todos)
    return {"ok": True}


@app.post("/api/note/{todo_id}")
async def set_note(todo_id: int, request: Request):
    body = await request.json()
    note = (body.get("note") or "").strip() or None
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["note"] = note
            break
    save_todos(todos)
    return {"ok": True}


@app.post("/api/done/{todo_id}")
def mark_done(todo_id: int):
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["done"] = True
            t["status"] = "done"
            t["status_updated_at"] = int(time.time())
            break
    save_todos(todos)
    return {"ok": True}


@app.post("/api/delete-done")
def delete_all_done():
    todos = load_todos()
    to_delete = [t for t in todos if t.get("done") or t.get("status") == "canceled"]
    accumulate_stats(to_delete)
    save_todos([t for t in todos if not t.get("done") and t.get("status") != "canceled"])
    return {"ok": True}


@app.post("/api/delete/{todo_id}")
def delete_todo(todo_id: int):
    todos = load_todos()
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if todo and todo.get("status") == "in_progress":
        return JSONResponse({"ok": False, "error": "Cannot delete an in-progress todo"}, status_code=409)
    if todo and (todo.get("done") or todo.get("status") in ("failed", "canceled", "context_limit")):
        accumulate_stats([todo])
    save_todos([t for t in todos if t["id"] != todo_id])
    return {"ok": True}


@app.post("/api/cancel/{todo_id}")
def cancel_todo(todo_id: int):
    pid_file = DATA_DIR / f"worker_{todo_id}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError, OSError):
            pass
        pid_file.unlink(missing_ok=True)

    work_dir = os.environ.get("CLAUDE_WORK_DIR") or str(Path.home())
    try:
        subprocess.run(
            ["git", "-C", work_dir, "reset", "--hard", "HEAD"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "-C", work_dir, "clean", "-fd"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["status"] = "canceled"
            t["done"] = False
            t["status_updated_at"] = int(time.time())
            t["progress"] = None
            break
    save_todos(todos)

    canceled = next((t for t in todos if t["id"] == todo_id), None)
    if canceled and canceled.get("project_id"):
        project_id = canceled["project_id"]
        next_todo = next(
            (t for t in reversed(todos)
             if t.get("project_id") == project_id and t.get("status") == "pending" and not t.get("locked")),
            None,
        )
        if next_todo:
            next_todo["status"] = "in_progress"
            next_todo["status_updated_at"] = int(time.time())
            save_todos(todos)
            spawn_worker(next_todo["id"])

    return {"ok": True}


@app.post("/api/resume/{todo_id}")
def resume_todo(todo_id: int):
    todos = load_todos()
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if not todo:
        return JSONResponse({"ok": False, "error": "Todo not found"}, status_code=404)
    if todo.get("status") != "context_limit":
        return JSONResponse({"ok": False, "error": "Todo is not interrupted"}, status_code=409)
    todo["status"] = "in_progress"
    todo["status_updated_at"] = int(time.time())
    todo["progress"] = "Resuming after context limit…"
    save_todos(todos)
    spawn_worker(todo_id)
    return {"ok": True}


@app.post("/api/lock/{todo_id}")
async def lock_todo(todo_id: int, request: Request):
    body = await request.json()
    locked = bool(body.get("locked", True))
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            if t.get("status") == "in_progress":
                return JSONResponse({"ok": False, "error": "Cannot lock an in-progress todo"}, status_code=409)
            t["locked"] = locked
            break
    save_todos(todos)
    return {"ok": True}


@app.post("/api/edit/{todo_id}")
async def edit_todo(todo_id: int, request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Text cannot be empty"}, status_code=400)
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            if t.get("status") == "in_progress":
                return JSONResponse({"ok": False, "error": "Cannot edit an in-progress todo"}, status_code=409)
            t["text"] = text
            t["locked"] = False
            break
    save_todos(todos)
    return {"ok": True}


@app.get("/api/statusline")
def get_statusline():
    return JSONResponse(load_statusline())


@app.post("/api/statusline")
async def set_statusline(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    save_statusline({"text": text, "updated_at": int(time.time())})
    return {"ok": True}


@app.get("/api/projects")
def get_projects():
    return JSONResponse(load_projects())


@app.post("/api/projects/add")
async def add_project(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False}, status_code=400)
    (PROJECTS_DIR / name).mkdir(exist_ok=True)
    projects = load_projects()
    proj = next((p for p in projects if p["name"] == name), None)
    return {"ok": True, "id": proj["id"] if proj else None}


@app.post("/api/projects/delete/{project_id}")
def delete_project(project_id: int):
    save_projects([p for p in load_projects() if p["id"] != project_id])
    todos = load_todos()
    for t in todos:
        if t.get("project_id") == project_id:
            t["project_id"] = None
    save_todos(todos)
    return {"ok": True}


@app.get("/api/stats")
def get_stats():
    return JSONResponse(load_stats())


@app.get("/api/state")
def get_state():
    mtime = TODOS_FILE.stat().st_mtime if TODOS_FILE.exists() else 0
    return JSONResponse({"mtime": mtime})


@app.get("/api/version")
def get_version():
    mtime = max(
        Path(__file__).stat().st_mtime,
        _TEMPLATE.stat().st_mtime,
    )
    return JSONResponse({"version": mtime})


@app.get("/api/requirements")
def get_requirements():
    return PlainTextResponse(load_rules())


@app.post("/api/requirements")
async def set_requirements(request: Request):
    save_rules((await request.body()).decode("utf-8"))
    return {"ok": True}
