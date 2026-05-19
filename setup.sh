#!/usr/bin/env bash

# When setup.sh is streamed into a shell there is no file-backed script path, so
# clone the repo first and then re-run the on-disk setup.sh with bash.
if [ -z "${BASH_VERSION:-}" ] || [ -z "${BASH_SOURCE:-}" ]; then
  set -eu

  bootstrap_print() {
    printf '%s\n' "$*" >&2
  }

  bootstrap_has_command() {
    command -v "$1" >/dev/null 2>&1
  }

  bootstrap_run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
      "$@"
    elif bootstrap_has_command sudo; then
      sudo "$@"
    else
      bootstrap_print "This step requires root privileges. Re-run as root or install sudo first."
      return 1
    fi
  }

  bootstrap_detect_os_family() {
    if [ "$(uname -s)" = "Darwin" ]; then
      printf '%s\n' "macos"
      return 0
    fi

    if [ ! -r /etc/os-release ]; then
      bootstrap_print "Unable to detect the operating system. Install git manually and rerun setup."
      return 1
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    case "$(printf '%s' "${ID:-unknown}" | tr '[:upper:]' '[:lower:]'):${VERSION_ID%%.*}" in
      ol:8|oraclelinux:8) printf '%s\n' "ol8" ;;
      ol:9|oraclelinux:9) printf '%s\n' "ol9" ;;
      ubuntu:*) printf '%s\n' "ubuntu" ;;
      *)
        bootstrap_print "Unsupported operating system: ${ID:-unknown} ${VERSION_ID:-unknown}. Install git manually and rerun setup."
        return 1
        ;;
    esac
  }

  bootstrap_install_git() {
    if bootstrap_has_command git; then
      return 0
    fi

    bootstrap_os_family="$(bootstrap_detect_os_family)" || return 1
    bootstrap_print "git was not found. Installing git for ${bootstrap_os_family}."

    case "$bootstrap_os_family" in
      ubuntu)
        bootstrap_run_as_root apt-get update
        bootstrap_run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y git
        ;;
      ol8|ol9)
        if bootstrap_has_command dnf; then
          bootstrap_run_as_root dnf install -y git
        elif bootstrap_has_command yum; then
          bootstrap_run_as_root yum install -y git
        else
          bootstrap_print "Neither dnf nor yum was found. Install git manually and rerun setup."
          return 1
        fi
        ;;
      macos)
        if bootstrap_has_command brew; then
          brew install git
        else
          if bootstrap_has_command xcode-select; then
            bootstrap_print "git was not found. Triggering Xcode Command Line Tools installation."
            xcode-select --install >/dev/null 2>&1 || true
          fi
          bootstrap_print "Install Xcode Command Line Tools or Homebrew, then rerun setup."
          return 1
        fi
        ;;
    esac

    if ! bootstrap_has_command git; then
      bootstrap_print "git installation did not complete successfully."
      return 1
    fi
  }

  bootstrap_timestamp() {
    date '+%Y%m%d%H%M%S'
  }

  bootstrap_prepare_target_dir() {
    if [ ! -e "$BOOTSTRAP_TARGET_DIR" ]; then
      return 0
    fi

    BOOTSTRAP_BACKUP_DIR="${BOOTSTRAP_TARGET_DIR}.$(bootstrap_timestamp)"
    while [ -e "$BOOTSTRAP_BACKUP_DIR" ]; do
      sleep 1
      BOOTSTRAP_BACKUP_DIR="${BOOTSTRAP_TARGET_DIR}.$(bootstrap_timestamp)"
    done

    bootstrap_print "Renaming existing $BOOTSTRAP_TARGET_DIR to $BOOTSTRAP_BACKUP_DIR"
    mv "$BOOTSTRAP_TARGET_DIR" "$BOOTSTRAP_BACKUP_DIR"
  }

  bootstrap_exec_cloned_setup() {
    if ! bootstrap_has_command bash; then
      bootstrap_print "bash is required to continue after cloning."
      return 1
    fi

    exec bash "$BOOTSTRAP_TARGET_DIR/setup.sh" "$@"
  }

  if [ -n "${0:-}" ] && [ -f "$0" ] && [ -r "$0" ]; then
    if ! bootstrap_has_command bash; then
      bootstrap_print "bash is required to run setup.sh."
      exit 1
    fi

    exec bash "$0" "$@"
  fi

  BOOTSTRAP_REPO_URL="${BOOTSTRAP_REPO_URL:-https://github.com/ivanxma/mysqlconsole.git}"
  bootstrap_repo_name="${BOOTSTRAP_REPO_URL##*/}"
  bootstrap_repo_name="${bootstrap_repo_name%.git}"
  BOOTSTRAP_CLONE_DIR="${BOOTSTRAP_CLONE_DIR:-$bootstrap_repo_name}"
  BOOTSTRAP_PARENT_DIR="${BOOTSTRAP_PARENT_DIR:-$(pwd -P)}"
  BOOTSTRAP_TARGET_DIR="${BOOTSTRAP_PARENT_DIR%/}/$BOOTSTRAP_CLONE_DIR"

  bootstrap_install_git

  mkdir -p "$BOOTSTRAP_PARENT_DIR"
  cd "$BOOTSTRAP_PARENT_DIR"
  bootstrap_prepare_target_dir

  bootstrap_print "Cloning $BOOTSTRAP_REPO_URL into $BOOTSTRAP_TARGET_DIR"
  git clone "$BOOTSTRAP_REPO_URL" "$BOOTSTRAP_TARGET_DIR"

  if [ ! -r "$BOOTSTRAP_TARGET_DIR/setup.sh" ]; then
    bootstrap_print "The cloned repository does not contain setup.sh at $BOOTSTRAP_TARGET_DIR/setup.sh"
    exit 1
  fi

  bootstrap_exec_cloned_setup "$@"
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/.venv}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$SCRIPT_DIR/.runtime.env}"
DBCONSOLE_PYTHON_MIN_VERSION="${DBCONSOLE_PYTHON_MIN_VERSION:-3.12}"
DBCONSOLE_PYTHON_BIN="${DBCONSOLE_PYTHON_BIN:-${PYTHON_BIN:-}}"
OS_FAMILY_INPUT="${OS_FAMILY:-}"
DEPLOY_MODE_INPUT="${DEPLOY_MODE:-}"
HTTP_PORT_INPUT="${HTTP_PORT:-}"
HTTPS_PORT_INPUT="${HTTPS_PORT:-}"
HOST_INPUT="${HOST:-}"
SSL_CERT_FILE_INPUT="${SSL_CERT_FILE:-}"
SSL_KEY_FILE_INPUT="${SSL_KEY_FILE:-}"
SERVICE_USER_INPUT="${SERVICE_USER:-}"
SERVICE_GROUP_INPUT="${SERVICE_GROUP:-}"
EMBEDDED_MYSQL_SHELL_DIR="${EMBEDDED_MYSQL_SHELL_DIR:-$SCRIPT_DIR/.embedded/mysql-shell}"
EMBEDDED_MYSQL_SERVER_DIR="${EMBEDDED_MYSQL_SERVER_DIR:-$SCRIPT_DIR/.embedded/mysql-server}"
LOCAL_MYSQL_BASEDIR_INPUT="${LOCAL_MYSQL_BASEDIR:-}"
LOCAL_MYSQL_DATADIR_INPUT="${LOCAL_MYSQL_DATADIR:-}"
LOCAL_MYSQL_CONFIG_FILE_INPUT="${LOCAL_MYSQL_CONFIG_FILE:-}"
LOCAL_MYSQL_ERROR_LOG_INPUT="${LOCAL_MYSQL_ERROR_LOG:-}"
LOCAL_MYSQL_PID_FILE_INPUT="${LOCAL_MYSQL_PID_FILE:-}"
MYSQL_SHELL_VENDOR_DOWNLOAD_BASE="${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE:-https://dev.mysql.com/get/Downloads/MySQL-Shell}"
MYSQL_SHELL_DOWNLOAD_PAGE="${MYSQL_SHELL_DOWNLOAD_PAGE:-https://dev.mysql.com/downloads/shell/}"
MYSQL_SHELL_EMBEDDED_URL="${MYSQL_SHELL_EMBEDDED_URL:-}"
MYSQL_SHELL_EMBEDDED_PACKAGE="${MYSQL_SHELL_EMBEDDED_PACKAGE:-}"
MYSQL_SHELL_MACOS_PACKAGE_TAG="${MYSQL_SHELL_MACOS_PACKAGE_TAG:-macos15}"
MYSQL_SERVER_VENDOR_DOWNLOAD_BASE="${MYSQL_SERVER_VENDOR_DOWNLOAD_BASE:-https://dev.mysql.com/get/Downloads}"
MYSQL_SERVER_DOWNLOAD_PAGE="${MYSQL_SERVER_DOWNLOAD_PAGE:-https://dev.mysql.com/downloads/mysql/}"
MYSQL_SERVER_EMBEDDED_URL="${MYSQL_SERVER_EMBEDDED_URL:-}"
MYSQL_SERVER_EMBEDDED_PACKAGE="${MYSQL_SERVER_EMBEDDED_PACKAGE:-}"
MYSQL_SERVER_VERSION="${MYSQL_SERVER_VERSION:-}"
MYSQL_SERVER_MACOS_PACKAGE_TAG="${MYSQL_SERVER_MACOS_PACKAGE_TAG:-macos15}"
LOCAL_MYSQL_PROFILE_NAME_INPUT="${LOCAL_MYSQL_PROFILE_NAME:-local-admin-profile}"
LOCAL_MYSQL_ADMIN_USER_INPUT="${LOCAL_MYSQL_ADMIN_USER:-}"
LOCAL_MYSQL_ADMIN_PASSWORD_INPUT="${LOCAL_MYSQL_ADMIN_PASSWORD:-}"
LOCAL_MYSQL_ROOT_PASSWORD_INPUT="${LOCAL_MYSQL_ROOT_PASSWORD:-}"
LOCAL_MYSQL_INIT_FILE_PROVISIONING="${LOCAL_MYSQL_INIT_FILE_PROVISIONING:-${LOCAL_MYSQL_RESET_UNKNOWN_ROOT:-1}}"
LOCAL_MYSQL_PORT_INPUT="${LOCAL_MYSQL_PORT:-3306}"
LOCAL_MYSQL_SOCKET_INPUT="${LOCAL_MYSQL_SOCKET:-}"
LOCAL_MYSQL_DATABASE_INPUT="${LOCAL_MYSQL_DATABASE:-mysql}"
DBCONSOLE_DEPENDENCY_AUDIT="${DBCONSOLE_DEPENDENCY_AUDIT:-warn}"
DBCONSOLE_DEPENDENCY_AUDIT_STRICT="${DBCONSOLE_DEPENDENCY_AUDIT_STRICT:-0}"
DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL="${DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL:-}"
DBCONSOLE_UPDATE_ALLOWED_BRANCH="${DBCONSOLE_UPDATE_ALLOWED_BRANCH:-main}"
EXISTING_DEFAULT_HTTP_PORT=""
EXISTING_DEFAULT_HTTPS_PORT=""
EXISTING_HOST=""
EXISTING_SSL_CERT_FILE=""
EXISTING_SSL_KEY_FILE=""
LOCAL_MYSQL_TEMP_ROOT_PASSWORD=""
LOCAL_MYSQL_DATADIR_INITIALIZED=0

print_usage() {
  cat <<EOF
Usage:
  ./setup.sh [os_family] [deploy_mode] [http_port] [https_port]
  ./setup.sh [os_family] [deploy_mode] [--http-port PORT] [--https-port PORT]
  LOCAL_MYSQL_ADMIN_USER=USER LOCAL_MYSQL_ADMIN_PASSWORD=PASSWORD ./setup.sh [os_family] [deploy_mode]
  curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/setup.sh | sh -s -- [args]

Arguments:
  os_family    ol8 | ol9 | ubuntu | macos
  deploy_mode  http | https | both | none

Environment overrides:
  OS_FAMILY, DEPLOY_MODE, HOST, HTTP_PORT, HTTPS_PORT, SSL_CERT_FILE,
  SSL_KEY_FILE, SERVICE_USER, SERVICE_GROUP, VENV_DIR, RUNTIME_ENV_FILE,
  DBCONSOLE_PYTHON_BIN, DBCONSOLE_PYTHON_MIN_VERSION,
  EMBEDDED_MYSQL_SHELL_DIR, EMBEDDED_MYSQL_SERVER_DIR,
  MYSQL_SHELL_EMBEDDED_URL,
  MYSQL_SHELL_EMBEDDED_PACKAGE, MYSQL_SHELL_MACOS_PACKAGE_TAG,
  MYSQL_SERVER_VERSION, MYSQL_SERVER_EMBEDDED_URL,
  MYSQL_SERVER_EMBEDDED_PACKAGE, MYSQL_SERVER_MACOS_PACKAGE_TAG,
  LOCAL_MYSQL_ADMIN_USER, LOCAL_MYSQL_ADMIN_PASSWORD, LOCAL_MYSQL_ROOT_PASSWORD,
  LOCAL_MYSQL_PROFILE_NAME, LOCAL_MYSQL_SOCKET, LOCAL_MYSQL_DATABASE,
  LOCAL_MYSQL_INIT_FILE_PROVISIONING, LOCAL_MYSQL_RESET_UNKNOWN_ROOT,
  DBCONSOLE_DEPENDENCY_AUDIT,
  DBCONSOLE_DEPENDENCY_AUDIT_STRICT, DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL,
  DBCONSOLE_UPDATE_ALLOWED_BRANCH

Bootstrap overrides for curl | sh:
  BOOTSTRAP_REPO_URL, BOOTSTRAP_CLONE_DIR, BOOTSTRAP_PARENT_DIR
EOF
}

is_interactive_terminal() {
  [[ -t 0 && -t 1 ]]
}

skip_privileged_setup_enabled() {
  case "$(printf '%s' "${SKIP_PRIVILEGED_SETUP:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
      return 0
      ;;
  esac
  return 1
}

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "This step requires root privileges. Re-run setup.sh from a shell with sudo access." >&2
    return 1
  fi

  if is_interactive_terminal; then
    sudo "$@"
  else
    sudo -n "$@"
  fi
}

write_root_file() {
  local target_path="$1"

  if [[ "$(id -u)" -eq 0 ]]; then
    cat >"$target_path"
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "This step requires root privileges. Re-run setup.sh from a shell with sudo access." >&2
    return 1
  fi

  if is_interactive_terminal; then
    sudo tee "$target_path" >/dev/null
  else
    sudo -n tee "$target_path" >/dev/null
  fi
}

append_root_file_once() {
  local target_path="$1"
  local line_value="$2"

  if [[ -f "$target_path" ]] && grep -Fxq "$line_value" "$target_path" 2>/dev/null; then
    return 0
  fi

  if [[ "$(id -u)" -eq 0 ]]; then
    if [[ -f "$target_path" ]] && grep -Fxq "$line_value" "$target_path" 2>/dev/null; then
      return 0
    fi
    printf '\n%s\n' "$line_value" >>"$target_path"
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "This step requires root privileges. Re-run setup.sh from a shell with sudo access." >&2
    return 1
  fi

  if is_interactive_terminal; then
    sudo /bin/sh -c 'target_path="$1"; line_value="$2"; grep -Fxq "$line_value" "$target_path" 2>/dev/null || printf "\n%s\n" "$line_value" >>"$target_path"' sh "$target_path" "$line_value"
  else
    sudo -n /bin/sh -c 'target_path="$1"; line_value="$2"; grep -Fxq "$line_value" "$target_path" 2>/dev/null || printf "\n%s\n" "$line_value" >>"$target_path"' sh "$target_path" "$line_value"
  fi
}

