from pathlib import Path


DEFAULT_OCI_CONFIG = {
    "oci_config_source": "user_folder",
    "oci_user_folder": "~/.oci",
    "oci_config_file": "~/.oci/config",
    "oci_config_profile": "DEFAULT",
}


def normalize_oci_config(payload):
    payload = payload or {}
    config_source = str(payload.get("oci_config_source", DEFAULT_OCI_CONFIG["oci_config_source"])).strip()
    if config_source not in {"user_folder", "config_file"}:
        config_source = DEFAULT_OCI_CONFIG["oci_config_source"]
    user_folder = str(payload.get("oci_user_folder", DEFAULT_OCI_CONFIG["oci_user_folder"])).strip()
    config_file = str(payload.get("oci_config_file", DEFAULT_OCI_CONFIG["oci_config_file"])).strip()
    profile = str(payload.get("oci_config_profile", payload.get("config_profile", ""))).strip()
    return {
        "oci_config_source": config_source,
        "oci_user_folder": user_folder or DEFAULT_OCI_CONFIG["oci_user_folder"],
        "oci_config_file": config_file or DEFAULT_OCI_CONFIG["oci_config_file"],
        "oci_config_profile": profile or DEFAULT_OCI_CONFIG["oci_config_profile"],
    }


def effective_oci_config_file(config):
    normalized = normalize_oci_config(config)
    if normalized["oci_config_source"] == "config_file":
        return normalized["oci_config_file"]
    return str(Path(normalized["oci_user_folder"]).expanduser() / "config")


def build_oci_sdk_config_kwargs(config):
    normalized = normalize_oci_config(config)
    return {
        "file_location": effective_oci_config_file(normalized),
        "profile_name": normalized["oci_config_profile"],
    }
