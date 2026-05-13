#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is intended for macOS." >&2
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required to install mysql-shell on macOS." >&2
  echo "Install Homebrew first, then rerun this script." >&2
  exit 1
fi

brew update

if brew list --cask mysql-shell >/dev/null 2>&1; then
  brew upgrade --cask mysql-shell || brew reinstall --cask mysql-shell
elif brew list --formula mysql-shell >/dev/null 2>&1; then
  brew upgrade mysql-shell || brew reinstall mysql-shell
else
  brew install --cask mysql-shell || brew install mysql-shell
fi

if ! command -v mysqlsh >/dev/null 2>&1; then
  echo "mysqlsh was not found in PATH after the macOS installation completed." >&2
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
