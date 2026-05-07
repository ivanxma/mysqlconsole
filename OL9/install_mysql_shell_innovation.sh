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
run_root dnf install -y mysql-shell

if ! command -v mysqlsh >/dev/null 2>&1; then
  echo "mysqlsh was not found in PATH after the OL9 installation completed." >&2
  exit 1
fi

echo "mysqlsh installed successfully."