restore_selinux_context() {
  local target_path="$1"
  if command -v restorecon >/dev/null 2>&1; then
    run_as_root restorecon "$target_path" >/dev/null 2>&1 || true
  fi
}

log_skipped_privileged_step() {
  local step_description="$1"
  echo "Skipping ${step_description} because SKIP_PRIVILEGED_SETUP=1. Re-run ./setup.sh from a shell with sudo access to apply privileged changes." >&2
}

parse_args() {
  local positional=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        print_usage
        exit 0
        ;;
      --http-port)
        if [[ $# -lt 2 ]]; then
          echo "--http-port requires a port value." >&2
          return 1
        fi
        HTTP_PORT_INPUT="$2"
        shift 2
        ;;
      --https-port)
        if [[ $# -lt 2 ]]; then
          echo "--https-port requires a port value." >&2
          return 1
        fi
        HTTPS_PORT_INPUT="$2"
        shift 2
        ;;
      --local-mysql-admin-user)
        if [[ $# -lt 2 ]]; then
          echo "--local-mysql-admin-user requires a username value." >&2
          return 1
        fi
        LOCAL_MYSQL_ADMIN_USER_INPUT="$2"
        shift 2
        ;;
      --local-mysql-admin-password)
        if [[ $# -lt 2 ]]; then
          echo "--local-mysql-admin-password requires a password value." >&2
          return 1
        fi
        echo "Warning: --local-mysql-admin-password can expose the password in shell history and process listings. Prefer LOCAL_MYSQL_ADMIN_PASSWORD or the interactive prompt." >&2
        LOCAL_MYSQL_ADMIN_PASSWORD_INPUT="$2"
        shift 2
        ;;
      --local-mysql-root-password)
        if [[ $# -lt 2 ]]; then
          echo "--local-mysql-root-password requires a password value." >&2
          return 1
        fi
        echo "Warning: --local-mysql-root-password can expose the password in shell history and process listings. Prefer LOCAL_MYSQL_ROOT_PASSWORD." >&2
        LOCAL_MYSQL_ROOT_PASSWORD_INPUT="$2"
        shift 2
        ;;
      --local-mysql-profile-name)
        if [[ $# -lt 2 ]]; then
          echo "--local-mysql-profile-name requires a profile name value." >&2
          return 1
        fi
        LOCAL_MYSQL_PROFILE_NAME_INPUT="$2"
        shift 2
        ;;
      --local-mysql-database)
        if [[ $# -lt 2 ]]; then
          echo "--local-mysql-database requires a database value." >&2
          return 1
        fi
        LOCAL_MYSQL_DATABASE_INPUT="$2"
        shift 2
        ;;
      --local-mysql-socket)
        if [[ $# -lt 2 ]]; then
          echo "--local-mysql-socket requires a socket path value." >&2
          return 1
        fi
        LOCAL_MYSQL_SOCKET_INPUT="$2"
        shift 2
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do
          positional+=("$1")
          shift
        done
        ;;
      -*)
        echo "Unknown option: $1" >&2
        return 1
        ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done

  case "${#positional[@]}" in
    0) ;;
    1)
      OS_FAMILY_INPUT="${positional[0]}"
      ;;
    2)
      OS_FAMILY_INPUT="${positional[0]}"
      DEPLOY_MODE_INPUT="${positional[1]}"
      ;;
    3)
      OS_FAMILY_INPUT="${positional[0]}"
      DEPLOY_MODE_INPUT="${positional[1]}"
      HTTP_PORT_INPUT="${positional[2]}"
      ;;
    4)
      OS_FAMILY_INPUT="${positional[0]}"
      DEPLOY_MODE_INPUT="${positional[1]}"
      HTTP_PORT_INPUT="${positional[2]}"
      HTTPS_PORT_INPUT="${positional[3]}"
      ;;
    *)
      echo "Too many positional arguments." >&2
      print_usage >&2
      return 1
      ;;
  esac
}

to_lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

normalize_os_family() {
  case "$(to_lower "$1")" in
    ol8|oraclelinux8|oracle-linux-8) echo "ol8" ;;
    ol9|oraclelinux9|oracle-linux-9) echo "ol9" ;;
    ubuntu) echo "ubuntu" ;;
    macos|mac|darwin|osx) echo "macos" ;;
    *)
      echo "Unsupported OS family '$1'. Use one of: ol8, ol9, ubuntu, macos." >&2
      return 1
      ;;
  esac
}

detect_os_family() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "macos"
    return 0
  fi

  if [[ ! -r /etc/os-release ]]; then
    echo "Unable to detect the operating system. Pass one of: ol8, ol9, ubuntu, macos." >&2
    return 1
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  case "$(to_lower "${ID:-unknown}"):${VERSION_ID%%.*}" in
    ol:8|oraclelinux:8) echo "ol8" ;;
    ol:9|oraclelinux:9) echo "ol9" ;;
    ubuntu:*) echo "ubuntu" ;;
    *)
      echo "Unsupported operating system: ${ID:-unknown} ${VERSION_ID:-unknown}. Pass one of: ol8, ol9, ubuntu, macos." >&2
      return 1
      ;;
  esac
}

normalize_deploy_mode() {
  local normalized
  normalized="$(to_lower "$1")"
  case "$normalized" in
    http|https|both|none) echo "$normalized" ;;
    *)
      echo "Unsupported deploy mode '$1'. Use http, https, both, or none." >&2
      return 1
      ;;
  esac
}

normalize_port() {
  local label="$1"
  local port_value="$2"

  if [[ ! "$port_value" =~ ^[0-9]+$ ]]; then
    echo "${label} port must be numeric. Received '$port_value'." >&2
    return 1
  fi

  if (( port_value < 1 || port_value > 65535 )); then
    echo "${label} port must be between 1 and 65535. Received '$port_value'." >&2
    return 1
  fi

  echo "$port_value"
}

port_requires_privileged_bind() {
  local port_value="$1"

  (( port_value > 0 && port_value < 1024 ))
}

truthy_value() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
      return 0
      ;;
  esac
  return 1
}

version_major_minor_ge() {
  local installed="$1"
  local required="$2"
  local installed_major installed_minor required_major required_minor

  installed_major="${installed%%.*}"
  installed_minor="${installed#*.}"
  installed_minor="${installed_minor%%.*}"
  required_major="${required%%.*}"
  required_minor="${required#*.}"
  required_minor="${required_minor%%.*}"

  [[ "$installed_major" =~ ^[0-9]+$ && "$installed_minor" =~ ^[0-9]+$ && "$required_major" =~ ^[0-9]+$ && "$required_minor" =~ ^[0-9]+$ ]] || return 1
  if (( installed_major > required_major )); then
    return 0
  fi
  if (( installed_major < required_major )); then
    return 1
  fi
  (( installed_minor >= required_minor ))
}

dependency_audit_enabled() {
  case "$(printf '%s' "$DBCONSOLE_DEPENDENCY_AUDIT" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off|skip|none)
      return 1
      ;;
  esac
  return 0
}

dependency_audit_strict_enabled() {
  truthy_value "$DBCONSOLE_DEPENDENCY_AUDIT_STRICT"
}

local_mysql_init_file_provisioning_enabled() {
  truthy_value "$LOCAL_MYSQL_INIT_FILE_PROVISIONING"
}

ensure_mysql_config_include_dir() {
  local os_family="$1"
  local main_config=""
  local include_dir=""

  case "$os_family" in
    ol8|ol9)
      main_config="/etc/my.cnf"
      include_dir="/etc/my.cnf.d"
      ;;
    ubuntu)
      main_config="/etc/mysql/my.cnf"
      include_dir="/etc/mysql/conf.d"
      ;;
    *)
      return 0
      ;;
  esac

  if [[ ! -f "$main_config" ]]; then
    return 0
  fi
  run_as_root mkdir -p "$include_dir"
  append_root_file_once "$main_config" "!includedir $include_dir"
}

current_update_remote_url() {
  if [[ -n "$DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL" ]]; then
    printf '%s\n' "$DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL"
    return 0
  fi
  if command -v git >/dev/null 2>&1 && git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true
    return 0
  fi
  printf '%s\n' "https://github.com/ivanxma/mysqlconsole.git"
}

current_update_branch() {
  if [[ -n "$DBCONSOLE_UPDATE_ALLOWED_BRANCH" ]]; then
    printf '%s\n' "$DBCONSOLE_UPDATE_ALLOWED_BRANCH"
    return 0
  fi
  if command -v git >/dev/null 2>&1 && git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$SCRIPT_DIR" branch --show-current 2>/dev/null || true
    return 0
  fi
  printf '%s\n' "main"
}

python_version_for_command() {
  local python_command="$1"
  "$python_command" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
}

python_meets_min_version() {
  local python_command="$1"
  local python_version

  if [[ -z "$python_command" ]] || ! command -v "$python_command" >/dev/null 2>&1; then
    return 1
  fi
  python_version="$(python_version_for_command "$python_command" 2>/dev/null || true)"
  [[ -n "$python_version" ]] && version_major_minor_ge "$python_version" "$DBCONSOLE_PYTHON_MIN_VERSION"
}

install_python_runtime() {
  local os_family="$1"
  local package_version="$DBCONSOLE_PYTHON_MIN_VERSION"

  if skip_privileged_setup_enabled; then
    log_skipped_privileged_step "Python ${package_version} runtime installation"
    return 1
  fi

  case "$os_family" in
    ol8|ol9)
      if command -v dnf >/dev/null 2>&1; then
        run_as_root dnf install -y "python${package_version}" "python${package_version}-pip" "python${package_version}-devel" >&2 || return 1
      elif command -v yum >/dev/null 2>&1; then
        run_as_root yum install -y "python${package_version}" "python${package_version}-pip" "python${package_version}-devel" >&2 || return 1
      else
        echo "Neither dnf nor yum was found. Install python${package_version} manually and rerun setup." >&2
        return 1
      fi
      ;;
    ubuntu)
      if ! command -v apt-get >/dev/null 2>&1; then
        echo "apt-get was not found. Install python${package_version} manually and rerun setup." >&2
        return 1
      fi
      run_as_root apt-get update >&2
      run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "python${package_version}" "python${package_version}-venv" "python${package_version}-dev" >&2 || return 1
      ;;
    macos)
      if command -v brew >/dev/null 2>&1; then
        brew install "python@${package_version}" >&2 || return 1
      else
        echo "Homebrew was not found. Install Python ${package_version} manually or set DBCONSOLE_PYTHON_BIN." >&2
        return 1
      fi
      ;;
    *)
      echo "Install Python ${package_version} manually or set DBCONSOLE_PYTHON_BIN." >&2
      return 1
      ;;
  esac
}

resolve_python_command() {
  local os_family="$1"
  local candidate
  local package_minor="$DBCONSOLE_PYTHON_MIN_VERSION"
  local candidates=()

  if [[ -n "$DBCONSOLE_PYTHON_BIN" ]]; then
    candidates+=("$DBCONSOLE_PYTHON_BIN")
  fi
  candidates+=("python${package_minor}" "python3.13" "python3.12" "python3")

  for candidate in "${candidates[@]}"; do
    if python_meets_min_version "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done

  echo "Python ${DBCONSOLE_PYTHON_MIN_VERSION}+ was not found. Attempting platform installation." >&2
  install_python_runtime "$os_family" || {
    echo "Python ${DBCONSOLE_PYTHON_MIN_VERSION}+ is required. Install it manually or set DBCONSOLE_PYTHON_BIN=/path/to/python." >&2
    return 1
  }

  for candidate in "${candidates[@]}"; do
    if python_meets_min_version "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done

  echo "Python ${DBCONSOLE_PYTHON_MIN_VERSION}+ installation completed, but no suitable interpreter was found in PATH." >&2
  return 1
}

prepare_virtualenv() {
  local python_command="$1"
  local os_family="$2"
  local existing_version=""

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    existing_version="$(python_version_for_command "$VENV_DIR/bin/python" 2>/dev/null || true)"
    if [[ -z "$existing_version" ]] || ! version_major_minor_ge "$existing_version" "$DBCONSOLE_PYTHON_MIN_VERSION"; then
      echo "Existing virtual environment uses Python ${existing_version:-unknown}; rebuilding with Python ${DBCONSOLE_PYTHON_MIN_VERSION}+."
      rm -rf "$VENV_DIR"
    fi
  fi

  if ! "$python_command" -m venv "$VENV_DIR"; then
    echo "Virtual environment creation failed. Installing Python ${DBCONSOLE_PYTHON_MIN_VERSION} runtime support and retrying." >&2
    rm -rf "$VENV_DIR"
    install_python_runtime "$os_family" || return 1
    if [[ ! -x "$python_command" ]]; then
      python_command="$(resolve_python_command "$os_family")"
    fi
    "$python_command" -m venv "$VENV_DIR"
  fi

  if ! python_meets_min_version "$VENV_DIR/bin/python"; then
    existing_version="$(python_version_for_command "$VENV_DIR/bin/python" 2>/dev/null || true)"
    echo "Virtual environment at $VENV_DIR uses Python ${existing_version:-unknown}, but Python ${DBCONSOLE_PYTHON_MIN_VERSION}+ is required." >&2
    echo "Remove $VENV_DIR and rerun setup, or set DBCONSOLE_PYTHON_BIN to a Python ${DBCONSOLE_PYTHON_MIN_VERSION}+ interpreter." >&2
    return 1
  fi

  "$VENV_DIR/bin/python" -m pip install --upgrade pip wheel setuptools
  "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
}

load_existing_runtime_env() {
  if [[ ! -f "$RUNTIME_ENV_FILE" ]]; then
    return 0
  fi

  unset DEFAULT_HTTP_PORT DEFAULT_HTTPS_PORT HOST SSL_CERT_FILE SSL_KEY_FILE DBCONSOLE_MYSQLSH
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
  EXISTING_DEFAULT_HTTP_PORT="${DEFAULT_HTTP_PORT:-}"
  EXISTING_DEFAULT_HTTPS_PORT="${DEFAULT_HTTPS_PORT:-}"
  EXISTING_HOST="${HOST:-}"
  EXISTING_SSL_CERT_FILE="${SSL_CERT_FILE:-}"
  EXISTING_SSL_KEY_FILE="${SSL_KEY_FILE:-}"
}

