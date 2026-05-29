"""Standalone todo worker — invoked as a subprocess when a new todo is created.

Python manages the todo lifecycle (in_progress/done/failed). Claude receives only
the task and context, and outputs FAILED:<reason> if it cannot complete the work.

Usage: python3 todo_board/worker.py <todo_id>
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_DATA_DIR = Path(os.environ.get("TODO_BOARD_DATA_DIR", Path(__file__).resolve().parent.parent))
TODOS_FILE = _DATA_DIR / "todos.json"
PROJECTS_FILE = _DATA_DIR / "projects.json"
RULES_FILE = _DATA_DIR / "rules.txt"
SESSIONS_FILE = _DATA_DIR / "sessions.json"
LOG_DIR = _DATA_DIR

BOARD_URL = os.environ.get("TODO_BOARD_URL", "http://localhost:7842")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
_memory_path = os.environ.get("MEMORY_FILE", "")
MEMORY_FILE = Path(_memory_path) if _memory_path else None
WORK_DIR = os.environ.get("CLAUDE_WORK_DIR") or str(Path.home())

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
CLAUDE_MAX_TURNS = os.environ.get("CLAUDE_MAX_TURNS", "30")
CLAUDE_MAX_BUDGET_USD = os.environ.get("CLAUDE_MAX_BUDGET_USD", "")


def _api(path: str, data: dict) -> None:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BOARD_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"API call failed: {e}", file=sys.stderr)


def _session_key(project_id) -> str:
    return str(project_id) if project_id is not None else "none"


def _load_sessions() -> dict:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return {}


def _save_sessions(sessions: dict) -> None:
    try:
        SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Failed to save sessions: {e}", file=sys.stderr)


def _build_system_prompt(rules: str, memory: str) -> str:
    parts = []
    if memory:
        parts.append(f"## Project Context (Memory)\n{memory}")
    if rules:
        parts.append(f"## Rules\n- Read relevant files before editing\n{rules}")
    return "\n\n".join(parts)


def _build_task_prompt(
    todo_id: int,
    project_name: str,
    task_text: str,
    prev_result: str = "",
    answered_questions: list | None = None,
) -> str:
    parts = [f"""You are processing a single todo item. Implement it fully.

## Task
Todo #{todo_id} — Project: {project_name}
{task_text}"""]
    if prev_result:
        parts.append(f"""## Previous Task Output
The preceding task in this sequence produced the following output — use it as context:

{prev_result}""")
    if answered_questions:
        qa_lines = "\n".join(
            f"Q: {q['question']}\nA: {q['answer']}" for q in answered_questions
        )
        parts.append(f"## Clarifications\nThe following questions were asked and answered before you started:\n\n{qa_lines}")
    parts.append("""## Asking for Clarification
If you need input before proceeding, collect ALL your questions first, then output them in this exact format:
  QUESTION: <your question>
  OPTION: <answer option 1>   ← up to 4 options, all optional
  OPTION: <answer option 2>
  ...
  QUESTION: <next question if any>
  OPTION: ...
  WAITING_FOR_ANSWERS

Rules:
- Only ask if truly necessary. When in doubt, make a reasonable assumption and proceed.
- Do NOT start any work before outputting WAITING_FOR_ANSWERS — output the questions first.
- After WAITING_FOR_ANSWERS, stop. Do not output anything else.""")
    parts.append("""## Status Updates
While working, emit STATUS: <one-line description> on its own line whenever you start a new step.
Examples:
  STATUS: Reading app.py
  STATUS: Writing fix to validate input
  STATUS: Running tests

