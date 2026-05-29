import asyncio
import os
import signal
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from .config import CRYPTO_STATE_FILE, DATA_DIR, MAX_RETRIES, MEMORY_BACKUP_FILE, MEMORY_FILE, PLUGIN_STATES_FILE, PROJECTS_DIR, TODOS_FILE
from .plugin_runner import is_running, run_plugin
from .spawner import project_has_active_worker, spawn_worker
from .storage import (
    accumulate_stats,
    load_counter,
    load_crypto_state,
    load_github_links,
    load_plugin_states,
    load_plugins,
    load_projects,
    load_rules,
    load_stats,
    load_statusline,
    load_todos,
    save_counter,
    save_crypto_state,
    save_github_links,
    save_projects,
    save_rules,
    save_statusline,
    save_todos,
)

_TEMPLATE = Path(__file__).parent / "templates" / "index.html"

_crypto_refresh_lock = asyncio.Lock()


async def _run_crypto_refresh(symbol: str = "BTC-USD") -> dict:
    """Fetch all crypto data and persist to crypto_state.json. Thread-safe via lock."""
    async with _crypto_refresh_lock:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch_crypto_data, symbol)


def _fetch_crypto_data(symbol: str = "BTC-USD") -> dict:
    """Blocking: runs btc_outlook data pipeline and returns serializable state dict."""
    try:
        from btc_outlook.analysis.macro import collect_macro_signals
        from btc_outlook.analysis.scenarios import build_scenarios
        from btc_outlook.analysis.waves import get_wave_forecast, label_waves, zigzag
        from btc_outlook.data.calendar import get_halving_context, upcoming_events
        from btc_outlook.data.news import fetch_news, get_news_sentiment
        from btc_outlook.data.prices import fetch_ohlcv
        from btc_outlook.report.chart import plot_outlook

        df = fetch_ohlcv(symbol, period="2y", interval="1wk")
        price = float(df["Close"].iloc[-1])

        pivots = zigzag(df["Close"], threshold=0.10)
        wave_points = label_waves(pivots[:6]) if len(pivots) >= 6 else label_waves(pivots)
        wave_forecast = get_wave_forecast(wave_points)
        wave_label = wave_points[-1].wave_label if wave_points else "?"

        halving = get_halving_context()
        macro = collect_macro_signals()

        news_items = fetch_news()
        news = get_news_sentiment(news_items)
        # Enrich top_headlines with URL from raw items
        raw_by_title = {n.title: n for n in news_items}
        for h in news.get("top_headlines", []):
            raw = raw_by_title.get(h["title"])
            if raw:
                h["url"] = raw.url
                h["published"] = raw.published.isoformat()

        scenarios = build_scenarios(price, wave_forecast, halving, macro, news)
        events = upcoming_events(days=90)

        tmp = tempfile.NamedTemporaryFile(
            suffix=f"_{symbol.replace('-', '_')}.png", delete=False, dir="/tmp"
        )
        chart_path = tmp.name
        tmp.close()
        plot_outlook(df, wave_points, scenarios, halving, macro, news, events, symbol, chart_path)

        state = {
            "symbol": symbol,
            "last_updated": int(time.time()),
            "price": round(price, 2),
            "wave_label": wave_label,
            "halving": halving,
            "macro": macro,
            "news": {
                **news,
                "items": [
                    {
                        "title": n.title,
                        "summary": n.summary,
                        "source": n.source,
                        "published": n.published.isoformat(),
                        "url": n.url,
                        "sentiment_score": n.sentiment_score,
                        "is_relevant": n.is_relevant,
                    }
                    for n in news_items[:30]
                ],
            },
            "scenarios": [
                {
                    "name": s.name,
                    "price_target": s.price_target,
                    "time_horizon": s.time_horizon,
                    "probability": s.probability,
                    "triggers": s.triggers,
                    "risks": s.risks,
                    "color": s.color,
                }
                for s in scenarios
            ],
            "upcoming_events": [
                {
                    "date": e.date.isoformat(),
                    "label": e.label,
                    "category": e.category,
                    "impact": e.impact,
                    "description": e.description,
                }
                for e in events[:10]
            ],
            "chart_path": chart_path,
            "error": None,
        }
    except Exception as exc:
        state = {
            "symbol": symbol,
            "last_updated": int(time.time()),
            "error": str(exc),
        }

    save_crypto_state(state)
    return state


