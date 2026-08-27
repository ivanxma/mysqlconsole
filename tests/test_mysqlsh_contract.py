import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from werkzeug.datastructures import MultiDict

from modules import dashboard_queries, mysqlsh_job_worker, mysqlsh_jobs, mysqlsh_option_form, mysqlsh_option_profiles, mysqlsh_par_store, mysqlsh_python_runner, mysqlsh_runner, object_storage_util, runtime_util
from modules.mysqlsh_routes import register_mysqlsh_routes


FAKE_REUSABLE_PAR = {
    "id": "registry-par-1",
    "par_id": "oci-par-1",
    "name": "dump-target",
    "profile_name": "objects",
    "region": "uk-london-1",
    "namespace": "ns",
    "bucket_name": "bucket",
    "bucket_prefix": "exports",
    "prefix": "exports/nightly",
    "access_type": "AnyObjectReadWrite",
    "expires_at": "2099-01-01T00:00:00+00:00",
    "delete_after_use": True,
    "par_url": "https://objectstorage.example/p/scoped/exports/nightly",
    "is_active": True,
    "target_display": "bucket/exports/nightly",
}


class MysqlshRunnerTests(unittest.TestCase):
    def test_schema_dump_request_and_preview_redact_par_query(self):
        request = mysqlsh_runner.build_operation_request(
            "dump_schemas",
            storage_url="https://objectstorage.example/p/secret?token=do-not-show",
            schema_names=["sales"],
            options={"threads": 4},
        )
        self.assertEqual(request["args"][0], ["sales"])
        self.assertIn("[redacted-par]", mysqlsh_runner.operation_preview(request))
        self.assertNotIn("do-not-show", mysqlsh_runner.operation_preview(request))
        self.assertNotIn("/p/secret", mysqlsh_runner.operation_preview(request))

    def test_connection_options_use_active_profile_and_support_ssh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "id"
            key_path.write_text("key", encoding="utf-8")
            options = mysqlsh_runner.build_connection_options(
                {"host": "db.example", "port": 3307, "database": "data", "ssl_mode": "REQUIRED", "ssh_enabled": True, "ssh_host": "jump.example", "ssh_user": "opc", "ssh_port": 22, "ssh_key_path": str(key_path)},
                {"username": "app", "password": "secret"},
            )
        self.assertEqual(options["ssh"], "opc@jump.example:22")
        self.assertEqual(options["host"], "db.example")

    def test_result_evaluation_uses_structured_mysqlsh_error(self):
        stdout = (
            mysqlsh_runner.RESULT_START
            + '\n{"status":"error","error":"dump failed"}\n'
            + mysqlsh_runner.RESULT_END
        )
        result = mysqlsh_runner.evaluate_execution(1, stdout, "ignored")
        self.assertFalse(result["succeeded"])
        self.assertEqual(result["error"], "dump failed")

    def test_python_runner_sets_active_session_and_deletes_secret_request(self):
        class FakeSession:
            def __init__(self):
                self.open = True

            def is_open(self):
                return self.open

            def close(self):
                self.open = False

        session = FakeSession()
        shell = SimpleNamespace(
            connect=Mock(return_value=session),
            get_session=Mock(return_value=session),
            options=SimpleNamespace(useWizards=True),
        )
        util = SimpleNamespace(dump_instance=Mock(return_value={}), dump_schemas=Mock(), load_dump=Mock())
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "function_name": "dump_instance",
                        "args": ["https://example/par?secret", {}],
                        "kwargs": {},
                        "connection_options": {"user": "admin", "password": "secret"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(mysqlsh_python_runner, "shell", shell, create=True), patch.object(
                mysqlsh_python_runner, "util", util, create=True
            ), patch.object(sys, "argv", ["mysqlsh_python_runner", str(request_path)]), patch(
                "sys.stdout", new_callable=StringIO
            ):
                mysqlsh_python_runner.main()
            self.assertFalse(request_path.exists())
        shell.connect.assert_called_once()
        util.dump_instance.assert_called_once()
        self.assertFalse(shell.options.useWizards)

    def test_operation_rejects_insecure_par_and_oversized_schema_scope(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            mysqlsh_runner.build_operation_request("dump_instance", storage_url="http://example/par")
        with self.assertRaisesRegex(ValueError, "at most 100"):
            mysqlsh_runner.build_operation_request(
                "dump_schemas",
                storage_url="https://example/par",
                schema_names=[f"schema_{index}" for index in range(101)],
            )

    def test_dashboard_uses_shared_mysqlsh_runtime_status(self):
        with patch("modules.dashboard_queries.mysqlsh_version_label", return_value="9.4.1") as label:
            self.assertEqual(dashboard_queries.fetch_mysql_shell_version(), "9.4.1")
        label.assert_called_once_with()

    def test_worker_redacts_urls_and_passwords(self):
        rendered = mysqlsh_job_worker._redact(
            "password=secret https://objectstorage.example/p/token/path",
            ["secret"],
        )
        self.assertNotIn("secret", rendered)
        self.assertNotIn("/p/token", rendered)


class MysqlshJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_runtime = os.environ.get("DBCONSOLE_RUNTIME_DIR")
        self.previous_state = os.environ.get("DBCONSOLE_STATE_DIR")
        os.environ["DBCONSOLE_RUNTIME_DIR"] = self.temp_dir.name
        os.environ["DBCONSOLE_STATE_DIR"] = self.temp_dir.name

    def tearDown(self):
        if self.previous_runtime is None:
            os.environ.pop("DBCONSOLE_RUNTIME_DIR", None)
        else:
            os.environ["DBCONSOLE_RUNTIME_DIR"] = self.previous_runtime
        if self.previous_state is None:
            os.environ.pop("DBCONSOLE_STATE_DIR", None)
        else:
            os.environ["DBCONSOLE_STATE_DIR"] = self.previous_state
        self.temp_dir.cleanup()

    @patch("modules.mysqlsh_jobs.subprocess.Popen")
    @patch("modules.mysqlsh_jobs.get_mysqlsh_status", return_value={"available": True, "binary": "/usr/bin/mysqlsh", "error": ""})
    def test_job_metadata_excludes_password_and_par_url(self, _status, popen):
        popen.return_value = SimpleNamespace(pid=1234)
        operation = mysqlsh_runner.build_operation_request(
            "dump_instance", storage_url="https://example/p/secret?token=hide", options={"threads": 4}
        )
        job = mysqlsh_jobs.submit_job(
            {"name": "primary", "host": "db.example", "port": 3306},
            {"username": "admin", "password": "db-secret"},
            operation,
            owner_session_id="session-1",
            storage_target={"profile_name": "objects"},
            par={"id": "par-1", "prefix": "mysqlsh/x"},
            operation_label="Dump Instance",
        )
        raw_metadata = (mysqlsh_jobs.job_directory(job["job_id"]) / "job.json").read_text(encoding="utf-8")
        self.assertNotIn("db-secret", raw_metadata)
        self.assertNotIn("token=hide", raw_metadata)
        request = json.loads(Path(job["request_path"]).read_text(encoding="utf-8"))
        self.assertEqual(request["connection_options"]["password"], "db-secret")
        self.assertIsNone(mysqlsh_jobs.job_snapshot(job["job_id"], owner_session_id="other", owner_profile_name="primary"))

    @patch("modules.mysqlsh_jobs.subprocess.Popen")
    @patch("modules.mysqlsh_jobs.get_mysqlsh_status", return_value={"available": True, "binary": "/usr/bin/mysqlsh", "version": "9.4.1", "error": ""})
    def test_cleanup_requires_par_revocation_and_removes_secret_request(self, _status, popen):
        popen.return_value = SimpleNamespace(pid=1234)
        operation = mysqlsh_runner.build_operation_request(
            "dump_instance", storage_url="https://example/p/secret", options={"threads": 4}
        )
        job = mysqlsh_jobs.submit_job(
            {"name": "primary", "host": "db.example", "port": 3306},
            {"username": "admin", "password": "db-secret"},
            operation,
            owner_session_id="session-1",
            storage_target={"profile_name": "objects"},
            par={"id": "par-1", "prefix": "mysqlsh/x", "expires_at": "later"},
            operation_label="Dump Instance",
        )
        mysqlsh_jobs.update_job(job["job_id"], status="succeeded")
        with self.assertRaisesRegex(RuntimeError, "revoked or expired"):
            mysqlsh_jobs.cleanup_job(
                job["job_id"], owner_session_id="session-1", owner_profile_name="primary"
            )
        mysqlsh_jobs.record_par_revoked(job["job_id"])
        mysqlsh_jobs.cleanup_job(
            job["job_id"], owner_session_id="session-1", owner_profile_name="primary"
        )
        self.assertFalse((mysqlsh_jobs.job_directory(job["job_id"]) / "request.json").exists())

    @patch("modules.mysqlsh_jobs.get_mysqlsh_status", return_value={"available": True, "binary": "/usr/bin/mysqlsh", "version": "9.4.1", "error": ""})
    def test_submission_limit_is_enforced_before_process_start(self, _status):
        operation = mysqlsh_runner.build_operation_request(
            "dump_instance", storage_url="https://example/p/secret", options={}
        )
        with patch.object(mysqlsh_jobs, "MAX_ACTIVE_JOBS_GLOBAL", 0), patch(
            "modules.mysqlsh_jobs.subprocess.Popen"
        ) as popen, self.assertRaisesRegex(RuntimeError, "global"):
            mysqlsh_jobs.submit_job(
                {"name": "primary", "host": "db.example"},
                {"username": "admin", "password": "secret"},
                operation,
                owner_session_id="session-1",
                storage_target={"profile_name": "objects"},
                par={"id": "par-1", "prefix": "mysqlsh/x"},
                operation_label="Dump Instance",
            )
        popen.assert_not_called()

    def test_zombie_process_is_not_treated_as_running(self):
        process_state = SimpleNamespace(returncode=0, stdout="Z    ")
        with patch("modules.mysqlsh_jobs.os.kill"), patch(
            "modules.mysqlsh_jobs.subprocess.run", return_value=process_state
        ):
            self.assertFalse(mysqlsh_jobs._pid_alive(1234))

    def test_terminal_job_revokes_par_from_immutable_storage_snapshot(self):
        job_id = "b" * 32
        mysqlsh_jobs.save_job(
            job_id,
            {
                "job_id": job_id,
                "status": "succeeded",
                "par_id": "par-1",
                "par_delete_after_use": True,
                "storage_profile_name": "objects",
                "storage_region": "uk-london-1",
                "storage_namespace": "ns",
                "storage_bucket_name": "bucket",
                "storage_bucket_prefix": "exports",
            },
        )
        with patch("modules.mysqlsh_jobs.oci_util.revoke_preauthenticated_request") as revoke:
            result = mysqlsh_jobs.finalize_job_par(job_id)
        self.assertEqual(
            revoke.call_args.args[0],
            {
                "profile_name": "objects",
                "region": "uk-london-1",
                "namespace": "ns",
                "bucket_name": "bucket",
                "bucket_prefix": "exports",
            },
        )
        self.assertEqual(result["cleanup_status"], "revoked")
        self.assertTrue(result["par_revoked_at"])

    def test_explicit_retention_keeps_par_until_expiry(self):
        job_id = "c" * 32
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mysqlsh_jobs.save_job(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "par_id": "par-2",
                "par_delete_after_use": False,
                "par_expires_at": future,
            },
        )
        with patch("modules.mysqlsh_jobs.oci_util.revoke_preauthenticated_request") as revoke:
            result = mysqlsh_jobs.finalize_job_par(job_id)
        revoke.assert_not_called()
        self.assertEqual(result["cleanup_status"], "retained_until_expiry")

    def test_fallback_runtime_is_exported_for_child_workers(self):
        previous_runtime = os.environ.pop("DBCONSOLE_RUNTIME_DIR", None)
        try:
            selected = runtime_util.get_runtime_directory()
            self.assertEqual(os.environ["DBCONSOLE_RUNTIME_DIR"], str(selected))
            self.assertEqual(selected, runtime_util.APPLICATION_ROOT / ".runtime")
        finally:
            if previous_runtime is None:
                os.environ.pop("DBCONSOLE_RUNTIME_DIR", None)
            else:
                os.environ["DBCONSOLE_RUNTIME_DIR"] = previous_runtime

    def test_worker_keeps_mysqlsh_in_worker_process_group(self):
        request_path = Path(self.temp_dir.name) / "request.json"
        request_path.write_text(
            json.dumps({"connection_options": {}, "args": [], "function_name": "dump_instance"}),
            encoding="utf-8",
        )
        job = {
            "job_id": "d" * 32,
            "request_path": str(request_path),
            "stdout_path": str(Path(self.temp_dir.name) / "stdout.log"),
            "stderr_path": str(Path(self.temp_dir.name) / "stderr.log"),
        }
        process = Mock(pid=4321, stdout=BytesIO(), stderr=BytesIO())
        process.wait.return_value = 0
        with patch.object(sys, "argv", ["mysqlsh_job_worker", job["job_id"]]), patch(
            "modules.mysqlsh_job_worker._wait_for_parent_submission", return_value=job
        ), patch(
            "modules.mysqlsh_job_worker.get_mysqlsh_status",
            return_value={"available": True, "binary": "/usr/bin/mysqlsh", "error": ""},
        ), patch(
            "modules.mysqlsh_job_worker.subprocess.Popen", return_value=process
        ) as popen, patch(
            "modules.mysqlsh_job_worker.update_job"
        ), patch(
            "modules.mysqlsh_job_worker.load_job", return_value={"status": "succeeded"}
        ), patch(
            "modules.mysqlsh_job_worker.evaluate_execution", return_value={"succeeded": True, "error": ""}
        ), patch(
            "modules.mysqlsh_job_worker.finalize_job_par"
        ):
            mysqlsh_job_worker.main()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_worker_publishes_terminal_state_after_par_finalization(self):
        events = []
        with patch(
            "modules.mysqlsh_job_worker.update_job",
            side_effect=lambda job_id, **fields: events.append(("update", fields.get("status"))),
        ), patch(
            "modules.mysqlsh_job_worker.finalize_job_par",
            side_effect=lambda job_id: events.append(("finalize", job_id)),
        ):
            mysqlsh_job_worker._complete_job("e" * 32, "succeeded", finished_at="now")
        self.assertEqual(
            events,
            [("update", "finalizing"), ("finalize", "e" * 32), ("update", "succeeded")],
        )

    def test_worker_publishes_redacted_live_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "stderr.log"
            updates = []
            with patch("modules.mysqlsh_job_worker.update_job", side_effect=lambda *args, **kwargs: updates.append(kwargs)):
                mysqlsh_job_worker._capture_stream(
                    BytesIO(b"Dump progress 50% https://objectstorage.example/p/secret password-value\n"),
                    bytearray(),
                    job_id="f" * 32,
                    log_path=log_path,
                    secrets=["password-value"],
                )
            rendered = log_path.read_text(encoding="utf-8")
        self.assertIn("Dump progress 50%", rendered)
        self.assertNotIn("password-value", rendered)
        self.assertNotIn("objectstorage.example", rendered)
        self.assertTrue(any("last_progress" in update for update in updates))
        self.assertTrue(any(update.get("progress_percent") == 50 for update in updates))


class MysqlshInstancePrincipalTests(unittest.TestCase):
    def test_par_uses_resolved_sdk_endpoint_and_trailing_prefix_slash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "pars.json"
            base_client = SimpleNamespace(
                endpoint="https://objectstorage.uk-london-1.{dualStack?ds.oci.:}oraclecloud.com",
                get_endpoint=lambda: "https://objectstorage.uk-london-1.oraclecloud.com",
            )
            client = SimpleNamespace(base_client=base_client)
            created = SimpleNamespace(id="oci-par", access_uri="/p/private/n/ns/b/bucket/o/")
            with patch(
                "modules.mysqlsh_par_store.oci_util.create_scoped_preauthenticated_request",
                return_value=(client, created),
            ):
                entry = mysqlsh_par_store.create_par(
                    store,
                    {
                        "profile_name": "objects",
                        "region": "uk-london-1",
                        "namespace": "ns",
                        "bucket_name": "bucket",
                        "bucket_prefix": "exports",
                    },
                    name="resolved-endpoint",
                    prefix="exports/resolved",
                    access_type="AnyObjectReadWrite",
                    delete_after_use=True,
                )
        self.assertEqual(
            entry["par_url"],
            "https://objectstorage.uk-london-1.oraclecloud.com/p/private/n/ns/b/bucket/o/exports/resolved/",
        )
        self.assertNotIn("{", entry["par_url"])

    def test_reusable_par_registry_filters_dump_and_load_access(self):
        target = {"profile_name": "objects", "region": "uk-london-1", "namespace": "ns", "bucket_name": "bucket", "bucket_prefix": "exports"}
        client = Mock()
        client.base_client.endpoint = "https://objectstorage.example"
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "pars.json"
            with patch(
                "modules.mysqlsh_par_store.oci_util.create_scoped_preauthenticated_request",
                side_effect=[
                    (client, SimpleNamespace(id="rw-par", access_uri="/p/rw")),
                    (client, SimpleNamespace(id="r-par", access_uri="/p/r")),
                ],
            ):
                writable = mysqlsh_par_store.create_par(
                    store, target, name="dump-and-load", prefix="exports/nightly", access_type="AnyObjectReadWrite", delete_after_use=True
                )
                readable = mysqlsh_par_store.create_par(
                    store, target, name="load-only", prefix="exports/nightly", access_type="AnyObjectRead", delete_after_use=False, expiry_hours=2
                )
            self.assertEqual([row["id"] for row in mysqlsh_par_store.list_pars(store, target, "dump", True)], [writable["id"]])
            self.assertEqual(
                {row["id"] for row in mysqlsh_par_store.list_pars(store, target, "load", True)},
                {writable["id"], readable["id"]},
            )
            self.assertEqual(store.stat().st_mode & 0o777, 0o600)

    def test_option_profiles_are_private_reusable_and_reject_secret_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Path(temp_dir) / "options.json"
            saved = mysqlsh_option_profiles.save_option_profile(
                store, "dump", "nightly", {"threads": 8, "consistent": True}
            )
            self.assertEqual(mysqlsh_option_profiles.get_option_profile(store, "dump", "nightly"), saved)
            self.assertEqual(store.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ValueError, "managed by DB Console"):
                mysqlsh_option_profiles.save_option_profile(store, "load", "unsafe", {"parUrl": "secret"})
            self.assertTrue(mysqlsh_option_profiles.delete_option_profile(store, "dump", "nightly"))
            self.assertEqual(mysqlsh_option_profiles.list_option_profiles(store, "dump"), [])

    def test_auto_delete_removes_reusable_par_registry_entry(self):
        target = {"profile_name": "objects", "region": "uk-london-1", "namespace": "ns", "bucket_name": "bucket", "bucket_prefix": "exports"}
        client = Mock()
        client.base_client.endpoint = "https://objectstorage.example"
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = Path(temp_dir) / "pars.json"
            with patch(
                "modules.mysqlsh_par_store.oci_util.create_scoped_preauthenticated_request",
                return_value=(client, SimpleNamespace(id="oci-par", access_uri="/p/auto")),
            ):
                entry = mysqlsh_par_store.create_par(
                    registry, target, name="one-use", prefix="exports/once", access_type="AnyObjectReadWrite", delete_after_use=True
                )
            previous_runtime = os.environ.get("DBCONSOLE_RUNTIME_DIR")
            os.environ["DBCONSOLE_RUNTIME_DIR"] = str(Path(temp_dir) / "runtime")
            try:
                job_id = "f" * 32
                mysqlsh_jobs.save_job(job_id, {
                    "job_id": job_id, "status": "succeeded", "par_id": entry["par_id"], "par_delete_after_use": True,
                    "par_registry_path": str(registry), "storage_profile_name": "objects", "storage_region": "uk-london-1",
                    "storage_namespace": "ns", "storage_bucket_name": "bucket", "storage_bucket_prefix": "exports",
                })
                with patch("modules.mysqlsh_jobs.oci_util.revoke_preauthenticated_request"):
                    result = mysqlsh_jobs.finalize_job_par(job_id)
                self.assertEqual(result["cleanup_status"], "revoked")
                self.assertEqual(mysqlsh_par_store.list_pars(registry), [])
            finally:
                if previous_runtime is None:
                    os.environ.pop("DBCONSOLE_RUNTIME_DIR", None)
                else:
                    os.environ["DBCONSOLE_RUNTIME_DIR"] = previous_runtime


class MysqlshRouteRegistrationTests(unittest.TestCase):
    def test_native_dbconsole_routes_register_without_a_second_app(self):
        app = Flask(__name__)
        app.secret_key = "test"
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "can_access_mysqlsh": lambda: True,
                "render_dashboard": lambda template, **context: (template, context),
                "get_session_profile": lambda: {"name": "primary"},
                "get_session_credentials": lambda: {"username": "admin", "password": "secret"},
                "get_server_session_id": lambda: "session-1",
                "set_server_session_state": lambda key, value: None,
                "pop_server_session_state": lambda key: None,
                "load_object_storage_config": lambda: {"active_profile_name": "objects", "profiles": []},
                "select_object_storage_config": lambda value: {"profile_name": value},
                "validate_object_storage_target": lambda value: value,
                "option_profile_store": "/tmp/dbconsole-test-option-profiles.json",
                "par_store": "/tmp/dbconsole-test-pars.json",
                "test_instance_principal_access": lambda value: {"ok": True},
            },
        )
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        self.assertTrue(
            {"mysqlsh_operations_page", "mysqlsh_jobs_page", "mysqlsh_job_detail_page", "mysqlsh_job_action", "mysqlsh_validation_page"}
            <= endpoints
        )

    def test_preview_does_not_call_oci_access(self):
        app = Flask(__name__)
        app.secret_key = "test"
        captured = {}
        review_state = {}
        access = Mock(return_value={"ok": True})
        select = Mock(return_value={"profile_name": "objects", "region": "uk-london-1", "namespace": "ns", "bucket_name": "bucket", "bucket_prefix": "exports"})
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "can_access_mysqlsh": lambda: True,
                "render_dashboard": lambda template, **context: captured.update(context) or "ok",
                "get_session_profile": lambda: {"name": "primary"},
                "get_session_credentials": lambda: {"username": "admin", "password": "secret"},
                "get_server_session_id": lambda: "session-1",
                "set_server_session_state": lambda key, value: review_state.__setitem__(key, value),
                "pop_server_session_state": lambda key: review_state.pop(key, None),
                "load_object_storage_config": lambda: {"active_profile_name": "objects", "profiles": []},
                "select_object_storage_config": select,
                "validate_object_storage_target": object_storage_util.validate_object_storage_target,
                "option_profile_store": "/tmp/dbconsole-test-option-profiles.json",
                "par_store": "/tmp/dbconsole-test-pars.json",
                "test_instance_principal_access": access,
            },
        )
        with patch("modules.mysqlsh_routes.list_jobs", return_value=[]), patch(
            "modules.mysqlsh_routes.list_pars", return_value=[FAKE_REUSABLE_PAR]
        ), patch(
            "modules.mysqlsh_routes.get_par", return_value=FAKE_REUSABLE_PAR
        ), patch(
            "modules.mysqlsh_routes.get_mysqlsh_status",
            return_value={"available": True, "version": "9.4.1", "error": ""},
        ):
            response = app.test_client().post(
                "/mysql-shell/operations",
                data={"operation": "dump_instance", "object_storage_profile": "objects", "par_entry_id": "registry-par-1", "mysqlsh_action": "preview"},
            )
        self.assertEqual(response.status_code, 200)
        access.assert_not_called()
        self.assertEqual(captured["confirmation"]["storage_profile"], "objects")

    def test_direct_submit_is_rejected_before_oci_access(self):
        app = Flask(__name__)
        app.secret_key = "test"
        access = Mock(return_value={"ok": True})
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "can_access_mysqlsh": lambda: True,
                "render_dashboard": lambda template, **context: "ok",
                "get_session_profile": lambda: {"name": "primary"},
                "get_session_credentials": lambda: {"username": "admin", "password": "secret"},
                "get_server_session_id": lambda: "session-1",
                "set_server_session_state": lambda key, value: None,
                "pop_server_session_state": lambda key: None,
                "load_object_storage_config": lambda: {
                    "active_profile_name": "objects",
                    "profiles": [{"profile_name": "objects"}],
                },
                "select_object_storage_config": lambda value: {
                    "profile_name": value,
                    "region": "uk-london-1",
                    "namespace": "ns",
                    "bucket_name": "bucket",
                },
                "validate_object_storage_target": object_storage_util.validate_object_storage_target,
                "option_profile_store": "/tmp/dbconsole-test-option-profiles.json",
                "par_store": "/tmp/dbconsole-test-pars.json",
                "test_instance_principal_access": access,
            },
        )
        with patch("modules.mysqlsh_routes.list_jobs", return_value=[]), patch(
            "modules.mysqlsh_routes.list_pars", return_value=[FAKE_REUSABLE_PAR]
        ), patch(
            "modules.mysqlsh_routes.get_par", return_value=FAKE_REUSABLE_PAR
        ), patch(
            "modules.mysqlsh_routes.get_mysqlsh_status",
            return_value={"available": True, "version": "9.4.1", "error": ""},
        ):
            response = app.test_client().post(
                "/mysql-shell/operations",
                data={
                    "operation": "dump_instance",
                    "object_storage_profile": "objects",
                    "par_entry_id": "registry-par-1",
                    "mysqlsh_action": "submit",
                },
            )
        self.assertEqual(response.status_code, 200)
        access.assert_not_called()

    def test_reviewed_submit_uses_delete_policy_and_server_state(self):
        app = Flask(__name__)
        app.secret_key = "test"
        review_state = {}
        access = Mock(return_value={"ok": True})
        target = {
            "profile_name": "objects",
            "region": "uk-london-1",
            "namespace": "ns",
            "bucket_name": "bucket",
            "bucket_prefix": "exports",
        }
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "can_access_mysqlsh": lambda: True,
                "render_dashboard": lambda template, **context: "ok",
                "get_session_profile": lambda: {"name": "primary"},
                "get_session_credentials": lambda: {"username": "admin", "password": "secret"},
                "get_server_session_id": lambda: "session-1",
                "set_server_session_state": lambda key, value: review_state.__setitem__(key, value),
                "pop_server_session_state": lambda key: review_state.pop(key, None),
                "load_object_storage_config": lambda: {"active_profile_name": "objects", "profiles": [target]},
                "select_object_storage_config": lambda value: target,
                "validate_object_storage_target": object_storage_util.validate_object_storage_target,
                "option_profile_store": "/tmp/dbconsole-test-option-profiles.json",
                "par_store": "/tmp/dbconsole-test-pars.json",
                "test_instance_principal_access": access,
            },
        )
        payload = {
            "operation": "dump_instance",
            "object_storage_profile": "objects",
            "par_entry_id": "registry-par-1",
        }
        with patch("modules.mysqlsh_routes.list_jobs", return_value=[]), patch(
            "modules.mysqlsh_routes.list_pars", return_value=[FAKE_REUSABLE_PAR]
        ), patch(
            "modules.mysqlsh_routes.get_par", return_value=FAKE_REUSABLE_PAR
        ), patch(
            "modules.mysqlsh_routes.get_mysqlsh_status",
            return_value={"available": True, "version": "9.4.1", "error": ""},
        ), patch("modules.mysqlsh_routes.submit_job", return_value={"job_id": "e" * 32}) as submit:
            client = app.test_client()
            self.assertEqual(client.post("/mysql-shell/operations", data={**payload, "mysqlsh_action": "preview"}).status_code, 200)
            response = client.post("/mysql-shell/operations", data={**payload, "mysqlsh_action": "submit"})
        self.assertEqual(response.status_code, 302)
        access.assert_called_once()
        self.assertEqual(access.call_args.args[0]["bucket_prefix"], target["bucket_prefix"])
        self.assertEqual(access.call_args.args[0]["bucket_name"], target["bucket_name"])
        self.assertTrue(submit.call_args.kwargs["par"]["delete_after_use"])
        self.assertEqual(submit.call_args.kwargs["par"]["registry_entry_id"], "registry-par-1")

    def test_retained_par_requires_an_expiry(self):
        with self.assertRaisesRegex(ValueError, "required"):
            mysqlsh_par_store.create_par(
                "/tmp/unused-par-store.json",
                {"profile_name": "objects", "region": "uk-london-1", "namespace": "ns", "bucket_name": "bucket", "bucket_prefix": "exports"},
                name="retained",
                prefix="exports/nightly",
                access_type="AnyObjectReadWrite",
                delete_after_use=False,
            )

    def test_succeeded_legacy_job_displays_complete_progress(self):
        self.assertEqual(mysqlsh_jobs._with_display_progress({"status": "succeeded"})["progress_percent"], 100)
        self.assertIsNone(mysqlsh_jobs._with_display_progress({"status": "running"})["progress_percent"])

    def test_cleanup_revokes_par_before_local_files(self):
        app = Flask(__name__)
        app.secret_key = "test"
        events = []
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "can_access_mysqlsh": lambda: True,
                "render_dashboard": lambda template, **context: "ok",
                "get_session_profile": lambda: {"name": "primary"},
                "get_session_credentials": lambda: {},
                "get_server_session_id": lambda: "session-1",
                "set_server_session_state": lambda key, value: None,
                "pop_server_session_state": lambda key: None,
                "load_object_storage_config": lambda: {"active_profile_name": "objects", "profiles": []},
                "select_object_storage_config": lambda value: {"profile_name": value},
                "validate_object_storage_target": lambda value: value,
                "option_profile_store": "/tmp/dbconsole-test-option-profiles.json",
                "par_store": "/tmp/dbconsole-test-pars.json",
                "test_instance_principal_access": Mock(),
            },
        )
        job = {"job_id": "a" * 32, "status": "succeeded", "par_id": "par-1", "par_revoked_at": "", "storage_profile_name": "objects"}
        with patch("modules.mysqlsh_routes.job_snapshot", return_value=job), patch(
            "modules.mysqlsh_routes.finalize_job_par", side_effect=lambda job_id: events.append("revoke")
        ), patch("modules.mysqlsh_routes.cleanup_job", side_effect=lambda *args, **kwargs: events.append("cleanup")):
            response = app.test_client().post(
                "/mysql-shell/jobs/" + "a" * 32 + "/action",
                data={"job_action": "cleanup"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(events, ["revoke", "cleanup"])


class MysqlshSetupContractTests(unittest.TestCase):
    def test_setup_uses_one_canonical_runtime_mysqlsh_variable(self):
        root = Path(__file__).resolve().parent.parent
        setup_text = (root / "setup.sh").read_text(encoding="utf-8")
        self.assertNotIn('${MYSQLSH:-}', setup_text)
        for start_name in ("start_http.sh", "start_https.sh"):
            start_text = (root / start_name).read_text(encoding="utf-8")
            self.assertIn("DBCONSOLE_MYSQLSH", start_text)
            self.assertNotIn(" DBCONSOLE_MYSQLSH_TIMEOUT", start_text)

    def test_launchers_use_single_worker_gunicorn_and_shared_runtime(self):
        root = Path(__file__).resolve().parent.parent
        self.assertIn("gunicorn", (root / "requirements.txt").read_text(encoding="utf-8"))
        for start_name in ("start_http.sh", "start_https.sh"):
            start_text = (root / start_name).read_text(encoding="utf-8")
            self.assertIn("--workers 1", start_text)
            self.assertIn("DBCONSOLE_RUNTIME_DIR", start_text)
            self.assertNotIn("app.run(", start_text)

    def test_mysqlsh_action_buttons_use_dbconsole_button_classes(self):
        root = Path(__file__).resolve().parent.parent
        operations = (root / "templates/mysqlsh_operations.html").read_text(encoding="utf-8")
        detail = (root / "templates/mysqlsh_job_detail.html").read_text(encoding="utf-8")
        self.assertNotIn('value="submit" disabled', operations)
        self.assertIn('class="button primary"', operations)
        self.assertIn('class="button secondary danger"', detail)
        self.assertNotIn('class="button danger"', detail)
        self.assertIn("Option Profiles", operations)
        self.assertIn("{% if can_manage_mysqlsh_configuration %}<a class=\"button secondary\" href=\"{{ url_for('mysqlsh_option_profiles_page'", operations)
        self.assertIn("Target' }} PAR", operations)
        self.assertIn("PAR Setup", operations)
        self.assertGreaterEqual(operations.count("{% if can_manage_mysqlsh_configuration %}"), 3)
        par_setup = (root / "templates/mysqlsh_pars.html").read_text(encoding="utf-8")
        self.assertIn("Available PARs", par_setup)
        self.assertIn("Delete after used", par_setup)
        self.assertNotIn("par_url", par_setup)
        jobs = (root / "templates/mysqlsh_jobs.html").read_text(encoding="utf-8")
        self.assertIn("<th>Progress</th>", jobs)
        self.assertIn("job.progress_percent", jobs)

    def test_option_profile_ui_constructs_documented_options_and_object_filters(self):
        form = MultiDict(
            [
                ("threads", "8"), ("maxRate", "100M"), ("compression", "zstd;level=8"),
                ("dialect", "default"), ("defaultCharacterSet", "utf8mb4"),
                ("showProgress", "1"), ("consistent", "1"), ("chunking", "1"),
                ("excludeLakehouseTables", "1"),
                ("bytesPerChunk", "128M"), ("users", "1"), ("events", "1"),
                ("routines", "1"), ("triggers", "1"), ("libraries", "1"),
                ("compatibility", "strip_definers"), ("compatibility", "strip_tablespaces"),
                ("include_schemas_json", '["sales"]'),
                ("exclude_schemas_json", "[]"),
                ("include_tables_json", '["sales.orders"]'),
                ("exclude_tables_json", '["sales.audit"]'),
                ("include_users_json", json.dumps(["'app'@'%'"])),
                ("exclude_users_json", "[]"),
                ("include_events_json", "[]"), ("exclude_events_json", "[]"),
                ("include_routines_json", "[]"), ("exclude_routines_json", "[]"),
                ("include_triggers_json", "[]"), ("exclude_triggers_json", "[]"),
                ("include_libraries_json", "[]"), ("exclude_libraries_json", "[]"),
                ("advanced_json", '{"where":{"sales.orders":"status = 1"}}'),
            ]
        )
        options = mysqlsh_option_form.build_options("dump", form)
        self.assertEqual(options["threads"], 8)
        self.assertEqual(options["includeSchemas"], ["sales"])
        self.assertEqual(options["includeTables"], ["sales.orders"])
        self.assertEqual(options["excludeTables"], ["sales.audit"])
        self.assertTrue(options["excludeLakehouseTables"])
        merged, excluded = mysqlsh_option_form.merge_lakehouse_exclusions(
            options, ["`lake`.`events`", "`lake`.`events`", "`lake`.`facts`"]
        )
        self.assertNotIn("excludeLakehouseTables", merged)
        self.assertEqual(merged["excludeTables"], ["sales.audit", "`lake`.`events`", "`lake`.`facts`"])
        self.assertEqual(excluded, ["`lake`.`events`", "`lake`.`events`", "`lake`.`facts`"])
        self.assertEqual(options["compatibility"], ["strip_definers", "strip_tablespaces"])
        self.assertEqual(options["where"]["sales.orders"], "status = 1")

        template = (Path(__file__).resolve().parent.parent / "templates/mysqlsh_option_profiles.html").read_text(encoding="utf-8")
        self.assertIn("Construct JSON and Save Profile", template)
        self.assertIn("data-filter-picker", template)
        self.assertIn("Include and exclude existing objects", template)
        self.assertIn("mysqlsh-control-section-grid", template)
        self.assertIn("Load and compatibility controls", template)
        self.assertNotIn("MySQL Shell Options JSON *", template)

        stylesheet = (Path(__file__).resolve().parent.parent / "static/style.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", stylesheet)
        self.assertIn(".mysqlsh-control-section-grid .form-section", stylesheet)

    def test_option_builder_rejects_conflicts_and_dbconsole_managed_options(self):
        base = MultiDict(
            [
                ("threads", "4"), ("analyzeTables", "off"), ("deferTableIndexes", "fulltext"),
                ("handleGrantErrors", "abort"), ("updateGtidSet", "off"),
                ("dropExistingObjects", "1"), ("ignoreExistingObjects", "1"),
            ] + [
                (f"{mode}_{item}_json", "[]")
                for item in mysqlsh_option_form.FILTER_TYPES for mode in ("include", "exclude")
            ]
        )
        with self.assertRaisesRegex(ValueError, "cannot both"):
            mysqlsh_option_form.build_options("load", base)
        base.setlist("dropExistingObjects", [])
        base.setlist("ignoreExistingObjects", [])
        base["advanced_json"] = '{"progressFile":"/tmp/unsafe"}'
        with self.assertRaisesRegex(ValueError, "managed"):
            mysqlsh_option_form.build_options("load", base)

    def test_load_option_form_constructs_typed_options_and_filters(self):
        form = MultiDict(
            [
                ("threads", "12"), ("backgroundThreads", "6"), ("waitDumpTimeout", "30"),
                ("analyzeTables", "histogram"), ("deferTableIndexes", "all"),
                ("handleGrantErrors", "ignore"), ("updateGtidSet", "append"),
                ("showProgress", "1"), ("loadDdl", "1"), ("loadData", "1"),
                ("loadUsers", "1"), ("loadIndexes", "1"),
                ("sessionInitSql", "SET SESSION foreign_key_checks=0;\nSET SESSION net_read_timeout=600;"),
                ("include_schemas_json", '["sales"]'), ("exclude_schemas_json", "[]"),
                ("include_tables_json", '["sales.orders"]'), ("exclude_tables_json", "[]"),
            ] + [
                (f"{mode}_{item}_json", "[]")
                for item in ("users", "events", "routines", "triggers", "libraries")
                for mode in ("include", "exclude")
            ]
        )
        options = mysqlsh_option_form.build_options("load", form)
        self.assertEqual(options["threads"], 12)
        self.assertEqual(options["backgroundThreads"], 6)
        self.assertEqual(options["waitDumpTimeout"], 30)
        self.assertEqual(options["analyzeTables"], "histogram")
        self.assertEqual(options["includeTables"], ["sales.orders"])
        self.assertEqual(len(options["sessionInitSql"]), 2)
        self.assertNotIn("progressFile", options)

    def test_filter_catalog_uses_one_authenticated_connection(self):
        class Cursor:
            def __init__(self):
                self.sql = ""
                self.execute_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _params):
                self.sql = sql
                self.execute_count += 1

            def fetchall(self):
                return [{"value": "sales", "label": "sales"}] if "schemata" in self.sql else []

        class Connection:
            def __init__(self, cursor):
                self._cursor = cursor

            def cursor(self):
                return self._cursor

        cursor = Cursor()
        class ConnectionContext:
            def __enter__(self):
                return Connection(cursor)

            def __exit__(self, *_args):
                return False

        mysql_connection = Mock(return_value=ConnectionContext())
        catalog = mysqlsh_option_form.fetch_filter_catalog(mysql_connection)
        mysql_connection.assert_called_once_with(connect_timeout=5)
        self.assertEqual(cursor.execute_count, len(mysqlsh_option_form.FILTER_TYPES))
        self.assertEqual(catalog["schemas"], [{"value": "sales", "label": "sales"}])

    def test_route_does_not_duplicate_dashboard_session_profile_context(self):
        route_source = (Path(__file__).resolve().parent.parent / "modules/mysqlsh_routes.py").read_text(encoding="utf-8")
        self.assertNotIn("session_profile=profile", route_source)


if __name__ == "__main__":
    unittest.main()
