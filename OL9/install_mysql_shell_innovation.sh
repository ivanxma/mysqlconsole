#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

REPO_RPM="${REPO_RPM:-mysql84-community-release-el9-3.noarch.rpm}"
REPO_URL="${REPO_URL:-https://dev.mysql.com/get/${REPO_RPM}}"

dnf install -y dnf-plugins-core
dnf install -y "${REPO_URL}"

if command -v yum-config-manager >/dev/null 2>&1; then
  yum-config-manager --disable mysql-8.4-lts-community mysql-tools-8.4-lts-community
  yum-config-manager --enable mysql-innovation-community mysql-tools-innovation-community
else
  dnf config-manager --disable mysql-8.4-lts-community mysql-tools-8.4-lts-community
  dnf config-manager --enable mysql-innovation-community mysql-tools-innovation-community
fi

dnf install -y mysql-shell wget tar gzip unzip

