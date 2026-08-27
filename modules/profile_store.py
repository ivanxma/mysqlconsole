import json
import re
from modules.mysql_util import normalize_profile
from modules.runtime_util import (
    atomic_write_private_bytes,
    atomic_write_private_text,
    ensure_private_directory,
    ensure_private_regular_file,
)


def ensure_profile_store(profile_store):
    if profile_store.exists():
        ensure_private_regular_file(profile_store)
        return
    atomic_write_private_text(profile_store, json.dumps({"profiles": []}, indent=2) + "\n")


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
    atomic_write_private_text(profile_store, json.dumps({"profiles": normalized_profiles}, indent=2) + "\n")


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

    profile_dir = ensure_private_directory(profile_key_dir / safe_profile_key_dir_name(profile_name))
    key_path = profile_dir / "ssh_private_key"
    atomic_write_private_bytes(key_path, key_payload)
    return str(key_path)
