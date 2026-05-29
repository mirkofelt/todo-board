"""Async plugin runner — executes external plugin commands and stores results."""
import asyncio
import time

from .storage import load_plugin_states, save_plugin_states

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


def is_running(name: str) -> bool:
    return name in _running
