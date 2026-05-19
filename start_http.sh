#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/.venv/bin/python}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$SCRIPT_DIR/.runtime.env}"
HOST="${HOST:-}"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

HOST="${HOST:-0.0.0.0}"
DEFAULT_HTTP_PORT="${DEFAULT_HTTP_PORT:-80}"
PORT="${PORT:-$DEFAULT_HTTP_PORT}"
export HOST PORT DBCONSOLE_MYSQLSH DBCONSOLE_PYTHON_BIN DBCONSOLE_PYTHON_MIN_VERSION DBCONSOLE_SESSION_COOKIE_SECURE DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL DBCONSOLE_UPDATE_ALLOWED_BRANCH

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
exec "$PYTHON_BIN" - <<'PY'
import os

import app as module

module.ensure_profile_store()
module.ensure_object_storage_store()
module.app.run(
    debug=False,
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "80")),
    threaded=True,
)
PY
