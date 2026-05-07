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

if brew list --cask mysql-shell >/dev/null 2>&1; then
  brew upgrade --cask mysql-shell || true
elif brew list --formula mysql-shell >/dev/null 2>&1; then
  brew upgrade mysql-shell || true
else
  brew install --cask mysql-shell || brew install mysql-shell
fi

if ! command -v mysqlsh >/dev/null 2>&1; then
  echo "mysqlsh was not found in PATH after the macOS installation completed." >&2
  exit 1
fi

echo "mysqlsh installed successfully."
