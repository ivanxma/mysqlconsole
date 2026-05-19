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

wait_for_rpm_lock() {
  local deadline=$((SECONDS + ${RPM_LOCK_TIMEOUT_SECONDS:-600}))
  while [[ -e /var/lib/rpm/.rpm.lock ]] && ! run_root rpm --eval '%{_db_backend}' >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for the RPM database lock to clear." >&2
      return 1
    fi
    sleep 5
  done
}

import_mysql_gpg_key() {
  local key_file="/etc/pki/rpm-gpg/RPM-GPG-KEY-mysql-2025"
  if [[ -r "$key_file" ]]; then
    wait_for_rpm_lock
    run_root rpm --import "$key_file" || true
  fi
}

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

wait_for_rpm_lock
run_root dnf install -y dnf-plugins-core ca-certificates curl
install_mysql_repo_release
import_mysql_gpg_key
set_mysql_repo_enabled "no" mysql-8.4-lts-community mysql-tools-8.4-lts-community || true
set_mysql_repo_enabled "yes" mysql-innovation-community mysql-tools-innovation-community

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

MYSQL_SHELL_PACKAGE="${MYSQL_SHELL_PACKAGE:-mysql-shell}"
MYSQL_SHELL_VENDOR_DOWNLOAD_BASE="${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE:-https://dev.mysql.com/get/Downloads/MySQL-Shell}"
MYSQL_SHELL_DOWNLOAD_PAGE="${MYSQL_SHELL_DOWNLOAD_PAGE:-https://dev.mysql.com/downloads/shell/}"

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

MYSQL_SHELL_MIN_VERSION="$(resolve_mysql_shell_min_version)"
echo "Using MySQL Shell target version $MYSQL_SHELL_MIN_VERSION."

current_mysqlsh_version() {
  local version_output
  version_output="$(mysqlsh --version 2>/dev/null || true)"
  printf '%s\n' "$version_output" | grep -Eo '[0-9]+([.][0-9]+){2}' | head -n 1 || true
}

show_mysql_shell_candidates() {
  echo "Available ${MYSQL_SHELL_PACKAGE} versions from enabled vendor repositories:" >&2
  if command -v timeout >/dev/null 2>&1; then
    timeout 20s dnf -q --showduplicates list "$MYSQL_SHELL_PACKAGE" >&2 || echo "Unable to list ${MYSQL_SHELL_PACKAGE} candidates within 20 seconds." >&2
  else
    dnf -q --showduplicates list "$MYSQL_SHELL_PACKAGE" >&2 || true
  fi
}

vendor_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      printf 'x86_64'
      ;;
    aarch64|arm64)
      printf 'aarch64'
      ;;
    *)
      return 1
      ;;
  esac
}

install_mysql_shell_vendor_package() {
  local arch package_url package_file
  arch="$(vendor_arch)" || {
    echo "Unsupported architecture for MySQL Shell vendor package fallback: $(uname -m)" >&2
    return 1
  }
  package_file="$(mktemp "/tmp/mysql-shell-${MYSQL_SHELL_MIN_VERSION}-XXXXXX.rpm")"
  package_url="${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE%/}/mysql-shell-${MYSQL_SHELL_MIN_VERSION}-1.el9.${arch}.rpm"

  echo "Enabled vendor repositories did not provide MySQL Shell $MYSQL_SHELL_MIN_VERSION or newer." >&2
  echo "Downloading MySQL Shell from vendor URL: $package_url" >&2
  curl -fsSL "$package_url" -o "$package_file"
  run_root dnf install -y "$package_file"
  rm -f "$package_file"
}

wait_for_rpm_lock
run_root dnf clean expire-cache
run_root dnf makecache -y --refresh
import_mysql_gpg_key
run_root dnf install -y --refresh --best --allowerasing "$MYSQL_SHELL_PACKAGE"

MYSQL_SHELL_VERSION="$(current_mysqlsh_version)"

if [[ -n "$MYSQL_SHELL_VERSION" ]] && ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  run_root dnf upgrade -y --refresh --best --allowerasing "$MYSQL_SHELL_PACKAGE"
  MYSQL_SHELL_VERSION="$(current_mysqlsh_version)"
fi

if [[ -n "$MYSQL_SHELL_VERSION" ]] && ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  install_mysql_shell_vendor_package
  MYSQL_SHELL_VERSION="$(current_mysqlsh_version)"
fi

if [[ -z "$MYSQL_SHELL_VERSION" ]]; then
  echo "mysqlsh was not found in PATH or its version could not be determined after the OL9 installation completed." >&2
  exit 1
fi

if ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  show_mysql_shell_candidates
  echo "mysqlsh $MYSQL_SHELL_VERSION is installed, but the enabled MySQL vendor repositories and computed vendor package URL did not provide MySQL Shell Innovation $MYSQL_SHELL_MIN_VERSION or newer." >&2
  exit 1
fi

echo "mysqlsh $MYSQL_SHELL_VERSION installed successfully."