def _spawn_next_pending(project_id, todos: list) -> None:
    """Find and spawn the next pending task for a project (respects prev_task_id chain)."""
    if project_has_active_worker(project_id, todos):
        return
    done_ids = {t["id"] for t in todos if t.get("status") == "done"}
    nxt = next(
        (t for t in reversed(todos)
         if t.get("project_id") == project_id
         and t.get("status") == "pending"
         and not t.get("locked")
         and (t.get("prev_task_id") is None or t.get("prev_task_id") in done_ids)),
        None,
    )
    if nxt:
        nxt["status"] = "in_progress"
        nxt["status_updated_at"] = int(time.time())
        save_todos(todos)
        spawn_worker(nxt["id"])


def _recover_orphaned_todos() -> None:
    """On startup, reset in_progress todos whose worker process is gone to pending,
    then kick off one worker per project that has pending work."""
    todos = load_todos()
    changed = False
    for t in todos:
        if t.get("status") != "in_progress":
            continue
        pid_file = DATA_DIR / f"worker_{t['id']}.pid"
        alive = False
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 0)
                alive = True
            except (ValueError, ProcessLookupError, OSError):
                pid_file.unlink(missing_ok=True)
        if not alive:
            t["status"] = "pending"
            t["status_updated_at"] = int(time.time())
            t["progress"] = None
            changed = True
    if changed:
        save_todos(todos)

    todos = load_todos()

    # Cancel pending sub-tasks whose parent no longer exists (orphaned by parent deletion).
    known_ids = {t["id"] for t in todos}
    orphaned = [
        t for t in todos
        if t.get("parent_id") and t["parent_id"] not in known_ids and t.get("status") == "pending"
    ]
    if orphaned:
        orphan_ids = {t["id"] for t in orphaned}
        for t in todos:
            if t["id"] in orphan_ids:
                t["status"] = "canceled"
                t["status_updated_at"] = int(time.time())
        save_todos(todos)
        todos = load_todos()

    # Auto-complete "planned" tasks whose sub-tasks have all terminated (none pending/in_progress).
    # This handles cases where sub-tasks finished without triggering the real-time auto-complete
    # (e.g. sub-tasks lacked parent_id, or some sub-tasks failed).
    plan_changed = False
    for t in todos:
        if t.get("status") == "planned":
            active_subs = [
                s for s in todos
                if s.get("parent_id") == t["id"] and s.get("status") in ("pending", "in_progress")
            ]
            if not active_subs:
                t["status"] = "done"
                t["done"] = True
                t["status_updated_at"] = int(time.time())
                plan_changed = True
    if plan_changed:
        save_todos(todos)
        todos = load_todos()

    done_ids = {t["id"] for t in todos if t.get("status") == "done"}
    projects_started: set = set()
    to_spawn: list = []
    for t in reversed(todos):
        pid = t.get("project_id")
        if t.get("status") != "pending" or t.get("locked"):
            continue
        prev_id = t.get("prev_task_id")
        if prev_id is not None and prev_id not in done_ids:
            continue
        if pid in projects_started or project_has_active_worker(pid, todos):
            continue
        t["status"] = "in_progress"
        t["status_updated_at"] = int(time.time())
        projects_started.add(pid)
        to_spawn.append(t["id"])
    if to_spawn:
        save_todos(todos)
        for tid in to_spawn:
            spawn_worker(tid)


