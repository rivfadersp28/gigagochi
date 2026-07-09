#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
STATE_DIR="$ROOT_DIR/.local-dev"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

BACKEND_PID_FILE="$STATE_DIR/backend.pid"
FRONTEND_PID_FILE="$STATE_DIR/frontend.pid"
BACKEND_LOG="$STATE_DIR/backend.log"
FRONTEND_LOG="$STATE_DIR/frontend.log"

usage() {
  cat <<EOF
Usage: $(basename "$0") {start|stop|restart|status|logs}

Environment:
  BACKEND_PORT=$BACKEND_PORT
  FRONTEND_PORT=$FRONTEND_PORT
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Command not found: $1"
    exit 1
  fi
}

pid_is_running() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1
}

pid_from_file() {
  local file="$1"
  [ -f "$file" ] && sed -n '1p' "$file" || true
}

port_pids() {
  local port="$1"
  lsof -ti "tcp:$port" 2>/dev/null || true
}

stop_pid_file() {
  local name="$1"
  local file="$2"
  local pid
  pid="$(pid_from_file "$file")"

  if pid_is_running "$pid"; then
    echo "Stopping $name pid=$pid"
    kill "$pid" 2>/dev/null || true
    sleep 1
    if pid_is_running "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi

  rm -f "$file"
}

stop_port() {
  local port="$1"
  local pids
  pids="$(port_pids "$port")"

  if [ -n "$pids" ]; then
    echo "Stopping processes on port $port: $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(port_pids "$port")"
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
  fi
}

wait_for_port() {
  local name="$1"
  local port="$2"
  local attempts="${3:-30}"
  local i=1

  while [ "$i" -le "$attempts" ]; do
    if [ -n "$(port_pids "$port")" ]; then
      echo "$name is listening on http://localhost:$port"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done

  echo "$name did not open port $port in ${attempts}s"
  return 1
}

ensure_backend_ready() {
  if [ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]; then
    cat <<EOF
Backend venv is not ready.
Run:
  cd "$BACKEND_DIR"
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e ".[dev]"
EOF
    exit 1
  fi

  if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "Missing $BACKEND_DIR/.env. Run: cp \"$BACKEND_DIR/.env.example\" \"$BACKEND_DIR/.env\""
    exit 1
  fi
}

ensure_frontend_ready() {
  require_command npm

  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "Frontend dependencies are not installed. Run: cd \"$FRONTEND_DIR\" && npm install"
    exit 1
  fi

  if [ ! -f "$FRONTEND_DIR/.env.local" ]; then
    echo "Missing $FRONTEND_DIR/.env.local. Run: cp \"$FRONTEND_DIR/.env.example\" \"$FRONTEND_DIR/.env.local\""
    exit 1
  fi
}

start_backend() {
  ensure_backend_ready
  echo "Starting backend on port $BACKEND_PORT"
  (
    cd "$BACKEND_DIR"
    "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --reload --port "$BACKEND_PORT" >"$BACKEND_LOG" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
}

start_frontend() {
  ensure_frontend_ready
  echo "Starting frontend on port $FRONTEND_PORT"
  (
    cd "$FRONTEND_DIR"
    npm run dev -- --port "$FRONTEND_PORT" >"$FRONTEND_LOG" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )
}

start_all() {
  local failed=0

  mkdir -p "$STATE_DIR"

  stop_pid_file backend "$BACKEND_PID_FILE"
  stop_pid_file frontend "$FRONTEND_PID_FILE"
  stop_port "$BACKEND_PORT"
  stop_port "$FRONTEND_PORT"

  start_backend
  start_frontend

  wait_for_port backend "$BACKEND_PORT" 30 || failed=1
  wait_for_port frontend "$FRONTEND_PORT" 45 || failed=1

  echo
  echo "Frontend: http://localhost:$FRONTEND_PORT"
  echo "Backend health: http://localhost:$BACKEND_PORT/health"
  echo "Logs:"
  echo "  tail -f \"$BACKEND_LOG\""
  echo "  tail -f \"$FRONTEND_LOG\""

  if [ "$failed" -ne 0 ]; then
    echo
    echo "One or more services did not start. Recent logs:"
    tail -n 40 "$BACKEND_LOG" "$FRONTEND_LOG" 2>/dev/null || true
    exit 1
  fi
}

stop_all() {
  mkdir -p "$STATE_DIR"

  stop_pid_file frontend "$FRONTEND_PID_FILE"
  stop_pid_file backend "$BACKEND_PID_FILE"
  stop_port "$FRONTEND_PORT"
  stop_port "$BACKEND_PORT"
}

status_one() {
  local name="$1"
  local pid_file="$2"
  local port="$3"
  local pid
  local pids

  pid="$(pid_from_file "$pid_file")"
  pids="$(port_pids "$port")"

  if pid_is_running "$pid"; then
    echo "$name: running pid=$pid port=$port"
  elif [ -n "$pids" ]; then
    echo "$name: port $port is occupied by pid(s): $pids"
  else
    echo "$name: stopped"
  fi
}

status_all() {
  status_one backend "$BACKEND_PID_FILE" "$BACKEND_PORT"
  status_one frontend "$FRONTEND_PID_FILE" "$FRONTEND_PORT"
}

logs_all() {
  mkdir -p "$STATE_DIR"
  touch "$BACKEND_LOG" "$FRONTEND_LOG"
  tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
}

case "${1:-}" in
  start)
    start_all
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    start_all
    ;;
  status)
    status_all
    ;;
  logs)
    logs_all
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
