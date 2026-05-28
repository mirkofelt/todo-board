# Backward-compatibility shim — allows starting with:
#   python -m uvicorn app:app --port 7842
# Prefer: python -m todo_board
from todo_board.server import app  # noqa: F401
