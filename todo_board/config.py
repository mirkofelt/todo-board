from pathlib import Path
import os

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("TODO_BOARD_DATA_DIR", PACKAGE_DIR.parent))

TODOS_FILE = DATA_DIR / "todos.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
RULES_FILE = DATA_DIR / "rules.txt"
STATUSLINE_FILE = DATA_DIR / "statusline.json"

CONTEXT_LIMIT_THRESHOLD = 25 * 60  # seconds

DEFAULT_PROJECTS = [{"id": 1, "name": "General"}]

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
