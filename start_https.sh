#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN_INPUT="${PYTHON_BIN:-}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$SCRIPT_DIR/.runtime.env}"
HOST="${HOST:-}"
SSL_CERT_FILE="${SSL_CERT_FILE:-}"
SSL_KEY_FILE="${SSL_KEY_FILE:-}"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

# The setup interpreter is persisted as DBCONSOLE_PYTHON_BIN so future setup
# and update runs can recreate the environment. The web service must execute
# the populated virtual environment unless an operator explicitly supplies
# PYTHON_BIN.
PYTHON_BIN="${PYTHON_BIN_INPUT:-$SCRIPT_DIR/.venv/bin/python}"
HOST="${HOST:-0.0.0.0}"
DEFAULT_HTTPS_PORT="${DEFAULT_HTTPS_PORT:-443}"
PORT="${PORT:-$DEFAULT_HTTPS_PORT}"
DBCONSOLE_RUNTIME_DIR="${DBCONSOLE_RUNTIME_DIR:-$SCRIPT_DIR/.runtime}"
DBCONSOLE_STATE_DIR="${DBCONSOLE_STATE_DIR:-$SCRIPT_DIR/.state}"
XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$DBCONSOLE_RUNTIME_DIR}"
# HTTPS is authoritative: a stale runtime env file or an operator override must
# never cause Flask to issue a session cookie without the Secure attribute.
DBCONSOLE_LISTENER_SCHEME=https
DBCONSOLE_SESSION_COOKIE_SECURE=1
export HOST PORT SSL_CERT_FILE SSL_KEY_FILE DBCONSOLE_RUNTIME_DIR DBCONSOLE_STATE_DIR XDG_RUNTIME_DIR DBCONSOLE_MYSQLSH DBCONSOLE_PYTHON_BIN DBCONSOLE_PYTHON_MIN_VERSION DBCONSOLE_LISTENER_SCHEME DBCONSOLE_SESSION_COOKIE_SECURE DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL DBCONSOLE_UPDATE_ALLOWED_BRANCH DBCONSOLE_OBJECT_STORAGE_REGION

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

if [[ -z "$SSL_CERT_FILE" || -z "$SSL_KEY_FILE" ]]; then
  echo "Set SSL_CERT_FILE and SSL_KEY_FILE before running start_https.sh." >&2
  exit 1
fi

if [[ ! -f "$SSL_CERT_FILE" || ! -f "$SSL_KEY_FILE" ]]; then
  echo "TLS certificate or key file does not exist." >&2
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
  --certfile "$SSL_CERT_FILE" \
  --keyfile "$SSL_KEY_FILE" \
  --access-logfile - \
  --error-logfile - \
  wsgi:application
