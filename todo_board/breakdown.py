"""Task breakdown via Claude reverse prompting.

Decomposes a high-level goal into concrete, actionable subtasks using the
project's existing Claude session so it has full project context.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_DATA_DIR = Path(os.environ.get("TODO_BOARD_DATA_DIR", Path(__file__).resolve().parent.parent))
_RULES_FILE = _DATA_DIR / "rules.txt"
_SESSIONS_FILE = _DATA_DIR / "sessions.json"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
_MEMORY_PATH = os.environ.get("MEMORY_FILE", "")

_PROMPT = """\
Break down the following goal into concrete, actionable subtasks.

## Goal
{task}

## Instructions
- Output ONLY a JSON array of strings, nothing else — no explanation, no markdown
- 3 to 7 subtasks; each should be independent and self-contained
- Each task must be a complete instruction a developer can act on directly

Example output:
["Understand the current structure of X", "Write tests for the new behavior", "Implement the feature", "Update the README"]
"""


def _load_sessions() -> dict:
    if not _SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(_SESSIONS_FILE.read_text())
    except Exception:
        return {}


def _save_sessions(sessions: dict) -> None:
    try:
        _SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _parse_tasks(text: str) -> list[str]:
    text = text.strip()
    m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', text)
    if m:
        text = m.group(1)
    else:
        m = re.search(r'\[[\s\S]*\]', text)
        if m:
            text = m.group(0)
    try:
        tasks = json.loads(text)
        if isinstance(tasks, list):
            return [str(t).strip() for t in tasks if str(t).strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def breakdown_task(task_text: str, project_id) -> list[str]:
    """Break down a high-level task using Claude with the project's session.

    Returns a list of subtask strings, or an empty list on failure.
    """
    rules = _RULES_FILE.read_text().strip() if _RULES_FILE.exists() else ""
    memory = ""
    if _MEMORY_PATH:
        mp = Path(_MEMORY_PATH)
        if mp.exists():
            memory = mp.read_text()

    session_key = str(project_id) if project_id is not None else "none"
    sessions = _load_sessions()
    prior_session = sessions.get(session_key)

    prompt = _PROMPT.format(task=task_text)

    if prior_session:
        cmd = [
            CLAUDE_BIN, "-p", "--resume", prior_session, prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
            "--model", CLAUDE_MODEL,
            "--max-turns", "3",
        ]
    else:
        system_parts = []
        if memory:
            system_parts.append(f"## Project Context\n{memory}")
        if rules:
            system_parts.append(f"## Rules\n{rules}")
        system_prompt = "\n\n".join(system_parts)
        cmd = [
            CLAUDE_BIN, "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
            "--model", CLAUDE_MODEL,
            "--max-turns", "3",
        ]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result_text = None
    new_session_id = None

    for raw in iter(proc.stdout.readline, ""):
        try:
            event = json.loads(raw.rstrip("\n"))
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result_text = event.get("result", "")
            new_session_id = event.get("session_id")

    proc.wait()

    if new_session_id:
        sessions[session_key] = new_session_id
        _save_sessions(sessions)

    if not result_text:
        return []

    return _parse_tasks(result_text)
