#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$SCRIPT_DIR/.runtime.env}"
LOCAL_MYSQL_ADMIN_USER="${LOCAL_MYSQL_ADMIN_USER:-localadmin}"
LOCAL_MYSQL_SOCKET="${LOCAL_MYSQL_SOCKET:-}"
LOCAL_MYSQL_DATABASE="${LOCAL_MYSQL_DATABASE:-mysql}"
if [[ -n "${LOCAL_MYSQL_INIT_FILE_PROVISIONING+x}" ]]; then
  LOCAL_MYSQL_INIT_FILE_PROVISIONING_WAS_SET=1
else
  LOCAL_MYSQL_INIT_FILE_PROVISIONING_WAS_SET=0
fi
LOCAL_MYSQL_INIT_FILE_PROVISIONING="${LOCAL_MYSQL_INIT_FILE_PROVISIONING:-${LOCAL_MYSQL_RESET_UNKNOWN_ROOT:-1}}"
LOCAL_MYSQL_SERVICE="${LOCAL_MYSQL_SERVICE:-}"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="${raw_line#"${raw_line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      LOCAL_MYSQL_ADMIN_USER)
        [[ "$LOCAL_MYSQL_ADMIN_USER" == "localadmin" && -n "$value" ]] && LOCAL_MYSQL_ADMIN_USER="$value"
        ;;
      LOCAL_MYSQL_SOCKET)
        [[ -z "$LOCAL_MYSQL_SOCKET" && -n "$value" ]] && LOCAL_MYSQL_SOCKET="$value"
        ;;
      LOCAL_MYSQL_DATABASE)
        [[ "$LOCAL_MYSQL_DATABASE" == "mysql" && -n "$value" ]] && LOCAL_MYSQL_DATABASE="$value"
        ;;
      LOCAL_MYSQL_SERVICE)
        [[ -z "$LOCAL_MYSQL_SERVICE" && -n "$value" ]] && LOCAL_MYSQL_SERVICE="$value"
        ;;
      LOCAL_MYSQL_INIT_FILE_PROVISIONING)
        if [[ -n "$value" ]]; then
          LOCAL_MYSQL_INIT_FILE_PROVISIONING="$value"
          LOCAL_MYSQL_INIT_FILE_PROVISIONING_WAS_SET=1
        fi
        ;;
      LOCAL_MYSQL_RESET_UNKNOWN_ROOT)
        [[ -n "$value" && "$LOCAL_MYSQL_INIT_FILE_PROVISIONING_WAS_SET" != "1" ]] && LOCAL_MYSQL_INIT_FILE_PROVISIONING="$value"
        ;;
    esac
  done <"$RUNTIME_ENV_FILE"
fi

