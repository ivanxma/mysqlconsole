import json

from modules.core_util import chmod_private_file
from modules.oci_util import DEFAULT_OCI_CONFIG, effective_oci_config_file, normalize_oci_config


DEFAULT_OBJECT_STORAGE = {
    **DEFAULT_OCI_CONFIG,
    "region": "",
    "namespace": "",
    "bucket_name": "",
    "bucket_prefix": "",
    "config_profile": "DEFAULT",
}


def normalize_object_storage(payload):
    payload = payload or {}
    oci_config = normalize_oci_config(payload)
    return {
        **oci_config,
        "region": str(payload.get("region", oci_config["oci_region"])).strip(),
        "namespace": str(payload.get("namespace", oci_config["oci_namespace"])).strip(),
        "bucket_name": str(payload.get("bucket_name", "")).strip(),
        "bucket_prefix": str(payload.get("bucket_prefix", "")).strip(),
        "config_profile": str(payload.get("config_profile", oci_config["oci_config_profile"])).strip()
        or DEFAULT_OBJECT_STORAGE["config_profile"],
        "effective_oci_config_file": effective_oci_config_file(oci_config),
    }


def ensure_object_storage_store(store_path):
    if store_path.exists():
        chmod_private_file(store_path)
        return
    store_path.write_text(json.dumps(DEFAULT_OBJECT_STORAGE, indent=2), encoding="utf-8")
    chmod_private_file(store_path)


def load_object_storage_config(store_path):
    ensure_object_storage_store(store_path)
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(DEFAULT_OBJECT_STORAGE)
    normalized = normalize_object_storage(payload)
    if not normalized["config_profile"]:
        normalized["config_profile"] = DEFAULT_OBJECT_STORAGE["config_profile"]
    return normalized


def save_object_storage_config(store_path, payload):
    store_path.write_text(json.dumps(normalize_object_storage(payload), indent=2), encoding="utf-8")
    chmod_private_file(store_path)


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
