import asyncio
import os
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .config import DATA_DIR, MAX_RETRIES, NEWS_FILE, PLUGIN_STATES_FILE, PROJECTS_DIR, TODOS_FILE
from .github_poller import poll_github_releases, run_release_poller
from .plugin_runner import is_running, run_plugin
from .spawner import project_has_active_worker, spawn_worker
from .storage import (
    accumulate_stats,
    load_counter,
    load_github_links,
    load_news,
    load_plugin_states,
    load_plugins,
    load_projects,
    load_rules,
    load_stats,
    load_statusline,
    load_todos,
    save_counter,
    save_github_links,
    save_news,
    save_projects,
    save_rules,
    save_statusline,
    save_todos,
)

_TEMPLATE = Path(__file__).parent / "templates" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_release_poller())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


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
    new_id = max(max((t["id"] for t in todos), default=0), load_counter()) + 1
    save_counter(new_id)
    project_id = body.get("project_id")
    model = (body.get("model") or "").strip() or None
    prev_task_id = body.get("prev_task_id")
    active = project_has_active_worker(project_id, todos)
    will_spawn = not active
    entry: dict = {
        "id": new_id,
        "text": text,
        "done": False,
        "status": "in_progress" if will_spawn else "pending",
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
    }
    if model:
        entry["model"] = model
    if prev_task_id is not None:
        entry["prev_task_id"] = int(prev_task_id)
    todos.insert(0, entry)
    save_todos(todos)
    if will_spawn:
        spawn_worker(new_id)
    return {"ok": True, "id": new_id}


@app.post("/api/breakdown")
async def breakdown_todo(request: Request):
    from .breakdown import breakdown_task
    body = await request.json()
    text = (body.get("text") or "").strip()
    project_id = body.get("project_id")
    if not text:
        return JSONResponse({"ok": False, "error": "Text required"}, status_code=400)
    tasks, error = await asyncio.to_thread(breakdown_task, text, project_id)
    if not tasks:
        return JSONResponse({"ok": False, "error": error or "Could not break down task"}, status_code=500)
    return JSONResponse({"ok": True, "tasks": tasks})


@app.post("/api/status/{todo_id}")
async def set_status(todo_id: int, request: Request):
    body = await request.json()
    status = body.get("status", "pending")
    duration_secs = body.get("duration_secs")
    tokens = body.get("tokens")
    result = body.get("result")
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
            if result is not None and status == "done":
                t["result"] = str(result)[:3000]
            break
    save_todos(todos)
    # context_limit: re-queue or fail after MAX_RETRIES
    if status == "context_limit":
        stalled = next((t for t in todos if t["id"] == todo_id), None)
        if stalled:
            retry_count = stalled.get("retry_count", 0) + 1
            stalled["retry_count"] = retry_count
            if retry_count > MAX_RETRIES:
                stalled["status"] = "failed"
                stalled["status_updated_at"] = int(time.time())
                stalled["progress"] = None
                stalled["note"] = f"Exceeded max retries ({MAX_RETRIES})"
                save_todos(todos)
            else:
                pid = stalled.get("project_id")
                other_active = any(
                    t["id"] != todo_id and t.get("project_id") == pid and t.get("status") == "in_progress"
                    for t in todos
                )
                if other_active:
                    stalled["status"] = "pending"
                    stalled["status_updated_at"] = int(time.time())
                    stalled["progress"] = None
                else:
                    stalled["status"] = "in_progress"
                    stalled["status_updated_at"] = int(time.time())
                    stalled["progress"] = f"Retry {retry_count}/{MAX_RETRIES} after context limit…"
                save_todos(todos)
                if not other_active:
                    spawn_worker(todo_id)
    # When a worker finishes, start the next pending todo in the same project
    elif status in ("done", "failed", "canceled"):
        finished = next((t for t in todos if t["id"] == todo_id), None)
        if finished and finished.get("project_id"):
            pid = finished["project_id"]
            if not project_has_active_worker(pid, todos):
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
        if not project_has_active_worker(project_id, todos):
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
    project_id = todo.get("project_id")
    if project_has_active_worker(project_id, todos):
        # Another task is already running in this project — queue behind it
        todo["status"] = "pending"
        todo["status_updated_at"] = int(time.time())
        todo["progress"] = None
        save_todos(todos)
    else:
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


