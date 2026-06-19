import json

from modules.core_util import chmod_private_file
from modules.oci_util import (
    DEFAULT_OCI_CONFIG,
    build_object_storage_uri,
    create_object_storage_folder as oci_create_object_storage_folder,
    effective_oci_config_file,
    list_object_storage_files as oci_list_object_storage_files,
    list_object_storage_folders as oci_list_object_storage_folders,
    normalize_oci_config,
    upload_object_storage_file as oci_upload_object_storage_file,
)


DEFAULT_OBJECT_STORAGE = {
    **DEFAULT_OCI_CONFIG,
    "region": "",
    "namespace": "",
    "bucket_name": "",
    "bucket_prefix": "",
    "config_profile": "DEFAULT",
}


def _normalize_object_storage_entry(payload):
    payload = payload or {}
    profile_name = str(
        payload.get("config_profile")
        or payload.get("oci_config_profile")
        or payload.get("profile_name")
        or DEFAULT_OBJECT_STORAGE["config_profile"]
    ).strip()
    if not profile_name:
        profile_name = DEFAULT_OBJECT_STORAGE["config_profile"]
    oci_payload = dict(payload)
    oci_payload["oci_config_profile"] = profile_name
    oci_config = normalize_oci_config(oci_payload)
    region = str(oci_config["oci_region"] or payload.get("region")).strip()
    namespace = str(payload.get("namespace") or oci_config["oci_namespace"]).strip()
    return {
        **oci_config,
        "region": region,
        "namespace": namespace,
        "bucket_name": str(payload.get("bucket_name", "")).strip(),
        "bucket_prefix": str(payload.get("bucket_prefix", "")).strip(),
        "config_profile": profile_name,
        "profile_name": profile_name,
        "effective_oci_config_file": effective_oci_config_file(oci_config),
    }


def normalize_object_storage(payload):
    return _normalize_object_storage_entry(payload)


def _profile_key(profile_name):
    return str(profile_name or "").strip().lower()


def _dedupe_profiles(profiles):
    ordered_keys = []
    by_key = {}
    for profile in profiles:
        normalized = _normalize_object_storage_entry(profile)
        key = _profile_key(normalized["profile_name"])
        if not key:
            continue
        if key not in by_key:
            ordered_keys.append(key)
        by_key[key] = normalized
    return [by_key[key] for key in ordered_keys]


def normalize_object_storage_store(payload):
    payload = payload or {}
    profiles = []
    raw_profiles = payload.get("profiles")
    if isinstance(raw_profiles, list):
        profiles = [row for row in raw_profiles if isinstance(row, dict)]
    if not profiles:
        profiles = [_normalize_object_storage_entry(payload)]
    profiles = _dedupe_profiles(profiles)
    if not profiles:
        profiles = [_normalize_object_storage_entry(DEFAULT_OBJECT_STORAGE)]

    active_name = str(
        payload.get("active_profile_name")
        or payload.get("config_profile")
        or payload.get("oci_config_profile")
        or payload.get("profile_name")
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


def ensure_object_storage_store(store_path):
    if store_path.exists():
        chmod_private_file(store_path)
        return
    default_store = normalize_object_storage_store(DEFAULT_OBJECT_STORAGE)
    store_path.write_text(json.dumps(default_store, indent=2), encoding="utf-8")
    chmod_private_file(store_path)


def _load_object_storage_payload(store_path):
    try:
        return json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_OBJECT_STORAGE)


def _write_object_storage_store(store_path, store):
    normalized_store = normalize_object_storage_store(store)
    store_path.write_text(json.dumps(normalized_store, indent=2), encoding="utf-8")
    chmod_private_file(store_path)
    return normalized_store


def load_object_storage_config(store_path):
    ensure_object_storage_store(store_path)
    return normalize_object_storage_store(_load_object_storage_payload(store_path))


def select_object_storage_config(store_path, profile_name):
    current_store = load_object_storage_config(store_path)
    selected_name = str(profile_name or "").strip()
    if not selected_name:
        return current_store
    selected_profile = next(
        (
            row
            for row in current_store["profiles"]
            if _profile_key(row["profile_name"]) == _profile_key(selected_name)
        ),
        None,
    )
    if not selected_profile:
        raise ValueError(f"Object Storage profile `{selected_name}` was not found.")
    return {
        **selected_profile,
        "active_profile_name": current_store["active_profile_name"],
        "profiles": current_store["profiles"],
    }