def _prepare_for_restart() -> None:
    """On shutdown, SIGTERM all running worker processes, reset in_progress todos to pending,
    and sync MEMORY.md to persistent storage so the next session starts with current state."""
    todos = load_todos()
    changed = False
    for t in todos:
        if t.get("status") != "in_progress":
            continue
        pid_file = DATA_DIR / f"worker_{t['id']}.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ValueError, ProcessLookupError, OSError):
                pass
            pid_file.unlink(missing_ok=True)
        t["status"] = "pending"
        t["status_updated_at"] = int(time.time())
        t["progress"] = None
        changed = True
    if changed:
        save_todos(todos)

    if MEMORY_FILE.exists() and MEMORY_BACKUP_FILE.parent.exists():
        import shutil
        shutil.copy2(MEMORY_FILE, MEMORY_BACKUP_FILE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _recover_orphaned_todos()
    yield
    _prepare_for_restart()


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
    parent_id = body.get("parent_id")
    subtask_idx = body.get("subtask_idx")
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
    if parent_id is not None:
        entry["parent_id"] = int(parent_id)
    if subtask_idx is not None:
        entry["subtask_idx"] = int(subtask_idx)
    todos.insert(0, entry)
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
    result = body.get("result")
    session_limit_reset_at = body.get("session_limit_reset_at")
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["status"] = status
            t["status_updated_at"] = int(time.time())
            t["done"] = status == "done"
            if status in ("done", "failed", "blocked", "pending", "session_limit", "planned"):
                t["progress"] = None
            if duration_secs is not None:
                t["duration_secs"] = int(duration_secs)
            if tokens:
                t["tokens"] = tokens
            if result is not None and status == "done":
                t["result"] = str(result)[:3000]
            if session_limit_reset_at is not None:
                t["session_limit_reset_at"] = int(session_limit_reset_at)
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
    # When a worker finishes or a task is planned, start the next pending todo in the same project
    elif status in ("done", "failed", "canceled", "planned"):
        finished = next((t for t in todos if t["id"] == todo_id), None)
        if finished and finished.get("project_id"):
            _spawn_next_pending(finished["project_id"], todos)
        # Auto-complete parent when all sub-tasks have terminated (done, failed, or canceled).
        if status in ("done", "failed", "canceled"):
            finished2 = next((t for t in todos if t["id"] == todo_id), None)
            if finished2 and finished2.get("parent_id"):
                parent_id_val = finished2["parent_id"]
                siblings = [t for t in todos if t.get("parent_id") == parent_id_val]
                if siblings and not any(s.get("status") in ("pending", "in_progress") for s in siblings):
                    parent = next((t for t in todos if t["id"] == parent_id_val), None)
                    if parent and parent.get("status") == "planned":
                        parent["status"] = "done"
                        parent["done"] = True
                        parent["status_updated_at"] = int(time.time())
                        save_todos(todos)
                        if parent.get("project_id"):
                            _spawn_next_pending(parent["project_id"], todos)
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


def _collect_subtask_ids(todos: list, parent_ids: set) -> set:
    """Collect IDs of all non-in_progress sub-tasks whose parent is in parent_ids."""
    return {
        t["id"] for t in todos
        if t.get("parent_id") in parent_ids and t.get("status") != "in_progress"
    }


@app.post("/api/delete-done")
def delete_all_done():
    todos = load_todos()
    to_delete = [t for t in todos if t.get("done") or t.get("status") == "canceled"]
    accumulate_stats(to_delete)
    deleted_ids = {t["id"] for t in to_delete}
    # Cascade: also remove pending sub-tasks of deleted parents
    orphan_ids = _collect_subtask_ids(todos, deleted_ids)
    deleted_ids |= orphan_ids
    save_todos([t for t in todos if t["id"] not in deleted_ids])
    return {"ok": True}


@app.post("/api/delete/{todo_id}")
def delete_todo(todo_id: int):
    todos = load_todos()
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if todo and todo.get("status") == "in_progress":
        return JSONResponse({"ok": False, "error": "Cannot delete an in-progress todo"}, status_code=409)
    if todo and (todo.get("done") or todo.get("status") in ("failed", "canceled", "context_limit", "session_limit")):
        accumulate_stats([todo])
    # Cascade: also remove non-in_progress sub-tasks of the deleted task
    deleted_ids = {todo_id} | _collect_subtask_ids(todos, {todo_id})
    save_todos([t for t in todos if t["id"] not in deleted_ids])
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
        _spawn_next_pending(canceled["project_id"], todos)

    return {"ok": True}


@app.post("/api/resume/{todo_id}")
def resume_todo(todo_id: int):
    todos = load_todos()
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if not todo:
        return JSONResponse({"ok": False, "error": "Todo not found"}, status_code=404)
    if todo.get("status") not in ("context_limit", "session_limit"):
        return JSONResponse({"ok": False, "error": "Todo is not interrupted"}, status_code=409)
    was_session_limit = todo.get("status") == "session_limit"
    todo.pop("session_limit_reset_at", None)
    project_id = todo.get("project_id")
    # Session-limit tasks always go to pending — spawning immediately would hit
    # the limit again. The heartbeat picks them up once the limit has reset.
    if was_session_limit or project_has_active_worker(project_id, todos):
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
    target = None
    for t in todos:
        if t["id"] == todo_id:
            if t.get("status") == "in_progress":
                return JSONResponse({"ok": False, "error": "Cannot lock an in-progress todo"}, status_code=409)
            t["locked"] = locked
            target = t
            break
    save_todos(todos)
    if not locked and target and target.get("status") == "pending":
        project_id = target.get("project_id")
        if not project_has_active_worker(project_id, todos):
            target["status"] = "in_progress"
            target["status_updated_at"] = int(time.time())
            save_todos(todos)
            spawn_worker(todo_id)
    return {"ok": True}


@app.post("/api/questions/{todo_id}")
async def post_questions(todo_id: int, request: Request):
    body = await request.json()
    questions = body.get("questions", [])
    if not questions:
        return JSONResponse({"ok": False, "error": "No questions provided"}, status_code=400)
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["status"] = "waiting"
            t["status_updated_at"] = int(time.time())
            t["questions"] = questions
            t["question_idx"] = 0
            t["progress"] = None
            break
    save_todos(todos)
    return {"ok": True}


@app.post("/api/questions/{todo_id}/answer")
async def answer_question(todo_id: int, request: Request):
    body = await request.json()
    answer = (body.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"ok": False, "error": "Answer required"}, status_code=400)
    todos = load_todos()
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if not todo:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    if todo.get("status") != "waiting":
        return JSONResponse({"ok": False, "error": "Task is not waiting"}, status_code=409)
    questions = todo.get("questions", [])
    idx = todo.get("question_idx", 0)
    if idx >= len(questions):
        return JSONResponse({"ok": False, "error": "No pending question"}, status_code=409)
    questions[idx]["answer"] = answer
    idx += 1
    todo["question_idx"] = idx
    todo["questions"] = questions
    all_answered = idx >= len(questions)
    if all_answered:
        if project_has_active_worker(todo.get("project_id"), todos):
            todo["status"] = "pending"
            todo["status_updated_at"] = int(time.time())
        else:
            todo["status"] = "in_progress"
            todo["status_updated_at"] = int(time.time())
            save_todos(todos)
            spawn_worker(todo_id)
            return {"ok": True, "all_answered": True}
    save_todos(todos)
    return {"ok": True, "all_answered": all_answered}


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
    plugin_states_mtime = PLUGIN_STATES_FILE.stat().st_mtime if PLUGIN_STATES_FILE.exists() else 0
    crypto_mtime = CRYPTO_STATE_FILE.stat().st_mtime if CRYPTO_STATE_FILE.exists() else 0
    return JSONResponse({
        "mtime": mtime,
        "plugin_states_mtime": plugin_states_mtime,
        "crypto_mtime": crypto_mtime,
    })


