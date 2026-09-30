#!/bin/sh
# Build and start DBConsole plus the local MySQL Innovation service.
set -eu

docker_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname "$docker_dir")
env_file="$docker_dir/.env"
legacy_env_file="$project_dir/.env"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose "$@"; }
else
  echo "Docker Compose is required. Install Docker Desktop, then retry." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop, then retry." >&2
  exit 1
fi

if [ ! -f "$env_file" ] && [ -f "$legacy_env_file" ]; then
  mv "$legacy_env_file" "$env_file"
  chmod 600 "$env_file"
  printf '%s\n' "Moved the local Docker password file to docker/.env."
fi

if [ ! -f "$env_file" ]; then
  umask 077
  printf '%s\n' "Create a password for the local MySQL root account."
  printf '%s' "Password: "
  stty -echo
  IFS= read -r mysql_password
  stty echo
  printf '\n%s' "Confirm password: "
  stty -echo
  IFS= read -r mysql_password_confirm
  stty echo
  printf '\n'

  if [ -z "$mysql_password" ] || [ "$mysql_password" != "$mysql_password_confirm" ]; then
    unset mysql_password mysql_password_confirm
    echo "Passwords were empty or did not match; no .env file was created." >&2
    exit 1
  fi

  printf 'DBCONSOLE_LOCAL_ADMIN_PASSWORD=%s\n' "$mysql_password" > "$env_file"
  chmod 600 "$env_file"
  unset mysql_password mysql_password_confirm
fi

cd "$docker_dir"
compose pull mysql
compose up --build --detach --force-recreate
compose exec -T dbconsole python3 -c '
import json
from pathlib import Path

socket_path = Path("/var/run/mysqld/mysqld.sock")
if not socket_path.is_socket():
    raise SystemExit(f"MySQL socket is unavailable: {socket_path}")
profiles = json.loads(Path("/var/lib/dbconsole/profiles.json").read_text(encoding="utf-8")).get("profiles", [])
if not any(item.get("name") == "local-admin-profile" and item.get("socket_enabled") and item.get("socket_path") == str(socket_path) for item in profiles if isinstance(item, dict)):
    raise SystemExit("The socket-backed local-admin-profile was not created.")
'
compose ps
printf '%s\n' "DBConsole is available at http://127.0.0.1:8080"
printf '%s\n' "Sign in with local-admin-profile, user root, and the password in docker/.env."
