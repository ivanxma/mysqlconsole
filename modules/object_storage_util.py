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


def normalize_object_storage(payload):
    payload = payload or {}
    oci_config = normalize_oci_config(payload)
    region = str(payload.get("region") or oci_config["oci_region"]).strip()
    namespace = str(payload.get("namespace") or oci_config["oci_namespace"]).strip()
    config_profile = str(payload.get("config_profile") or oci_config["oci_config_profile"]).strip()
    return {
        **oci_config,
        "region": region,
        "namespace": namespace,
        "bucket_name": str(payload.get("bucket_name", "")).strip(),
        "bucket_prefix": str(payload.get("bucket_prefix", "")).strip(),
        "config_profile": config_profile or DEFAULT_OBJECT_STORAGE["config_profile"],
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
