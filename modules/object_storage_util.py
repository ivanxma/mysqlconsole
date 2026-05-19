import json

from modules.core_util import chmod_private_file


DEFAULT_OBJECT_STORAGE = {
    "region": "",
    "namespace": "",
    "bucket_name": "",
    "bucket_prefix": "",
    "config_profile": "DEFAULT",
}


def normalize_object_storage(payload):
    payload = payload or {}
    return {
        "region": str(payload.get("region", "")).strip(),
        "namespace": str(payload.get("namespace", "")).strip(),
        "bucket_name": str(payload.get("bucket_name", "")).strip(),
        "bucket_prefix": str(payload.get("bucket_prefix", "")).strip(),
        "config_profile": str(payload.get("config_profile", "")).strip() or DEFAULT_OBJECT_STORAGE["config_profile"],
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
    return {
        "configured": not missing,
        "missing_fields": missing,
        "summary": "Configured" if not missing else f"Missing {', '.join(missing)}",
    }
