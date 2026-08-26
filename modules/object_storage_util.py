import json
import re
from datetime import date
from uuid import uuid4

from modules.core_util import chmod_private_file
from modules.oci_util import (
    DEFAULT_MAX_UPLOAD_BYTES,
    MAX_CONFIGURABLE_UPLOAD_BYTES,
    build_object_storage_uri,
    create_object_storage_folder as oci_create_object_storage_folder,
    list_object_storage_files as oci_list_object_storage_files,
    list_object_storage_folders as oci_list_object_storage_folders,
    test_instance_principal_access as oci_test_instance_principal_access,
    upload_object_storage_file as oci_upload_object_storage_file,
    validate_object_storage_upload as oci_validate_object_storage_upload,
)


DEFAULT_OBJECT_STORAGE_PROFILE = {
    "profile_name": "DEFAULT",
    "region": "",
    "namespace": "",
    "bucket_name": "",
    "bucket_prefix": "",
    "upload_validation_max_bytes": DEFAULT_MAX_UPLOAD_BYTES,
}
LEGACY_OBJECT_STORAGE_MIGRATION_CUTOFF = date(2027, 2, 28)


def normalize_region(value):
    region = str(value or "").strip().lower()
    if region and not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Object Storage region must use only lowercase letters, numbers, and hyphens.")
    return region


def normalize_upload_validation_max_bytes(payload):
    raw_mib = (payload or {}).get("upload_validation_max_mib")
    raw_bytes = (payload or {}).get("upload_validation_max_bytes")
    value = raw_mib if raw_mib not in (None, "") else raw_bytes
    if value in (None, ""):
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        max_bytes = int(value) * 1024 * 1024 if raw_mib not in (None, "") else int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Upload validation limit must be a whole number of MiB.") from error
    if max_bytes < 1024 * 1024 or max_bytes > MAX_CONFIGURABLE_UPLOAD_BYTES:
        raise ValueError(
            f"Upload validation limit must be between 1 MiB and {MAX_CONFIGURABLE_UPLOAD_BYTES // (1024 * 1024)} MiB."
        )
    return max_bytes


def normalize_object_prefix(value):
    prefix = str(value or "").strip().strip("/")
    if not prefix:
        return ""
    if "\\" in prefix or any(ord(character) < 32 for character in prefix):
        raise ValueError("Object Storage prefix contains invalid characters.")
    segments = prefix.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("Object Storage prefix must contain valid relative path segments.")
    return "/".join(segments)


def _legacy_value(payload, current_key, legacy_key):
    if payload.get(legacy_key) and date.today() > LEGACY_OBJECT_STORAGE_MIGRATION_CUTOFF:
        raise ValueError(
            f"The legacy `{legacy_key}` Object Storage setting is no longer supported; migrate to `{current_key}`."
        )
    return payload.get(current_key) or payload.get(legacy_key) or ""


def _normalize_object_storage_entry(payload, *, default_region=""):
    payload = payload or {}
    legacy_profile_key = next((key for key in ("config_profile", "oci_config_profile") if payload.get(key)), "")
    if legacy_profile_key and date.today() > LEGACY_OBJECT_STORAGE_MIGRATION_CUTOFF:
        raise ValueError("Legacy Object Storage profile aliases are no longer supported; use `profile_name`.")
    profile_name = str(
        payload.get("profile_name")
        or payload.get("config_profile")
        or payload.get("oci_config_profile")
        or DEFAULT_OBJECT_STORAGE_PROFILE["profile_name"]
    ).strip()
    if not profile_name:
        profile_name = DEFAULT_OBJECT_STORAGE_PROFILE["profile_name"]
    return {
        "profile_name": profile_name[:80],
        "region": normalize_region(_legacy_value(payload, "region", "oci_region") or default_region),
        "namespace": str(_legacy_value(payload, "namespace", "oci_namespace")).strip(),
        "bucket_name": str(payload.get("bucket_name") or "").strip(),
        "bucket_prefix": normalize_object_prefix(payload.get("bucket_prefix") or ""),
        "upload_validation_max_bytes": normalize_upload_validation_max_bytes(payload),
    }


def normalize_object_storage(payload, *, default_region=""):
    return _normalize_object_storage_entry(payload, default_region=default_region)


def _profile_key(profile_name):
    return str(profile_name or "").strip().lower()


