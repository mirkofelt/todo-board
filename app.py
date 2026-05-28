import json
import os
import subprocess
import sys
import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
import uvicorn

BASE = Path(__file__).parent
TODOS_FILE = BASE / "todos.json"
PROJECTS_FILE = BASE / "projects.json"
REQUIREMENTS_FILE = BASE / "rules.txt"
STATUSLINE_FILE = BASE / "statusline.json"

DEFAULT_PROJECTS = [
    {"id": 1, "name": "General"},
    {"id": 2, "name": "coupon-extension"},
    {"id": 3, "name": "sensor-dashboard"},
    {"id": 4, "name": "home-recorder"},
    {"id": 5, "name": "btc-outlook"},
]

DEFAULT_REQUIREMENTS = """\
- README up to date and complete before closing a project
- No credentials, API keys, or secrets in code or config files
- No personal data (names, emails, phone numbers) in source files
- No infrastructure details (IPs, hostnames, ports) in code
- All code, comments, and commit messages in English
- No CDN — bundle all JS/CSS dependencies locally
- Web UIs: dark theme, no external resources, mobile-friendly
- Test in browser before declaring UI work done"""


def load_todos() -> list:
    if not TODOS_FILE.exists():
        return []
    return json.loads(TODOS_FILE.read_text())


def save_todos(todos: list):
    TODOS_FILE.write_text(json.dumps(todos, ensure_ascii=False, indent=2))


def load_projects() -> list:
    if not PROJECTS_FILE.exists():
        save_projects(DEFAULT_PROJECTS)
        return DEFAULT_PROJECTS
    return json.loads(PROJECTS_FILE.read_text())


def save_projects(projects: list):
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2))


def load_requirements() -> str:
    if not REQUIREMENTS_FILE.exists():
        REQUIREMENTS_FILE.write_text(DEFAULT_REQUIREMENTS)
        return DEFAULT_REQUIREMENTS
    return REQUIREMENTS_FILE.read_text()


def save_requirements(text: str):
    REQUIREMENTS_FILE.write_text(text)


def load_statusline() -> dict:
    if not STATUSLINE_FILE.exists():
        return {"text": "", "updated_at": 0}
    return json.loads(STATUSLINE_FILE.read_text())


def save_statusline(data: dict):
    STATUSLINE_FILE.write_text(json.dumps(data, ensure_ascii=False))


