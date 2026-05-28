import json

from .config import (
    DEFAULT_PROJECTS,
    DEFAULT_RULES,
    PROJECTS_FILE,
    RULES_FILE,
    STATUSLINE_FILE,
    TODOS_FILE,
)


def load_todos() -> list:
    if not TODOS_FILE.exists():
        return []
    return json.loads(TODOS_FILE.read_text())


def save_todos(todos: list) -> None:
    TODOS_FILE.write_text(json.dumps(todos, ensure_ascii=False, indent=2))


def load_projects() -> list:
    if not PROJECTS_FILE.exists():
        save_projects(DEFAULT_PROJECTS)
        return DEFAULT_PROJECTS
    return json.loads(PROJECTS_FILE.read_text())


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
