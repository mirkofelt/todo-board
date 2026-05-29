import json

from .config import (
    COUNTER_FILE,
    CRYPTO_STATE_FILE,
    DEFAULT_PROJECTS,
    DEFAULT_RULES,
    GITHUB_LINKS_FILE,
    PLUGIN_STATES_FILE,
    PLUGINS_FILE,
    PROJECTS_DIR,
    PROJECTS_FILE,
    RULES_FILE,
    SESSIONS_FILE,
    STATS_FILE,
    STATUSLINE_FILE,
    TODOS_FILE,
    is_project_dir,
)


def load_todos() -> list:
    if not TODOS_FILE.exists():
        return []
    return json.loads(TODOS_FILE.read_text())


def save_todos(todos: list) -> None:
    TODOS_FILE.write_text(json.dumps(todos, ensure_ascii=False, indent=2))


def load_counter() -> int:
    if not COUNTER_FILE.exists():
        return 0
    return json.loads(COUNTER_FILE.read_text()).get("last_id", 0)


def save_counter(last_id: int) -> None:
    COUNTER_FILE.write_text(json.dumps({"last_id": last_id}))


def load_projects() -> list:
    stored = json.loads(PROJECTS_FILE.read_text()) if PROJECTS_FILE.exists() else DEFAULT_PROJECTS[:]

    if not PROJECTS_DIR.exists():
        return stored

    stored_by_name = {p["name"]: p for p in stored}
    next_id = max((p["id"] for p in stored), default=0) + 1
    changed = False

    dir_names = sorted(p.name for p in PROJECTS_DIR.iterdir() if is_project_dir(p))

    result = []
    for name in dir_names:
        if name in stored_by_name:
            result.append(stored_by_name[name])
        else:
            entry = {"id": next_id, "name": name}
            stored.append(entry)
            stored_by_name[name] = entry
            next_id += 1
            changed = True
            result.append(entry)

    if changed:
        PROJECTS_FILE.write_text(json.dumps(stored, ensure_ascii=False, indent=2))

    return result


def save_projects(projects: list) -> None:
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2))


def load_rules() -> str:
    if not RULES_FILE.exists():
        RULES_FILE.write_text(DEFAULT_RULES)
        return DEFAULT_RULES
    return RULES_FILE.read_text()


def save_rules(text: str) -> None:
    RULES_FILE.write_text(text)


def load_statusline() -> dict:
    if not STATUSLINE_FILE.exists():
        return {"text": "", "updated_at": 0}
    return json.loads(STATUSLINE_FILE.read_text())


def save_statusline(data: dict) -> None:
    STATUSLINE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def load_sessions() -> dict:
    if not SESSIONS_FILE.exists():
        return {}
    return json.loads(SESSIONS_FILE.read_text())


def save_sessions(sessions: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))


def load_stats() -> dict:
    defaults = {
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_output_tokens": 0,
        "total_duration_secs": 0,
    }
    if not STATS_FILE.exists():
        return defaults
    return {**defaults, **json.loads(STATS_FILE.read_text())}


def save_stats(data: dict) -> None:
    STATS_FILE.write_text(json.dumps(data, ensure_ascii=False))


def load_github_links() -> dict:
    if not GITHUB_LINKS_FILE.exists():
        return {}
    return json.loads(GITHUB_LINKS_FILE.read_text())


def save_github_links(links: dict) -> None:
    GITHUB_LINKS_FILE.write_text(json.dumps(links, ensure_ascii=False, indent=2))


def load_plugins() -> dict:
    if not PLUGINS_FILE.exists():
        return {}
    return json.loads(PLUGINS_FILE.read_text())


def save_plugins(plugins: dict) -> None:
    PLUGINS_FILE.write_text(json.dumps(plugins, ensure_ascii=False, indent=2))


def load_plugin_states() -> dict:
    if not PLUGIN_STATES_FILE.exists():
        return {}
    return json.loads(PLUGIN_STATES_FILE.read_text())


def save_plugin_states(states: dict) -> None:
    PLUGIN_STATES_FILE.write_text(json.dumps(states, ensure_ascii=False, indent=2))


def load_crypto_state() -> dict:
    if not CRYPTO_STATE_FILE.exists():
        return {}
    return json.loads(CRYPTO_STATE_FILE.read_text())


def save_crypto_state(state: dict) -> None:
    CRYPTO_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def accumulate_stats(todos: list) -> None:
    """Add tokens/duration from the given todos into the persistent stats file."""
    stats = load_stats()
    for t in todos:
        if t.get("tokens"):
            stats["total_input_tokens"] += t["tokens"].get("input", 0)
            stats["total_cache_creation_tokens"] += t["tokens"].get("cache_creation", 0)
            stats["total_cache_read_tokens"] += t["tokens"].get("cache_read", 0)
            stats["total_output_tokens"] += t["tokens"].get("output", 0)
        if t.get("duration_secs") is not None:
            stats["total_duration_secs"] += t["duration_secs"]
    save_stats(stats)