def _is_empty_default_profile(profile):
    return (
        _profile_key(profile.get("profile_name")) == _profile_key(DEFAULT_OBJECT_STORAGE_PROFILE["profile_name"])
        and not profile.get("namespace")
        and not profile.get("bucket_name")
        and not profile.get("bucket_prefix")
    )


def _dedupe_profiles(profiles, *, default_region=""):
    ordered_keys = []
    by_key = {}
    for profile in profiles:
        normalized = _normalize_object_storage_entry(profile, default_region=default_region)
        key = _profile_key(normalized["profile_name"])
        if not key:
            continue
        if key not in by_key:
            ordered_keys.append(key)
        by_key[key] = normalized
    return [by_key[key] for key in ordered_keys]


def normalize_object_storage_store(payload, *, default_region=""):
    payload = payload if isinstance(payload, dict) else {}
    raw_profiles = payload.get("profiles")
    profiles = [row for row in raw_profiles if isinstance(row, dict)] if isinstance(raw_profiles, list) else []
    if not profiles:
        profiles = [payload]
    profiles = _dedupe_profiles(profiles, default_region=default_region)
    if not profiles:
        profiles = [_normalize_object_storage_entry(DEFAULT_OBJECT_STORAGE_PROFILE, default_region=default_region)]

    active_name = str(
        payload.get("active_profile_name")
        or payload.get("profile_name")
        or payload.get("config_profile")
        or payload.get("oci_config_profile")
        or ""
    ).strip()
    active_profile = next(
        (profile for profile in profiles if _profile_key(profile["profile_name"]) == _profile_key(active_name)),
        profiles[0],
    )
    return {
        **active_profile,
        "active_profile_name": active_profile["profile_name"],
        "profiles": profiles,
    }


