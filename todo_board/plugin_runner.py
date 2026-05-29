"""Async plugin runner — executes external plugin commands and posts results as news."""
import asyncio
import time

from .storage import load_news, load_plugin_states, save_news, save_plugin_states

_running: set[str] = set()


async def run_plugin(name: str, plugin: dict) -> None:
    if name in _running:
        return
    _running.add(name)

    states = load_plugin_states()
    states[name] = {**states.get(name, {}), "status": "running", "started_at": int(time.time())}
    save_plugin_states(states)

    result = ""
    status = "failed"
    try:
        path = plugin.get("path", ".")
        command = plugin.get("command", [])

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")

        lines = output.split("\n")
        non_progress = [l for l in lines if not (l.startswith("[") or l.startswith("  →"))]
        result = "\n".join(non_progress).strip()
        if not result:
            result = output.strip() or err.strip() or "No output"

        status = "failed" if proc.returncode != 0 else "done"

    except Exception as e:
        result = str(e)
        status = "failed"
    finally:
        _running.discard(name)

    states = load_plugin_states()
    states[name] = {
        "status": status,
        "last_run_at": int(time.time()),
        "result": result[:3000],
    }
    save_plugin_states(states)

    _post_news(plugin.get("name", name), status, result)


def _post_news(display_name: str, status: str, result: str) -> None:
    if status == "failed":
        msg_type = "error"
        msg = f"Plugin {display_name} failed: {result[:200]}"
    else:
        first_line = next((l.strip() for l in result.split("\n") if l.strip()), "")
        first_line = first_line.replace("*", "")
        msg = f"{display_name}: {first_line[:200]}"
        msg_type = "info"

    news = load_news()
    new_id = max((n["id"] for n in news), default=0) + 1
    news.insert(0, {
        "id": new_id,
        "type": msg_type,
        "message": msg[:500],
        "todo_id": None,
        "project_id": None,
        "created": int(time.time()),
        "read": False,
    })
    save_news(news[:200])


def is_running(name: str) -> bool:
    return name in _running