app = FastAPI()

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Todo Queue</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0f172a; color: #e2e8f0;
  min-height: 100vh; padding: 32px 16px;
}
.container { max-width: 680px; margin: 0 auto; }
h1 { font-size: 20px; color: #10b981; margin-bottom: 20px; }

.statusline-bar {
  background: #0d1f1a; border: 1px solid #10b981; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 24px;
  display: flex; align-items: center; gap: 10px;
  font-size: 13px; color: #6ee7b7;
}
.statusline-text { flex: 1; }
.statusline-time { font-size: 11px; color: #334155; flex-shrink: 0; }

.add-form { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; margin-bottom: 32px; }
.add-row { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.add-row:last-child { margin-bottom: 0; }
textarea, input[type="text"] {
  background: #0f172a; border: 1px solid #334155; border-radius: 6px;
  color: #f1f5f9; padding: 9px 12px; font-size: 13px; font-family: inherit;
}
textarea { flex: 1; min-width: 200px; resize: vertical; min-height: 42px; }
textarea:focus, input[type="text"]:focus, select:focus { outline: none; border-color: #10b981; }
select {
  background: #0f172a; border: 1px solid #334155; border-radius: 6px;
  color: #94a3b8; padding: 9px 12px; font-size: 13px; cursor: pointer;
  flex: 1; min-width: 140px;
}
select.error { border-color: #ef4444; }
#new-project-wrap { display: none; flex: 1; }
#new-project-wrap input { width: 100%; }
button {
  background: #10b981; color: #fff; border: none;
  border-radius: 6px; padding: 8px 18px;
  font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap;
}
button:hover { background: #059669; }
.btn-sm { padding: 5px 12px; font-size: 12px; }
.btn-ghost { background: none; border: 1px solid #334155; color: #64748b; }
.btn-ghost:hover { border-color: #94a3b8; color: #e2e8f0; background: none; }
.btn-icon { background: none; border: none; color: #334155; font-size: 16px; padding: 3px 7px; cursor: pointer; }
.btn-icon:hover { color: #ef4444; }

.section { margin-bottom: 36px; }
.section-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
  color: #475569; font-weight: 500; margin-bottom: 12px;
}

.todo-item {
  display: flex; align-items: flex-start; gap: 10px;
  background: #1e293b; border: 1px solid #334155; border-radius: 8px;
  padding: 11px 14px; margin-bottom: 8px;
}
.todo-item.done { opacity: .35; transition: opacity .15s; }
.todo-item.done:hover { opacity: 1; }
.todo-item.done .todo-text { text-decoration: line-through; color: #475569; }
.todo-item.in-progress { border-color: #f59e0b; background: #1c1a10; }
.todo-item.blocked { border-color: #ef4444; background: #1a0e0e; }
.todo-item.failed { border-color: #ef4444; background: #1a0e0e; }
.todo-item.context-limit { border-color: #f59e0b; background: #1c1800; }

.todo-body { flex: 1; min-width: 0; }
.todo-text { font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.todo-note {
  font-size: 12px; color: #94a3b8; margin-top: 5px; font-style: italic;
  border-left: 2px solid #334155; padding-left: 8px; line-height: 1.5;
}
.todo-progress {
  font-size: 11px; color: #f59e0b; margin-top: 5px;
  font-family: monospace; opacity: .85;
}
.todo-meta { display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap; }

.badge {
  font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 10px;
  background: #0f172a; border: 1px solid #334155; color: #64748b;
  text-transform: uppercase; letter-spacing: .05em;
}
.status-badge {
  font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px;
  text-transform: uppercase; letter-spacing: .06em; display: flex; align-items: center; gap: 4px;
}
.status-badge.in-progress { background: #451a03; border: 1px solid #f59e0b; color: #f59e0b; }
.status-badge.blocked { background: #1e0a0a; border: 1px solid #ef4444; color: #ef4444; }
.status-badge.failed { background: #1e0a0a; border: 1px solid #ef4444; color: #ef4444; }
.status-badge.context-limit { background: #1c1800; border: 1px solid #f59e0b; color: #fbbf24; }
.status-badge.done-label { background: #0d2318; border: 1px solid #10b981; color: #10b981; }
.pulse {
  width: 6px; height: 6px; border-radius: 50%; background: #f59e0b;
  animation: pulse 1.2s ease-in-out infinite;
}
.pulse-green {
  width: 6px; height: 6px; border-radius: 50%; background: #10b981;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .4; transform: scale(.7); }
}

.meta-time { font-size: 11px; color: #334155; }
.todo-actions { display: flex; gap: 4px; flex-shrink: 0; align-items: flex-start; padding-top: 1px; }
.empty { color: #334155; font-size: 13px; padding: 8px 0; }
.divider { border: none; border-top: 1px solid #1e293b; margin: 16px 0; }

details { margin-bottom: 28px; }
details summary {
  cursor: pointer; user-select: none; list-style: none;
  font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
  color: #475569; font-weight: 500; padding: 4px 0;
}
details summary::before { content: "▶  "; font-size: 9px; }
details[open] summary::before { content: "▼  "; }
details[open] summary { margin-bottom: 14px; }

.projects-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.proj-chip {
  display: flex; align-items: center; gap: 6px;
  background: #1e293b; border: 1px solid #334155; border-radius: 20px;
  padding: 4px 12px; font-size: 12px; color: #94a3b8;
}
.add-project-row { display: flex; gap: 8px; }
.add-project-row input { flex: 1; }

.req-textarea {
  width: 100%; background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; color: #94a3b8; padding: 12px 14px;
  font-size: 12px; font-family: monospace; resize: vertical; min-height: 200px;
  line-height: 1.7; margin-bottom: 8px;
}
.req-textarea:focus { outline: none; border-color: #10b981; }
.save-row { display: flex; align-items: center; gap: 10px; }
.save-status { font-size: 11px; color: #10b981; }
</style>
</head>
<body>
<div class="container">
  <h1>Todo Queue</h1>

  <div id="statusline-bar" class="statusline-bar" style="display:none">
    <span class="pulse-green"></span>
    <span class="statusline-text" id="statusline-text"></span>
    <span class="statusline-time" id="statusline-time"></span>
  </div>

  <div class="add-form">
    <div class="add-row">
      <textarea id="new-todo" placeholder="New task… (Enter to submit, Shift+Enter for newline)" rows="1"></textarea>
    </div>
    <div class="add-row">
      <select id="project-select">
        <option value="">— No project —</option>
      </select>
      <div id="new-project-wrap">
        <input type="text" id="new-project-name" placeholder="Project name…">
      </div>
      <button id="add-btn">Add</button>
    </div>
  </div>

  <div class="section" id="pending-section">
    <div class="section-label">Pending</div>
    <div id="pending-list"></div>
  </div>

  <div class="section" id="inprogress-section">
    <div class="section-label">In Progress</div>
    <div id="inprogress-list"></div>
  </div>

  <div class="section" id="done-section">
    <div class="section-label">Done</div>
    <div id="done-list"></div>
  </div>

  <details id="req-details">
    <summary>Global Requirements</summary>
    <textarea class="req-textarea" id="req-textarea"></textarea>
    <div class="save-row">
      <button class="btn-sm btn-ghost" id="save-req-btn">Save</button>
      <span class="save-status" id="req-status"></span>
    </div>
  </details>
</div>

<script>
let projects = [];
let todos = [];

async function loadAll() {
  [projects, todos] = await Promise.all([
    fetch("/api/projects").then(r => r.json()),
    fetch("/api/todos").then(r => r.json()),
  ]);
  renderDropdown();
  renderTodos();
}

async function loadReq() {
  document.getElementById("req-textarea").value = await fetch("/api/requirements").then(r => r.text());
}

async function loadStatusline() {
  const data = await fetch("/api/statusline").then(r => r.json());
  const bar = document.getElementById("statusline-bar");
  if (data.text) {
    document.getElementById("statusline-text").textContent = data.text;
    const ts = data.updated_at
      ? new Date(data.updated_at * 1000).toLocaleString("de-DE", {hour:"2-digit",minute:"2-digit"})
      : "";
    document.getElementById("statusline-time").textContent = ts;
    bar.style.display = "flex";
  } else {
    bar.style.display = "none";
  }
}

function renderDropdown() {
  const sel = document.getElementById("project-select");
  const cur = sel.value;
  sel.innerHTML = '<option value="">— No project —</option>' +
    projects.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("") +
    '<option value="__new__">+ New project…</option>';
  if (cur) sel.value = cur;
}

function renderTodos() {
  const projMap = Object.fromEntries(projects.map(p => [p.id, p.name]));
  const pending = todos.filter(t => !t.done && t.status !== "in_progress");
  const inProgress = todos.filter(t => t.status === "in_progress");
  const done = todos.filter(t => t.done);

  document.getElementById("pending-section").style.display = pending.length ? "" : "none";
  document.getElementById("pending-list").innerHTML =
    pending.map(t => todoHtml(t, projMap)).join("") || "";

  document.getElementById("inprogress-section").style.display = inProgress.length ? "" : "none";
  document.getElementById("inprogress-list").innerHTML =
    inProgress.map(t => todoHtml(t, projMap)).join("") || "";

  document.getElementById("done-section").style.display = done.length ? "" : "none";
  document.getElementById("done-list").innerHTML =
    done.map(t => todoHtml(t, projMap)).join("") || "";
}

function todoHtml(t, projMap) {
  const inProgress = t.status === "in_progress";
  const isBlocked = t.status === "blocked";
  const isFailed = t.status === "failed";
  const isContextLimit = t.status === "context_limit";
  const isDone = t.done;

  // ID badge shown before the project badge in the meta row
  const idBadge = `<span style="color:#475569;font-size:11px;font-family:monospace;flex-shrink:0">#${t.id}</span>`;

  const badge = t.project_id && projMap[t.project_id]
    ? `<span class="badge">${esc(projMap[t.project_id])}</span>` : "";

  let statusBadge = "";
  if (isDone) {
    statusBadge = `<span class="status-badge done-label">Done ✓</span>`;
  } else if (inProgress) {
    statusBadge = `<span class="status-badge in-progress"><span class="pulse"></span>Working…</span>`;
  } else if (isBlocked) {
    statusBadge = `<span class="status-badge blocked">⚠ Blocked</span>`;
  } else if (isFailed) {
    statusBadge = `<span class="status-badge failed">✗ Failed</span>`;
  } else if (isContextLimit) {
    statusBadge = `<span class="status-badge context-limit">⚡ Interrupted</span>`;
  }

  const ts = new Date(t.created * 1000).toLocaleString("de-DE", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});

  let cls = "";
  if (isDone) cls = "done";
  else if (inProgress) cls = "in-progress";
  else if (isBlocked) cls = "blocked";
  else if (isFailed) cls = "failed";
  else if (isContextLimit) cls = "context-limit";

  const note = t.note ? `<div class="todo-note">${esc(t.note)}</div>` : "";
  const progress = inProgress && t.progress ? `<div class="todo-progress">↳ ${esc(t.progress)}</div>` : "";
  const deleteBtn = !inProgress ? `<button class="btn-icon" onclick="del(${t.id})" title="Remove">×</button>` : "";

  return `<div class="todo-item ${cls}">
    <div class="todo-body">
      <div class="todo-text">${esc(t.text)}</div>
      ${note}
      ${progress}
      <div class="todo-meta">${idBadge}${badge}${statusBadge}<span class="meta-time">${ts}</span></div>
    </div>
    <div class="todo-actions">${deleteBtn}</div>
  </div>`;
}

async function del(id) {
  await fetch("/api/delete/" + id, { method: "POST" });
  todos = await fetch("/api/todos").then(r => r.json());
  renderTodos();
}

document.getElementById("project-select").addEventListener("change", function() {
  const wrap = document.getElementById("new-project-wrap");
  wrap.style.display = this.value === "__new__" ? "flex" : "none";
  if (this.value === "__new__") document.getElementById("new-project-name").focus();
});

async function addTodo() {
  const ta = document.getElementById("new-todo");
  const text = ta.value.trim();
  if (!text) return;
  const sel = document.getElementById("project-select");
  if (!sel.value || sel.value === "") {
    sel.classList.add("error");
    sel.focus();
    setTimeout(() => sel.classList.remove("error"), 1500);
    return;
  }
  let projectId = null;
  if (sel.value === "__new__") {
    const name = document.getElementById("new-project-name").value.trim();
    if (!name) { document.getElementById("new-project-name").focus(); return; }
    const data = await fetch("/api/projects/add", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name}),
    }).then(r => r.json());
    projectId = data.id;
    document.getElementById("new-project-name").value = "";
    document.getElementById("new-project-wrap").style.display = "none";
  } else if (sel.value) {
    projectId = parseInt(sel.value);
  }
  await fetch("/api/add", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({text, project_id: projectId}),
  });
  ta.value = "";
  if (projectId) sel.value = String(projectId);
  loadAll();
}

document.getElementById("add-btn").addEventListener("click", addTodo);
document.getElementById("new-todo").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addTodo(); }
});

document.getElementById("save-req-btn").addEventListener("click", async () => {
  await fetch("/api/requirements", {
    method: "POST", headers: {"Content-Type":"text/plain"},
    body: document.getElementById("req-textarea").value,
  });
  const s = document.getElementById("req-status");
  s.textContent = "Saved ✓";
  setTimeout(() => s.textContent = "", 2000);
});

function esc(s) {
  return s ? String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;") : "";
}

let appVersion = null;
async function checkVersion() {
  const data = await fetch("/api/version").then(r => r.json()).catch(() => null);
  if (!data) return;
  if (appVersion === null) { appVersion = data.version; return; }
  if (data.version !== appVersion) window.location.reload();
}

loadAll();
loadReq();
loadStatusline();
checkVersion();
setInterval(loadAll, 5000);
setInterval(loadStatusline, 3000);
setInterval(checkVersion, 5000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


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
    active = _project_has_active_worker(project_id, todos)
    todos.insert(0, {
        "id": new_id,
        "text": text,
        "done": False,
        "status": "pending",
        "created": int(time.time()),
        "project_id": project_id,
        "note": None,
        "status_updated_at": int(time.time()),
    })
    save_todos(todos)
    if not active:
        _spawn_worker(new_id)
    return {"ok": True, "id": new_id}


def _project_has_active_worker(project_id, todos: list) -> bool:
    if project_id is None:
        return False
    return any(
        t.get("project_id") == project_id and t.get("status") == "in_progress"
        for t in todos
    )


def _spawn_worker(todo_id: int) -> None:
    worker = BASE / "todo_worker.py"
    subprocess.Popen(
        [sys.executable, str(worker), str(todo_id)],
        cwd=str(Path.home()),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
    mtime = Path(__file__).stat().st_mtime
    return JSONResponse({"version": mtime})


@app.get("/api/requirements")
def get_requirements():
    return PlainTextResponse(load_requirements())


@app.post("/api/requirements")
async def set_requirements(request: Request):
    save_requirements((await request.body()).decode("utf-8"))
    return {"ok": True}


if __name__ == "__main__":
    port = int(os.environ.get("TODO_BOARD_PORT", 7842))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