def save_object_storage_config(store_path, payload):
    ensure_object_storage_store(store_path)
    current_store = normalize_object_storage_store(_load_object_storage_payload(store_path))
    profile = _normalize_object_storage_entry(payload)
    profile_key = _profile_key(profile["profile_name"])
    profiles = [
        row for row in current_store["profiles"] if _profile_key(row["profile_name"]) != profile_key
    ]
    profiles.append(profile)
    return _write_object_storage_store(
        store_path,
        {
            **profile,
            "active_profile_name": profile["profile_name"],
            "profiles": profiles,
        },
    )


def set_active_object_storage_profile(store_path, profile_name):
    ensure_object_storage_store(store_path)
    current_store = normalize_object_storage_store(_load_object_storage_payload(store_path))
    selected_key = _profile_key(profile_name)
    selected_profile = next(
        (row for row in current_store["profiles"] if _profile_key(row["profile_name"]) == selected_key),
        None,
    )
    if not selected_profile:
        raise ValueError(f"OCI config profile `{profile_name}` was not found.")
    return _write_object_storage_store(
        store_path,
        {
            **selected_profile,
            "active_profile_name": selected_profile["profile_name"],
            "profiles": current_store["profiles"],
        },
    )


def delete_object_storage_profile(store_path, profile_name):
    ensure_object_storage_store(store_path)
    current_store = normalize_object_storage_store(_load_object_storage_payload(store_path))
    selected_key = _profile_key(profile_name)
    profiles = [row for row in current_store["profiles"] if _profile_key(row["profile_name"]) != selected_key]
    if len(profiles) == len(current_store["profiles"]):
        raise ValueError(f"OCI config profile `{profile_name}` was not found.")
    if not profiles:
        raise ValueError("At least one OCI config profile must remain configured.")
    active_profile = current_store
    if _profile_key(current_store["active_profile_name"]) == selected_key:
        active_profile = profiles[0]
    return _write_object_storage_store(
        store_path,
        {
            **active_profile,
            "active_profile_name": active_profile["profile_name"],
            "profiles": profiles,
        },
    )


def fetch_setup_status(store_path):
    config = load_object_storage_config(store_path)
    missing = [key for key in ("region", "namespace", "bucket_name") if not config.get(key)]
    oci_missing = []
    if config.get("oci_config_source") == "config_file" and not config.get("oci_config_file"):
        oci_missing.append("oci_config_file")
    required_oci_keys = ["oci_config_profile"]
    if config.get("oci_config_source") != "config_file":
        required_oci_keys.extend(["oci_user", "oci_fingerprint", "oci_tenancy", "oci_region", "oci_key_file"])
    for key in required_oci_keys:
        if not config.get(key):
            oci_missing.append(key)
    return {
        "configured": not missing and not oci_missing,
        "missing_fields": missing,
        "oci_missing_fields": oci_missing,
        "summary": "Configured" if not missing and not oci_missing else f"Missing {', '.join(missing + oci_missing)}",
    }


def build_object_storage_prefix_uri(namespace, bucket_name, prefix=""):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    if not namespace_value or not bucket_value:
        return ""
    prefix_value = str(prefix or "").strip().strip("/")
    if prefix_value:
        return build_object_storage_uri(namespace_value, bucket_value, f"{prefix_value}/")
    return f"oci://{bucket_value}@{namespace_value}/"


def list_object_storage_folders(config):
    return oci_list_object_storage_folders(
        config,
        namespace=config.get("namespace"),
        bucket_name=config.get("bucket_name"),
        base_prefix=config.get("bucket_prefix"),
    )


def list_object_storage_files(config, folder_prefix):
    return oci_list_object_storage_files(
        config,
        namespace=config.get("namespace"),
        bucket_name=config.get("bucket_name"),
        folder_prefix=folder_prefix,
    )


def create_object_storage_folder(config, parent_prefix, folder_name):
    return oci_create_object_storage_folder(
        config,
        namespace=config.get("namespace"),
        bucket_name=config.get("bucket_name"),
        parent_prefix=parent_prefix,
        folder_name=folder_name,
    )


def upload_object_storage_file(config, folder_prefix, upload_storage):
    return oci_upload_object_storage_file(
        config,
        namespace=config.get("namespace"),
        bucket_name=config.get("bucket_name"),
        folder_prefix=folder_prefix,
        upload_storage=upload_storage,
    )
