from modules import object_storage_util, profile_store


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
    def __init__(self, *, object_storage_store_path, default_region=""):
        self.object_storage_store_path = object_storage_store_path
        self.default_region = default_region

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
