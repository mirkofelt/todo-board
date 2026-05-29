#!/usr/bin/env bash
# Restart the todo-board uvicorn server.
# Sends SIGTERM and waits for graceful shutdown (_prepare_for_restart) before starting fresh.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE=/tmp/todo-board.pid
LOG_FILE=/tmp/todo-board.log
PORT=7842

# Find PID of the process listening on PORT via /proc/net/tcp
_find_pid() {
  python3 - <<'PYEOF'
import os, glob
PORT = 7842
HEX_PORT = f"{PORT:04X}"
inode = None
with open('/proc/net/tcp') as f:
    for line in f:
        p = line.split()
        if len(p) > 9 and p[1].endswith(f':{HEX_PORT}') and p[3] == '0A':
            inode = p[9]; break
if not inode:
    exit(0)
for pid_dir in glob.glob('/proc/[0-9]*'):
    pid = pid_dir.split('/')[-1]
    try:
        for fd in os.listdir(f'{pid_dir}/fd'):
            if f'socket:[{inode}]' in os.readlink(f'{pid_dir}/fd/{fd}'):
                print(pid)
                exit(0)
    except:
        pass
PYEOF
}

OLD_PID=$(_find_pid)
if [ -n "$OLD_PID" ]; then
  kill "$OLD_PID" 2>/dev/null && echo "Stopping server (PID $OLD_PID), waiting for graceful shutdown..." || true
  # Wait up to 10s for _prepare_for_restart() to complete (SIGTERM workers, flush state)
  for i in $(seq 1 10); do
    if ! kill -0 "$OLD_PID" 2>/dev/null; then
      echo "Server stopped after ${i}s"
      break
    fi
    sleep 1
  done
  # Force-kill if still alive after graceful window
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Force-killing server (PID $OLD_PID)"
    kill -9 "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
fi

cd "$SCRIPT_DIR"
# Unset any test env vars that pytest may have injected into the environment
unset TODO_BOARD_DATA_DIR TODO_BOARD_PROJECTS_DIR
nohup python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" \
  > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
sleep 2

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Server started (PID $(cat "$PID_FILE"))"
else
  echo "Server failed to start — check $LOG_FILE"
  tail -20 "$LOG_FILE"
  exit 1
fi
