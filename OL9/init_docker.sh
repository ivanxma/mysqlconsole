#!/usr/bin/env bash
# Install Docker Engine and the Compose plugin on Oracle Linux 9.
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  run_root() { "$@"; }
  target_user="${SUDO_USER:-opc}"
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Run this script as root or install sudo." >&2
    exit 1
  fi
  run_root() { sudo "$@"; }
  target_user="$(id -un)"
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Unable to identify the operating system." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ol" || "${VERSION_ID%%.*}" != "9" ]]; then
  echo "This installer supports Oracle Linux 9 only." >&2
  exit 1
fi

run_root dnf install -y dnf-plugins-core git
run_root dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
run_root dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
run_root systemctl enable --now docker
run_root usermod -aG docker "$target_user"

docker version --format 'Docker Engine {{.Server.Version}}'
docker compose version
printf '%s\n' "Docker installed. Sign out and back in before using Docker without sudo."
