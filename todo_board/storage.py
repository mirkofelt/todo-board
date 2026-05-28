import json

from .config import (
    DEFAULT_PROJECTS,
    DEFAULT_RULES,
    PROJECTS_DIR,
    PROJECTS_FILE,
    RULES_FILE,
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
