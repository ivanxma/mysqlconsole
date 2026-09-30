import re
from pathlib import Path

from modules import object_storage_util, profile_store
from modules.runtime_util import atomic_write_private_bytes, atomic_write_private_text, ensure_private_directory


class ProfileConfigService:
    def __init__(self, *, profile_store_path, profile_ssh_key_dir):
        self.profile_store_path = profile_store_path
        self.profile_ssh_key_dir = profile_ssh_key_dir

    def ensure_profile_store(self):
        profile_store.ensure_profile_store(self.profile_store_path)

    def load_profiles(self):
        return profile_store.load_profiles(self.profile_store_path)

    def save_profiles(self, profiles):
        profile_store.save_profiles(self.profile_store_path, profiles)

    def get_profile_by_name(self, profile_name):
        return profile_store.get_profile_by_name(self.profile_store_path, profile_name)

    def save_uploaded_profile_ssh_key(self, profile_name, upload_storage):
        return profile_store.save_uploaded_profile_ssh_key(self.profile_ssh_key_dir, profile_name, upload_storage)


class ObjectStorageConfigService:
    def __init__(self, *, object_storage_store_path, default_region="", oci_config_dir=None):
        self.object_storage_store_path = object_storage_store_path
        self.default_region = default_region
        self.oci_config_dir = Path(oci_config_dir or Path(object_storage_store_path).parent / "oci-config")

    def ensure_object_storage_store(self):
        object_storage_util.ensure_object_storage_store(
            self.object_storage_store_path,
            default_region=self.default_region,
        )

    def normalize_object_storage(self, payload):
        return object_storage_util.normalize_object_storage(payload, default_region=self.default_region)

    def load_object_storage_config(self):
        return object_storage_util.load_object_storage_config(
            self.object_storage_store_path,
            default_region=self.default_region,
        )

    def select_object_storage_config(self, profile_name):
        return object_storage_util.select_object_storage_config(
            self.object_storage_store_path,
            profile_name,
            default_region=self.default_region,
        )

    def save_object_storage_config(self, payload):
        return object_storage_util.save_object_storage_config(
            self.object_storage_store_path,
            payload,
            default_region=self.default_region,
        )

    def set_active_object_storage_profile(self, profile_name):
        return object_storage_util.set_active_object_storage_profile(
            self.object_storage_store_path,
            profile_name,
            default_region=self.default_region,
        )

    def delete_object_storage_profile(self, profile_name):
        return object_storage_util.delete_object_storage_profile(
            self.object_storage_store_path,
            profile_name,
            default_region=self.default_region,
        )

    def fetch_setup_status(self):
        return object_storage_util.fetch_setup_status(
            self.object_storage_store_path,
            default_region=self.default_region,
        )

    def test_instance_principal_access(self, payload):
        return object_storage_util.test_instance_principal_access(payload)

    def object_storage_authentication_label(self):
        return object_storage_util.object_storage_authentication_label()

    def save_uploaded_oci_config(self, config_upload, key_upload):
        if config_upload is None or not getattr(config_upload, "filename", ""):
            raise ValueError("Choose an OCI config file to upload.")
        if key_upload is None or not getattr(key_upload, "filename", ""):
            raise ValueError("Choose the OCI private key file to upload.")
        config_bytes = config_upload.read()
        key_bytes = key_upload.read()
        if not config_bytes or not key_bytes or len(config_bytes) > 1024 * 1024 or len(key_bytes) > 1024 * 1024:
            raise ValueError("OCI config and private key uploads must be non-empty and no larger than 1 MiB.")
        try:
            config_text = config_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("OCI config upload must be UTF-8 text.") from error
        config_dir = ensure_private_directory(self.oci_config_dir)
        key_path = config_dir / "oci_api_key.pem"
        rewritten, replacements = re.subn(
            r"(?m)^(\s*key_file\s*=\s*).*$",
            lambda match: match.group(1) + str(key_path),
            config_text,
        )
        if not replacements:
            raise ValueError("OCI config upload must contain a key_file entry.")
        atomic_write_private_bytes(key_path, key_bytes)
        atomic_write_private_text(config_dir / "config", rewritten)
        return {"config_path": str(config_dir / "config"), "key_path": str(key_path)}