@app.get("/api/version")
def get_version():
    mtime = max(
        Path(__file__).stat().st_mtime,
        _TEMPLATE.stat().st_mtime,
    )
    return JSONResponse({"version": mtime})


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


# ── Crypto Forecast ────────────────────────────────────────────────────────────

_crypto_refreshing: bool = False


@app.get("/api/crypto/data")
def get_crypto_data():
    state = load_crypto_state()
    return JSONResponse(state)


@app.post("/api/crypto/refresh")
async def trigger_crypto_refresh(request: Request):
    global _crypto_refreshing
    if _crypto_refreshing:
        return JSONResponse({"ok": False, "error": "Already refreshing"}, status_code=409)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    symbol = (body.get("symbol") or "BTC-USD").strip() if isinstance(body, dict) else "BTC-USD"
    _crypto_refreshing = True

    async def _run():
        global _crypto_refreshing
        try:
            await _run_crypto_refresh(symbol)
        finally:
            _crypto_refreshing = False

    asyncio.create_task(_run())
    return {"ok": True}


@app.get("/api/crypto/chart")
def get_crypto_chart():
    state = load_crypto_state()
    chart_path = state.get("chart_path")
    if not chart_path or not Path(chart_path).exists():
        return JSONResponse({"error": "No chart available"}, status_code=404)
    return FileResponse(chart_path, media_type="image/png")
