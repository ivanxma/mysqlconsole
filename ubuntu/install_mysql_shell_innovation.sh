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

MYSQL_APT_KEY_URL="${MYSQL_APT_KEY_URL:-https://repo.mysql.com/RPM-GPG-KEY-mysql-2025}"
MYSQL_APT_KEYRING="${MYSQL_APT_KEYRING:-/etc/apt/keyrings/mysql.gpg}"
MYSQL_APT_LIST="${MYSQL_APT_LIST:-/etc/apt/sources.list.d/mysql.list}"
MYSQL_APT_REPO_URL="${MYSQL_APT_REPO_URL:-http://repo.mysql.com/apt/ubuntu/}"
MYSQL_APT_COMPONENTS="${MYSQL_APT_COMPONENTS:-mysql-innovation mysql-tools}"
MYSQL_SHELL_PACKAGE="${MYSQL_SHELL_PACKAGE:-mysql-shell}"
MYSQL_SHELL_VENDOR_DOWNLOAD_BASE="${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE:-https://dev.mysql.com/get/Downloads/MySQL-Shell}"
MYSQL_SHELL_DOWNLOAD_PAGE="${MYSQL_SHELL_DOWNLOAD_PAGE:-https://dev.mysql.com/downloads/shell/}"
TMP_KEYRING_FILE="$(mktemp)"
TMP_LIST_FILE="$(mktemp)"

cleanup() {
  rm -f "$TMP_KEYRING_FILE" "$TMP_LIST_FILE"
}

trap cleanup EXIT

if [[ -f "$MYSQL_APT_LIST" ]]; then
  run_root rm -f "$MYSQL_APT_LIST"
fi

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
  echo "Available ${MYSQL_SHELL_PACKAGE} versions from configured vendor repositories:" >&2
  if command -v timeout >/dev/null 2>&1; then
    timeout 20s apt-cache policy "$MYSQL_SHELL_PACKAGE" >&2 || echo "Unable to list ${MYSQL_SHELL_PACKAGE} candidates within 20 seconds." >&2
  else
    apt-cache policy "$MYSQL_SHELL_PACKAGE" >&2 || true
  fi
}

vendor_arch() {
  case "$(dpkg --print-architecture)" in
    amd64)
      printf 'amd64'
      ;;
    arm64)
      printf 'arm64'
      ;;
    *)
      return 1
      ;;
  esac
}

install_mysql_shell_vendor_package() {
  local arch package_url package_file
  arch="$(vendor_arch)" || {
    echo "Unsupported architecture for MySQL Shell vendor package fallback: $(dpkg --print-architecture)" >&2
    return 1
  }
  package_file="$(mktemp "/tmp/mysql-shell-${MYSQL_SHELL_MIN_VERSION}-XXXXXX.deb")"
  package_url="${MYSQL_SHELL_VENDOR_DOWNLOAD_BASE%/}/mysql-shell_${MYSQL_SHELL_MIN_VERSION}-1ubuntu${VERSION_ID}_${arch}.deb"

  echo "Configured vendor repositories did not provide MySQL Shell $MYSQL_SHELL_MIN_VERSION or newer." >&2
  echo "Downloading MySQL Shell from vendor URL: $package_url" >&2
  curl -fsSL "$package_url" -o "$package_file"
  run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$package_file"
  rm -f "$package_file"
}

if ! run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$MYSQL_SHELL_PACKAGE"; then
  echo "Unable to install ${MYSQL_SHELL_PACKAGE} from the MySQL innovation APT repository on Ubuntu." >&2
  exit 1
fi

MYSQL_SHELL_VERSION="$(current_mysqlsh_version)"

if [[ -n "$MYSQL_SHELL_VERSION" ]] && ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --only-upgrade "$MYSQL_SHELL_PACKAGE"
  MYSQL_SHELL_VERSION="$(current_mysqlsh_version)"
fi

if [[ -n "$MYSQL_SHELL_VERSION" ]] && ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  install_mysql_shell_vendor_package
  MYSQL_SHELL_VERSION="$(current_mysqlsh_version)"
fi

if [[ -z "$MYSQL_SHELL_VERSION" ]]; then
  echo "mysqlsh was not found in PATH or its version could not be determined after the Ubuntu installation completed." >&2
  exit 1
fi

if ! version_ge "$MYSQL_SHELL_VERSION" "$MYSQL_SHELL_MIN_VERSION"; then
  show_mysql_shell_candidates
  echo "mysqlsh $MYSQL_SHELL_VERSION is installed, but the configured MySQL vendor repositories and computed vendor package URL did not provide MySQL Shell Innovation $MYSQL_SHELL_MIN_VERSION or newer." >&2
  exit 1
fi

echo "mysqlsh $MYSQL_SHELL_VERSION installed successfully."
