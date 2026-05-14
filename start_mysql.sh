#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$SCRIPT_DIR/.runtime.env}"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

LOCAL_MYSQL_SERVICE="${LOCAL_MYSQL_SERVICE:-mysql}"
LOCAL_MYSQL_SOCKET="${LOCAL_MYSQL_SOCKET:-}"
LOCAL_MYSQL_CONFIG_FILE="${LOCAL_MYSQL_CONFIG_FILE:-$SCRIPT_DIR/etc/my.cnf}"
LOCAL_MYSQL_BASEDIR="${LOCAL_MYSQL_BASEDIR:-}"
LOCAL_MYSQL_DATADIR="${LOCAL_MYSQL_DATADIR:-}"

if [[ -z "$LOCAL_MYSQL_SOCKET" && -f "$LOCAL_MYSQL_CONFIG_FILE" ]]; then
  LOCAL_MYSQL_SOCKET="$(awk -F= '$1 == "socket" {print $2; exit}' "$LOCAL_MYSQL_CONFIG_FILE")"
fi

socket_ready() {
  [[ -n "$LOCAL_MYSQL_SOCKET" && -S "$LOCAL_MYSQL_SOCKET" ]]
}

start_with_privilege() {
  if "$@" >/dev/null 2>&1; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -n "$@" >/dev/null 2>&1
    return $?
  fi
  return 1
}

if socket_ready; then
  echo "Local MySQL is already running at $LOCAL_MYSQL_SOCKET."
  exit 0
fi

echo "Starting local MySQL service ${LOCAL_MYSQL_SERVICE}."
case "$(uname -s)" in
  Darwin)
    mysql_base="${LOCAL_MYSQL_BASEDIR:-$SCRIPT_DIR/.embedded/mysql-server}"
    mysql_data="${LOCAL_MYSQL_DATADIR:-$mysql_base/data}"
    if [[ -x "$mysql_base/bin/mysqld_safe" ]]; then
      mkdir -p "$(dirname "${LOCAL_MYSQL_SOCKET:-$mysql_base/run/mysql.sock}")" "$mysql_base/run" "$mysql_base/log" "$mysql_base/tmp"
      "$mysql_base/bin/mysqld_safe" \
        --basedir="$mysql_base" \
        --datadir="$mysql_data" \
        --socket="${LOCAL_MYSQL_SOCKET:-$mysql_base/run/mysql.sock}" \
        --pid-file="$mysql_base/run/mysqld.pid" \
        --log-error="$mysql_base/log/mysqld.err" \
        --tmpdir="$mysql_base/tmp" \
        --skip-networking \
        --mysqlx=0 >/dev/null 2>&1 &
    else
      echo "Embedded MySQL Server was not found at $mysql_base. Run ./setup.sh first." >&2
      exit 1
    fi
    ;;
  *)
    if [[ -n "$LOCAL_MYSQL_CONFIG_FILE" && -f "$LOCAL_MYSQL_CONFIG_FILE" ]]; then
      if command -v mysqld_safe >/dev/null 2>&1; then
        mysqld_safe --defaults-file="$LOCAL_MYSQL_CONFIG_FILE" >/dev/null 2>&1 &
      elif command -v mysqld >/dev/null 2>&1; then
        mysqld --defaults-file="$LOCAL_MYSQL_CONFIG_FILE" --daemonize >/dev/null 2>&1
      else
        echo "mysqld was not found. Run ./setup.sh first." >&2
        exit 1
      fi
    elif command -v systemctl >/dev/null 2>&1; then
      start_with_privilege systemctl start "$LOCAL_MYSQL_SERVICE" || true
    elif command -v service >/dev/null 2>&1; then
      start_with_privilege service "$LOCAL_MYSQL_SERVICE" start || true
    else
      echo "No supported service manager found. Start MySQL manually." >&2
      exit 1
    fi
    ;;
esac

for _ in 1 2 3 4 5; do
  if socket_ready; then
    echo "Local MySQL started at $LOCAL_MYSQL_SOCKET."
    exit 0
  fi
  sleep 1
done

if [[ -n "$LOCAL_MYSQL_SOCKET" ]]; then
  echo "Local MySQL start was requested, but socket was not created at $LOCAL_MYSQL_SOCKET." >&2
else
  echo "Local MySQL start was requested. Set LOCAL_MYSQL_SOCKET in .runtime.env to verify socket readiness." >&2
fi
exit 1
