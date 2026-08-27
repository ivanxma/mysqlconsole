"""Private registry for reusable Instance Principal PARs used by mysqlsh."""
import json
import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from modules import oci_util
from modules.object_storage_util import normalize_object_prefix, validate_object_storage_target
from modules.runtime_util import atomic_write_private_text, ensure_private_directory, ensure_private_regular_file


ACCESS_TYPES = {"AnyObjectRead", "AnyObjectReadWrite"}
PURPOSE_ACCESS = {
    "dump": {"AnyObjectReadWrite"},
    "load": {"AnyObjectRead", "AnyObjectReadWrite"},
}


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


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _normalize_entry(payload):
    target = payload or {}
    access_type = str(target.get("access_type") or "AnyObjectReadWrite")
    if access_type not in ACCESS_TYPES:
        raise ValueError("Unsupported PAR access type.")
    entry = {
        "id": str(target.get("id") or uuid4().hex),
        "par_id": str(target.get("par_id") or ""),
        "name": str(target.get("name") or "").strip()[:120],
        "profile_name": str(target.get("profile_name") or ""),
        "region": str(target.get("region") or ""),
        "namespace": str(target.get("namespace") or ""),
        "bucket_name": str(target.get("bucket_name") or ""),
        "bucket_prefix": str(target.get("bucket_prefix") or ""),
        "prefix": normalize_object_prefix(target.get("prefix") or ""),
        "access_type": access_type,
        "created_at": str(target.get("created_at") or ""),
        "expires_at": str(target.get("expires_at") or ""),
        "delete_after_use": bool(target.get("delete_after_use")),
        "par_url": str(target.get("par_url") or ""),
    }
    if not entry["name"]:
        raise ValueError("PAR name is required.")
    return entry


def _annotate(entry):
    row = dict(entry)
    expiry = _parse_time(row.get("expires_at"))
    row["is_active"] = bool(row.get("par_id") and row.get("par_url") and expiry and expiry > _now())
    row["status_label"] = "Active" if row["is_active"] else "Expired"
    row["target_display"] = "/".join(
        part for part in (row.get("bucket_name"), row.get("prefix")) if part
    ) or "-"
    return row


def _read_store(store_path):
    path = Path(store_path)
    if not path.exists():
        return []
    ensure_private_regular_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("pars", []) if isinstance(payload, dict) else []
    normalized = []
    for row in rows if isinstance(rows, list) else []:
        try:
            normalized.append(_normalize_entry(row))
        except ValueError:
            continue
    return normalized


def _write_store(store_path, rows):
    path = Path(store_path)
    atomic_write_private_text(path, json.dumps({"pars": rows}, indent=2) + "\n")


def purge_expired_pars(store_path):
    with _store_lock(store_path):
        rows = _read_store(store_path)
        retained = [row for row in rows if (_parse_time(row.get("expires_at")) or _now()) > _now()]
        if len(retained) != len(rows):
            _write_store(store_path, retained)
        return len(rows) - len(retained)


def list_pars(store_path, target=None, purpose=None, active_only=False):
    purge_expired_pars(store_path)
    allowed = PURPOSE_ACCESS.get(str(purpose or "").lower()) if purpose else None
    target_snapshot = validate_object_storage_target(target) if target else None
    rows = []
    for entry in _read_store(store_path):
        row = _annotate(entry)
        if target_snapshot and any(
            row.get(key) != target_snapshot.get(key)
            for key in ("region", "namespace", "bucket_name")
        ):
            continue
        if allowed and row["access_type"] not in allowed:
            continue
        if active_only and not row["is_active"]:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)


def get_par(store_path, entry_id, *, target=None, purpose=None, active_only=False):
    lookup = str(entry_id or "").strip()
    return next(
        (row for row in list_pars(store_path, target, purpose, active_only) if row["id"] == lookup),
        None,
    )


def create_par(store_path, target, *, name, prefix, access_type, delete_after_use, expiry_hours=None):
    config = validate_object_storage_target(target)
    par_name = str(name or "").strip()
    if not par_name:
        raise ValueError("PAR name is required.")
    if access_type not in ACCESS_TYPES:
        raise ValueError("Unsupported PAR access type.")
    delete_after_use = bool(delete_after_use)
    if delete_after_use:
        effective_hours = 24
    else:
        try:
            effective_hours = int(expiry_hours)
        except (TypeError, ValueError) as error:
            raise ValueError("PAR expiry is required when Delete after used is not selected.") from error
        if not 1 <= effective_hours <= 168:
            raise ValueError("PAR expiry must be between 1 and 168 hours.")
    normalized_prefix = normalize_object_prefix(prefix)
    if not normalized_prefix:
        normalized_prefix = normalize_object_prefix(config.get("bucket_prefix"))
    if not normalized_prefix:
        raise ValueError("A configured bucket prefix or PAR prefix is required.")
    base_prefix = normalize_object_prefix(config.get("bucket_prefix"))
    if base_prefix and normalized_prefix != base_prefix and not normalized_prefix.startswith(base_prefix + "/"):
        raise ValueError("PAR prefix must remain within the configured Object Storage prefix.")

    expires_at = _now() + timedelta(hours=effective_hours)
    client, created = oci_util.create_scoped_preauthenticated_request(
        config,
        namespace=config["namespace"],
        bucket_name=config["bucket_name"],
        prefix=normalized_prefix,
        name=par_name,
        access_type=access_type,
        expires_at=expires_at,
    )
    access_uri = str(getattr(created, "access_uri", "") or "").strip()
    par_id = str(getattr(created, "id", "") or "").strip()
    if not access_uri or not par_id:
        raise RuntimeError("OCI did not return a complete Pre-Authenticated Request.")
    raw_url = oci_util.resolved_object_storage_endpoint(client) + (
        access_uri if access_uri.startswith("/") else "/" + access_uri
    )
    entry = _normalize_entry(
        {
            "id": uuid4().hex,
            "par_id": par_id,
            "name": par_name,
            **{key: config[key] for key in ("profile_name", "region", "namespace", "bucket_name", "bucket_prefix")},
            "prefix": normalized_prefix,
            "access_type": access_type,
            "created_at": _now().isoformat(),
            "expires_at": expires_at.isoformat(),
            "delete_after_use": delete_after_use,
            "par_url": raw_url.rstrip("/") + "/" + normalized_prefix + "/",
        }
    )
    with _store_lock(store_path):
        rows = _read_store(store_path)
        rows.append(entry)
        _write_store(store_path, rows)
    return _annotate(entry)


def remove_par(store_path, entry_id, *, revoke=True):
    lookup = str(entry_id or "").strip()
    rows = _read_store(store_path)
    entry = next((row for row in rows if row["id"] == lookup), None)
    if entry is None:
        raise ValueError("PAR entry was not found.")
    if revoke and entry.get("par_id"):
        target = {key: entry.get(key) for key in ("profile_name", "region", "namespace", "bucket_name", "bucket_prefix")}
        oci_util.revoke_preauthenticated_request(
            target,
            namespace=entry["namespace"],
            bucket_name=entry["bucket_name"],
            par_id=entry["par_id"],
        )
    with _store_lock(store_path):
        current_rows = _read_store(store_path)
        _write_store(store_path, [row for row in current_rows if row["id"] != lookup])
    return _annotate(entry)


def remove_par_by_oci_id(store_path, par_id):
    lookup = str(par_id or "").strip()
    with _store_lock(store_path):
        rows = _read_store(store_path)
        retained = [row for row in rows if row.get("par_id") != lookup]
        if len(retained) != len(rows):
            _write_store(store_path, retained)
