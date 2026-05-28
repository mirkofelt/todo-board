import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .spawner import project_has_active_worker, spawn_worker
from .storage import (
    load_projects,
    load_rules,
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
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["status"] = status
            t["status_updated_at"] = int(time.time())
            t["done"] = status == "done"
            if status in ("done", "failed", "blocked", "pending"):
                t["progress"] = None
            break
    save_todos(todos)
    # When a worker finishes, start the next pending todo in the same project
    if status in ("done", "failed"):
        finished = next((t for t in todos if t["id"] == todo_id), None)
        if finished and finished.get("project_id"):
            pid = finished["project_id"]
            next_todo = next(
                (t for t in reversed(todos)
                 if t.get("project_id") == pid and t.get("status") == "pending"),
                None,
            )
            if next_todo:
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


@app.post("/api/delete/{todo_id}")
def delete_todo(todo_id: int):
    todos = load_todos()
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if todo and todo.get("status") == "in_progress":
        return JSONResponse({"ok": False, "error": "Cannot delete an in-progress todo"}, status_code=409)
    save_todos([t for t in todos if t["id"] != todo_id])
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
    projects = load_projects()
    new_id = max((p["id"] for p in projects), default=0) + 1
    projects.append({"id": new_id, "name": name})
    save_projects(projects)
    return {"ok": True, "id": new_id}


@app.post("/api/projects/delete/{project_id}")
def delete_project(project_id: int):
    save_projects([p for p in load_projects() if p["id"] != project_id])
    todos = load_todos()
    for t in todos:
        if t.get("project_id") == project_id:
            t["project_id"] = None
    save_todos(todos)
    return {"ok": True}


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
