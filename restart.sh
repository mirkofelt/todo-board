#!/usr/bin/env bash
# Restart the todo-board uvicorn server.
# Run this after any change to server code.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE=/tmp/todo-board.pid
LOG_FILE=/tmp/todo-board.log
PORT=7842

# Kill any process currently listening on the port
_kill_port() {
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

OLD_PID=$(_kill_port)
if [ -n "$OLD_PID" ]; then
  kill "$OLD_PID" 2>/dev/null && echo "Stopped old server (PID $OLD_PID)" || true
  sleep 1
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