def _load_object_storage_payload(store_path):
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_object_storage_store(store_path, store, *, default_region=""):
    normalized_store = normalize_object_storage_store(store, default_region=default_region)
    temp_path = store_path.with_name(f".{store_path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(normalized_store, indent=2) + "\n", encoding="utf-8")
    chmod_private_file(temp_path)
    temp_path.replace(store_path)
    chmod_private_file(store_path)
    return normalized_store


def ensure_object_storage_store(store_path, *, default_region=""):
    payload = _load_object_storage_payload(store_path) if store_path.exists() else {}
    normalized = normalize_object_storage_store(payload, default_region=default_region)
    if not store_path.exists() or payload != normalized:
        _write_object_storage_store(store_path, normalized, default_region=default_region)
    else:
        chmod_private_file(store_path)


def load_object_storage_config(store_path, *, default_region=""):
    ensure_object_storage_store(store_path, default_region=default_region)
    return normalize_object_storage_store(_load_object_storage_payload(store_path), default_region=default_region)


def select_object_storage_config(store_path, profile_name, *, default_region=""):
    current_store = load_object_storage_config(store_path, default_region=default_region)
    selected_name = str(profile_name or "").strip()
    if not selected_name:
        return current_store
    selected_profile = next(
        (row for row in current_store["profiles"] if _profile_key(row["profile_name"]) == _profile_key(selected_name)),
        None,
    )
    if not selected_profile:
        raise ValueError(f"Object Storage profile `{selected_name}` was not found.")
    return {
        **selected_profile,
        "active_profile_name": current_store["active_profile_name"],
        "profiles": current_store["profiles"],
    }


def save_object_storage_config(store_path, payload, *, default_region=""):
    current_store = load_object_storage_config(store_path, default_region=default_region)
    profile = validate_object_storage_target(
        _normalize_object_storage_entry(payload, default_region=default_region)
    )
    profile_key = _profile_key(profile["profile_name"])
    profiles = [
        row
        for row in current_store["profiles"]
        if _profile_key(row["profile_name"]) != profile_key
        and not (_is_empty_default_profile(row) and profile_key != _profile_key(DEFAULT_OBJECT_STORAGE_PROFILE["profile_name"]))
    ]
    profiles.append(profile)
    return _write_object_storage_store(
        store_path,
        {**profile, "active_profile_name": profile["profile_name"], "profiles": profiles},
        default_region=default_region,
    )


def set_active_object_storage_profile(store_path, profile_name, *, default_region=""):
    current_store = load_object_storage_config(store_path, default_region=default_region)
    selected_key = _profile_key(profile_name)
    selected_profile = next(
        (row for row in current_store["profiles"] if _profile_key(row["profile_name"]) == selected_key),
        None,
    )
    if not selected_profile:
        raise ValueError(f"Object Storage profile `{profile_name}` was not found.")
    return _write_object_storage_store(
        store_path,
        {**selected_profile, "active_profile_name": selected_profile["profile_name"], "profiles": current_store["profiles"]},
        default_region=default_region,
    )


def delete_object_storage_profile(store_path, profile_name, *, default_region=""):
    current_store = load_object_storage_config(store_path, default_region=default_region)
    selected_key = _profile_key(profile_name)
    profiles = [row for row in current_store["profiles"] if _profile_key(row["profile_name"]) != selected_key]
    if len(profiles) == len(current_store["profiles"]):
        raise ValueError(f"Object Storage profile `{profile_name}` was not found.")
    if not profiles:
        raise ValueError("At least one Object Storage profile must remain configured.")
    active_profile = current_store
    if _profile_key(current_store["active_profile_name"]) == selected_key:
        active_profile = profiles[0]
    return _write_object_storage_store(
        store_path,
        {**active_profile, "active_profile_name": active_profile["profile_name"], "profiles": profiles},
        default_region=default_region,
    )


def fetch_setup_status(store_path, *, default_region=""):
    config = load_object_storage_config(store_path, default_region=default_region)
    missing = [key for key in ("region", "namespace", "bucket_name") if not config.get(key)]
    return {
        "configured": not missing,
        "missing_fields": missing,
        "summary": "Configured" if not missing else f"Missing {', '.join(missing)}",
    }


def validate_object_storage_target(config):
    normalized = _normalize_object_storage_entry(config)
    missing = [key for key in ("profile_name", "region", "namespace", "bucket_name") if not normalized.get(key)]
    if missing:
        raise ValueError("Object Storage configuration is missing: " + ", ".join(missing))
    return normalized


def normalize_folder_for_target(config, folder_prefix):
    target = validate_object_storage_target(config)
    base_prefix = target["bucket_prefix"]
    folder = normalize_object_prefix(folder_prefix)
    if not folder:
        return base_prefix
    if base_prefix and folder != base_prefix and not folder.startswith(base_prefix + "/"):
        raise ValueError("Object Storage folder must remain within the configured bucket prefix.")
    return folder


def test_instance_principal_access(config):
    return oci_test_instance_principal_access(validate_object_storage_target(config))


def build_object_storage_prefix_uri(namespace, bucket_name, prefix=""):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    if not namespace_value or not bucket_value:
        return ""
    prefix_value = normalize_object_prefix(prefix)
    if prefix_value:
        return build_object_storage_uri(namespace_value, bucket_value, f"{prefix_value}/")
    return f"oci://{bucket_value}@{namespace_value}/"


def list_object_storage_folders(config):
    target = validate_object_storage_target(config)
    return oci_list_object_storage_folders(
        target,
        namespace=target["namespace"],
        bucket_name=target["bucket_name"],
        base_prefix=target["bucket_prefix"],
    )


def list_object_storage_files(config, folder_prefix):
    target = validate_object_storage_target(config)
    folder = normalize_folder_for_target(target, folder_prefix)
    return oci_list_object_storage_files(
        target,
        namespace=target["namespace"],
        bucket_name=target["bucket_name"],
        folder_prefix=folder,
    )


def validate_object_storage_upload(config, folder_prefix, upload_storage):
    target = validate_object_storage_target(config)
    folder = normalize_folder_for_target(target, folder_prefix)
    upload = oci_validate_object_storage_upload(
        upload_storage,
        max_upload_bytes=target["upload_validation_max_bytes"],
    )
    return {"target": target, "folder": folder, "upload": upload}


def create_object_storage_folder(config, parent_prefix, folder_name):
    target = validate_object_storage_target(config)
    parent = normalize_folder_for_target(target, parent_prefix)
    return oci_create_object_storage_folder(
        target,
        namespace=target["namespace"],
        bucket_name=target["bucket_name"],
        parent_prefix=parent,
        folder_name=folder_name,
    )


def upload_object_storage_file(config, folder_prefix, upload_storage, *, validated=None):
    prepared = validated or validate_object_storage_upload(config, folder_prefix, upload_storage)
    target = prepared["target"]
    return oci_upload_object_storage_file(
        target,
        namespace=target["namespace"],
        bucket_name=target["bucket_name"],
        folder_prefix=prepared["folder"],
        upload_storage=upload_storage,
        validated_upload=prepared["upload"],
    )
