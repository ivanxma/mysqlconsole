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
  printf '%s\n' 'DBCONSOLE_LOCAL_ADMIN_PASSWORD=ChangeMe123!' > "$env_file"
  chmod 600 "$env_file"
  printf '%s\n' "Created docker/.env with the default localadmin password ChangeMe123!. Change it after first login."
fi

cd "$docker_dir"
compose pull mysql
compose up --build --detach --force-recreate
localadmin_password_b64="$(compose exec -T mysql sh -ec 'printf %s "$MYSQL_ROOT_PASSWORD" | base64 | tr -d "\n"')"
printf '%s\n' "SET @dbconsole_password = CONVERT(FROM_BASE64('$localadmin_password_b64') USING utf8mb4);" \
  "SET @dbconsole_create = CONCAT(\"CREATE USER IF NOT EXISTS 'localadmin'@'localhost' IDENTIFIED BY \", QUOTE(@dbconsole_password));" \
  'PREPARE dbconsole_stmt FROM @dbconsole_create; EXECUTE dbconsole_stmt; DEALLOCATE PREPARE dbconsole_stmt;' \
  "SET @dbconsole_alter = CONCAT(\"ALTER USER 'localadmin'@'localhost' IDENTIFIED BY \", QUOTE(@dbconsole_password));" \
  'PREPARE dbconsole_stmt FROM @dbconsole_alter; EXECUTE dbconsole_stmt; DEALLOCATE PREPARE dbconsole_stmt;' \
  "GRANT ALL PRIVILEGES ON *.* TO 'localadmin'@'localhost' WITH GRANT OPTION;" \
  "DROP USER IF EXISTS 'root'@'%'; FLUSH PRIVILEGES;" \
  | compose exec -T mysql sh -ec 'mysql --socket=/var/run/mysqld/mysqld.sock -uroot -p"$MYSQL_ROOT_PASSWORD"'
network_status="$(compose exec -T mysql sh -ec 'mysql --batch --skip-column-names --socket=/var/run/mysqld/mysqld.sock -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT @@skip_networking"')"
if [ "$network_status" != "1" ]; then
  echo "MySQL classic TCP networking is still enabled: $network_status" >&2
  exit 1
fi
if ! compose exec -T mysql sh -ec 'awk "NR > 1 && \$4 == \"0A\" && (\$2 ~ /:0CEA$/ || \$2 ~ /:8114$/) { found = 1 } END { exit found }" /proc/net/tcp /proc/net/tcp6'; then
  echo "MySQL classic or X Protocol TCP listener is still enabled." >&2
  exit 1
fi
compose exec -T mysql sh -ec 'mysql --socket=/var/run/mysqld/mysqld.sock -ulocaladmin -p"$MYSQL_ROOT_PASSWORD" -e "SELECT 1" >/dev/null'
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
printf '%s\n' "Sign in with local-admin-profile, user localadmin, and the password in docker/.env. Change the default ChangeMe123! password immediately."
