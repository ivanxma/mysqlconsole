"""Private reusable MySQL Shell dump/load option profiles."""
import json
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from modules.runtime_util import atomic_write_private_text, ensure_private_directory, ensure_private_regular_file


PROFILE_KINDS = {"dump", "load"}
MAX_PROFILE_NAME_LENGTH = 80
MAX_OPTIONS_BYTES = 64 * 1024


@contextmanager
def _store_lock(store_path):
    lock_path = Path(str(store_path) + ".lock")
    ensure_private_directory(lock_path.parent)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _kind(kind):
    value = str(kind or "").strip().lower()
    if value not in PROFILE_KINDS:
        raise ValueError("Option profile type must be dump or load.")
    return value


def _normalize_options(options):
    if not isinstance(options, dict):
        raise ValueError("MySQL Shell options must be a JSON object.")
    encoded = json.dumps(options, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_OPTIONS_BYTES:
        raise ValueError("MySQL Shell option profile is too large.")
    if len(options) > 100:
        raise ValueError("MySQL Shell option profile may contain at most 100 options.")
    forbidden = {"osbucketname", "osnamespace", "ociconfigfile", "ociprofile", "progressfile"}
    for key in options:
        name = str(key or "").strip()
        if not name or len(name) > 80:
            raise ValueError("MySQL Shell option names must be 1 to 80 characters.")
        compact_name = name.lower().replace("_", "").replace("-", "")
        if compact_name in forbidden or "password" in compact_name or "parurl" in compact_name or "storageurl" in compact_name:
            raise ValueError(f"MySQL Shell option `{name}` is managed by DB Console and cannot be stored in a profile.")
    return json.loads(encoded)


def _normalize_entry(payload):
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise ValueError("Option profile name is required.")
    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise ValueError("Option profile name may not exceed 80 characters.")
    return {
        "name": name,
        "kind": _kind((payload or {}).get("kind")),
        "options": _normalize_options((payload or {}).get("options") or {}),
    }


def _read_store(store_path):
    path = Path(store_path)
    if not path.exists():
        return []
    ensure_private_regular_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("profiles", []) if isinstance(payload, dict) else []
    normalized = []
    for row in rows if isinstance(rows, list) else []:
        try:
            normalized.append(_normalize_entry(row))
        except ValueError:
            continue
    return normalized


def _write_store(store_path, rows):
    path = Path(store_path)
    atomic_write_private_text(path, json.dumps({"profiles": rows}, indent=2) + "\n")


def list_option_profiles(store_path, kind):
    profile_kind = _kind(kind)
    rows = [row for row in _read_store(store_path) if row["kind"] == profile_kind]
    return sorted(rows, key=lambda row: row["name"].lower())


def get_option_profile(store_path, kind, name):
    lookup = str(name or "").strip().lower()
    return next((row for row in list_option_profiles(store_path, kind) if row["name"].lower() == lookup), None)


def save_option_profile(store_path, kind, name, options):
    entry = _normalize_entry({"kind": kind, "name": name, "options": options})
    with _store_lock(store_path):
        rows = [
            row
            for row in _read_store(store_path)
            if not (row["kind"] == entry["kind"] and row["name"].lower() == entry["name"].lower())
        ]
        rows.append(entry)
        _write_store(store_path, sorted(rows, key=lambda row: (row["kind"], row["name"].lower())))
    return entry


def delete_option_profile(store_path, kind, name):
    profile_kind = _kind(kind)
    lookup = str(name or "").strip().lower()
    with _store_lock(store_path):
        rows = _read_store(store_path)
        retained = [row for row in rows if not (row["kind"] == profile_kind and row["name"].lower() == lookup)]
        if len(retained) == len(rows):
            return False
        _write_store(store_path, retained)
    return True
