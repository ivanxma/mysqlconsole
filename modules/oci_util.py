from pathlib import Path
import re
from uuid import uuid4

from modules.core_util import chmod_private_file


DEFAULT_OCI_CONFIG = {
    "oci_config_source": "user_folder",
    "oci_user_folder": "~/.oci",
    "oci_config_file": "~/.oci/config",
    "oci_config_profile": "DEFAULT",
    "oci_user": "",
    "oci_fingerprint": "",
    "oci_tenancy": "",
    "oci_region": "",
    "oci_key_file": "",
    "oci_compartment": "",
    "oci_namespace": "",
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
        "oci_user": str(payload.get("oci_user", "")).strip(),
        "oci_fingerprint": str(payload.get("oci_fingerprint", "")).strip(),
        "oci_tenancy": str(payload.get("oci_tenancy", "")).strip(),
        "oci_region": str(payload.get("oci_region", payload.get("region", ""))).strip(),
        "oci_key_file": str(payload.get("oci_key_file", "")).strip(),
        "oci_compartment": str(payload.get("oci_compartment", "")).strip(),
        "oci_namespace": str(payload.get("oci_namespace", payload.get("namespace", ""))).strip(),
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


def safe_oci_profile_dir_name(profile_name):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(profile_name or "").strip()).strip("._")
    if not cleaned:
        cleaned = "DEFAULT"
    return cleaned[:80]


def save_uploaded_oci_private_key(key_root_dir, profile_name, upload_storage):
    if upload_storage is None or not getattr(upload_storage, "filename", ""):
        return ""
    key_payload = upload_storage.read()
    if not key_payload:
        raise ValueError("Uploaded OCI private key file is empty.")
    if len(key_payload) > 131072:
        raise ValueError("Uploaded OCI private key file is too large.")
    key_text = key_payload.decode("utf-8", errors="ignore")
    if "PRIVATE KEY" not in key_text:
        raise ValueError("Upload a valid OCI private key file.")

    profile_dir = key_root_dir / safe_oci_profile_dir_name(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        key_root_dir.chmod(0o700)
        profile_dir.chmod(0o700)
    except OSError:
        pass

    key_path = profile_dir / "oci_api_private_key.pem"
    temp_path = profile_dir / f".{uuid4().hex}.tmp"
    temp_path.write_bytes(key_payload)
    temp_path.chmod(0o600)
    temp_path.replace(key_path)
    chmod_private_file(key_path)
    return str(key_path)


def build_oci_config_text(config):
    normalized = normalize_oci_config(config)
    profile = normalized["oci_config_profile"] or "DEFAULT"
    lines = [
        f"[{profile}]",
        f"user={normalized['oci_user']}",
        f"fingerprint={normalized['oci_fingerprint']}",
        f"tenancy={normalized['oci_tenancy']}",
        f"region={normalized['oci_region']}",
        f"key_file={normalized['oci_key_file']}",
    ]
    if normalized["oci_compartment"]:
        lines.append(f"compartment={normalized['oci_compartment']}")
    if normalized["oci_namespace"]:
        lines.append(f"namespace={normalized['oci_namespace']}")
    return "\n".join(lines) + "\n"