## Output
When you finish successfully, output nothing extra (or a brief summary).
If the task CANNOT be completed, output exactly: FAILED:<one-line reason>
""")
    return "\n\n".join(parts)


def _parse_questions(output_lines: list) -> list:
    """Extract QUESTION:/OPTION: blocks from output lines. Returns list of {question, options, answer} dicts."""
    questions = []
    current_q: str | None = None
    current_opts: list = []

    for line in output_lines:
        stripped = line.strip()
        if stripped.startswith("QUESTION:"):
            if current_q is not None:
                questions.append({"question": current_q, "options": current_opts[:4], "answer": None})
            current_q = stripped[9:].strip()
            current_opts = []
        elif stripped.startswith("OPTION:") and current_q is not None:
            opt = stripped[7:].strip()
            if opt and len(current_opts) < 4:
                current_opts.append(opt)

    if current_q is not None:
        questions.append({"question": current_q, "options": current_opts[:4], "answer": None})

    return questions


def _build_cold_cmd(task_prompt: str, system_prompt: str, model: str) -> list:
    cmd = [
        CLAUDE_BIN, "-p", task_prompt,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--max-turns", CLAUDE_MAX_TURNS,
    ]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if CLAUDE_MAX_BUDGET_USD:
        cmd += ["--max-budget-usd", CLAUDE_MAX_BUDGET_USD]
    return cmd


def _build_resume_cmd(session_id: str, task_prompt: str, model: str) -> list:
    cmd = [
        CLAUDE_BIN, "-p", "--resume", session_id, task_prompt,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--max-turns", CLAUDE_MAX_TURNS,
    ]
    if CLAUDE_MAX_BUDGET_USD:
        cmd += ["--max-budget-usd", CLAUDE_MAX_BUDGET_USD]
    return cmd


def _invoke_claude(cmd: list, todo_id: int) -> tuple:
    """Run claude subprocess, stream output, report progress.

    Returns (returncode, output_lines, final_result_text, token_data, session_id, stderr_out).
    """
    proc = subprocess.Popen(
        cmd,
        cwd=WORK_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    output_lines: list = []
    final_result_text = None
    result_subtype = None
    token_data: dict = {}
    session_id = None

    for raw_line in iter(proc.stdout.readline, ""):
        raw = raw_line.rstrip("\n")
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            output_lines.append(raw)
            continue

        etype = event.get("type")
        if etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    for text_line in block["text"].split("\n"):
                        text_line = text_line.rstrip()
                        if not text_line:
                            continue
                        output_lines.append(text_line)
                        if text_line.startswith("STATUS:"):
                            status_text = text_line[7:].strip()[:150]
                            _api(f"/api/progress/{todo_id}", {"text": status_text})
                            _api("/api/statusline", {"text": f"#{todo_id} → {status_text}"})
        elif etype == "result":
            final_result_text = event.get("result", "")
            result_subtype = event.get("subtype", "")
            session_id = event.get("session_id")
            usage = event.get("usage", {})
            token_data = {
                "input": usage.get("input_tokens", 0),
                "cache_creation": usage.get("cache_creation_input_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            }

    proc.wait()
    stderr_out = proc.stderr.read()
    return proc.returncode, output_lines, final_result_text, result_subtype, token_data, session_id, stderr_out


_LIMIT_SUBTYPES = frozenset({"error_max_turns", "error_usage", "error_context_window"})
_LIMIT_STDERR_PATTERNS = (
    "ContextWindowExceededError",
    "context_length_exceeded",
    "max_tokens",
    "too many tokens",
)


def _is_context_limit(subtype: str | None, stderr: str) -> bool:
    if subtype in _LIMIT_SUBTYPES:
        return True
    stderr_lower = stderr.lower()
    return any(pat.lower() in stderr_lower for pat in _LIMIT_STDERR_PATTERNS)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: worker.py <todo_id>", file=sys.stderr)
        sys.exit(1)

    todo_id = int(sys.argv[1])

    todos = json.loads(TODOS_FILE.read_text())
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if not todo:
        print(f"Todo #{todo_id} not found", file=sys.stderr)
        sys.exit(1)

    if todo.get("status") in ("done", "failed"):
        print(f"Todo #{todo_id} status={todo['status']}, skipping", file=sys.stderr)
        sys.exit(0)

    project_name = "General"
    project_model = ""
    if PROJECTS_FILE.exists():
        projects = json.loads(PROJECTS_FILE.read_text())
        proj = next((p for p in projects if p["id"] == todo.get("project_id")), None)
        if proj:
            project_name = proj.get("name", "General")
            project_model = proj.get("model", "")

    model = todo.get("model") or project_model or CLAUDE_MODEL

    memory = MEMORY_FILE.read_text() if MEMORY_FILE and MEMORY_FILE.exists() else ""
    rules = RULES_FILE.read_text().strip() if RULES_FILE.exists() else ""
    task_text = todo["text"]
    task_preview = task_text[:60].replace('"', "'")

    prev_result = ""
    prev_task_id = todo.get("prev_task_id")
    if prev_task_id:
        prev_todo = next((t for t in todos if t["id"] == prev_task_id), None)
        if prev_todo and prev_todo.get("result"):
            prev_result = prev_todo["result"]

    pid_file = LOG_DIR / f"worker_{todo_id}.pid"
    pid_file.write_text(str(os.getpid()))

    _api(f"/api/status/{todo_id}", {"status": "in_progress"})
    _api("/api/statusline", {"text": f"Todo #{todo_id}: {task_preview}"})

    # If this is a restart after the user answered questions, inject those answers.
    existing_questions = todo.get("questions", [])
    answered_questions = (
        existing_questions
        if existing_questions and all(q.get("answer") for q in existing_questions)
        else []
    )

    session_key = _session_key(todo.get("project_id"))
    sessions = _load_sessions()
    # Skip session resume when restarting after question-answer cycle (fresh context needed).
    prior_session = None if answered_questions else sessions.get(session_key)

    system_prompt = _build_system_prompt(rules, memory)
    task_prompt = _build_task_prompt(todo_id, project_name, task_text, prev_result, answered_questions)

    log_file = LOG_DIR / f"worker_{todo_id}.log"
    start_time = time.time()

    if prior_session:
        cmd = _build_resume_cmd(prior_session, task_prompt, model)
        rc, output_lines, final_result_text, result_subtype, token_data, new_session_id, stderr_out = _invoke_claude(cmd, todo_id)

        if rc != 0:
            # Session expired or invalid — fall back to cold start
            sessions.pop(session_key, None)
            _save_sessions(sessions)
            cmd = _build_cold_cmd(task_prompt, system_prompt, model)
            rc, output_lines, final_result_text, result_subtype, token_data, new_session_id, stderr_out = _invoke_claude(cmd, todo_id)
    else:
        cmd = _build_cold_cmd(task_prompt, system_prompt, model)
        rc, output_lines, final_result_text, result_subtype, token_data, new_session_id, stderr_out = _invoke_claude(cmd, todo_id)

    duration_secs = int(time.time() - start_time)

    if new_session_id:
        sessions[session_key] = new_session_id
        _save_sessions(sessions)

    if final_result_text is not None:
        output = final_result_text.strip()
    else:
        non_status_lines = [l for l in output_lines if not l.startswith("STATUS:")]
        output = "\n".join(non_status_lines).strip()

    log_file.write_text(
        "\n".join(output_lines) + ("\n\nSTDERR:\n" + stderr_out if stderr_out else "")
    )

    # Check if Claude is waiting for user input before proceeding.
    wants_answers = any(l.strip() == "WAITING_FOR_ANSWERS" for l in output_lines)
    if wants_answers:
        questions = _parse_questions(output_lines)
        if questions:
            _api(f"/api/questions/{todo_id}", {"questions": questions})
            _api("/api/news", {
                "type": "warning",
                "message": f"Task #{todo_id} has {len(questions)} question(s) — waiting for your input",
                "todo_id": todo_id,
                "project_id": todo.get("project_id"),
            })
            _api("/api/statusline", {"text": ""})
            pid_file.unlink(missing_ok=True)
            return

    hit_limit = _is_context_limit(result_subtype, stderr_out)

    if hit_limit:
        _api(f"/api/status/{todo_id}", {"status": "context_limit", "duration_secs": duration_secs, "tokens": token_data})
        _api("/api/news", {
            "type": "warning",
            "message": f"Task #{todo_id} interrupted — context limit reached",
            "todo_id": todo_id,
            "project_id": todo.get("project_id"),
        })
    elif output.startswith("FAILED:") or rc != 0:
        reason = output[7:].strip() if output.startswith("FAILED:") else f"Exit code {rc}"
        _api(f"/api/status/{todo_id}", {"status": "failed", "duration_secs": duration_secs, "tokens": token_data})
        _api(f"/api/note/{todo_id}", {"note": reason[:300]})
        _api("/api/news", {
            "type": "error",
            "message": f"Task #{todo_id} failed: {reason[:200]}",
            "todo_id": todo_id,
            "project_id": todo.get("project_id"),
        })
    else:
        _api(f"/api/status/{todo_id}", {"status": "done", "duration_secs": duration_secs, "tokens": token_data, "result": output[:3000]})
        trimmed = output.strip()
        # Only post news if the output is substantive (not just a generic completion message)
        if len(trimmed) > 60:
            snippet = " ".join(trimmed.split())[:180]
            _api("/api/news", {
                "type": "info",
                "message": f"Task #{todo_id}: {snippet}",
                "todo_id": todo_id,
                "project_id": todo.get("project_id"),
            })

    _api("/api/statusline", {"text": ""})
    pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
