#!/usr/bin/env bash
set -euo pipefail

run_root() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Run this script as root or install sudo." >&2
    return 1
  fi
}

if ! command -v dnf >/dev/null 2>&1; then
  echo "dnf is required on OL9 but was not found." >&2
  exit 1
fi

mysql_repo_release_installed() {
  rpm -qa | grep -Eq '^mysql[0-9]+-community-release'
}

install_mysql_repo_release() {
  local repo_url_prefix="${REPO_URL_PREFIX:-https://dev.mysql.com/get}"
  local repo_rpm
  local repo_candidates=()

  if [[ -n "${REPO_RPM:-}" ]]; then
    repo_candidates=("$REPO_RPM")
  else
    repo_candidates=(
      "mysql84-community-release-el9-4.noarch.rpm"
      "mysql84-community-release-el9-3.noarch.rpm"
    )
  fi

  if mysql_repo_release_installed; then
    return 0
  fi

  for repo_rpm in "${repo_candidates[@]}"; do
    if run_root dnf install -y "${repo_url_prefix%/}/${repo_rpm}"; then
      return 0
    fi
  done

  echo "Unable to install the MySQL community repository package for Oracle Linux 9." >&2
  echo "Set REPO_RPM to a valid mysql84-community-release RPM name and rerun this script." >&2
  exit 1
}

set_mysql_repo_enabled() {
  local enabled="$1"
  shift
  local repo_id

  if command -v yum-config-manager >/dev/null 2>&1; then
    for repo_id in "$@"; do
      if [[ "$enabled" == "yes" ]]; then
        run_root yum-config-manager --enable "$repo_id" >/dev/null
      else
        run_root yum-config-manager --disable "$repo_id" >/dev/null
      fi
    done
  else
    for repo_id in "$@"; do
      if [[ "$enabled" == "yes" ]]; then
        run_root dnf config-manager --set-enabled "$repo_id" >/dev/null
      else
        run_root dnf config-manager --set-disabled "$repo_id" >/dev/null
      fi
    done
  fi
}

run_root dnf install -y dnf-plugins-core ca-certificates
install_mysql_repo_release
set_mysql_repo_enabled "no" mysql-8.4-lts-community mysql-tools-8.4-lts-community || true
set_mysql_repo_enabled "yes" mysql-innovation-community mysql-tools-innovation-community
run_root dnf makecache -y --refresh

if rpm -q mysql-shell >/dev/null 2>&1; then
  run_root dnf upgrade -y mysql-shell
else
  run_root dnf install -y mysql-shell
fi

if ! command -v mysqlsh >/dev/null 2>&1; then
  echo "mysqlsh was not found in PATH after the OL9 installation completed." >&2
  exit 1
fi

version_ge() {
  local installed="$1"
  local required="$2"
  local IFS=.
  local installed_parts required_parts
  read -r -a installed_parts <<<"$installed"
  read -r -a required_parts <<<"$required"

  for index in 0 1 2; do
    local installed_part="${installed_parts[$index]:-0}"
    local required_part="${required_parts[$index]:-0}"
    if ((10#$installed_part > 10#$required_part)); then
      return 0
    fi
    if ((10#$installed_part < 10#$required_part)); then
      return 1
    fi
  done

  return 0
}

MYSQL_SHELL_MIN_VERSION="${MYSQL_SHELL_MIN_VERSION:-9.7.0}"
MYSQL_SHELL_VERSION_OUTPUT="$(mysqlsh --version 2>/dev/null || true)"
MYSQL_SHELL_VERSION="$(printf '%s\n' "$MYSQL_SHELL_VERSION_OUTPUT" | grep -Eo '[0-9]+([.][0-9]+){2}' | head -n 1 || true)"

if [[ -z "$MYSQL_SHELL_VERSION" ]]; then
  echo "Unable to determine mysqlsh version from: $MYSQL_SHELL_VERSION_OUTPUT" >&2
  exit 1
fi

if ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  echo "mysqlsh $MYSQL_SHELL_VERSION is installed, but MySQL Shell Innovation $MYSQL_SHELL_MIN_VERSION or newer is required." >&2
  exit 1
fi

echo "mysqlsh $MYSQL_SHELL_VERSION installed successfully."