version_ge() {
  local installed="$1"
  local required="$2"
  local IFS=.
  local installed_parts required_parts
  local index installed_part required_part
  read -r -a installed_parts <<<"$installed"
  read -r -a required_parts <<<"$required"

  for index in 0 1 2; do
    installed_part="${installed_parts[$index]:-0}"
    required_part="${required_parts[$index]:-0}"
    if ((10#$installed_part > 10#$required_part)); then
      return 0
    fi
    if ((10#$installed_part < 10#$required_part)); then
      return 1
    fi
  done

  return 0
}

resolve_value() {
  local provided="$1"
  local existing="$2"
  local fallback="$3"

  if [[ -n "$provided" ]]; then
    echo "$provided"
  elif [[ -n "$existing" ]]; then
    echo "$existing"
  else
    echo "$fallback"
  fi
}

display_prompt_value() {
  local value="$1"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '<empty>'
  fi
}

prompt_for_normalized_value() {
  local label="$1"
  local current_value="$2"
  local normalizer="$3"
  local help_text="$4"
  local entered_value
  local normalized_value

  while true; do
    printf '%s [%s]: ' "$label" "$(display_prompt_value "$current_value")" >&2
    if ! read -r entered_value; then
      echo >&2
      echo "$current_value"
      return 0
    fi
    if [[ -z "$entered_value" ]]; then
      echo "$current_value"
      return 0
    fi

    if normalized_value="$("$normalizer" "$entered_value" 2>/dev/null)"; then
      echo "$normalized_value"
      return 0
    fi

    echo "$help_text" >&2
  done
}

prompt_for_text_value() {
  local label="$1"
  local current_value="$2"
  local allow_empty="$3"
  local entered_value

  while true; do
    printf '%s [%s]: ' "$label" "$(display_prompt_value "$current_value")" >&2
    if ! read -r entered_value; then
      echo >&2
      echo "$current_value"
      return 0
    fi
    if [[ -z "$entered_value" ]]; then
      if [[ "$allow_empty" == "yes" || -n "$current_value" ]]; then
        echo "$current_value"
        return 0
      fi
      echo "$label cannot be empty." >&2
      continue
    fi

    echo "$entered_value"
    return 0
  done
}

prompt_for_secret_value() {
  local label="$1"
  local entered_value

  while true; do
    printf '%s: ' "$label" >&2
    if [[ -t 0 ]]; then
      stty -echo
      if ! read -r entered_value; then
        stty echo
        echo >&2
        return 1
      fi
      stty echo
      echo >&2
    else
      if ! read -r entered_value; then
        echo >&2
        return 1
      fi
    fi

    if [[ -n "$entered_value" ]]; then
      echo "$entered_value"
      return 0
    fi
    echo "$label cannot be empty." >&2
  done
}

prompt_for_port_value() {
  local label="$1"
  local current_value="$2"
  local entered_value
  local normalized_value

  while true; do
    printf '%s port [%s]: ' "$label" "$current_value" >&2
    if ! read -r entered_value; then
      echo >&2
      echo "$current_value"
      return 0
    fi
    if [[ -z "$entered_value" ]]; then
      echo "$current_value"
      return 0
    fi

    if normalized_value="$(normalize_port "$label" "$entered_value" 2>/dev/null)"; then
      echo "$normalized_value"
      return 0
    fi

    echo "Enter a numeric port between 1 and 65535, or press Enter to keep $current_value." >&2
  done
}

prompt_for_ports_if_needed() {
  local deploy_mode="$1"
  local http_port="$2"
  local https_port="$3"

  if ! is_interactive_terminal; then
    printf '%s\n%s\n' "$http_port" "$https_port"
    return 0
  fi

  case "$deploy_mode" in
    http)
      if [[ -z "$HTTP_PORT_INPUT" ]]; then
        echo "Press Enter to keep the current HTTP port." >&2
        http_port="$(prompt_for_port_value "HTTP" "$http_port")"
      fi
      ;;
    https)
      if [[ -z "$HTTPS_PORT_INPUT" ]]; then
        echo "Press Enter to keep the current HTTPS port." >&2
        https_port="$(prompt_for_port_value "HTTPS" "$https_port")"
      fi
      ;;
    both)
      if [[ -z "$HTTP_PORT_INPUT" || -z "$HTTPS_PORT_INPUT" ]]; then
        echo "Press Enter to keep the current port values." >&2
      fi
      if [[ -z "$HTTP_PORT_INPUT" ]]; then
        http_port="$(prompt_for_port_value "HTTP" "$http_port")"
      fi
      if [[ -z "$HTTPS_PORT_INPUT" ]]; then
        https_port="$(prompt_for_port_value "HTTPS" "$https_port")"
      fi
      ;;
    none)
      echo "Deploy mode is 'none'; keeping saved HTTP and HTTPS port defaults." >&2
      ;;
  esac

  printf '%s\n%s\n' "$http_port" "$https_port"
}

run_with_timeout() {
  local seconds="$1"
  shift
  local python_bin=""
  local candidate

  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      python_bin="$candidate"
      break
    fi
  done

  if [[ -n "$python_bin" ]]; then
    "$python_bin" - "$seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys
import time

seconds = float(sys.argv[1])
command = sys.argv[2:]

try:
    process = subprocess.Popen(command, start_new_session=True)
except FileNotFoundError:
    sys.exit(127)

try:
    sys.exit(process.wait(timeout=seconds))
except subprocess.TimeoutExpired:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = process.poll()
        if result is not None:
            sys.exit(124)
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    sys.exit(124)
PY
    return $?
  fi

  if command -v timeout >/dev/null 2>&1; then
    timeout -k 5s "$seconds" "$@"
  else
    "$@"
  fi
}

run_as_root_with_timeout() {
  local seconds="$1"
  shift
  if [[ "$(id -u)" -eq 0 ]]; then
    run_with_timeout "$seconds" "$@"
    return $?
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "This step requires root privileges. Re-run setup.sh from a shell with sudo access." >&2
    return 1
  fi

  if is_interactive_terminal; then
    run_with_timeout "$seconds" sudo "$@"
  else
    run_with_timeout "$seconds" sudo -n "$@"
  fi
}

open_firewall_port() {
  local protocol_label="$1"
  local port_value="$2"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "macOS does not expose Linux-style port opening here. Allow the Python process through the macOS firewall if prompted, or open ${port_value}/tcp for ${protocol_label} manually." >&2
    return 0
  fi

  if command -v firewall-cmd >/dev/null 2>&1; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "firewall update for port ${port_value}/tcp"
      return 0
    fi
    local firewalld_zone
    firewalld_zone="$(run_as_root_with_timeout 10 firewall-cmd --get-default-zone 2>/dev/null || true)"
    firewalld_zone="${firewalld_zone:-public}"

    if run_as_root_with_timeout 20 firewall-cmd --zone="$firewalld_zone" --add-port="${port_value}/tcp"; then
      echo "Opened runtime firewall port ${port_value}/tcp for ${protocol_label} with firewall-cmd zone ${firewalld_zone}."
      if run_as_root_with_timeout 20 firewall-cmd --permanent --zone="$firewalld_zone" --add-port="${port_value}/tcp"; then
        if run_as_root_with_timeout 20 firewall-cmd --reload; then
          echo "Persisted firewall port ${port_value}/tcp for ${protocol_label} with firewall-cmd."
        else
          echo "Warning: firewall-cmd reload timed out or failed after persisting ${port_value}/tcp for ${protocol_label}; runtime access is already open." >&2
        fi
      else
        echo "Warning: firewall-cmd could not persist ${port_value}/tcp for ${protocol_label} within 20 seconds; runtime access is already open." >&2
      fi
      return 0
    fi

    if run_as_root_with_timeout 20 firewall-cmd --permanent --zone="$firewalld_zone" --add-port="${port_value}/tcp"; then
      if run_as_root_with_timeout 20 firewall-cmd --reload; then
        echo "Opened firewall port ${port_value}/tcp for ${protocol_label} with firewall-cmd zone ${firewalld_zone}."
        return 0
      fi
    fi

    if command -v nft >/dev/null 2>&1 &&
      run_as_root nft list chain inet firewalld "filter_IN_${firewalld_zone}_allow" >/dev/null 2>&1 &&
      run_as_root nft insert rule inet firewalld "filter_IN_${firewalld_zone}_allow" tcp dport "$port_value" accept; then
      echo "Opened runtime firewall port ${port_value}/tcp for ${protocol_label} with nftables firewalld chain filter_IN_${firewalld_zone}_allow."
      return 0
    else
      echo "Warning: firewall-cmd could not add ${port_value}/tcp for ${protocol_label} within 20 seconds. Open it manually if external access is required." >&2
    fi
    echo "Trying another firewall tool for ${port_value}/tcp because firewall-cmd did not confirm the change." >&2
  fi

  if command -v ufw >/dev/null 2>&1; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "firewall update for port ${port_value}/tcp"
      return 0
    fi
    run_as_root ufw allow "${port_value}/tcp"
    echo "Opened firewall port ${port_value}/tcp for ${protocol_label} with ufw."
    return 0
  fi

  if command -v iptables >/dev/null 2>&1; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "firewall update for port ${port_value}/tcp"
      return 0
    fi
    if run_as_root /bin/sh -c '
      port_value="$1"
      protocol_label="$2"
      if iptables -C INPUT -p tcp -m state --state NEW -m tcp --dport "$port_value" -j ACCEPT >/dev/null 2>&1 ||
         iptables -C INPUT -p tcp -m tcp --dport "$port_value" -j ACCEPT >/dev/null 2>&1; then
        echo "Firewall port ${port_value}/tcp for ${protocol_label} is already open in iptables."
        exit 0
      fi

      insert_at="$(iptables -L INPUT --line-numbers -n 2>/dev/null | awk '\''$2 == "REJECT" || $2 == "DROP" { print $1; exit }'\'')"
      if [ -n "$insert_at" ]; then
        iptables -I INPUT "$insert_at" -p tcp -m state --state NEW -m tcp --dport "$port_value" -j ACCEPT
      else
        iptables -I INPUT 1 -p tcp -m state --state NEW -m tcp --dport "$port_value" -j ACCEPT
      fi

      if command -v iptables-save >/dev/null 2>&1; then
        mkdir -p /etc/iptables 2>/dev/null || true
        if [ -d /etc/iptables ]; then
          iptables-save > /etc/iptables/rules.v4 || echo "Warning: unable to persist iptables rules to /etc/iptables/rules.v4." >&2
        fi
      fi
      echo "Opened firewall port ${port_value}/tcp for ${protocol_label} with iptables."
    ' sh "$port_value" "$protocol_label"; then
      return 0
    fi
    echo "Warning: iptables could not open ${port_value}/tcp for ${protocol_label}. Open it manually if external access is required." >&2
    return 0
  fi

  echo "Firewall tool not found. Open ${port_value}/tcp for ${protocol_label} manually on this host." >&2
}

close_firewall_port() {
  local protocol_label="$1"
  local port_value="$2"

  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "macOS does not expose Linux-style port closing here. Remove ${port_value}/tcp for ${protocol_label} manually in the macOS firewall if needed." >&2
    return 0
  fi

  if command -v firewall-cmd >/dev/null 2>&1; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "firewall cleanup for port ${port_value}/tcp"
      return 0
    fi
    if ! run_as_root_with_timeout 20 firewall-cmd --permanent --query-port="${port_value}/tcp" >/dev/null 2>&1; then
      echo "Firewall port ${port_value}/tcp for ${protocol_label} was not open in firewall-cmd."
    elif run_as_root_with_timeout 20 firewall-cmd --permanent --remove-port="${port_value}/tcp"; then
      if run_as_root_with_timeout 20 firewall-cmd --reload; then
        echo "Removed firewall port ${port_value}/tcp for ${protocol_label} with firewall-cmd."
        return 0
      else
        echo "Warning: firewall-cmd reload timed out or failed after removing ${port_value}/tcp for ${protocol_label}; verify firewall state manually." >&2
      fi
    else
      echo "Warning: firewall-cmd could not remove ${port_value}/tcp for ${protocol_label} within 20 seconds. Close it manually if needed." >&2
    fi
    echo "Trying another firewall tool for ${port_value}/tcp cleanup because firewall-cmd did not confirm the change." >&2
  fi

  if command -v ufw >/dev/null 2>&1; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "firewall cleanup for port ${port_value}/tcp"
      return 0
    fi
    if run_as_root ufw status | grep -Fq "${port_value}/tcp"; then
      run_as_root ufw --force delete allow "${port_value}/tcp"
      echo "Removed firewall port ${port_value}/tcp for ${protocol_label} with ufw."
    else
      echo "Firewall port ${port_value}/tcp for ${protocol_label} was not open in ufw."
    fi
    return 0
  fi

  if command -v iptables >/dev/null 2>&1; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "firewall cleanup for port ${port_value}/tcp"
      return 0
    fi
    if run_as_root /bin/sh -c '
      port_value="$1"
      protocol_label="$2"
      removed=0
      while iptables -D INPUT -p tcp -m state --state NEW -m tcp --dport "$port_value" -j ACCEPT >/dev/null 2>&1; do
        removed=1
      done
      while iptables -D INPUT -p tcp -m tcp --dport "$port_value" -j ACCEPT >/dev/null 2>&1; do
        removed=1
      done

      if [ "$removed" -eq 1 ]; then
        if command -v iptables-save >/dev/null 2>&1 && [ -d /etc/iptables ]; then
          iptables-save > /etc/iptables/rules.v4 || echo "Warning: unable to persist iptables rules to /etc/iptables/rules.v4." >&2
        fi
        echo "Removed firewall port ${port_value}/tcp for ${protocol_label} with iptables."
      else
        echo "Firewall port ${port_value}/tcp for ${protocol_label} was not open in iptables."
      fi
    ' sh "$port_value" "$protocol_label"; then
      return 0
    fi
    echo "Warning: iptables could not remove ${port_value}/tcp for ${protocol_label}. Close it manually if needed." >&2
    return 0
  fi

  echo "Firewall tool not found. Close ${port_value}/tcp for ${protocol_label} manually on this host." >&2
}

port_list_contains() {
  local target_port="$1"
  shift || true
  local port_value

  for port_value in "$@"; do
    if [[ "$port_value" == "$target_port" ]]; then
      return 0
    fi
  done

  return 1
}

sync_firewall_ports() {
  local deploy_mode="$1"
  local http_port="$2"
  local https_port="$3"
  local existing_http_port="$4"
  local existing_https_port="$5"
  local desired_ports=()
  local candidate_ports=("$http_port" "$https_port" "$existing_http_port" "$existing_https_port")
  local handled_ports=()
  local port_value

  case "$deploy_mode" in
    http)
      desired_ports+=("$http_port")
      ;;
    https)
      desired_ports+=("$https_port")
      ;;
    both)
      desired_ports+=("$http_port" "$https_port")
      ;;
    none)
      ;;
  esac

  for port_value in "${candidate_ports[@]}"; do
    if [[ -z "$port_value" ]]; then
      continue
    fi

    if [[ "${#handled_ports[@]}" -gt 0 ]] && port_list_contains "$port_value" "${handled_ports[@]}"; then
      continue
    fi
    handled_ports+=("$port_value")

    if [[ "${#desired_ports[@]}" -gt 0 ]] && port_list_contains "$port_value" "${desired_ports[@]}"; then
      open_firewall_port "DBConsole" "$port_value"
    else
      close_firewall_port "DBConsole" "$port_value"
    fi
  done
}

