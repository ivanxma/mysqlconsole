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
LOCAL_MYSQL_PID_FILE="${LOCAL_MYSQL_PID_FILE:-}"
LOCAL_MYSQL_CONFIG_FILE="${LOCAL_MYSQL_CONFIG_FILE:-$SCRIPT_DIR/etc/my.cnf}"

if [[ -z "$LOCAL_MYSQL_SOCKET" && -f "$LOCAL_MYSQL_CONFIG_FILE" ]]; then
  LOCAL_MYSQL_SOCKET="$(awk -F= '$1 == "socket" {print $2; exit}' "$LOCAL_MYSQL_CONFIG_FILE")"
fi
if [[ -z "$LOCAL_MYSQL_PID_FILE" && -f "$LOCAL_MYSQL_CONFIG_FILE" ]]; then
  LOCAL_MYSQL_PID_FILE="$(awk -F= '$1 == "pid-file" {print $2; exit}' "$LOCAL_MYSQL_CONFIG_FILE")"
fi

socket_ready() {
  [[ -n "$LOCAL_MYSQL_SOCKET" && -S "$LOCAL_MYSQL_SOCKET" ]]
}

stop_with_privilege() {
  if "$@" >/dev/null 2>&1; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -n "$@" >/dev/null 2>&1
    return $?
  fi
  return 1
}

echo "Stopping local MySQL service ${LOCAL_MYSQL_SERVICE}."
case "$(uname -s)" in
  Darwin)
    mysql_base="${LOCAL_MYSQL_BASEDIR:-$SCRIPT_DIR/.embedded/mysql-server}"
    if [[ -x "$mysql_base/bin/mysqladmin" && -n "${LOCAL_MYSQL_SOCKET:-}" ]]; then
      "$mysql_base/bin/mysqladmin" --protocol=socket --socket="$LOCAL_MYSQL_SOCKET" -uroot shutdown >/dev/null 2>&1 || true
    else
      echo "Embedded MySQL Server was not found at $mysql_base." >&2
      exit 1
    fi
    ;;
  *)
    if [[ -n "$LOCAL_MYSQL_PID_FILE" && -f "$LOCAL_MYSQL_PID_FILE" ]]; then
      pid="$(cat "$LOCAL_MYSQL_PID_FILE" 2>/dev/null || true)"
      if [[ "$pid" =~ ^[0-9]+$ ]]; then
        kill "$pid" >/dev/null 2>&1 || true
      fi
    elif command -v systemctl >/dev/null 2>&1; then
      stop_with_privilege systemctl stop "$LOCAL_MYSQL_SERVICE" || true
    elif command -v service >/dev/null 2>&1; then
      stop_with_privilege service "$LOCAL_MYSQL_SERVICE" stop || true
    else
      echo "No supported service manager found. Stop MySQL manually." >&2
      exit 1
    fi
    ;;
esac

for _ in 1 2 3 4 5; do
  if ! socket_ready; then
    echo "Local MySQL stopped."
    exit 0
  fi
  sleep 1
done

if [[ -n "$LOCAL_MYSQL_SOCKET" ]]; then
  echo "Local MySQL stop was requested, but socket still exists at $LOCAL_MYSQL_SOCKET." >&2
else
  echo "Local MySQL stop was requested. Set LOCAL_MYSQL_SOCKET in .runtime.env to verify socket shutdown." >&2
fi
exit 1