usage() {
  cat <<'USAGE'
Usage:
  LOCAL_MYSQL_ADMIN_PASSWORD='<new-password>' ./reset_localadmin_password.sh
  ./reset_localadmin_password.sh --user localadmin --socket /var/lib/mysql/mysql.sock

This support script creates or resets only localadmin@localhost. It does not
create a MySQL root user, reset root@localhost, or store the password.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      [[ $# -ge 2 ]] || { echo "--user requires a value." >&2; exit 2; }
      LOCAL_MYSQL_ADMIN_USER="$2"
      shift 2
      ;;
    --socket)
      [[ $# -ge 2 ]] || { echo "--socket requires a value." >&2; exit 2; }
      LOCAL_MYSQL_SOCKET="$2"
      shift 2
      ;;
    --database)
      [[ $# -ge 2 ]] || { echo "--database requires a value." >&2; exit 2; }
      LOCAL_MYSQL_DATABASE="$2"
      shift 2
      ;;
    --service)
      [[ $# -ge 2 ]] || { echo "--service requires a value." >&2; exit 2; }
      LOCAL_MYSQL_SERVICE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$LOCAL_MYSQL_ADMIN_USER" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$ ]]; then
  echo "Invalid local admin username: $LOCAL_MYSQL_ADMIN_USER" >&2
  exit 2
fi

prompt_password() {
  local password_one
  local password_two
  read -r -s -p "New password for $LOCAL_MYSQL_ADMIN_USER: " password_one
  printf '\n'
  read -r -s -p "Confirm new password: " password_two
  printf '\n'
  if [[ -z "$password_one" ]]; then
    echo "Password cannot be empty." >&2
    exit 2
  fi
  if [[ "$password_one" != "$password_two" ]]; then
    echo "Password confirmation does not match." >&2
    exit 2
  fi
  LOCAL_MYSQL_ADMIN_PASSWORD="$password_one"
}

if [[ -z "${LOCAL_MYSQL_ADMIN_PASSWORD:-}" ]]; then
  if [[ -t 0 ]]; then
    prompt_password
  else
    echo "Set LOCAL_MYSQL_ADMIN_PASSWORD or run interactively to enter the new password." >&2
    exit 2
  fi
fi

if [[ "$LOCAL_MYSQL_ADMIN_PASSWORD" == *$'\n'* || "$LOCAL_MYSQL_ADMIN_PASSWORD" == *$'\r'* ]]; then
  echo "Password must not contain newline characters." >&2
  exit 2
fi

find_mysql_client() {
  if command -v mysql >/dev/null 2>&1; then
    command -v mysql
    return 0
  fi
  for candidate in /usr/bin/mysql /usr/local/bin/mysql /opt/homebrew/bin/mysql; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

detect_socket() {
  if [[ -n "$LOCAL_MYSQL_SOCKET" ]]; then
    return 0
  fi
  for candidate in /var/lib/mysql/mysql.sock /var/run/mysqld/mysqld.sock /tmp/mysql.sock; do
    if [[ -S "$candidate" ]]; then
      LOCAL_MYSQL_SOCKET="$candidate"
      return 0
    fi
  done
}

detect_service() {
  if [[ -n "$LOCAL_MYSQL_SERVICE" ]]; then
    return 0
  fi
  if command -v systemctl >/dev/null 2>&1; then
    for candidate in mysqld mysql; do
      if systemctl list-unit-files "${candidate}.service" >/dev/null 2>&1; then
        LOCAL_MYSQL_SERVICE="$candidate"
        return 0
      fi
    done
  fi
  if [[ -x /etc/init.d/mysqld ]]; then
    LOCAL_MYSQL_SERVICE="mysqld"
  elif [[ -x /etc/init.d/mysql ]]; then
    LOCAL_MYSQL_SERVICE="mysql"
  fi
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "This operation needs root privileges and sudo is not installed." >&2
    return 1
  fi
}

write_root_file() {
  local source_file="$1"
  local target_file="$2"
  run_as_root mkdir -p "$(dirname "$target_file")"
  run_as_root cp "$source_file" "$target_file"
}

append_root_file_once() {
  local target_file="$1"
  local line="$2"
  if [[ -f "$target_file" ]] && grep -Fxq "$line" "$target_file"; then
    return 0
  fi
  local temp_file
  temp_file="$(mktemp)"
  if [[ -f "$target_file" ]]; then
    run_as_root cp "$target_file" "$temp_file"
  fi
  printf '%s\n' "$line" >>"$temp_file"
  write_root_file "$temp_file" "$target_file"
  rm -f "$temp_file"
}

restart_mysql_service() {
  detect_service
  if [[ -z "$LOCAL_MYSQL_SERVICE" ]]; then
    echo "Unable to detect the local MySQL service name. Pass --service mysqld or --service mysql." >&2
    return 1
  fi
  if command -v systemctl >/dev/null 2>&1; then
    run_as_root systemctl restart "$LOCAL_MYSQL_SERVICE"
  else
    run_as_root service "$LOCAL_MYSQL_SERVICE" restart
  fi
}

sql_quote() {
  local value="$1"
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

mysql_option_file_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

make_sql_file() {
  local sql_file="$1"
  local user_literal
  local password_literal
  user_literal="$(sql_quote "$LOCAL_MYSQL_ADMIN_USER")"
  password_literal="$(sql_quote "$LOCAL_MYSQL_ADMIN_PASSWORD")"
  {
    printf 'CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s;\n' "$user_literal" "'localhost'" "$password_literal"
    printf 'ALTER USER %s@%s IDENTIFIED BY %s;\n' "$user_literal" "'localhost'" "$password_literal"
    printf 'GRANT ALL PRIVILEGES ON *.* TO %s@%s WITH GRANT OPTION;\n' "$user_literal" "'localhost'"
    printf 'FLUSH PRIVILEGES;\n'
  } >"$sql_file"
  chmod 600 "$sql_file"
}

write_defaults_file() {
  local defaults_file="$1"
  local user="$2"
  local password="$3"
  local quoted_user quoted_password quoted_socket quoted_database
  quoted_user="$(mysql_option_file_quote "$user")"
  quoted_password="$(mysql_option_file_quote "$password")"
  quoted_socket="$(mysql_option_file_quote "$LOCAL_MYSQL_SOCKET")"
  quoted_database="$(mysql_option_file_quote "$LOCAL_MYSQL_DATABASE")"
  {
    printf '[client]\n'
    printf 'user=%s\n' "$quoted_user"
    printf 'password=%s\n' "$quoted_password"
    printf 'protocol=socket\n'
    printf 'socket=%s\n' "$quoted_socket"
    printf 'database=%s\n' "$quoted_database"
  } >"$defaults_file"
  chmod 600 "$defaults_file"
}

verify_localadmin_login() {
  local mysql_bin="$1"
  local defaults_file="$2"
  "$mysql_bin" --defaults-extra-file="$defaults_file" -e "SELECT 1" >/dev/null
}

configure_with_socket_root() {
  local mysql_bin="$1"
  local sql_file="$2"
  if [[ "$(id -u)" -eq 0 ]]; then
    "$mysql_bin" --protocol=socket --socket="$LOCAL_MYSQL_SOCKET" -uroot <"$sql_file"
  else
    run_as_root "$mysql_bin" --protocol=socket --socket="$LOCAL_MYSQL_SOCKET" -uroot <"$sql_file"
  fi
}

ensure_mysql_include_dir() {
  local include_dir="$1"
  local main_config="$2"
  run_as_root mkdir -p "$include_dir"
  append_root_file_once "$main_config" "!includedir $include_dir"
}

configure_with_init_file() {
  local mysql_bin="$1"
  local sql_file="$2"
  local defaults_file="$3"
  local config_file
  local init_file
  local temp_config

  if ! truthy "$LOCAL_MYSQL_INIT_FILE_PROVISIONING"; then
    return 1
  fi

  case "$(uname -s)" in
    Linux)
      if [[ -d /etc/my.cnf.d || -f /etc/my.cnf ]]; then
        ensure_mysql_include_dir /etc/my.cnf.d /etc/my.cnf
        config_file="/etc/my.cnf.d/dbconsole-localadmin-reset.cnf"
      elif [[ -d /etc/mysql/conf.d || -f /etc/mysql/my.cnf ]]; then
        ensure_mysql_include_dir /etc/mysql/conf.d /etc/mysql/my.cnf
        config_file="/etc/mysql/conf.d/dbconsole-localadmin-reset.cnf"
      else
        return 1
      fi
      init_file="/var/lib/mysql/dbconsole-localadmin-reset.sql"
      ;;
    *)
      return 1
      ;;
  esac

  temp_config="$(mktemp)"
  run_as_root cp "$sql_file" "$init_file"
  run_as_root chown mysql:mysql "$init_file" 2>/dev/null || true
  run_as_root chmod 600 "$init_file"
  {
    printf '[mysqld]\n'
    printf 'init-file=%s\n' "$init_file"
  } >"$temp_config"
  chmod 600 "$temp_config"
  write_root_file "$temp_config" "$config_file"
  run_as_root chmod 644 "$config_file"
  rm -f "$temp_config"

  restart_mysql_service
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if verify_localadmin_login "$mysql_bin" "$defaults_file" >/dev/null 2>&1; then
      run_as_root rm -f "$config_file" "$init_file"
      restart_mysql_service
      return 0
    fi
    sleep 1
  done

  run_as_root rm -f "$config_file" "$init_file"
  restart_mysql_service || true
  return 1
}

detect_socket
if [[ -z "$LOCAL_MYSQL_SOCKET" || ! -S "$LOCAL_MYSQL_SOCKET" ]]; then
  echo "Local MySQL socket was not found. Pass --socket or set LOCAL_MYSQL_SOCKET." >&2
  exit 1
fi

MYSQL_BIN="$(find_mysql_client)" || {
  echo "mysql client was not found." >&2
  exit 1
}

SQL_FILE="$(mktemp)"
DEFAULTS_FILE="$(mktemp)"
cleanup() {
  rm -f "$SQL_FILE" "$DEFAULTS_FILE"
}
trap cleanup EXIT

make_sql_file "$SQL_FILE"
write_defaults_file "$DEFAULTS_FILE" "$LOCAL_MYSQL_ADMIN_USER" "$LOCAL_MYSQL_ADMIN_PASSWORD"

if verify_localadmin_login "$MYSQL_BIN" "$DEFAULTS_FILE" >/dev/null 2>&1; then
  echo "The supplied password already works for '$LOCAL_MYSQL_ADMIN_USER' on $LOCAL_MYSQL_SOCKET."
  exit 0
fi

if configure_with_socket_root "$MYSQL_BIN" "$SQL_FILE" >/dev/null 2>&1; then
  if verify_localadmin_login "$MYSQL_BIN" "$DEFAULTS_FILE" >/dev/null 2>&1; then
    echo "Reset '$LOCAL_MYSQL_ADMIN_USER' using socket-root access. No MySQL root account was created or reset."
    exit 0
  fi
fi

if configure_with_init_file "$MYSQL_BIN" "$SQL_FILE" "$DEFAULTS_FILE"; then
  echo "Reset '$LOCAL_MYSQL_ADMIN_USER' using one-time MySQL init-file provisioning. No MySQL root account was created or reset."
  exit 0
fi

echo "Unable to reset '$LOCAL_MYSQL_ADMIN_USER'. Verify sudo access, the MySQL service name, and socket path." >&2
exit 1
