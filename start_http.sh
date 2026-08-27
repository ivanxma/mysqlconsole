#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN_INPUT="${PYTHON_BIN:-}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$SCRIPT_DIR/.runtime.env}"
HOST="${HOST:-}"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

# DBCONSOLE_PYTHON_BIN identifies the base interpreter used to build/update
# the environment. Run the web service from that environment by default.
PYTHON_BIN="${PYTHON_BIN_INPUT:-$SCRIPT_DIR/.venv/bin/python}"
HOST="${HOST:-127.0.0.1}"
DEFAULT_HTTP_PORT="${DEFAULT_HTTP_PORT:-80}"
PORT="${PORT:-$DEFAULT_HTTP_PORT}"
DBCONSOLE_RUNTIME_DIR="${DBCONSOLE_RUNTIME_DIR:-$SCRIPT_DIR/.runtime}"
DBCONSOLE_STATE_DIR="${DBCONSOLE_STATE_DIR:-$SCRIPT_DIR/.state}"
XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$DBCONSOLE_RUNTIME_DIR}"
# Do not inherit an HTTPS-only cookie setting when both listener services use
# the same runtime env file. HTTPS sets this flag independently in start_https.
DBCONSOLE_LISTENER_SCHEME=http
DBCONSOLE_SESSION_COOKIE_SECURE=0
export HOST PORT DBCONSOLE_RUNTIME_DIR DBCONSOLE_STATE_DIR XDG_RUNTIME_DIR DBCONSOLE_MYSQLSH DBCONSOLE_PYTHON_BIN DBCONSOLE_PYTHON_MIN_VERSION DBCONSOLE_LISTENER_SCHEME DBCONSOLE_SESSION_COOKIE_SECURE DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL DBCONSOLE_UPDATE_ALLOWED_BRANCH DBCONSOLE_OBJECT_STORAGE_REGION

ensure_local_mysql_started() {
  if [[ "${LOCAL_MYSQL_AUTOSTART:-0}" != "1" ]]; then
    return 0
  fi
  if [[ -n "${LOCAL_MYSQL_SOCKET:-}" && -S "$LOCAL_MYSQL_SOCKET" ]]; then
    return 0
  fi

  "$SCRIPT_DIR/start_mysql.sh"
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runtime not found at $PYTHON_BIN. Run ./setup.sh first or set PYTHON_BIN." >&2
  exit 1
fi

ensure_local_mysql_started

cd "$SCRIPT_DIR"
exec "$PYTHON_BIN" -m gunicorn \
  --workers 1 \
  --threads "${DBCONSOLE_WEB_THREADS:-8}" \
  --timeout "${DBCONSOLE_WEB_TIMEOUT:-120}" \
  --graceful-timeout 30 \
  --bind "$HOST:$PORT" \
  --access-logfile - \
  --error-logfile - \
  wsgi:application
