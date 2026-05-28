import os

import uvicorn

from .server import app


def main() -> None:
    port = int(os.environ.get("TODO_BOARD_PORT", 7842))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
