import json
import re
from uuid import uuid4

from modules.core_util import chmod_private_file
from modules.mysql_util import normalize_profile


def ensure_profile_store(profile_store):
    if profile_store.exists():
        chmod_private_file(profile_store)
        return
    profile_store.write_text(json.dumps({"profiles": []}, indent=2), encoding="utf-8")
    chmod_private_file(profile_store)


def load_profiles(profile_store):
    ensure_profile_store(profile_store)
    try:
        payload = json.loads(profile_store.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    profiles = []
    for row in payload.get("profiles", []):
        profile = normalize_profile(row)
        if profile["name"]:
            profiles.append(profile)
    return sorted(profiles, key=lambda item: item["name"].lower())


def save_profiles(profile_store, profiles):
    normalized_profiles = []
    seen = set()
    for row in profiles:
        profile = normalize_profile(row)
        if not profile["name"]:
            continue
        key = profile["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        normalized_profiles.append(profile)
    profile_store.write_text(json.dumps({"profiles": normalized_profiles}, indent=2), encoding="utf-8")
    chmod_private_file(profile_store)


def get_profile_by_name(profile_store, profile_name):
    profile_lookup = str(profile_name or "").strip().lower()
    for profile in load_profiles(profile_store):
        if profile["name"].lower() == profile_lookup:
            return profile
    return None


def safe_profile_key_dir_name(profile_name):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(profile_name or "").strip()).strip("._")
    if not cleaned:
        cleaned = "profile"
    return cleaned[:80]


def save_uploaded_profile_ssh_key(profile_key_dir, profile_name, upload_storage):
    if upload_storage is None or not getattr(upload_storage, "filename", ""):
        return ""
    key_payload = upload_storage.read()
    if not key_payload:
        raise ValueError("Uploaded SSH private key file is empty.")
    if len(key_payload) > 65536:
        raise ValueError("Uploaded SSH private key file is too large.")
    key_text = key_payload.decode("utf-8", errors="ignore")
    if "PRIVATE KEY" not in key_text:
        raise ValueError("Upload a valid SSH private key file.")

    profile_dir = profile_key_dir / safe_profile_key_dir_name(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        profile_key_dir.chmod(0o700)
        profile_dir.chmod(0o700)
    except OSError:
        pass

    key_path = profile_dir / "ssh_private_key"
    temp_path = profile_dir / f".{uuid4().hex}.tmp"
    temp_path.write_bytes(key_payload)
    temp_path.chmod(0o600)
    temp_path.replace(key_path)
    chmod_private_file(key_path)
    return str(key_path)
