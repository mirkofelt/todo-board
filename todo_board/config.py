import os
import re
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("TODO_BOARD_DATA_DIR", PACKAGE_DIR.parent))

TODOS_FILE = DATA_DIR / "todos.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
RULES_FILE = DATA_DIR / "rules.txt"
STATUSLINE_FILE = DATA_DIR / "statusline.json"
STATS_FILE = DATA_DIR / "stats.json"
COUNTER_FILE = DATA_DIR / "counter.json"

PROJECTS_DIR = Path(os.environ.get("TODO_BOARD_PROJECTS_DIR", DATA_DIR.parent))

SESSIONS_FILE = DATA_DIR / "sessions.json"
GITHUB_LINKS_FILE = DATA_DIR / "github_links.json"
NEWS_FILE = DATA_DIR / "news.json"
PLUGINS_FILE = DATA_DIR / "plugins.json"
PLUGIN_STATES_FILE = DATA_DIR / "plugin_states.json"

CONTEXT_LIMIT_THRESHOLD = 25 * 60  # seconds

MEMORY_FILE = Path(os.environ.get("MEMORY_FILE", "/root/MEMORY.md"))
MEMORY_BACKUP_FILE = Path(os.environ.get("MEMORY_BACKUP_FILE", "/root/.claude/MEMORY.md"))

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
CLAUDE_MAX_TURNS = int(os.environ.get("CLAUDE_MAX_TURNS", "30"))
CLAUDE_MAX_BUDGET_USD = os.environ.get("CLAUDE_MAX_BUDGET_USD", "")
MAX_RETRIES = int(os.environ.get("TODO_MAX_RETRIES", "2"))

DEFAULT_PROJECTS = [{"id": 1, "name": "General"}]

_PROJECTS_EXCLUDE: frozenset = frozenset({"memory", "__pycache__"})
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_project_dir(path: Path) -> bool:
    name = path.name
    return (
        path.is_dir()
        and not name.startswith(".")
        and not name.startswith("_")
        and name not in _PROJECTS_EXCLUDE
        and not _UUID_RE.match(name)
    )

DEFAULT_RULES = """\
- README up to date and complete before closing a project
- No credentials, API keys, or secrets in code or config files
- No personal data (names, emails, phone numbers) in source files
- No infrastructure details (IPs, hostnames, ports) in code
- All code, comments, and commit messages in English
- Ensure proper folder structure and file naming in repository
- Ensure testing of requested features and relevant functionality
- No CDN — bundle all JS/CSS dependencies locally
- Web UIs: dark theme, no external resources, mobile-friendly
- Test in browser before declaring UI work done"""
