from pathlib import Path
import configparser
import os
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


def list_oci_config_profiles(config_file):
    config_path = Path(os.path.expanduser(str(config_file or "").strip()))
    if not str(config_file or "").strip() or not config_path.exists():
        return []
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []
    profiles = []
    for line in text.splitlines():
        match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if match:
            profiles.append(match.group(1).strip())
    return profiles


def read_oci_config_profile(config_file, profile_name):
    config_path = Path(os.path.expanduser(str(config_file or "").strip()))
    profile = str(profile_name or "DEFAULT").strip() or "DEFAULT"
    if not config_path.exists():
        return {
            "profile": profile,
            "exists": False,
            "error": f"OCI config file was not found: {config_path}",
            "values": {},
            "section_text": "",
        }
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        return {
            "profile": profile,
            "exists": False,
            "error": str(error),
            "values": {},
            "section_text": "",
        }

    parser = configparser.RawConfigParser()
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        return {
            "profile": profile,
            "exists": False,
            "error": str(error),
            "values": {},
            "section_text": "",
        }

    if profile == "DEFAULT":
        values = dict(parser.defaults())
        exists = bool(values)
    elif parser.has_section(profile):
        values = dict(parser.items(profile))
        exists = True
    else:
        values = {}
        exists = False

    section_lines = []
    in_section = False
    header_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    for line in text.splitlines():
        match = header_pattern.match(line)
        if match:
            current_profile = match.group(1).strip()
            if current_profile == profile:
                in_section = True
                section_lines.append(line)
                continue
            if in_section:
                break
        if in_section:
            section_lines.append(line)

    return {
        "profile": profile,
        "exists": exists,
        "error": "" if exists else f"Profile `{profile}` was not found in {config_path}.",
        "values": values,
        "section_text": "\n".join(section_lines).strip(),
    }


def build_oci_config_status(config):
    normalized = normalize_oci_config(config)
    effective_file = effective_oci_config_file(normalized)
    expanded_file = os.path.expanduser(effective_file)
    default_config_file = str(Path("~/.oci/config").expanduser())
    return {
        "config_source": normalized["oci_config_source"],
        "effective_file": effective_file,
        "expanded_file": expanded_file,
        "exists": Path(expanded_file).exists(),
        "profiles": list_oci_config_profiles(effective_file),
        "default_config_file": "~/.oci/config",
        "default_profiles": list_oci_config_profiles(default_config_file),
        "active_profile": normalized["oci_config_profile"],
        "user_folder": normalized["oci_user_folder"],
    }


def validate_user_folder_oci_config(config):
    normalized = normalize_oci_config(config)
    required_fields = {
        "profile": normalized["oci_config_profile"],
        "user": normalized["oci_user"],
        "fingerprint": normalized["oci_fingerprint"],
        "tenancy": normalized["oci_tenancy"],
        "region": normalized["oci_region"],
        "key_file": normalized["oci_key_file"],
    }
    missing = [key for key, value in required_fields.items() if not value]
    if missing:
        raise ValueError("OCI user-folder config is missing: " + ", ".join(missing))
    key_file = Path(normalized["oci_key_file"]).expanduser()
    if not key_file.exists():
        raise ValueError(f"OCI private key file was not found: {key_file}")
    return normalized


def write_user_folder_oci_config(config):
    normalized = normalize_oci_config(config)
    if normalized["oci_config_source"] != "user_folder":
        return ""
    normalized = validate_user_folder_oci_config(normalized)
    config_dir = Path(normalized["oci_user_folder"]).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        config_dir.chmod(0o700)
    except OSError:
        pass
    config_path = config_dir / "config"
    config_path.write_text(build_oci_config_text(normalized), encoding="utf-8")
    chmod_private_file(config_path)
    return str(config_path)


def build_oci_sdk_config(config):
    normalized = normalize_oci_config(config)
    if normalized["oci_config_source"] == "config_file":
        try:
            import oci
        except Exception as error:
            raise RuntimeError("The OCI SDK is not installed. Run setup to install current requirements.") from error
        config_path = Path(effective_oci_config_file(normalized)).expanduser()
        if not config_path.exists():
            raise ValueError(f"OCI config file was not found: {config_path}")
        try:
            return oci.config.from_file(file_location=str(config_path), profile_name=normalized["oci_config_profile"])
        except Exception as error:
            raise ValueError(
                f"Unable to read OCI profile `{normalized['oci_config_profile']}` from `{config_path}`: {error}"
            ) from error

    write_user_folder_oci_config(normalized)
    normalized = validate_user_folder_oci_config(normalized)
    required_fields = {
        "user": normalized["oci_user"],
        "fingerprint": normalized["oci_fingerprint"],
        "tenancy": normalized["oci_tenancy"],
        "region": normalized["oci_region"],
        "key_file": normalized["oci_key_file"],
    }
    key_file = Path(required_fields["key_file"]).expanduser()
    required_fields["key_file"] = str(key_file)
    return required_fields


def test_oci_config(config):
    normalized = normalize_oci_config(config)
    try:
        import oci
    except Exception as error:
        raise RuntimeError("The OCI SDK is not installed. Run setup to install current requirements.") from error

    sdk_config = build_oci_sdk_config(normalized)
    try:
        identity_client = oci.identity.IdentityClient(sdk_config)
        user_response = identity_client.get_user(sdk_config["user"])
        tenancy_response = identity_client.get_tenancy(sdk_config["tenancy"])
        namespace_status = ""
        namespace = normalized.get("oci_namespace", "")
        if namespace:
            try:
                object_storage_client = oci.object_storage.ObjectStorageClient(sdk_config)
                namespace_response = object_storage_client.get_namespace()
                returned_namespace = str(namespace_response.data or "").strip()
                if returned_namespace and returned_namespace != namespace:
                    namespace_status = f"; namespace returned `{returned_namespace}`"
                else:
                    namespace_status = f"; namespace `{namespace}` verified"
            except Exception as error:
                namespace_status = f"; namespace check skipped: {error}"
        user_name = getattr(user_response.data, "name", "") or sdk_config["user"]
        tenancy_name = getattr(tenancy_response.data, "name", "") or sdk_config["tenancy"]
        return {
            "ok": True,
            "message": (
                f"OCI config test succeeded for user `{user_name}` in tenancy `{tenancy_name}` "
                f"using region `{sdk_config['region']}`{namespace_status}."
            ),
        }
    except Exception as error:
        return {
            "ok": False,
            "message": f"OCI config test failed: {error}",
        }