write_runtime_env() {
  local http_port="$1"
  local https_port="$2"
  local host_value="$3"
  local ssl_cert_file="$4"
  local ssl_key_file="$5"
  local os_family="$6"
  local deploy_mode="$7"
  local embedded_mysqlsh="${DBCONSOLE_MYSQLSH:-}"
  local update_remote_url
  local update_branch
  local session_cookie_secure
  local python_bin_value="${DBCONSOLE_PYTHON_BIN:-}"

  update_remote_url="$(current_update_remote_url)"
  update_branch="$(current_update_branch)"
  case "$deploy_mode" in
    https|both) session_cookie_secure=1 ;;
    *) session_cookie_secure=0 ;;
  esac

  {
    echo "# Generated by setup.sh"
    echo "HOST=$host_value"
    echo "DEPLOY_MODE=$deploy_mode"
    echo "DEFAULT_HTTP_PORT=$http_port"
    echo "DEFAULT_HTTPS_PORT=$https_port"
    echo "DBCONSOLE_PYTHON_MIN_VERSION=$DBCONSOLE_PYTHON_MIN_VERSION"
    if [[ -n "$python_bin_value" ]]; then
      echo "DBCONSOLE_PYTHON_BIN=$python_bin_value"
    fi
    echo "DBCONSOLE_SESSION_COOKIE_SECURE=$session_cookie_secure"
    if [[ -n "$update_remote_url" ]]; then
      echo "DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL=$update_remote_url"
    fi
    if [[ -n "$update_branch" ]]; then
      echo "DBCONSOLE_UPDATE_ALLOWED_BRANCH=$update_branch"
    fi
    if local_mysql_bootstrap_requested; then
      echo "LOCAL_MYSQL_AUTOSTART=1"
      echo "LOCAL_MYSQL_SOCKET=$LOCAL_MYSQL_SOCKET_INPUT"
      echo "LOCAL_MYSQL_SERVICE=$(local_mysql_service_name "$os_family")"
      if [[ "$os_family" == "macos" ]]; then
        echo "LOCAL_MYSQL_BASEDIR=$EMBEDDED_MYSQL_SERVER_DIR"
        echo "LOCAL_MYSQL_DATADIR=$EMBEDDED_MYSQL_SERVER_DIR/data"
      else
        echo "LOCAL_MYSQL_BASEDIR=$LOCAL_MYSQL_BASEDIR_INPUT"
        echo "LOCAL_MYSQL_DATADIR=$(local_mysql_datadir "$os_family")"
        echo "LOCAL_MYSQL_CONFIG_FILE=$(local_mysql_config_file "$os_family")"
        echo "LOCAL_MYSQL_ERROR_LOG=$(local_mysql_error_log "$os_family")"
        echo "LOCAL_MYSQL_PID_FILE=$(local_mysql_pid_file "$os_family")"
      fi
    else
      echo "LOCAL_MYSQL_AUTOSTART=0"
      echo "# LOCAL_MYSQL_SOCKET="
      echo "# LOCAL_MYSQL_SERVICE="
      echo "# LOCAL_MYSQL_BASEDIR="
      echo "# LOCAL_MYSQL_DATADIR="
    fi
    if [[ -n "$ssl_cert_file" ]]; then
      echo "SSL_CERT_FILE=$ssl_cert_file"
    else
      echo "# SSL_CERT_FILE=/path/to/cert.pem"
    fi
    if [[ -n "$ssl_key_file" ]]; then
      echo "SSL_KEY_FILE=$ssl_key_file"
    else
      echo "# SSL_KEY_FILE=/path/to/key.pem"
    fi
    if [[ -n "$embedded_mysqlsh" ]]; then
      echo "DBCONSOLE_MYSQLSH=$embedded_mysqlsh"
    fi
  } >"$RUNTIME_ENV_FILE"
  chmod 600 "$RUNTIME_ENV_FILE"
}

fix_tls_permissions() {
  local ssl_cert_file="$1"
  local ssl_key_file="$2"
  local service_user="$3"
  local service_group="$4"

  chmod 644 "$ssl_cert_file"
  chmod 600 "$ssl_key_file"

  if [[ -n "$service_user" && -n "$service_group" ]]; then
    if skip_privileged_setup_enabled; then
      log_skipped_privileged_step "TLS file ownership update"
      return 0
    fi
    run_as_root chown "$service_user:$service_group" "$ssl_cert_file" "$ssl_key_file"
  fi
}

run_dependency_audit() {
  local audit_cache_dir="$SCRIPT_DIR/.cache/pip-audit"

  if ! dependency_audit_enabled; then
    echo "Dependency audit skipped because DBCONSOLE_DEPENDENCY_AUDIT=$DBCONSOLE_DEPENDENCY_AUDIT."
    return 0
  fi

  echo "Running dependency vulnerability audit with pip-audit."
  mkdir -p "$audit_cache_dir"
  if ! "$VENV_DIR/bin/python" -m pip install --upgrade pip-audit; then
    if dependency_audit_strict_enabled; then
      echo "Dependency audit setup failed and DBCONSOLE_DEPENDENCY_AUDIT_STRICT=1." >&2
      return 1
    fi
    echo "Dependency audit setup failed; continuing because audit is warn-only. Set DBCONSOLE_DEPENDENCY_AUDIT_STRICT=1 to fail setup." >&2
    return 0
  fi

  if "$VENV_DIR/bin/python" -m pip_audit -r "$SCRIPT_DIR/requirements.txt" --cache-dir "$audit_cache_dir"; then
    echo "Dependency vulnerability audit completed without reported vulnerabilities."
    return 0
  fi

  if dependency_audit_strict_enabled; then
    echo "Dependency vulnerability audit failed and strict mode is enabled." >&2
    return 1
  fi

  echo "Dependency vulnerability audit reported issues or could not complete; continuing because audit is warn-only." >&2
  return 0
}

harden_local_file_permissions() {
  local file_path
  local dir_path

  for file_path in \
    "$RUNTIME_ENV_FILE" \
    "$SCRIPT_DIR/.flask_secret_key" \
    "$SCRIPT_DIR/profiles.json" \
    "$SCRIPT_DIR/object_storage.json"; do
    if [[ -f "$file_path" ]]; then
      chmod 600 "$file_path" 2>/dev/null || true
    fi
  done

  if [[ -d "$SCRIPT_DIR/profile_ssh_keys" ]]; then
    chmod 700 "$SCRIPT_DIR/profile_ssh_keys" 2>/dev/null || true
    while IFS= read -r -d '' dir_path; do
      chmod 700 "$dir_path" 2>/dev/null || true
    done < <(find "$SCRIPT_DIR/profile_ssh_keys" -type d -print0 2>/dev/null)
    while IFS= read -r -d '' file_path; do
      chmod 600 "$file_path" 2>/dev/null || true
    done < <(find "$SCRIPT_DIR/profile_ssh_keys" -type f -print0 2>/dev/null)
  fi

  if [[ -d "$SCRIPT_DIR/.data" ]]; then
    chmod 700 "$SCRIPT_DIR/.data" 2>/dev/null || true
  fi
  if [[ -f "$SCRIPT_DIR/etc/my.cnf" ]]; then
    chmod 600 "$SCRIPT_DIR/etc/my.cnf" 2>/dev/null || true
  fi

  if [[ -d "$SCRIPT_DIR/tls" ]]; then
    chmod 700 "$SCRIPT_DIR/tls" 2>/dev/null || true
    while IFS= read -r -d '' file_path; do
      case "$file_path" in
        *.key|*.pem|*.p12|*.pfx|*.jks|*.keystore)
          chmod 600 "$file_path" 2>/dev/null || true
          ;;
        *)
          chmod 644 "$file_path" 2>/dev/null || true
          ;;
      esac
    done < <(find "$SCRIPT_DIR/tls" -type f -print0 2>/dev/null)
  fi
}

generate_self_signed_tls_assets() {
  local host_value="$1"
  local ssl_cert_file="$2"
  local ssl_key_file="$3"
  local service_user="$4"
  local service_group="$5"
  local common_name="localhost"
  local tls_dir

  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl is required to generate a default TLS certificate. Install openssl or provide SSL_CERT_FILE and SSL_KEY_FILE." >&2
    return 1
  fi

  if [[ -n "$host_value" && "$host_value" != "0.0.0.0" && "$host_value" != "::" ]]; then
    common_name="$host_value"
  fi

  tls_dir="$(dirname "$ssl_cert_file")"
  mkdir -p "$tls_dir"

  openssl req \
    -x509 \
    -nodes \
    -newkey rsa:2048 \
    -days 365 \
    -keyout "$ssl_key_file" \
    -out "$ssl_cert_file" \
    -subj "/CN=$common_name" >/dev/null 2>&1

  fix_tls_permissions "$ssl_cert_file" "$ssl_key_file" "$service_user" "$service_group"
  echo "Generated self-signed TLS certificate: $ssl_cert_file" >&2
}

ensure_https_tls_assets() {
  local deploy_mode="$1"
  local host_value="$2"
  local ssl_cert_file="$3"
  local ssl_key_file="$4"
  local service_user="$5"
  local service_group="$6"
  local default_tls_dir="$SCRIPT_DIR/tls"

  if [[ "$deploy_mode" != "https" && "$deploy_mode" != "both" ]]; then
    printf '%s\n%s\n' "$ssl_cert_file" "$ssl_key_file"
    return 0
  fi

  if [[ -n "$ssl_cert_file" || -n "$ssl_key_file" ]]; then
    printf '%s\n%s\n' "$ssl_cert_file" "$ssl_key_file"
    return 0
  fi

  ssl_cert_file="$default_tls_dir/dbconsole-selfsigned.crt"
  ssl_key_file="$default_tls_dir/dbconsole-selfsigned.key"

  if [[ ! -f "$ssl_cert_file" || ! -f "$ssl_key_file" ]]; then
    generate_self_signed_tls_assets "$host_value" "$ssl_cert_file" "$ssl_key_file" "$service_user" "$service_group" || return 1
  else
    fix_tls_permissions "$ssl_cert_file" "$ssl_key_file" "$service_user" "$service_group"
    echo "Reusing self-signed TLS certificate: $ssl_cert_file" >&2
  fi

  printf '%s\n%s\n' "$ssl_cert_file" "$ssl_key_file"
}

resolve_service_user() {
  if [[ -n "$SERVICE_USER_INPUT" ]]; then
    echo "$SERVICE_USER_INPUT"
  elif [[ -n "${SUDO_USER:-}" ]]; then
    echo "$SUDO_USER"
  else
    id -un
  fi
}

resolve_service_group() {
  local service_user="$1"

  if [[ -n "$SERVICE_GROUP_INPUT" ]]; then
    echo "$SERVICE_GROUP_INPUT"
  else
    id -gn "$service_user"
  fi
}

resolve_bash_bin() {
  local bash_bin

  bash_bin="$(command -v bash || true)"
  if [[ -z "$bash_bin" ]]; then
    echo "bash is required but was not found in PATH." >&2
    return 1
  fi

  printf '%s\n' "$bash_bin"
}

install_systemd_service() {
  local service_name="$1"
  local description="$2"
  local exec_script="$3"
  local service_user="$4"
  local service_group="$5"
  local needs_privileged_bind="$6"
  local unit_path="/etc/systemd/system/${service_name}.service"
  local bash_bin

  bash_bin="$(resolve_bash_bin)" || return 1

  {
    cat <<EOF
[Unit]
Description=$description
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$SCRIPT_DIR
EnvironmentFile=-$RUNTIME_ENV_FILE
ExecStart=$bash_bin $exec_script
Restart=on-failure
RestartSec=5
EOF

    if [[ "$needs_privileged_bind" == "yes" ]]; then
      cat <<EOF
AmbientCapabilities=CAP_NET_BIND_SERVICE
EOF
    fi

    cat <<EOF
[Install]
WantedBy=multi-user.target
EOF
  } | write_root_file "$unit_path"
}

enable_systemd_service() {
  local service_name="$1"

  run_as_root systemctl enable --now "${service_name}.service"
  echo "Enabled systemd service ${service_name}.service."
}

disable_systemd_service() {
  local service_name="$1"

  run_as_root systemctl disable --now "${service_name}.service" >/dev/null 2>&1 || true
}

https_service_ready() {
  local ssl_cert_file="$1"
  local ssl_key_file="$2"

  if [[ -z "$ssl_cert_file" || -z "$ssl_key_file" ]]; then
    echo "HTTPS service was installed but not started because SSL_CERT_FILE and SSL_KEY_FILE are not set in $RUNTIME_ENV_FILE." >&2
    return 1
  fi

  if [[ ! -f "$ssl_cert_file" || ! -f "$ssl_key_file" ]]; then
    echo "HTTPS service was installed but not started because the TLS certificate or key file does not exist." >&2
    return 1
  fi

  return 0
}

setup_systemd_services() {
  local os_family="$1"
  local deploy_mode="$2"
  local ssl_cert_file="$3"
  local ssl_key_file="$4"
  local http_port="$5"
  local https_port="$6"
  local service_user
  local service_group
  local http_needs_privileged_bind="no"
  local https_needs_privileged_bind="no"

  case "$os_family" in
    ol8|ol9|ubuntu) ;;
    *)
      return 0
      ;;
  esac

  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl was not found. Create the service manually if you need background startup on this host." >&2
    return 0
  fi

  if skip_privileged_setup_enabled; then
    log_skipped_privileged_step "systemd unit installation and enablement"
    return 0
  fi

  service_user="$(resolve_service_user)"
  service_group="$(resolve_service_group "$service_user")"

  if port_requires_privileged_bind "$http_port"; then
    http_needs_privileged_bind="yes"
  fi

  if port_requires_privileged_bind "$https_port"; then
    https_needs_privileged_bind="yes"
  fi

  install_systemd_service "dbconsole-http" "DBConsole HTTP service" "$SCRIPT_DIR/start_http.sh" "$service_user" "$service_group" "$http_needs_privileged_bind"
  install_systemd_service "dbconsole-https" "DBConsole HTTPS service" "$SCRIPT_DIR/start_https.sh" "$service_user" "$service_group" "$https_needs_privileged_bind"
  run_as_root systemctl daemon-reload
  echo "Installed systemd unit files for dbconsole."

  case "$deploy_mode" in
    http)
      enable_systemd_service "dbconsole-http"
      disable_systemd_service "dbconsole-https"
      ;;
    https)
      disable_systemd_service "dbconsole-http"
      if https_service_ready "$ssl_cert_file" "$ssl_key_file"; then
        enable_systemd_service "dbconsole-https"
      else
        disable_systemd_service "dbconsole-https"
      fi
      ;;
    both)
      enable_systemd_service "dbconsole-http"
      if https_service_ready "$ssl_cert_file" "$ssl_key_file"; then
        enable_systemd_service "dbconsole-https"
      else
        disable_systemd_service "dbconsole-https"
      fi
      ;;
    none)
      disable_systemd_service "dbconsole-http"
      disable_systemd_service "dbconsole-https"
      echo "Installed systemd units but left them disabled because deploy mode is 'none'."
      ;;
  esac
}

print_privileged_port_guidance() {
  local os_family="$1"
  local deploy_mode="$2"
  local http_port="$3"
  local https_port="$4"
  local http_needs_privileged_bind="no"
  local https_needs_privileged_bind="no"

  case "$deploy_mode" in
    http|both)
      if port_requires_privileged_bind "$http_port"; then
        http_needs_privileged_bind="yes"
      fi
      ;;
  esac

  case "$deploy_mode" in
    https|both)
      if port_requires_privileged_bind "$https_port"; then
        https_needs_privileged_bind="yes"
      fi
      ;;
  esac

  if [[ "$http_needs_privileged_bind" != "yes" && "$https_needs_privileged_bind" != "yes" ]]; then
    return 0
  fi

  case "$os_family" in
    ol8|ol9|ubuntu)
      if command -v systemctl >/dev/null 2>&1; then
        echo "Privileged port note: generated systemd services include CAP_NET_BIND_SERVICE for ports below 1024."
        echo "Directly running start scripts outside systemd on those ports can still require sudo."
      else
        echo "Privileged port note: ports below 1024 require elevated privileges when not started through systemd."
      fi
      ;;
    macos)
      echo "Privileged port note: macOS requires sudo or a non-privileged port above 1023 for ports below 1024."
      ;;
  esac
}

