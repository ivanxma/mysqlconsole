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

if ! command -v apt-get >/dev/null 2>&1; then
  echo "apt-get is required on Ubuntu but was not found." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Unable to detect the Ubuntu release codename from /etc/os-release." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

UBUNTU_RELEASE_CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [[ -z "$UBUNTU_RELEASE_CODENAME" && -x /usr/bin/lsb_release ]]; then
  UBUNTU_RELEASE_CODENAME="$(lsb_release -sc)"
fi

if [[ -z "$UBUNTU_RELEASE_CODENAME" ]]; then
  echo "Unable to determine the Ubuntu release codename." >&2
  exit 1
fi

MYSQL_APT_KEY_URL="${MYSQL_APT_KEY_URL:-https://repo.mysql.com/RPM-GPG-KEY-mysql-2023}"
MYSQL_APT_KEYRING="${MYSQL_APT_KEYRING:-/etc/apt/keyrings/mysql.gpg}"
MYSQL_APT_LIST="${MYSQL_APT_LIST:-/etc/apt/sources.list.d/mysql.list}"
MYSQL_APT_REPO_URL="${MYSQL_APT_REPO_URL:-http://repo.mysql.com/apt/ubuntu/}"
MYSQL_APT_COMPONENTS="${MYSQL_APT_COMPONENTS:-mysql-innovation mysql-tools}"
TMP_KEYRING_FILE="$(mktemp)"
TMP_LIST_FILE="$(mktemp)"

cleanup() {
  rm -f "$TMP_KEYRING_FILE" "$TMP_LIST_FILE"
}

trap cleanup EXIT

run_root apt-get update
run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg

curl -fsSL "$MYSQL_APT_KEY_URL" | gpg --dearmor >"$TMP_KEYRING_FILE"
printf 'deb [signed-by=%s] %s %s %s\n' \
  "$MYSQL_APT_KEYRING" \
  "${MYSQL_APT_REPO_URL%/}/" \
  "$UBUNTU_RELEASE_CODENAME" \
  "$MYSQL_APT_COMPONENTS" >"$TMP_LIST_FILE"

run_root install -d -m 0755 /etc/apt/keyrings /etc/apt/sources.list.d
run_root install -m 0644 "$TMP_KEYRING_FILE" "$MYSQL_APT_KEYRING"
run_root install -m 0644 "$TMP_LIST_FILE" "$MYSQL_APT_LIST"
run_root apt-get update

if ! run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-shell; then
  echo "Unable to install mysql-shell from the MySQL innovation APT repository on Ubuntu." >&2
  exit 1
fi

if ! command -v mysqlsh >/dev/null 2>&1; then
  echo "mysqlsh was not found in PATH after the Ubuntu installation completed." >&2
  exit 1
fi

echo "mysqlsh installed successfully."
