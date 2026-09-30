#!/bin/sh
set -eu

state_dir="${DBCONSOLE_STATE_DIR:-/var/lib/dbconsole}"
profile_store="$state_dir/profiles.json"

mkdir -p "$state_dir"

python3 - "$profile_store" <<'PY'
import json
import os
import sys
from pathlib import Path

profile_store = Path(sys.argv[1])
profile = {
    "name": "local-admin-profile",
    "host": "",
    "port": 3306,
    "database": "mysql",
    "username": "localadmin",
    "ssl_mode": "DISABLED",
    "socket_enabled": True,
    "socket_path": os.environ.get("DBCONSOLE_LOCAL_MYSQL_SOCKET", "/var/run/mysqld/mysqld.sock"),
    "ssh_enabled": False,
    "require_password_change": True,
}

try:
    payload = json.loads(profile_store.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    payload = {"profiles": []}

profiles = payload.get("profiles")
if not isinstance(profiles, list):
    profiles = []
profile_key = profile["name"].lower()
updated_profiles = []
profile_found = False
for item in profiles:
    if isinstance(item, dict) and item.get("name", "").strip().lower() == profile_key:
        reconciled_profile = {**item, **profile}
        if item.get("socket_enabled") and item.get("username") == profile["username"]:
            reconciled_profile["require_password_change"] = bool(item.get("require_password_change"))
        updated_profiles.append(reconciled_profile)
        profile_found = True
    else:
        updated_profiles.append(item)
if not profile_found:
    updated_profiles.append(profile)

if updated_profiles != profiles:
    profiles = updated_profiles
    payload["profiles"] = profiles
    temporary = profile_store.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(profile_store)
PY

exec "$@"