resolve_mysql_shell_min_version() {
  local page versions resolved

  if [[ -n "${MYSQL_SHELL_MIN_VERSION:-}" ]]; then
    printf '%s\n' "$MYSQL_SHELL_MIN_VERSION"
    return 0
  fi

  page="$(curl -fsSL "$MYSQL_SHELL_DOWNLOAD_PAGE" 2>/dev/null || true)"
  versions="$(
    printf '%s\n' "$page" |
      grep -Eo 'mysql-shell[-_][0-9]+[.][0-9]+[.][0-9]+|MySQL Shell[[:space:]]+[0-9]+[.][0-9]+[.][0-9]+' |
      grep -Eo '[0-9]+[.][0-9]+[.][0-9]+' || true
  )"
  resolved="$(
    printf '%s\n' "$versions" |
      awk -F. 'NF == 3 {key=sprintf("%06d.%06d.%06d", $1, $2, $3); if (key > best_key) {best_key=key; best=$0}} END {print best}'
  )"

  if [[ -z "$resolved" ]]; then
    echo "Unable to discover the latest MySQL Shell version from $MYSQL_SHELL_DOWNLOAD_PAGE." >&2
    echo "Set MYSQL_SHELL_MIN_VERSION explicitly and rerun setup." >&2
    return 1
  fi

  printf '%s\n' "$resolved"
}

mysqlsh_path_for_command() {
  local command_name="$1"

  if [[ "$command_name" == */* ]]; then
    if [[ -x "$command_name" ]]; then
      printf '%s\n' "$command_name"
      return 0
    fi
    return 1
  fi

  command -v "$command_name"
}

mysqlsh_version_for_command() {
  local command_name="$1"
  local command_path
  local tmp_home
  local version_output

  command_path="$(mysqlsh_path_for_command "$command_name")" || return 0
  tmp_home="$(mktemp -d)"
  version_output="$(HOME="$tmp_home" "$command_path" --version 2>/dev/null || true)"
  rm -rf "$tmp_home"
  printf '%s\n' "$version_output" | grep -Eo '[0-9]+([.][0-9]+){2}' | head -n 1 || true
}

embedded_mysqlsh_path() {
  local candidate
  for candidate in \
    "$EMBEDDED_MYSQL_SHELL_DIR/bin/mysqlsh" \
    "$EMBEDDED_MYSQL_SHELL_DIR/usr/bin/mysqlsh"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

mysql_shell_vendor_arch() {
  local os_family="$1"

  case "$os_family" in
    ubuntu)
      case "$(dpkg --print-architecture 2>/dev/null || uname -m)" in
        amd64|x86_64) printf '%s\n' "amd64" ;;
        arm64|aarch64) printf '%s\n' "arm64" ;;
        *) return 1 ;;
      esac
      ;;
    ol8|ol9)
      case "$(uname -m)" in
        x86_64|amd64) printf '%s\n' "x86_64" ;;
        aarch64|arm64) printf '%s\n' "aarch64" ;;
        *) return 1 ;;
      esac
      ;;
    macos)
      case "$(uname -m)" in
        x86_64|amd64) printf '%s\n' "x86-64bit" ;;
        arm64|aarch64) printf '%s\n' "arm64" ;;
        *) return 1 ;;
      esac
      ;;
    *)
      return 1
      ;;
  esac
}

mysql_shell_embedded_package_url() {
  local os_family="$1"
  local required_version="$2"
  local arch
  local version_id

  if [[ -n "$MYSQL_SHELL_EMBEDDED_URL" ]]; then
    printf '%s\n' "$MYSQL_SHELL_EMBEDDED_URL"
    return 0
  fi

  arch="$(mysql_shell_vendor_arch "$os_family")" || return 1
  case "$os_family" in
    ol8)
      printf '%s\n' "${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE%/}/mysql-shell-${required_version}-1.el8.${arch}.rpm"
      ;;
    ol9)
      printf '%s\n' "${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE%/}/mysql-shell-${required_version}-1.el9.${arch}.rpm"
      ;;
    ubuntu)
      version_id=""
      if [[ -r /etc/os-release ]]; then
        version_id="$(. /etc/os-release && printf '%s\n' "${VERSION_ID:-}")"
      fi
      if [[ -z "$version_id" ]]; then
        echo "Unable to determine Ubuntu VERSION_ID for embedded mysqlsh package URL." >&2
        return 1
      fi
      printf '%s\n' "${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE%/}/mysql-shell_${required_version}-1ubuntu${version_id}_${arch}.deb"
      ;;
    macos)
      printf '%s\n' "${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE%/}/mysql-shell-${required_version}-${MYSQL_SHELL_MACOS_PACKAGE_TAG}-${arch}.pkg"
      ;;
    *)
      return 1
      ;;
  esac
}

find_macos_mysql_shell_pkg() {
  local candidate

  for candidate in \
    /opt/homebrew/Caskroom/mysql-shell/*/mysql-shell-*.pkg \
    /usr/local/Caskroom/mysql-shell/*/mysql-shell-*.pkg; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

download_mysql_shell_embedded_package() {
  local os_family="$1"
  local required_version="$2"
  local package_file="$3"
  local package_url

  package_url="$(mysql_shell_embedded_package_url "$os_family" "$required_version")" || return 1
  echo "Downloading embedded MySQL Shell package: $package_url" >&2
  curl -fsSL "$package_url" -o "$package_file"
}

extract_embedded_mysql_shell_package() {
  local os_family="$1"
  local package_file="$2"
  local staging_dir
  local extracted_root
  local mysqlsh_candidate

  staging_dir="$(mktemp -d)"
  extracted_root="$staging_dir/root"
  mkdir -p "$extracted_root"

  case "$package_file" in
    *.deb)
      if ! command -v dpkg-deb >/dev/null 2>&1; then
        echo "dpkg-deb is required to extract embedded MySQL Shell from a .deb package." >&2
        rm -rf "$staging_dir"
        return 1
      fi
      dpkg-deb -x "$package_file" "$extracted_root"
      ;;
    *.rpm)
      if ! command -v rpm2cpio >/dev/null 2>&1 || ! command -v cpio >/dev/null 2>&1; then
        echo "rpm2cpio and cpio are required to extract embedded MySQL Shell from an RPM package." >&2
        rm -rf "$staging_dir"
        return 1
      fi
      (
        cd "$extracted_root"
        rpm2cpio "$package_file" | cpio -idm --quiet
      )
      ;;
    *.pkg)
      if ! command -v pkgutil >/dev/null 2>&1; then
        echo "pkgutil is required to extract embedded MySQL Shell from a macOS pkg package." >&2
        rm -rf "$staging_dir"
        return 1
      fi
      pkgutil --expand-full "$package_file" "$staging_dir/pkg"
      mysqlsh_candidate="$(find "$staging_dir/pkg" -type f -path '*/bin/mysqlsh' | head -n 1 || true)"
      if [[ -z "$mysqlsh_candidate" ]]; then
        echo "Unable to locate mysqlsh inside $package_file." >&2
        rm -rf "$staging_dir"
        return 1
      fi
      extracted_root="$(dirname "$(dirname "$mysqlsh_candidate")")"
      ;;
    *)
      echo "Unsupported embedded MySQL Shell package type: $package_file" >&2
      rm -rf "$staging_dir"
      return 1
      ;;
  esac

  mkdir -p "$(dirname "$EMBEDDED_MYSQL_SHELL_DIR")"
  rm -rf "$EMBEDDED_MYSQL_SHELL_DIR"
  cp -R "$extracted_root" "$EMBEDDED_MYSQL_SHELL_DIR"
  chmod +x "$EMBEDDED_MYSQL_SHELL_DIR/bin/mysqlsh" 2>/dev/null || true
  chmod +x "$EMBEDDED_MYSQL_SHELL_DIR/usr/bin/mysqlsh" 2>/dev/null || true
  rm -rf "$staging_dir"
}

install_embedded_mysql_shell() {
  local os_family="$1"
  local required_version="$2"
  local package_file="$MYSQL_SHELL_EMBEDDED_PACKAGE"
  local temp_package=""

  if [[ -z "$package_file" && "$os_family" == "macos" ]]; then
    package_file="$(find_macos_mysql_shell_pkg || true)"
  fi

  if [[ -z "$package_file" ]]; then
    case "$os_family" in
      ol8|ol9) temp_package="$(mktemp "/tmp/mysql-shell-${required_version}-XXXXXX.rpm")" ;;
      ubuntu) temp_package="$(mktemp "/tmp/mysql-shell-${required_version}-XXXXXX.deb")" ;;
      macos) temp_package="$(mktemp "/tmp/mysql-shell-${required_version}-XXXXXX.pkg")" ;;
      *)
        echo "Embedded MySQL Shell fallback is not supported for OS family '$os_family'." >&2
        return 1
        ;;
    esac
    download_mysql_shell_embedded_package "$os_family" "$required_version" "$temp_package"
    package_file="$temp_package"
  fi

  echo "Installing embedded MySQL Shell under $EMBEDDED_MYSQL_SHELL_DIR." >&2
  extract_embedded_mysql_shell_package "$os_family" "$package_file"
  if [[ -n "$temp_package" ]]; then
    rm -f "$temp_package"
  fi
}

ensure_mysqlsh_target_version() {
  local os_family="$1"
  local required_version
  local mysqlsh_command
  local mysqlsh_version
  local embedded_command

  required_version="$(resolve_mysql_shell_min_version)" || return 1
  echo "Verifying MySQL Shell target version $required_version."

  for mysqlsh_command in "${DBCONSOLE_MYSQLSH:-}" "${MYSQLSH:-}" "mysqlsh"; do
    if [[ -z "$mysqlsh_command" ]]; then
      continue
    fi
    mysqlsh_version="$(mysqlsh_version_for_command "$mysqlsh_command")"
    if [[ -n "$mysqlsh_version" ]] && version_ge "$mysqlsh_version" "$required_version"; then
      DBCONSOLE_MYSQLSH="$(mysqlsh_path_for_command "$mysqlsh_command")"
      export DBCONSOLE_MYSQLSH
      echo "Using mysqlsh $mysqlsh_version at $DBCONSOLE_MYSQLSH."
      return 0
    fi
  done

  embedded_command="$(embedded_mysqlsh_path || true)"
  if [[ -n "$embedded_command" ]]; then
    mysqlsh_version="$(mysqlsh_version_for_command "$embedded_command")"
    if [[ -n "$mysqlsh_version" ]] && version_ge "$mysqlsh_version" "$required_version"; then
      DBCONSOLE_MYSQLSH="$embedded_command"
      export DBCONSOLE_MYSQLSH
      echo "Using embedded mysqlsh $mysqlsh_version at $DBCONSOLE_MYSQLSH."
      return 0
    fi
  fi

  echo "No mysqlsh $required_version or newer was found. Installing embedded MySQL Shell with the application." >&2
  install_embedded_mysql_shell "$os_family" "$required_version"
  embedded_command="$(embedded_mysqlsh_path)" || {
    echo "Embedded MySQL Shell installation did not produce a mysqlsh executable." >&2
    return 1
  }
  mysqlsh_version="$(mysqlsh_version_for_command "$embedded_command")"
  if [[ -z "$mysqlsh_version" ]] || ! version_ge "$mysqlsh_version" "$required_version"; then
    echo "Embedded mysqlsh ${mysqlsh_version:-unknown} does not meet required version $required_version." >&2
    return 1
  fi

  DBCONSOLE_MYSQLSH="$embedded_command"
  export DBCONSOLE_MYSQLSH
  echo "Using embedded mysqlsh $mysqlsh_version at $DBCONSOLE_MYSQLSH."
}

run_mysqlsh_installer() {
  local os_family="$1"
  local platform_dir
  local installer

  if skip_privileged_setup_enabled; then
    case "$os_family" in
      ol8|ol9|ubuntu)
        log_skipped_privileged_step "MySQL Shell package installation"
        return 0
        ;;
    esac
  fi

  platform_dir="$(resolve_platform_dir "$os_family")" || return 1
  installer="$platform_dir/install_mysql_shell_innovation.sh"
  if [[ ! -x "$installer" ]]; then
    echo "Installer script not found or not executable: $installer" >&2
    return 1
  fi
  if ! "$installer"; then
    echo "Platform MySQL Shell installer did not provide a usable target version. Trying embedded fallback." >&2
  fi
  ensure_mysqlsh_target_version "$os_family"
}

local_mysql_bootstrap_requested() {
  [[ -n "$LOCAL_MYSQL_ADMIN_USER_INPUT" || -n "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT" ]]
}

local_admin_profile_needs_patch() {
  PROFILE_STORE="$SCRIPT_DIR/profiles.json" \
  LOCAL_MYSQL_PROFILE_NAME="$LOCAL_MYSQL_PROFILE_NAME_INPUT" \
  LOCAL_MYSQL_SOCKET="$LOCAL_MYSQL_SOCKET_INPUT" \
  python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

profile_store = Path(os.environ["PROFILE_STORE"])
profile_name = os.environ["LOCAL_MYSQL_PROFILE_NAME"].strip().lower()
socket_path = os.environ["LOCAL_MYSQL_SOCKET"].strip()

try:
    payload = json.loads(profile_store.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    sys.exit(0)

profiles = payload.get("profiles", [])
if not isinstance(profiles, list):
    sys.exit(0)

for row in profiles:
    if not isinstance(row, dict):
        continue
    if str(row.get("name", "")).strip().lower() != profile_name:
        continue
    if not row.get("socket_enabled"):
        sys.exit(0)
    if str(row.get("socket_path", "")).strip() != socket_path:
        sys.exit(0)
    if str(row.get("host", "")).strip():
        sys.exit(0)
    sys.exit(1)

sys.exit(0)
PY
}

prompt_for_default_local_mysql_bootstrap() {
  local original_arg_count="$1"
  local default_admin_user="${LOCAL_MYSQL_ADMIN_USER_INPUT:-localadmin}"
  local password_confirm
  local profile_needs_patch="no"

  if local_admin_profile_needs_patch; then
    profile_needs_patch="yes"
  fi
  if ! is_interactive_terminal; then
    if [[ "$profile_needs_patch" == "yes" && ! local_mysql_bootstrap_requested ]]; then
      echo "local-admin-profile is missing or not socket-only. Set LOCAL_MYSQL_ADMIN_USER and LOCAL_MYSQL_ADMIN_PASSWORD to let setup patch it non-interactively." >&2
      echo "If this is running from an older DBConsole Auto-Update page, complete this update first, wait for DBConsole to restart, then open the refreshed Auto-Update page and enter the local admin username and temporary password there." >&2
    fi
    return 0
  fi
  if [[ "$original_arg_count" -ne 0 && "$profile_needs_patch" != "yes" ]]; then
    return 0
  fi
  if local_mysql_bootstrap_requested; then
    if [[ -z "$LOCAL_MYSQL_ADMIN_USER_INPUT" ]]; then
      LOCAL_MYSQL_ADMIN_USER_INPUT="$(prompt_for_text_value "Local MySQL admin username" "$default_admin_user" "no")"
    fi
    if [[ -z "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT" ]]; then
      LOCAL_MYSQL_ADMIN_PASSWORD_INPUT="$(prompt_for_secret_value "Local MySQL admin password")"
    fi
    return 0
  fi

  if [[ "$profile_needs_patch" == "yes" ]]; then
    echo "local-admin-profile is missing or not socket-only. Setup will patch it after provisioning local MySQL." >&2
  else
    echo "Local MySQL admin bootstrap will install/provision a socket-only local MySQL profile." >&2
  fi
  LOCAL_MYSQL_ADMIN_USER_INPUT="$(prompt_for_text_value "Local MySQL admin username" "$default_admin_user" "no")"
  while true; do
    LOCAL_MYSQL_ADMIN_PASSWORD_INPUT="$(prompt_for_secret_value "Local MySQL admin password")"
    password_confirm="$(prompt_for_secret_value "Confirm local MySQL admin password")"
    if [[ "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT" == "$password_confirm" ]]; then
      break
    fi
    echo "Local MySQL admin password confirmation does not match." >&2
  done
}

validate_local_mysql_bootstrap_inputs() {
  if ! local_mysql_bootstrap_requested; then
    return 0
  fi
  if [[ -z "$LOCAL_MYSQL_ADMIN_USER_INPUT" || -z "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT" ]]; then
    echo "LOCAL_MYSQL_ADMIN_USER and LOCAL_MYSQL_ADMIN_PASSWORD must both be provided to bootstrap the local MySQL admin account." >&2
    return 1
  fi
  if [[ ! "$LOCAL_MYSQL_ADMIN_USER_INPUT" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$ ]]; then
    echo "Local MySQL admin username must start with a letter, digit, or underscore and contain only letters, digits, underscore, dot, or dash." >&2
    return 1
  fi
  if [[ ! "$LOCAL_MYSQL_PROFILE_NAME_INPUT" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "Local MySQL profile name may contain only letters, digits, underscore, dot, or dash." >&2
    return 1
  fi
  LOCAL_MYSQL_PORT_INPUT="$(normalize_port "Local MySQL" "$LOCAL_MYSQL_PORT_INPUT")"
}

default_local_mysql_socket() {
  local os_family="$1"
  case "$os_family" in
    ol8|ol9|ubuntu)
      printf '%s\n' "$SCRIPT_DIR/.data/run/mysql.sock"
      ;;
    macos)
      printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/run/mysql.sock"
      ;;
    *)
      printf '%s\n' "/tmp/mysql.sock"
      ;;
  esac
}