@app.post("/api/reorder")
async def reorder_todos(request: Request):
    body = await request.json()
    ids = [int(i) for i in (body.get("ids") or [])]
    if not ids:
        return {"ok": True}
    todos = load_todos()
    id_set = set(ids)
    id_to_todo = {t["id"]: t for t in todos}
    # Positions (ascending) of todos being reordered in current array
    positions = sorted(i for i, t in enumerate(todos) if t["id"] in id_set)
    # ids[0] = runs first = needs highest array index (reversed() picks last element first)
    result = list(todos)
    for pos, todo_id in zip(reversed(positions), ids):
        if todo_id in id_to_todo:
            result[pos] = id_to_todo[todo_id]
    save_todos(result)
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
    model = (body.get("model") or "").strip() or None
    (PROJECTS_DIR / name).mkdir(exist_ok=True)
    projects = load_projects()
    proj = next((p for p in projects if p["name"] == name), None)
    if proj and model is not None:
        proj["model"] = model
        save_projects(projects)
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
    news_mtime = NEWS_FILE.stat().st_mtime if NEWS_FILE.exists() else 0
    news_unread = sum(1 for n in load_news() if not n.get("read"))
    plugin_states_mtime = PLUGIN_STATES_FILE.stat().st_mtime if PLUGIN_STATES_FILE.exists() else 0
    return JSONResponse({
        "mtime": mtime,
        "news_mtime": news_mtime,
        "news_unread": news_unread,
        "plugin_states_mtime": plugin_states_mtime,
    })


@app.get("/api/version")
def get_version():
    mtime = max(
        Path(__file__).stat().st_mtime,
        _TEMPLATE.stat().st_mtime,
    )
    return JSONResponse({"version": mtime})


@app.get("/api/news")
def get_news():
    return JSONResponse(load_news())


@app.post("/api/news")
async def create_news(request: Request):
    body = await request.json()
    msg_type = body.get("type", "info")
    if msg_type not in ("info", "warning", "error"):
        msg_type = "info"
    message = (body.get("message") or "").strip()[:500]
    if not message:
        return JSONResponse({"ok": False, "error": "Message required"}, status_code=400)
    news = load_news()
    new_id = max((n["id"] for n in news), default=0) + 1
    entry = {
        "id": new_id,
        "type": msg_type,
        "message": message,
        "todo_id": body.get("todo_id"),
        "project_id": body.get("project_id"),
        "created": int(time.time()),
        "read": False,
    }
    news.insert(0, entry)
    # Keep last 200 news items
    save_news(news[:200])
    return {"ok": True, "id": new_id}


@app.post("/api/news/mark-read")
async def mark_news_read(request: Request):
    body = await request.json()
    ids = body.get("ids")  # None = mark all read
    news = load_news()
    for n in news:
        if ids is None or n["id"] in ids:
            n["read"] = True
    save_news(news)
    return {"ok": True}


@app.post("/api/news/clear")
def clear_news():
    save_news([])
    return {"ok": True}


@app.post("/api/poll-releases")
async def trigger_release_poll():
    count = await poll_github_releases()
    return {"ok": True, "new_releases": count}


@app.get("/api/github-links")
def get_github_links():
    return JSONResponse(load_github_links())


@app.post("/api/github-links")
async def set_github_links(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Expected object"}, status_code=400)
    save_github_links(body)
    return {"ok": True}


@app.get("/api/plugins")
def get_plugins():
    definitions = load_plugins()
    states = load_plugin_states()
    result = {}
    for key, plugin in definitions.items():
        state = states.get(key, {})
        result[key] = {
            "name": plugin.get("name", key),
            "description": plugin.get("description", ""),
            "status": "running" if is_running(key) else state.get("status", "idle"),
            "last_run_at": state.get("last_run_at"),
            "result": state.get("result", ""),
        }
    return JSONResponse(result)


@app.post("/api/plugins/{name}/run")
async def trigger_plugin(name: str):
    definitions = load_plugins()
    plugin = definitions.get(name)
    if not plugin:
        return JSONResponse({"ok": False, "error": "Plugin not found"}, status_code=404)
    if is_running(name):
        return JSONResponse({"ok": False, "error": "Already running"}, status_code=409)
    asyncio.create_task(run_plugin(name, plugin))
    return {"ok": True}


@app.get("/api/requirements")
def get_requirements():
    return PlainTextResponse(load_rules())


@app.post("/api/requirements")
async def set_requirements(request: Request):
    save_rules((await request.body()).decode("utf-8"))
    return {"ok": True}
