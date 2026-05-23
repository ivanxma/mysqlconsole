from modules import object_storage_util, oci_util, profile_store


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


class OciConfigService:
    def __init__(self, *, private_key_dir, app_config_dir):
        self.private_key_dir = private_key_dir
        self.app_config_dir = app_config_dir

    def save_uploaded_private_key(self, profile_name, upload_storage):
        return oci_util.save_uploaded_oci_private_key(self.private_key_dir, profile_name, upload_storage)

    def test_config(self, payload):
        return oci_util.test_oci_config(payload)

    def write_user_folder_config(self, payload):
        return oci_util.write_user_folder_oci_config(payload)

    def build_config_status(self, payload):
        return oci_util.build_oci_config_status(payload)

    def read_config_profile(self, config_file, profile_name):
        return oci_util.read_oci_config_profile(config_file, profile_name)

    def app_config_dir_path(self):
        return str(self.app_config_dir)


class ObjectStorageConfigService:
    def __init__(self, *, object_storage_store_path):
        self.object_storage_store_path = object_storage_store_path

    def ensure_object_storage_store(self):
        object_storage_util.ensure_object_storage_store(self.object_storage_store_path)

    def normalize_object_storage(self, payload):
        return object_storage_util.normalize_object_storage(payload)

    def load_object_storage_config(self):
        return object_storage_util.load_object_storage_config(self.object_storage_store_path)

    def save_object_storage_config(self, payload):
        object_storage_util.save_object_storage_config(self.object_storage_store_path, payload)

    def fetch_setup_status(self):
        return object_storage_util.fetch_setup_status(self.object_storage_store_path)