local_mysql_config_file() {
  local os_family="$1"
  case "$os_family" in
    ol8|ol9|ubuntu)
      printf '%s\n' "${LOCAL_MYSQL_CONFIG_FILE_INPUT:-$SCRIPT_DIR/etc/my.cnf}"
      ;;
    *)
      printf '%s\n' ""
      ;;
  esac
}

local_mysql_datadir() {
  local os_family="$1"
  case "$os_family" in
    ol8|ol9|ubuntu)
      printf '%s\n' "${LOCAL_MYSQL_DATADIR_INPUT:-$SCRIPT_DIR/.data/mysql}"
      ;;
    macos)
      printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/data"
      ;;
    *)
      printf '%s\n' "${LOCAL_MYSQL_DATADIR_INPUT:-$SCRIPT_DIR/.data/mysql}"
      ;;
  esac
}

local_mysql_error_log() {
  local os_family="$1"
  case "$os_family" in
    ol8|ol9|ubuntu)
      printf '%s\n' "${LOCAL_MYSQL_ERROR_LOG_INPUT:-$SCRIPT_DIR/.data/log/mysqld.err}"
      ;;
    macos)
      printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/log/mysqld.err"
      ;;
    *)
      printf '%s\n' "${LOCAL_MYSQL_ERROR_LOG_INPUT:-$SCRIPT_DIR/.data/log/mysqld.err}"
      ;;
  esac
}

local_mysql_pid_file() {
  local os_family="$1"
  case "$os_family" in
    ol8|ol9|ubuntu)
      printf '%s\n' "${LOCAL_MYSQL_PID_FILE_INPUT:-$SCRIPT_DIR/.data/run/mysqld.pid}"
      ;;
    macos)
      printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/run/mysqld.pid"
      ;;
    *)
      printf '%s\n' "${LOCAL_MYSQL_PID_FILE_INPUT:-$SCRIPT_DIR/.data/run/mysqld.pid}"
      ;;
  esac
}

find_mysqld_server() {
  if [[ -n "${MYSQLD_BIN:-}" && -x "$MYSQLD_BIN" ]]; then
    printf '%s\n' "$MYSQLD_BIN"
    return 0
  fi
  if [[ -x "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld" ]]; then
    printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld"
    return 0
  fi
  if command -v mysqld >/dev/null 2>&1; then
    command -v mysqld
    return 0
  fi
  if [[ -x /usr/sbin/mysqld ]]; then
    printf '%s\n' "/usr/sbin/mysqld"
    return 0
  fi
  if [[ -x /usr/bin/mysqld ]]; then
    printf '%s\n' "/usr/bin/mysqld"
    return 0
  fi
  return 1
}

find_mysqld_safe() {
  if [[ -n "${MYSQLD_SAFE_BIN:-}" && -x "$MYSQLD_SAFE_BIN" ]]; then
    printf '%s\n' "$MYSQLD_SAFE_BIN"
    return 0
  fi
  if [[ -x "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld_safe" ]]; then
    printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld_safe"
    return 0
  fi
  if command -v mysqld_safe >/dev/null 2>&1; then
    command -v mysqld_safe
    return 0
  fi
  if [[ -x /usr/bin/mysqld_safe ]]; then
    printf '%s\n' "/usr/bin/mysqld_safe"
    return 0
  fi
  return 1
}

mysql_basedir_from_mysqld() {
  local mysqld_bin="$1"
  if [[ -n "$LOCAL_MYSQL_BASEDIR_INPUT" ]]; then
    printf '%s\n' "$LOCAL_MYSQL_BASEDIR_INPUT"
    return 0
  fi
  case "$mysqld_bin" in
    /usr/sbin/mysqld|/usr/bin/mysqld)
      printf '%s\n' "/usr"
      ;;
    *)
      dirname "$(dirname "$mysqld_bin")"
      ;;
  esac
}

local_mysql_service_name() {
  local os_family="$1"
  case "$os_family" in
    ol8|ol9) printf '%s\n' "mysqld" ;;
    ubuntu) printf '%s\n' "mysql" ;;
    macos) printf '%s\n' "mysql" ;;
    *) printf '%s\n' "mysql" ;;
  esac
}

restart_local_mysql_service() {
  local os_family="$1"
  local service_name
  service_name="$(local_mysql_service_name "$os_family")"
  case "$os_family" in
    macos)
      stop_macos_embedded_mysql_server || true
      start_macos_embedded_mysql_server
      ;;
    ol8|ol9|ubuntu)
      stop_app_managed_mysql_server "$os_family" || true
      start_app_managed_mysql_server "$os_family"
      ;;
    *)
      if command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl restart "$service_name"
      else
        run_as_root service "$service_name" restart
      fi
      ;;
  esac
}

mysql_server_macos_arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf '%s\n' "x86_64" ;;
    arm64|aarch64) printf '%s\n' "arm64" ;;
    *) return 1 ;;
  esac
}

resolve_mysql_server_version() {
  local page versions resolved

  if [[ -n "$MYSQL_SERVER_VERSION" ]]; then
    printf '%s\n' "$MYSQL_SERVER_VERSION"
    return 0
  fi

  page="$(curl -fsSL "$MYSQL_SERVER_DOWNLOAD_PAGE" 2>/dev/null || true)"
  versions="$(
    printf '%s\n' "$page" |
      grep -Eo 'mysql-[0-9]+[.][0-9]+[.][0-9]+-[^[:space:]"]*macos[^[:space:]"]*[.]tar[.]gz|MySQL Community Server[[:space:]]+[0-9]+[.][0-9]+[.][0-9]+' |
      grep -Eo '[0-9]+[.][0-9]+[.][0-9]+' || true
  )"
  resolved="$(
    printf '%s\n' "$versions" |
      awk -F. 'NF == 3 {key=sprintf("%06d.%06d.%06d", $1, $2, $3); if (key > best_key) {best_key=key; best=$0}} END {print best}'
  )"

  if [[ -z "$resolved" ]]; then
    resolve_mysql_shell_min_version
    return $?
  fi

  printf '%s\n' "$resolved"
}

mysql_server_macos_tar_url() {
  local version="$1"
  local arch
  local series

  if [[ -n "$MYSQL_SERVER_EMBEDDED_URL" ]]; then
    printf '%s\n' "$MYSQL_SERVER_EMBEDDED_URL"
    return 0
  fi

  arch="$(mysql_server_macos_arch)" || {
    echo "Unsupported macOS architecture for MySQL Server tar fallback: $(uname -m)" >&2
    return 1
  }
  series="$(printf '%s\n' "$version" | awk -F. '{print $1 "." $2}')"
  printf '%s\n' "${MYSQL_SERVER_VENDOR_DOWNLOAD_BASE%/}/MySQL-${series}/mysql-${version}-${MYSQL_SERVER_MACOS_PACKAGE_TAG}-${arch}.tar.gz"
}

download_macos_mysql_server_tar() {
  local package_file="$1"
  local version
  local package_url

  if [[ -n "$MYSQL_SERVER_EMBEDDED_PACKAGE" ]]; then
    cp "$MYSQL_SERVER_EMBEDDED_PACKAGE" "$package_file"
    return 0
  fi

  version="$(resolve_mysql_server_version)" || return 1
  package_url="$(mysql_server_macos_tar_url "$version")" || return 1
  echo "Downloading MySQL Server tar package: $package_url" >&2
  curl -fsSL "$package_url" -o "$package_file"
}

install_macos_embedded_mysql_server() {
  local package_file
  local staging_dir
  local mysql_root

  if [[ -x "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld" ]]; then
    return 0
  fi

  if ! command -v tar >/dev/null 2>&1; then
    echo "tar is required to install local MySQL Server from the public macOS archive." >&2
    return 1
  fi

  package_file="$(mktemp "/tmp/mysql-server-macos-XXXXXX.tar.gz")"
  staging_dir="$(mktemp -d)"
  download_macos_mysql_server_tar "$package_file"
  tar -xzf "$package_file" -C "$staging_dir"
  mysql_root="$(find "$staging_dir" -maxdepth 2 -type f -path '*/bin/mysqld' -print -quit)"
  if [[ -z "$mysql_root" ]]; then
    rm -rf "$package_file" "$staging_dir"
    echo "Unable to locate mysqld inside the downloaded MySQL Server tar package." >&2
    return 1
  fi
  mysql_root="$(dirname "$(dirname "$mysql_root")")"

  mkdir -p "$(dirname "$EMBEDDED_MYSQL_SERVER_DIR")"
  rm -rf "$EMBEDDED_MYSQL_SERVER_DIR"
  cp -R "$mysql_root" "$EMBEDDED_MYSQL_SERVER_DIR"
  rm -rf "$package_file" "$staging_dir"
}

initialize_macos_embedded_mysql_datadir() {
  local datadir="$EMBEDDED_MYSQL_SERVER_DIR/data"

  if [[ -d "$datadir/mysql" ]]; then
    return 0
  fi

  mkdir -p "$datadir" "$EMBEDDED_MYSQL_SERVER_DIR/run" "$EMBEDDED_MYSQL_SERVER_DIR/log" "$EMBEDDED_MYSQL_SERVER_DIR/tmp"
  "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld" \
    --initialize-insecure \
    --basedir="$EMBEDDED_MYSQL_SERVER_DIR" \
    --datadir="$datadir"
}

