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

_DATA_DIR = Path(__file__).resolve().parent.parent
TODOS_FILE = _DATA_DIR / "todos.json"
PROJECTS_FILE = _DATA_DIR / "projects.json"
RULES_FILE = _DATA_DIR / "rules.txt"
LOG_DIR = _DATA_DIR

BOARD_URL = os.environ.get("TODO_BOARD_URL", "http://localhost:7842")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
_memory_path = os.environ.get("MEMORY_FILE", "")
MEMORY_FILE = Path(_memory_path) if _memory_path else None
WORK_DIR = os.environ.get("CLAUDE_WORK_DIR") or str(Path.home())


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
    if PROJECTS_FILE.exists():
        projects = json.loads(PROJECTS_FILE.read_text())
        proj = next((p for p in projects if p["id"] == todo.get("project_id")), None)
        if proj:
            project_name = proj["name"]

    memory = MEMORY_FILE.read_text() if MEMORY_FILE and MEMORY_FILE.exists() else ""
    rules = RULES_FILE.read_text().strip() if RULES_FILE.exists() else ""
    task_text = todo["text"]
    task_preview = task_text[:60].replace('"', "'")

    pid_file = LOG_DIR / f"worker_{todo_id}.pid"
    pid_file.write_text(str(os.getpid()))

    _api(f"/api/status/{todo_id}", {"status": "in_progress"})
    _api("/api/statusline", {"text": f"Todo #{todo_id}: {task_preview}"})

    prompt = f"""You are processing a single todo item. Implement it fully.

## Project Context (Memory)
{memory}

## Task
Todo #{todo_id} — Project: {project_name}
{task_text}

## Rules
- Read relevant files before editing
{rules}

## Status Updates
While working, emit STATUS: <one-line description> on its own line whenever you start a new step.
Examples:
  STATUS: Reading app.py
  STATUS: Writing fix to validate input
  STATUS: Running tests
These lines are stripped from final output evaluation.

## Output
When you finish successfully, output nothing extra (or a brief summary).
If the task CANNOT be completed, output exactly: FAILED:<one-line reason>
"""

    log_file = LOG_DIR / f"worker_{todo_id}.log"
    start_time = time.time()
    proc = subprocess.Popen(
        [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose"],
        cwd=WORK_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    output_lines = []
    final_result_text = None
    token_data: dict = {}

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
            usage = event.get("usage", {})
            token_data = {
                "input": (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                ),
                "output": usage.get("output_tokens", 0),
            }

    proc.wait()
    stderr_out = proc.stderr.read()
    duration_secs = int(time.time() - start_time)

    if final_result_text is not None:
        output = final_result_text.strip()
    else:
        non_status_lines = [l for l in output_lines if not l.startswith("STATUS:")]
        output = "\n".join(non_status_lines).strip()

    log_file.write_text(
        "\n".join(output_lines) + ("\n\nSTDERR:\n" + stderr_out if stderr_out else "")
    )

    if output.startswith("FAILED:") or proc.returncode != 0:
        reason = output[7:].strip() if output.startswith("FAILED:") else f"Exit code {proc.returncode}"
        _api(f"/api/status/{todo_id}", {"status": "failed", "duration_secs": duration_secs, "tokens": token_data})
        _api(f"/api/note/{todo_id}", {"note": reason[:300]})
    else:
        _api(f"/api/status/{todo_id}", {"status": "done", "duration_secs": duration_secs, "tokens": token_data})

    _api("/api/statusline", {"text": ""})
    pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
