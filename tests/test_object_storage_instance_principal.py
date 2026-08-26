import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader
from flask import Flask
from werkzeug.datastructures import FileStorage

from modules import object_storage_util, oci_util
from modules.admin_routes import register_admin_routes


class ObjectStorageStoreTests(unittest.TestCase):
    def test_legacy_api_key_fields_are_removed_during_load(self):
        legacy = {
            "active_profile_name": "DEFAULT",
            "profiles": [
                {
                    "oci_config_profile": "DEFAULT",
                    "oci_region": "UK-LONDON-1",
                    "oci_namespace": "example-namespace",
                    "bucket_name": "lakehouse",
                    "bucket_prefix": "incoming",
                    "oci_user": "ocid1.user.example",
                    "oci_tenancy": "ocid1.tenancy.example",
                    "oci_fingerprint": "aa:bb",
                    "oci_key_file": "/private/key.pem",
                    "oci_config_file": "~/.oci/config",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "object_storage.json"
            store_path.write_text(json.dumps(legacy), encoding="utf-8")

            config = object_storage_util.load_object_storage_config(store_path)
            persisted = json.loads(store_path.read_text(encoding="utf-8"))

        self.assertEqual(config["region"], "uk-london-1")
        self.assertEqual(config["namespace"], "example-namespace")
        self.assertEqual(
            set(persisted["profiles"][0]),
            {
                "profile_name",
                "region",
                "namespace",
                "bucket_name",
                "bucket_prefix",
                "upload_validation_max_bytes",
            },
        )
        self.assertNotIn("ocid1.user.example", json.dumps(persisted))
        self.assertNotIn("/private/key.pem", json.dumps(persisted))

    def test_deployment_region_seeds_only_missing_region(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "object_storage.json"
            seeded = object_storage_util.load_object_storage_config(
                store_path,
                default_region="uk-london-1",
            )
            seeded.update(
                {
                    "region": "us-phoenix-1",
                    "namespace": "example-ns",
                    "bucket_name": "lakehouse",
                }
            )
            object_storage_util.save_object_storage_config(
                store_path,
                seeded,
                default_region="uk-london-1",
            )
            loaded = object_storage_util.load_object_storage_config(
                store_path,
                default_region="eu-frankfurt-1",
            )

        self.assertEqual(seeded["region"], "us-phoenix-1")
        self.assertEqual(loaded["region"], "us-phoenix-1")

    def test_profile_upload_validation_limit_defaults_to_two_gib_and_is_configurable(self):
        profile = object_storage_util.normalize_object_storage(
            {
                "profile_name": "primary",
                "region": "uk-london-1",
                "namespace": "example-ns",
                "bucket_name": "lakehouse",
                "upload_validation_max_mib": "3072",
            }
        )
        self.assertEqual(
            object_storage_util.normalize_object_storage({})["upload_validation_max_bytes"],
            2 * 1024 * 1024 * 1024,
        )
        self.assertEqual(profile["upload_validation_max_bytes"], 3072 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "between 1 MiB"):
            object_storage_util.normalize_object_storage({"upload_validation_max_mib": "0"})

    def test_folder_must_remain_inside_configured_prefix(self):
        target = {
            "profile_name": "primary",
            "region": "uk-london-1",
            "namespace": "ns",
            "bucket_name": "bucket",
            "bucket_prefix": "lakehouse/input",
        }
        self.assertEqual(
            object_storage_util.normalize_folder_for_target(target, "lakehouse/input/day-1"),
            "lakehouse/input/day-1",
        )
        with self.assertRaisesRegex(ValueError, "within the configured bucket prefix"):
            object_storage_util.normalize_folder_for_target(target, "other-prefix")
        with self.assertRaisesRegex(ValueError, "valid relative path"):
            object_storage_util.normalize_folder_for_target(target, "lakehouse/input/../private")


class InstancePrincipalClientTests(unittest.TestCase):
    def tearDown(self):
        oci_util.reset_instance_principal_signer()

    def test_signer_is_cached_and_region_is_explicit(self):
        signer_instances = []
        client_calls = []

        class FakeSigner:
            def __init__(self):
                signer_instances.append(self)

        def fake_client(config, **kwargs):
            client_calls.append((config, kwargs))
            return SimpleNamespace()

        fake_sdk = SimpleNamespace(
            auth=SimpleNamespace(signers=SimpleNamespace(InstancePrincipalsSecurityTokenSigner=FakeSigner)),
            object_storage=SimpleNamespace(ObjectStorageClient=fake_client),
        )
        with patch("modules.oci_util._load_oci_sdk", return_value=fake_sdk):
            oci_util.build_object_storage_client({"region": "uk-london-1"})
            oci_util.build_object_storage_client({"region": "us-phoenix-1"})

        self.assertEqual(len(signer_instances), 1)
        self.assertEqual(client_calls[0][0], {"region": "uk-london-1"})
        self.assertEqual(client_calls[1][0], {"region": "us-phoenix-1"})
        self.assertIs(client_calls[0][1]["signer"], client_calls[1][1]["signer"])

    def test_access_test_checks_namespace_and_bucket(self):
        calls = []

        class FakeClient:
            def get_namespace(self):
                return SimpleNamespace(data="example-ns")

            def list_objects(self, namespace, bucket, **kwargs):
                calls.append((namespace, bucket, kwargs))

        with patch("modules.oci_util.build_object_storage_client", return_value=FakeClient()) as factory:
            result = oci_util.test_instance_principal_access(
                {"region": "uk-london-1", "namespace": "example-ns", "bucket_name": "lakehouse"}
            )

        self.assertTrue(result["ok"])
        factory.assert_called_once_with({"region": "uk-london-1"})
        self.assertEqual(calls, [("example-ns", "lakehouse", {"limit": 1})])

    def test_folder_population_recurses_below_the_configured_prefix(self):
        calls = []
        prefixes_by_parent = {
            "incoming/": ["incoming/2026/", "incoming/archive/"],
            "incoming/2026/": ["incoming/2026/08/"],
            "incoming/archive/": [],
            "incoming/2026/08/": [],
        }

        class FakeClient:
            def list_objects(self, namespace, bucket, **kwargs):
                calls.append((namespace, bucket, kwargs))
                return SimpleNamespace(
                    data=SimpleNamespace(
                        prefixes=prefixes_by_parent.get(kwargs["prefix"], []),
                        next_start_with=None,
                    )
                )

        with patch("modules.oci_util.build_object_storage_client", return_value=FakeClient()):
            folders = oci_util.list_object_storage_folders(
                {"region": "uk-london-1"},
                namespace="example-ns",
                bucket_name="lakehouse",
                base_prefix="incoming",
            )

        self.assertEqual(
            folders,
            ["incoming/", "incoming/2026/", "incoming/archive/", "incoming/2026/08/"],
        )
        self.assertEqual({call[2]["prefix"] for call in calls}, set(prefixes_by_parent))


class UploadValidationTests(unittest.TestCase):
    @staticmethod
    def upload(filename, payload):
        return FileStorage(stream=io.BytesIO(payload), filename=filename)

    def test_csv_validation_rejects_empty_binary_and_delta_uploads(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            oci_util.validate_object_storage_upload(self.upload("empty.csv", b""))
        with self.assertRaisesRegex(ValueError, "binary NUL"):
            oci_util.validate_object_storage_upload(self.upload("bad.csv", b"a,b\n1,\x00\n"))
        with self.assertRaisesRegex(ValueError, "supported Lakehouse file"):
            oci_util.validate_object_storage_upload(self.upload("table.delta", b"not-a-delta-table"))

    def test_text_validation_reads_beyond_the_old_64_kib_prefix(self):
        payload = b"name,value\n" + (b"valid,row\n" * 7000) + b"bad,\x00\n"
        self.assertGreater(len(payload), 65536)
        with self.assertRaisesRegex(ValueError, "binary NUL at byte"):
            oci_util.validate_object_storage_upload(self.upload("late-error.csv", payload))

    def test_json_reports_its_syntax_location(self):
        with self.assertRaisesRegex(ValueError, r"line \d+, column \d+.*byte \d+|byte \d+, line \d+, column \d+"):
            oci_util.validate_object_storage_upload(
                self.upload("invalid.json", b"{\n  \"name\": \"Ada\",\n  \"active\":\n}\n")
            )

    def test_parquet_and_avro_signatures_are_checked(self):
        parquet = oci_util.validate_object_storage_upload(self.upload("sample.parquet", b"PAR1payloadPAR1"))
        avro = oci_util.validate_object_storage_upload(self.upload("sample.avro", b"Obj\x01payload"))
        self.assertEqual(parquet["suffix"], "parquet")
        self.assertEqual(avro["suffix"], "avro")
        with self.assertRaisesRegex(ValueError, "closing PAR1"):
            oci_util.validate_object_storage_upload(self.upload("bad.parquet", b"PAR1payload"))

    def test_upload_is_verified_with_head_object(self):
        upload = self.upload("data.csv", b"id,name\n1,Ada\n")
        events = []

        class FakeClient:
            def put_object(self, namespace, bucket, object_name, stream, **kwargs):
                events.append(("put", namespace, bucket, object_name, stream.read(), kwargs))
                return SimpleNamespace(headers={"etag": "etag-1", "opc-request-id": "request-1"})

            def head_object(self, namespace, bucket, object_name):
                events.append(("head", namespace, bucket, object_name))
                return SimpleNamespace(headers={"content-length": "14"})

        with patch("modules.oci_util.build_object_storage_client", return_value=FakeClient()):
            result = oci_util.upload_object_storage_file(
                {"region": "uk-london-1"},
                namespace="example-ns",
                bucket_name="lakehouse",
                folder_prefix="incoming",
                upload_storage=upload,
            )

        self.assertEqual(result["object_name"], "incoming/data.csv")
        self.assertEqual(result["size"], 14)
        self.assertEqual([event[0] for event in events], ["put", "head"])


class DeploymentContractTests(unittest.TestCase):
    def test_region_default_is_preserved_across_deployment_paths(self):
        root = Path(__file__).resolve().parents[1]
        setup = (root / "setup.sh").read_text(encoding="utf-8")
        init = (root / "oci_compute_init.sh").read_text(encoding="utf-8")
        updater = (root / "dbconsole_update_worker.py").read_text(encoding="utf-8")
        self.assertIn("DBCONSOLE_OBJECT_STORAGE_REGION_INPUT", setup)
        self.assertIn("Authorization: Bearer Oracle", setup)
        self.assertIn("DBCONSOLE_OBJECT_STORAGE_REGION", init)
        self.assertIn('"DBCONSOLE_OBJECT_STORAGE_REGION"', updater)

    def test_templates_compile_without_api_key_fields(self):
        root = Path(__file__).resolve().parents[1]
        environment = Environment(loader=FileSystemLoader(root / "templates"))
        for template_name in ("setup_object_storage.html", "heatwave_external_lakehouse.html"):
            environment.get_template(template_name)
        setup_template = (root / "templates" / "setup_object_storage.html").read_text(encoding="utf-8")
        forbidden = ("oci_fingerprint", "oci_private_key", "oci_config_file", "oci_user", "oci_tenancy")
        self.assertFalse([value for value in forbidden if value in setup_template])


class ObjectStorageRouteTests(unittest.TestCase):
    def test_setup_route_saves_compact_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "object_storage.json"
            app = Flask(__name__)
            app.secret_key = "test-only"
            identity = lambda function: function
            deps = {
                "login_required": identity,
                "session_login_required": identity,
                "render_dashboard": lambda template, **context: f"rendered:{template}",
                "load_object_storage_config": lambda: object_storage_util.load_object_storage_config(store_path),
                "select_object_storage_config": lambda name: object_storage_util.select_object_storage_config(
                    store_path, name
                ),
                "normalize_object_storage": object_storage_util.normalize_object_storage,
                "save_object_storage_config": lambda payload: object_storage_util.save_object_storage_config(
                    store_path, payload
                ),
                "set_active_object_storage_profile": lambda name: object_storage_util.set_active_object_storage_profile(
                    store_path, name
                ),
                "delete_object_storage_profile": lambda name: object_storage_util.delete_object_storage_profile(
                    store_path, name
                ),
                "test_instance_principal_access": lambda payload: {"ok": True, "message": "ok"},
                "deployment_region_default": "uk-london-1",
            }
            register_admin_routes(app, deps)

            response = app.test_client().post(
                "/admin/setup-object-storage",
                data={
                    "setup_action": "save_object_storage_profile",
                    "profile_name": "primary",
                    "region": "us-phoenix-1",
                    "namespace": "example-ns",
                    "bucket_name": "lakehouse",
                    "bucket_prefix": "incoming",
                },
                follow_redirects=True,
            )
            persisted = json.loads(store_path.read_text(encoding="utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"rendered:setup_object_storage.html", response.data)
        self.assertEqual(persisted["active_profile_name"], "primary")
        self.assertEqual(persisted["profiles"][0]["region"], "us-phoenix-1")
        self.assertEqual(
            set(persisted["profiles"][0]),
            {
                "profile_name",
                "region",
                "namespace",
                "bucket_name",
                "bucket_prefix",
                "upload_validation_max_bytes",
            },
        )


if __name__ == "__main__":
    unittest.main()