start_macos_embedded_mysql_server() {
  local datadir="$EMBEDDED_MYSQL_SERVER_DIR/data"
  local pid_file="$EMBEDDED_MYSQL_SERVER_DIR/run/mysqld.pid"
  local log_file="$EMBEDDED_MYSQL_SERVER_DIR/log/mysqld.err"

  install_macos_embedded_mysql_server
  initialize_macos_embedded_mysql_datadir
  mkdir -p "$(dirname "$LOCAL_MYSQL_SOCKET_INPUT")" "$EMBEDDED_MYSQL_SERVER_DIR/run" "$EMBEDDED_MYSQL_SERVER_DIR/log" "$EMBEDDED_MYSQL_SERVER_DIR/tmp"
  if [[ -S "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
    return 0
  fi
  "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld_safe" \
    --basedir="$EMBEDDED_MYSQL_SERVER_DIR" \
    --datadir="$datadir" \
    --socket="$LOCAL_MYSQL_SOCKET_INPUT" \
    --pid-file="$pid_file" \
    --log-error="$log_file" \
    --tmpdir="$EMBEDDED_MYSQL_SERVER_DIR/tmp" \
    --skip-networking \
    --mysqlx=0 >/dev/null 2>&1 &

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [[ -S "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
      return 0
    fi
    sleep 1
  done

  echo "Embedded MySQL Server did not create socket $LOCAL_MYSQL_SOCKET_INPUT. Check $log_file." >&2
  return 1
}

stop_macos_embedded_mysql_server() {
  if [[ ! -x "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqladmin" || ! -S "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
    return 0
  fi
  "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqladmin" --protocol=socket --socket="$LOCAL_MYSQL_SOCKET_INPUT" -uroot shutdown >/dev/null 2>&1 || true
}

extract_temporary_mysql_password() {
  local log_file="$1"
  if [[ ! -f "$log_file" ]]; then
    return 1
  fi
  sed -n 's/^.*temporary password.*root@localhost: //p' "$log_file" | tail -n 1
}

initialize_app_managed_mysql_datadir() {
  local os_family="$1"
  local mysqld_bin
  local basedir
  local datadir
  local log_file
  local config_file

  datadir="$(local_mysql_datadir "$os_family")"
  if [[ -d "$datadir/mysql" ]]; then
    return 0
  fi

  mysqld_bin="$(find_mysqld_server)" || {
    echo "mysqld was not found after local MySQL Server installation." >&2
    return 1
  }
  basedir="$(mysql_basedir_from_mysqld "$mysqld_bin")"
  LOCAL_MYSQL_BASEDIR_INPUT="$basedir"
  config_file="$(local_mysql_config_file "$os_family")"
  log_file="$(local_mysql_error_log "$os_family")"

  mkdir -p "$datadir" "$(dirname "$LOCAL_MYSQL_SOCKET_INPUT")" "$(dirname "$log_file")" "$SCRIPT_DIR/.data/tmp"
  if [[ "$os_family" == "ubuntu" && -d "$datadir" && -z "$(find "$datadir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    rmdir "$datadir"
  fi
  rm -f "$log_file"
  echo "Initializing DBConsole-managed local MySQL datadir at $datadir."
  "$mysqld_bin" --defaults-file="$config_file" --initialize
  LOCAL_MYSQL_TEMP_ROOT_PASSWORD="$(extract_temporary_mysql_password "$log_file" || true)"
  if [[ -z "$LOCAL_MYSQL_TEMP_ROOT_PASSWORD" ]]; then
    echo "Unable to read the temporary MySQL root password from $log_file after mysqld --initialize." >&2
    return 1
  fi
  LOCAL_MYSQL_DATADIR_INITIALIZED=1
}

start_app_managed_mysql_server() {
  local os_family="$1"
  local mysqld_safe_bin
  local mysqld_bin
  local config_file
  local log_file

  config_file="$(local_mysql_config_file "$os_family")"
  log_file="$(local_mysql_error_log "$os_family")"
  initialize_app_managed_mysql_datadir "$os_family"

  if [[ -S "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
    return 0
  fi

  mysqld_safe_bin="$(find_mysqld_safe || true)"
  if [[ -n "$mysqld_safe_bin" ]]; then
    "$mysqld_safe_bin" --defaults-file="$config_file" >/dev/null 2>&1 &
  else
    mysqld_bin="$(find_mysqld_server)" || {
      echo "mysqld was not found after local MySQL Server installation." >&2
      return 1
    }
    "$mysqld_bin" --defaults-file="$config_file" --daemonize >/dev/null 2>&1
  fi

  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if [[ -S "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
      return 0
    fi
    sleep 1
  done

  echo "DBConsole-managed local MySQL did not create socket $LOCAL_MYSQL_SOCKET_INPUT. Check $log_file." >&2
  return 1
}

stop_app_managed_mysql_server() {
  local os_family="$1"
  local pid_file
  local pid

  pid_file="$(local_mysql_pid_file "$os_family")"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  fi

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [[ ! -S "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
      return 0
    fi
    sleep 1
  done
  return 0
}

write_local_mysql_socket_only_config() {
  local os_family="$1"
  local config_path
  local basedir
  local datadir
  local error_log
  local pid_file
  local socket_dir
  local tmp_dir
  local mysqld_bin

  if ! local_mysql_bootstrap_requested; then
    return 0
  fi

  socket_dir="$(dirname "$LOCAL_MYSQL_SOCKET_INPUT")"
  case "$os_family" in
    ol8|ol9|ubuntu)
      mysqld_bin="$(find_mysqld_server)" || {
        echo "mysqld was not found after local MySQL Server installation." >&2
        return 1
      }
      LOCAL_MYSQL_BASEDIR_INPUT="$(mysql_basedir_from_mysqld "$mysqld_bin")"
      basedir="$LOCAL_MYSQL_BASEDIR_INPUT"
      datadir="$(local_mysql_datadir "$os_family")"
      config_path="$(local_mysql_config_file "$os_family")"
      error_log="$(local_mysql_error_log "$os_family")"
      pid_file="$(local_mysql_pid_file "$os_family")"
      tmp_dir="$SCRIPT_DIR/.data/tmp"
      ;;
    macos)
      mkdir -p "$socket_dir" "$EMBEDDED_MYSQL_SERVER_DIR/run" "$EMBEDDED_MYSQL_SERVER_DIR/log" "$EMBEDDED_MYSQL_SERVER_DIR/tmp"
      return 0
      ;;
    *)
      echo "Local MySQL socket-only config is not supported for OS family '$os_family'." >&2
      return 1
      ;;
  esac

  mkdir -p "$(dirname "$config_path")" "$datadir" "$socket_dir" "$(dirname "$error_log")" "$(dirname "$pid_file")" "$tmp_dir"

  {
    echo "[mysqld]"
    echo "basedir=$basedir"
    echo "datadir=$datadir"
    echo "skip-networking"
    echo "mysqlx=0"
    echo "socket=$LOCAL_MYSQL_SOCKET_INPUT"
    echo "pid-file=$pid_file"
    echo "log-error=$error_log"
    echo "tmpdir=$tmp_dir"
    echo ""
    echo "[client]"
    echo "socket=$LOCAL_MYSQL_SOCKET_INPUT"
  } >"$config_path"
  chmod 600 "$config_path" 2>/dev/null || true
}

configure_ubuntu_mysqld_apparmor() {
  local os_family="$1"
  local apparmor_profile="/etc/apparmor.d/usr.sbin.mysqld"
  local apparmor_local="/etc/apparmor.d/local/usr.sbin.mysqld"

  if [[ "$os_family" != "ubuntu" ]] || ! local_mysql_bootstrap_requested; then
    return 0
  fi
  if [[ ! -r "$apparmor_profile" ]] || ! command -v apparmor_parser >/dev/null 2>&1; then
    return 0
  fi
  if skip_privileged_setup_enabled; then
    log_skipped_privileged_step "Ubuntu AppArmor allowance for DBConsole local MySQL"
    return 0
  fi

  run_as_root install -d -m 0755 "$(dirname "$apparmor_local")"
  run_as_root touch "$apparmor_local"
  append_root_file_once "$apparmor_local" "# DBConsole app-managed socket-only MySQL state"
  append_root_file_once "$apparmor_local" "$SCRIPT_DIR/etc/my.cnf r,"
  append_root_file_once "$apparmor_local" "$SCRIPT_DIR/.embedded/mysql-server/ r,"
  append_root_file_once "$apparmor_local" "$SCRIPT_DIR/.embedded/mysql-server/** rm,"
  append_root_file_once "$apparmor_local" "$SCRIPT_DIR/.data/ rw,"
  append_root_file_once "$apparmor_local" "$SCRIPT_DIR/.data/** rwk,"
  run_as_root apparmor_parser -r "$apparmor_profile" || {
    echo "Unable to reload the mysqld AppArmor profile. DBConsole local MySQL may not start until AppArmor is reloaded." >&2
    return 1
  }
  echo "Configured Ubuntu AppArmor allowances for DBConsole local MySQL paths. AppArmor does not control HTTPS port 443 ingress; setup opens that port through the host firewall."
}

install_local_mysql_server() {
  local os_family="$1"

  if ! local_mysql_bootstrap_requested; then
    return 0
  fi

  if skip_privileged_setup_enabled && [[ "$os_family" != "macos" ]]; then
    log_skipped_privileged_step "local MySQL Server installation"
    return 1
  fi

  case "$os_family" in
    ol8|ol9)
      if ! command -v dnf >/dev/null 2>&1; then
        echo "dnf is required to install local MySQL Server on $os_family." >&2
        return 1
      fi
      if [[ "$os_family" == "ol8" ]]; then
        run_as_root dnf -y module disable mysql >/dev/null 2>&1 || true
      fi
      run_as_root dnf install -y --refresh --best --allowerasing mysql-community-server mysql-community-client
      if command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl disable --now mysqld >/dev/null 2>&1 || true
      else
        run_as_root service mysqld stop >/dev/null 2>&1 || true
      fi
      ;;
    ubuntu)
      if ! command -v apt-get >/dev/null 2>&1; then
        echo "apt-get is required to install local MySQL Server on Ubuntu." >&2
        return 1
      fi
      run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-community-server mysql-community-client
      if command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl disable --now mysql >/dev/null 2>&1 || true
      else
        run_as_root service mysql stop >/dev/null 2>&1 || true
      fi
      ;;
    macos)
      install_macos_embedded_mysql_server
      start_macos_embedded_mysql_server
      ;;
    *)
      echo "Local MySQL Server bootstrap is not supported for OS family '$os_family'." >&2
      return 1
      ;;
  esac
}

sql_quote_literal() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

mysql_identifier_quote() {
  local value="$1"
  if [[ ! "$value" =~ ^[A-Za-z0-9_$]+$ ]]; then
    echo "Invalid MySQL identifier '$value'." >&2
    return 1
  fi
  printf '`%s`' "$value"
}

find_mysql_client() {
  if [[ -n "${MYSQL_CLIENT:-}" && -x "$MYSQL_CLIENT" ]]; then
    printf '%s\n' "$MYSQL_CLIENT"
    return 0
  fi
  if [[ -x "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysql" ]]; then
    printf '%s\n' "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysql"
    return 0
  fi
  command -v mysql
}

mysql_option_file_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

write_mysql_defaults_file() {
  local target_file="$1"
  local user_name="$2"
  local password_value="$3"
  local protocol="${4:-socket}"
  local socket_path="${5:-}"

  {
    echo "[client]"
    printf 'user=%s\n' "$(mysql_option_file_quote "$user_name")"
    printf 'password=%s\n' "$(mysql_option_file_quote "$password_value")"
    echo "protocol=$protocol"
    if [[ "$protocol" == "socket" && -n "$socket_path" ]]; then
      printf 'socket=%s\n' "$(mysql_option_file_quote "$socket_path")"
    fi
  } >"$target_file"
  chmod 600 "$target_file"
}

run_mysql_file() {
  local mysql_bin="$1"
  local defaults_file="$2"
  local sql_file="$3"
  shift 3
  "$mysql_bin" --defaults-extra-file="$defaults_file" "$@" <"$sql_file"
}

run_mysql_socket_root_file() {
  local mysql_bin="$1"
  local sql_file="$2"
  local root_args=(--protocol=socket --socket="$LOCAL_MYSQL_SOCKET_INPUT" -uroot)

  if [[ "$(uname -s)" == "Darwin" && -x "$EMBEDDED_MYSQL_SERVER_DIR/bin/mysqld" ]]; then
    "$mysql_bin" "${root_args[@]}" <"$sql_file"
    return 0
  fi

  if [[ "$(id -u)" -eq 0 ]]; then
    "$mysql_bin" "${root_args[@]}" <"$sql_file"
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    return 1
  fi
  if is_interactive_terminal; then
    sudo "$mysql_bin" "${root_args[@]}" <"$sql_file"
  else
    sudo -n "$mysql_bin" "${root_args[@]}" <"$sql_file"
  fi
}

write_local_admin_sql() {
  local sql_file="$1"
  local admin_user_literal
  local admin_password_literal

  admin_user_literal="$(sql_quote_literal "$LOCAL_MYSQL_ADMIN_USER_INPUT")"
  admin_password_literal="$(sql_quote_literal "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT")"

  {
    printf 'CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s;\n' "$admin_user_literal" "'localhost'" "$admin_password_literal"
    printf 'ALTER USER %s@%s IDENTIFIED BY %s;\n' "$admin_user_literal" "'localhost'" "$admin_password_literal"
    printf 'GRANT ALL PRIVILEGES ON *.* TO %s@%s WITH GRANT OPTION;\n' "$admin_user_literal" "'localhost'"
    printf 'FLUSH PRIVILEGES;\n'
  } >"$sql_file"
  chmod 600 "$sql_file"
}

configure_initialized_mysql_root_account() {
  local mysql_bin="$1"
  local admin_defaults="$2"
  local sql_file="$3"
  local root_temp_defaults
  local root_admin_defaults
  local root_reset_sql_file
  local root_rename_sql_file
  local admin_user_literal
  local admin_password_literal

  if [[ -z "$LOCAL_MYSQL_TEMP_ROOT_PASSWORD" ]]; then
    return 1
  fi

  root_temp_defaults="$(mktemp)"
  root_admin_defaults="$(mktemp)"
  root_reset_sql_file="$(mktemp)"
  root_rename_sql_file="$(mktemp)"
  admin_user_literal="$(sql_quote_literal "$LOCAL_MYSQL_ADMIN_USER_INPUT")"
  admin_password_literal="$(sql_quote_literal "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT")"

  write_mysql_defaults_file "$root_temp_defaults" "root" "$LOCAL_MYSQL_TEMP_ROOT_PASSWORD" "socket" "$LOCAL_MYSQL_SOCKET_INPUT"
  write_mysql_defaults_file "$root_admin_defaults" "root" "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT" "socket" "$LOCAL_MYSQL_SOCKET_INPUT"
  {
    printf 'ALTER USER %s@%s IDENTIFIED BY %s;\n' "'root'" "'localhost'" "$admin_password_literal"
  } >"$root_reset_sql_file"
  chmod 600 "$root_reset_sql_file"
  {
    if [[ "$LOCAL_MYSQL_ADMIN_USER_INPUT" != "root" ]]; then
      printf 'RENAME USER %s@%s TO %s@%s;\n' "'root'" "'localhost'" "$admin_user_literal" "'localhost'"
    fi
    printf 'GRANT ALL PRIVILEGES ON *.* TO %s@%s WITH GRANT OPTION;\n' "$admin_user_literal" "'localhost'"
    printf 'FLUSH PRIVILEGES;\n'
  } >"$root_rename_sql_file"
  chmod 600 "$root_rename_sql_file"

  if run_mysql_file "$mysql_bin" "$root_temp_defaults" "$root_reset_sql_file" --connect-expired-password >/dev/null 2>&1 &&
    run_mysql_file "$mysql_bin" "$root_admin_defaults" "$root_rename_sql_file" >/dev/null 2>&1 &&
    run_mysql_file "$mysql_bin" "$admin_defaults" "$sql_file" >/dev/null 2>&1; then
    rm -f "$root_temp_defaults" "$root_admin_defaults" "$root_reset_sql_file" "$root_rename_sql_file"
    echo "Renamed initialized MySQL root account to '$LOCAL_MYSQL_ADMIN_USER_INPUT'@'localhost' and set the supplied password."
    return 0
  fi

  rm -f "$root_temp_defaults" "$root_admin_defaults" "$root_reset_sql_file" "$root_rename_sql_file"
  return 1
}

provision_local_mysql_admin_with_init_file() {
  local os_family="$1"
  local mysql_bin="$2"
  local admin_defaults="$3"
  local sql_file="$4"
  local service_name
  local init_sql_file
  local init_config_file
  local admin_init_sql_file
  local attempt
  local mysql_user="mysql"

  if ! local_mysql_init_file_provisioning_enabled; then
    return 1
  fi
  if skip_privileged_setup_enabled; then
    return 1
  fi

  case "$os_family" in
    ol8|ol9)
      ensure_mysql_config_include_dir "$os_family"
      init_config_file="/etc/my.cnf.d/dbconsole-local-init.cnf"
      init_sql_file="/var/lib/mysql/dbconsole-local-init.sql"
      ;;
    ubuntu)
      ensure_mysql_config_include_dir "$os_family"
      init_config_file="/etc/mysql/conf.d/dbconsole-local-init.cnf"
      init_sql_file="/var/lib/mysql/dbconsole-local-init.sql"
      ;;
    *)
      return 1
      ;;
  esac

  admin_init_sql_file="$(mktemp)"
  write_local_admin_sql "$admin_init_sql_file"
  service_name="$(local_mysql_service_name "$os_family")"

  echo "Attempting one-time local MySQL admin init-file provisioning for '$LOCAL_MYSQL_ADMIN_USER_INPUT'."
  if ! run_as_root cp "$admin_init_sql_file" "$init_sql_file"; then
    rm -f "$admin_init_sql_file"
    return 1
  fi
  run_as_root chown "$mysql_user:$mysql_user" "$init_sql_file" 2>/dev/null || true
  run_as_root chmod 600 "$init_sql_file" || true
  restore_selinux_context "$init_sql_file"

  {
    echo "[mysqld]"
    echo "init-file=$init_sql_file"
  } | write_root_file "$init_config_file"
  run_as_root chmod 644 "$init_config_file" || true
  restore_selinux_context "$init_config_file"

  if command -v systemctl >/dev/null 2>&1; then
    run_as_root systemctl restart "$service_name" || true
  else
    run_as_root service "$service_name" restart || true
  fi

  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if run_mysql_file "$mysql_bin" "$admin_defaults" "$sql_file" >/dev/null 2>&1; then
      run_as_root rm -f "$init_config_file" "$init_sql_file" >/dev/null 2>&1 || true
      if command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl restart "$service_name" || true
      else
        run_as_root service "$service_name" restart || true
      fi
      rm -f "$admin_init_sql_file"
      echo "Local MySQL admin account '$LOCAL_MYSQL_ADMIN_USER_INPUT' configured after one-time init-file provisioning."
      return 0
    fi
    sleep 1
  done

  run_as_root rm -f "$init_config_file" "$init_sql_file" >/dev/null 2>&1 || true
  if command -v systemctl >/dev/null 2>&1; then
    run_as_root systemctl restart "$service_name" || true
  else
    run_as_root service "$service_name" restart || true
  fi
  rm -f "$admin_init_sql_file"
  return 1
}

provision_local_mysql_admin_with_grant_table_bypass() {
  local os_family="$1"
  local mysql_bin="$2"
  local admin_defaults="$3"
  local sql_file="$4"
  local service_name
  local bypass_config_file
  local bypass_sql_file
  local attempt
  local verify_attempt
  local config_file
  local app_managed_mysql=0

  if ! local_mysql_init_file_provisioning_enabled; then
    return 1
  fi
  if skip_privileged_setup_enabled; then
    return 1
  fi

  case "$os_family" in
    ol8|ol9)
      config_file="$(local_mysql_config_file "$os_family")"
      if [[ -f "$config_file" ]]; then
        app_managed_mysql=1
      else
        ensure_mysql_config_include_dir "$os_family"
        bypass_config_file="/etc/my.cnf.d/dbconsole-local-grant-bypass.cnf"
      fi
      ;;
    ubuntu)
      config_file="$(local_mysql_config_file "$os_family")"
      if [[ -f "$config_file" ]]; then
        app_managed_mysql=1
      else
        ensure_mysql_config_include_dir "$os_family"
        bypass_config_file="/etc/mysql/conf.d/dbconsole-local-grant-bypass.cnf"
      fi
      ;;
    *)
      return 1
      ;;
  esac

  bypass_sql_file="$(mktemp)"
  {
    echo "FLUSH PRIVILEGES;"
    cat "$sql_file"
    if [[ "$LOCAL_MYSQL_ADMIN_USER_INPUT" != "root" ]]; then
      echo "DROP USER IF EXISTS 'root'@'localhost';"
    fi
  } >"$bypass_sql_file"
  chmod 600 "$bypass_sql_file"

  service_name="$(local_mysql_service_name "$os_family")"
  echo "Attempting one-time local MySQL grant-table bypass provisioning for '$LOCAL_MYSQL_ADMIN_USER_INPUT'."
  echo "This temporary recovery path uses skip-grant-tables with skip-networking and creates or resets only '$LOCAL_MYSQL_ADMIN_USER_INPUT'@'localhost'."

  if [[ "$app_managed_mysql" == "1" ]]; then
    stop_app_managed_mysql_server "$os_family" || true
    if ! "$(find_mysqld_server)" --defaults-file="$config_file" --skip-grant-tables --daemonize >/dev/null 2>&1; then
      rm -f "$bypass_sql_file"
      return 1
    fi
  else
    {
      echo "[mysqld]"
      echo "skip-grant-tables"
      echo "skip-networking"
      echo "mysqlx=0"
      echo "socket=$LOCAL_MYSQL_SOCKET_INPUT"
    } | write_root_file "$bypass_config_file"
    run_as_root chmod 644 "$bypass_config_file" || true
    restore_selinux_context "$bypass_config_file"

    if command -v systemctl >/dev/null 2>&1; then
      run_as_root systemctl restart "$service_name" || true
    else
      run_as_root service "$service_name" restart || true
    fi
  fi

  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if run_mysql_socket_root_file "$mysql_bin" "$bypass_sql_file" >/dev/null 2>&1; then
      if [[ "$app_managed_mysql" == "1" ]]; then
        stop_app_managed_mysql_server "$os_family" || true
        start_app_managed_mysql_server "$os_family"
      else
        run_as_root rm -f "$bypass_config_file" >/dev/null 2>&1 || true
        if command -v systemctl >/dev/null 2>&1; then
          run_as_root systemctl restart "$service_name" || true
        else
          run_as_root service "$service_name" restart || true
        fi
      fi
      for verify_attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
        if run_mysql_file "$mysql_bin" "$admin_defaults" "$sql_file" >/dev/null 2>&1; then
          rm -f "$bypass_sql_file"
          echo "Local MySQL admin account '$LOCAL_MYSQL_ADMIN_USER_INPUT' configured after one-time grant-table bypass provisioning."
          return 0
        fi
        sleep 1
      done
      rm -f "$bypass_sql_file"
      return 1
    fi
    sleep 1
  done

  if [[ "$app_managed_mysql" == "1" ]]; then
    stop_app_managed_mysql_server "$os_family" || true
    start_app_managed_mysql_server "$os_family" || true
  else
    run_as_root rm -f "$bypass_config_file" >/dev/null 2>&1 || true
    if command -v systemctl >/dev/null 2>&1; then
      run_as_root systemctl restart "$service_name" || true
    else
      run_as_root service "$service_name" restart || true
    fi
  fi
  rm -f "$bypass_sql_file"
  return 1
}

configure_local_mysql_admin_account() {
  local os_family="$1"
  local mysql_bin
  local admin_defaults
  local root_defaults
  local sql_file
  local existing_root_sql_file

  if ! local_mysql_bootstrap_requested; then
    return 0
  fi

  mysql_bin="$(find_mysql_client)" || {
    echo "mysql client was not found after local MySQL Server installation." >&2
    return 1
  }

  admin_defaults="$(mktemp)"
  root_defaults="$(mktemp)"
  sql_file="$(mktemp)"
  existing_root_sql_file="$(mktemp)"

  write_mysql_defaults_file "$admin_defaults" "$LOCAL_MYSQL_ADMIN_USER_INPUT" "$LOCAL_MYSQL_ADMIN_PASSWORD_INPUT" "socket" "$LOCAL_MYSQL_SOCKET_INPUT"
  write_local_admin_sql "$sql_file"

  if configure_initialized_mysql_root_account "$mysql_bin" "$admin_defaults" "$sql_file"; then
    rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
    return 0
  fi

  if run_mysql_file "$mysql_bin" "$admin_defaults" "$sql_file" >/dev/null 2>&1; then
    echo "Local MySQL admin account '$LOCAL_MYSQL_ADMIN_USER_INPUT' refreshed."
    rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
    return 0
  fi

  if run_mysql_socket_root_file "$mysql_bin" "$sql_file" >/dev/null 2>&1; then
    echo "Local MySQL admin account '$LOCAL_MYSQL_ADMIN_USER_INPUT' configured with socket-root access."
    rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
    return 0
  fi

  if [[ -n "$LOCAL_MYSQL_ROOT_PASSWORD_INPUT" ]]; then
    write_mysql_defaults_file "$root_defaults" "root" "$LOCAL_MYSQL_ROOT_PASSWORD_INPUT" "socket" "$LOCAL_MYSQL_SOCKET_INPUT"
    if run_mysql_file "$mysql_bin" "$root_defaults" "$sql_file" --connect-expired-password >/dev/null 2>&1; then
      echo "Local MySQL admin account '$LOCAL_MYSQL_ADMIN_USER_INPUT' configured with supplied root credentials."
      rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
      return 0
    fi
    write_local_admin_sql "$existing_root_sql_file"
    if run_mysql_file "$mysql_bin" "$root_defaults" "$existing_root_sql_file" --connect-expired-password >/dev/null 2>&1; then
      echo "Local MySQL admin account '$LOCAL_MYSQL_ADMIN_USER_INPUT' configured with supplied root credentials."
      rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
      return 0
    fi
  fi

  if provision_local_mysql_admin_with_init_file "$os_family" "$mysql_bin" "$admin_defaults" "$sql_file"; then
    rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
    return 0
  fi

  if provision_local_mysql_admin_with_grant_table_bypass "$os_family" "$mysql_bin" "$admin_defaults" "$sql_file"; then
    rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
    return 0
  fi

  rm -f "$admin_defaults" "$root_defaults" "$sql_file" "$existing_root_sql_file"
  echo "Unable to configure the local MySQL admin account automatically. If this is not a DBConsole-managed local MySQL instance, rerun setup with known LOCAL_MYSQL_ROOT_PASSWORD plus LOCAL_MYSQL_ADMIN_USER and LOCAL_MYSQL_ADMIN_PASSWORD, or create '$LOCAL_MYSQL_ADMIN_USER_INPUT' manually. Setup never creates or resets a MySQL root account." >&2
  return 1
}

write_local_admin_profile() {
  if ! local_mysql_bootstrap_requested; then
    return 0
  fi

  PROFILE_STORE="$SCRIPT_DIR/profiles.json" \
  LOCAL_MYSQL_PROFILE_NAME="$LOCAL_MYSQL_PROFILE_NAME_INPUT" \
  LOCAL_MYSQL_PORT="$LOCAL_MYSQL_PORT_INPUT" \
  LOCAL_MYSQL_SOCKET="$LOCAL_MYSQL_SOCKET_INPUT" \
  LOCAL_MYSQL_DATABASE="$LOCAL_MYSQL_DATABASE_INPUT" \
  LOCAL_MYSQL_ADMIN_USER="$LOCAL_MYSQL_ADMIN_USER_INPUT" \
  "$VENV_DIR/bin/python" - <<'PY'
import json
import os
from pathlib import Path

profile_store = Path(os.environ["PROFILE_STORE"])
profile_name = os.environ["LOCAL_MYSQL_PROFILE_NAME"]
profile = {
    "name": profile_name,
    "host": "",
    "port": int(os.environ["LOCAL_MYSQL_PORT"]),
    "database": os.environ["LOCAL_MYSQL_DATABASE"],
    "username": os.environ["LOCAL_MYSQL_ADMIN_USER"],
    "socket_enabled": True,
    "socket_path": os.environ["LOCAL_MYSQL_SOCKET"],
    "ssh_enabled": False,
    "ssh_host": "",
    "ssh_port": 22,
    "ssh_user": "",
    "ssh_key_path": "",
    "require_password_change": True,
}

try:
    payload = json.loads(profile_store.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    payload = {"profiles": []}

profiles = payload.get("profiles", [])
if not isinstance(profiles, list):
    profiles = []

remaining = [
    row for row in profiles
    if str((row or {}).get("name", "")).strip().lower() != profile_name.lower()
]
remaining.insert(0, profile)
profile_store.write_text(json.dumps({"profiles": remaining}, indent=2) + "\n", encoding="utf-8")
profile_store.chmod(0o600)
PY
}

resolve_platform_dir() {
  local os_family="$1"
  local candidate
  local lowercase_dir="$SCRIPT_DIR/$os_family"
  local uppercase_dir="$SCRIPT_DIR/$(printf '%s' "$os_family" | tr '[:lower:]' '[:upper:]')"

  for candidate in "$lowercase_dir" "$uppercase_dir"; do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  echo "Platform directory not found for '$os_family'. Checked: $lowercase_dir and $uppercase_dir" >&2
  return 1
}

main() {
  local original_arg_count="$#"
  local os_family="$OS_FAMILY_INPUT"
  local deploy_mode
  local host_value
  local http_port
  local https_port
  local ssl_cert_file
  local ssl_key_file
  local service_user
  local service_group
  local prompted_ports
  local tls_assets
  local python_command

  load_existing_runtime_env
  parse_args "$@"
  os_family="$OS_FAMILY_INPUT"

  if [[ -z "$os_family" ]]; then
    os_family="$(detect_os_family)"
    if is_interactive_terminal; then
      os_family="$(prompt_for_normalized_value "OS family" "$os_family" normalize_os_family "Enter one of: ol8, ol9, ubuntu, macos.")"
    fi
  else
    os_family="$(normalize_os_family "$os_family")"
  fi

  if [[ -z "$LOCAL_MYSQL_SOCKET_INPUT" ]]; then
    LOCAL_MYSQL_SOCKET_INPUT="$(default_local_mysql_socket "$os_family")"
  fi
  prompt_for_default_local_mysql_bootstrap "$original_arg_count"
  validate_local_mysql_bootstrap_inputs

  if [[ -z "$DEPLOY_MODE_INPUT" ]]; then
    deploy_mode="http"
    if is_interactive_terminal; then
      deploy_mode="$(prompt_for_normalized_value "Deploy mode" "$deploy_mode" normalize_deploy_mode "Enter one of: http, https, both, none.")"
    fi
  else
    deploy_mode="$(normalize_deploy_mode "$DEPLOY_MODE_INPUT")"
  fi

  host_value="$(resolve_value "$HOST_INPUT" "$EXISTING_HOST" "0.0.0.0")"
  if is_interactive_terminal && [[ -z "$HOST_INPUT" ]]; then
    host_value="$(prompt_for_text_value "Host bind address" "$host_value" "no")"
  fi

  http_port="$(normalize_port "HTTP" "$(resolve_value "$HTTP_PORT_INPUT" "$EXISTING_DEFAULT_HTTP_PORT" "80")")"
  https_port="$(normalize_port "HTTPS" "$(resolve_value "$HTTPS_PORT_INPUT" "$EXISTING_DEFAULT_HTTPS_PORT" "443")")"
  prompted_ports="$(prompt_for_ports_if_needed "$deploy_mode" "$http_port" "$https_port")"
  http_port="$(printf '%s\n' "$prompted_ports" | sed -n '1p')"
  https_port="$(printf '%s\n' "$prompted_ports" | sed -n '2p')"

  ssl_cert_file="$(resolve_value "$SSL_CERT_FILE_INPUT" "$EXISTING_SSL_CERT_FILE" "")"
  ssl_key_file="$(resolve_value "$SSL_KEY_FILE_INPUT" "$EXISTING_SSL_KEY_FILE" "")"
  case "$deploy_mode" in
    https|both)
      if is_interactive_terminal && [[ -z "$SSL_CERT_FILE_INPUT" ]]; then
        ssl_cert_file="$(prompt_for_text_value "SSL certificate file" "$ssl_cert_file" "yes")"
      fi
      if is_interactive_terminal && [[ -z "$SSL_KEY_FILE_INPUT" ]]; then
        ssl_key_file="$(prompt_for_text_value "SSL private key file" "$ssl_key_file" "yes")"
      fi
      ;;
  esac

  service_user=""
  service_group=""
  case "$os_family" in
    ol8|ol9|ubuntu)
      service_user="$(resolve_service_user)"
      if is_interactive_terminal && [[ -z "$SERVICE_USER_INPUT" ]]; then
        service_user="$(prompt_for_text_value "Systemd service user" "$service_user" "no")"
      fi
      SERVICE_USER_INPUT="$service_user"

      service_group="$(resolve_service_group "$service_user")"
      if is_interactive_terminal && [[ -z "$SERVICE_GROUP_INPUT" ]]; then
        service_group="$(prompt_for_text_value "Systemd service group" "$service_group" "no")"
      fi
      SERVICE_GROUP_INPUT="$service_group"
      ;;
  esac

  tls_assets="$(ensure_https_tls_assets "$deploy_mode" "$host_value" "$ssl_cert_file" "$ssl_key_file" "$service_user" "$service_group")"
  ssl_cert_file="$(printf '%s\n' "$tls_assets" | sed -n '1p')"
  ssl_key_file="$(printf '%s\n' "$tls_assets" | sed -n '2p')"

  python_command="$(resolve_python_command "$os_family")"
  DBCONSOLE_PYTHON_BIN="$python_command"
  prepare_virtualenv "$python_command" "$os_family"
  run_dependency_audit

  run_mysqlsh_installer "$os_family"
  install_local_mysql_server "$os_family"
  write_local_mysql_socket_only_config "$os_family"
  configure_ubuntu_mysqld_apparmor "$os_family"
  if local_mysql_bootstrap_requested; then
    restart_local_mysql_service "$os_family"
  fi
  configure_local_mysql_admin_account "$os_family"
  write_local_admin_profile
  write_runtime_env "$http_port" "$https_port" "$host_value" "$ssl_cert_file" "$ssl_key_file" "$os_family" "$deploy_mode"
  harden_local_file_permissions
  setup_systemd_services "$os_family" "$deploy_mode" "$ssl_cert_file" "$ssl_key_file" "$http_port" "$https_port"

  sync_firewall_ports "$deploy_mode" "$http_port" "$https_port" "$EXISTING_DEFAULT_HTTP_PORT" "$EXISTING_DEFAULT_HTTPS_PORT"

  print_privileged_port_guidance "$os_family" "$deploy_mode" "$http_port" "$https_port"

  echo "Setup completed."
  echo "Virtual environment: $VENV_DIR"
  echo "Saved runtime defaults: $RUNTIME_ENV_FILE"
  echo "Default host: $host_value"
  echo "Default HTTP port: $http_port"
  echo "Default HTTPS port: $https_port"
  if [[ -n "$ssl_cert_file" && -n "$ssl_key_file" ]]; then
    echo "TLS certificate: $ssl_cert_file"
    echo "TLS key: $ssl_key_file"
  fi
  echo "HTTP start script: $SCRIPT_DIR/start_http.sh"
  echo "HTTPS start script: $SCRIPT_DIR/start_https.sh"
  case "$os_family" in
    ol8|ol9|ubuntu)
      echo "Systemd services: dbconsole-http.service and dbconsole-https.service"
      ;;
  esac
  echo "Use PORT=<port> at launch time to override either saved default temporarily."
}

main "$@"
