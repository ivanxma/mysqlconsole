import os
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask, make_response
from werkzeug.datastructures import FileStorage

from modules import mysql_import, mysqlsh_jobs, oci_util, runtime_util, update_util
from dbconsole_update_worker import UpdateWorker
from modules.auth_routes import register_auth_routes
from modules.mysqlsh_routes import register_mysqlsh_routes
from modules.mysqlsh_configuration_routes import register_mysqlsh_configuration_routes
from modules.session_services import DbConsoleSessionService
from modules.update_service import DbConsoleUpdateService
from modules.session_util import SessionManager


ROOT_DIR = Path(__file__).resolve().parents[1]


class OciPaginationTerminationTests(unittest.TestCase):
    def _repeating_client(self):
        client = Mock()
        client.list_objects.return_value = SimpleNamespace(
            data=SimpleNamespace(prefixes=[], objects=[], next_start_with="same-token")
        )
        return client

    def test_folder_listing_rejects_repeated_token(self):
        with patch("modules.oci_util.build_object_storage_client", return_value=self._repeating_client()):
            with self.assertRaisesRegex(RuntimeError, "repeated page token"):
                oci_util.list_object_storage_folders(
                    {}, namespace="ns", bucket_name="bucket", max_pages=5
                )

    def test_file_listing_rejects_repeated_token(self):
        with patch("modules.oci_util.build_object_storage_client", return_value=self._repeating_client()):
            with self.assertRaisesRegex(RuntimeError, "repeated page token"):
                oci_util.list_object_storage_files(
                    {}, namespace="ns", bucket_name="bucket", max_pages=5
                )


class PrivateStateLifecycleTests(unittest.TestCase):
    def test_nested_private_directories_are_all_owner_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("DBCONSOLE_RUNTIME_DIR")
            os.environ["DBCONSOLE_RUNTIME_DIR"] = str(Path(temp_dir) / "runtime")
            try:
                nested = runtime_util.ensure_private_directory(
                    runtime_util.get_runtime_directory() / "mysqlsh" / "jobs"
                )
                self.assertEqual(nested.stat().st_mode & 0o777, 0o700)
                self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(nested.parent.parent.stat().st_mode & 0o777, 0o700)
            finally:
                if previous is None:
                    os.environ.pop("DBCONSOLE_RUNTIME_DIR", None)
                else:
                    os.environ["DBCONSOLE_RUNTIME_DIR"] = previous

    def test_dead_worker_reconciliation_removes_secret_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_runtime = os.environ.get("DBCONSOLE_RUNTIME_DIR")
            previous_state = os.environ.get("DBCONSOLE_STATE_DIR")
            os.environ["DBCONSOLE_RUNTIME_DIR"] = str(Path(temp_dir) / "run")
            os.environ["DBCONSOLE_STATE_DIR"] = str(Path(temp_dir) / "state")
            try:
                job_id = "a" * 32
                request_path = mysqlsh_jobs.request_root() / f"{job_id}.json"
                runtime_util.atomic_write_private_text(request_path, '{"password":"secret"}')
                mysqlsh_jobs.save_job(
                    job_id,
                    {
                        "job_id": job_id,
                        "status": "running",
                        "worker_pid": 12345,
                        "mysqlsh_pid": None,
                        "request_path": str(request_path),
                        "submitted_at": mysqlsh_jobs._now(),
                        "owner_id": "owner",
                        "owner_profile_name": "profile",
                    },
                )
                with patch("modules.mysqlsh_jobs._pid_alive", return_value=False):
                    rows = mysqlsh_jobs.reconcile_jobs()
                self.assertFalse(request_path.exists())
                self.assertEqual(rows[0]["status"], "failed")
            finally:
                if previous_runtime is None:
                    os.environ.pop("DBCONSOLE_RUNTIME_DIR", None)
                else:
                    os.environ["DBCONSOLE_RUNTIME_DIR"] = previous_runtime
                if previous_state is None:
                    os.environ.pop("DBCONSOLE_STATE_DIR", None)
                else:
                    os.environ["DBCONSOLE_STATE_DIR"] = previous_state

    def test_job_owner_is_stable_across_browser_sessions(self):
        first = mysqlsh_jobs.build_owner_id("profile", "user")
        self.assertEqual(first, mysqlsh_jobs.build_owner_id("profile", "user"))
        self.assertNotEqual(first, mysqlsh_jobs.build_owner_id("profile", "other"))


class ResourceAndUpdateGuardTests(unittest.TestCase):
    def test_session_service_forwards_the_csp_nonce_to_the_session_manager(self):
        app = Flask(__name__)
        app.secret_key = "test"
        service = DbConsoleSessionService(
            default_profile={},
            normalize_profile=lambda value: value,
            close_cached_connection=lambda value: None,
            parse_iso_datetime=update_util.parse_iso_datetime,
            utc_now_iso=lambda: "2026-08-27T00:00:00+00:00",
            credential_ttl_seconds=300,
            scope_key="scope",
            scope_value="dbconsole",
            version_key="version",
            version=1,
            credential_session_key="credential",
            csrf_session_key="csrf",
            profile_service=SimpleNamespace(),
            local_admin_profile_name="local-admin-profile",
            nav_groups=[],
        )
        with app.test_request_context("/", base_url="https://dbconsole.example"):
            nonce = service.csp_nonce()
            response = service.add_authenticated_no_store_headers(make_response("ok"))
        self.assertIn(f"nonce-{nonce}", response.headers["Content-Security-Policy"])

    def test_update_worker_detects_no_new_privileges_without_invoking_sudo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worker = UpdateWorker(root, root / "status.json", root / "update.log")
            with (
                patch("dbconsole_update_worker.os.geteuid", return_value=1000),
                patch(
                    "dbconsole_update_worker.Path.read_text",
                    return_value="Name:\tdbconsole\nNoNewPrivs:\t1\n",
                ),
                patch("dbconsole_update_worker.subprocess.run") as run,
            ):
                available, reason = worker.passwordless_sudo_available()
            self.assertFalse(available)
            self.assertIn("NoNewPrivileges", reason)
            run.assert_not_called()

    def test_https_responses_receive_security_headers(self):
        app = Flask(__name__)
        app.secret_key = "test"
        manager = SessionManager(
            default_profile={},
            normalize_profile=lambda value: value,
            close_cached_connection=lambda value: None,
            parse_iso_datetime=update_util.parse_iso_datetime,
            utc_now_iso=lambda: "2026-08-27T00:00:00+00:00",
            credential_ttl_seconds=300,
            scope_key="scope",
            scope_value="dbconsole",
            version_key="version",
            version=1,
            credential_session_key="credential",
            csrf_session_key="csrf",
        )
        with app.test_request_context("/", base_url="https://dbconsole.example"):
            response = manager.add_authenticated_no_store_headers(make_response("ok"))
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertRegex(policy, r"script-src 'self' 'nonce-[A-Za-z0-9_-]+'")
        self.assertRegex(policy, r"style-src 'self' 'nonce-[A-Za-z0-9_-]+'")
        self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])
        self.assertEqual(response.headers["Permissions-Policy"], "camera=(), microphone=(), geolocation=()")

    def test_import_rejects_payload_above_route_limit(self):
        upload = FileStorage(stream=BytesIO(b"x" * 11), filename="large.csv")
        with patch.object(mysql_import, "MAX_IMPORT_UPLOAD_BYTES", 10):
            with self.assertRaisesRegex(Exception, "may not exceed"):
                mysql_import.parse_import_upload(upload)

    def test_auto_update_is_blocked_while_mysqlsh_job_is_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = DbConsoleUpdateService(
                repo_dir=root,
                app_version_file=root / "appver.json",
                worker_script=root / "worker.py",
                status_file=root / "status.json",
                log_file=root / "update.log",
                process_started_at=update_util.parse_iso_datetime("2026-08-27T00:00:00+00:00"),
                version_check_session_key="version",
                is_local_admin_profile_session=lambda: True,
                active_job_count=lambda: 1,
            )
            with self.assertRaisesRegex(ValueError, "blocked"):
                service.start_job()

    def test_repository_version_url_requires_https_and_approved_host(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            update_util.validate_repository_version_url("http://github.com/example/appver.json")
        with self.assertRaisesRegex(ValueError, "not approved"):
            update_util.validate_repository_version_url("https://untrusted.example/appver.json")
        self.assertEqual(
            update_util.validate_repository_version_url("https://api.github.com/repos/example/project"),
            "https://api.github.com/repos/example/project",
        )


class FlowContractTests(unittest.TestCase):
    def test_login_rate_limit_blocks_repeated_connection_failures(self):
        class FailedConnection:
            def __enter__(self):
                raise RuntimeError("connection failed")

            def __exit__(self, *args):
                return False

        app = Flask(__name__)
        app.secret_key = "test"
        profile = {"name": "primary", "host": "db.example", "socket_enabled": False}
        register_auth_routes(
            app,
            {
                "app_title": "DBConsole",
                "default_profile": profile,
                "local_admin_profile_name": "local-admin-profile",
                "session_login_required": lambda view: view,
                "get_profile_by_name": lambda name: profile,
                "normalize_profile": lambda value: dict(value),
                "get_session_profile": lambda: profile,
                "public_profiles": lambda values: values,
                "public_profile": lambda value: value,
                "load_profiles": lambda: [profile],
                "clear_login_state": lambda **kwargs: None,
                "set_session_profile": lambda value: None,
                "set_session_credentials": lambda username, password: None,
                "mysql_connection": lambda **kwargs: FailedConnection(),
                "set_logged_in": lambda value: None,
                "local_admin_password_change_required": lambda: False,
                "refresh_repo_version_check": lambda: {},
                "is_local_admin_profile_session": lambda: False,
                "should_show_update_page_after_login": lambda value: False,
                "change_local_admin_profile_password": lambda value: None,
                "clear_local_admin_password_change_required": lambda: None,
            },
        )
        client = app.test_client()
        with patch("modules.auth_routes.render_template", return_value="login"):
            for _ in range(5):
                self.assertEqual(
                    client.post(
                        "/",
                        data={"profile_picker": "primary", "username": "user", "password": "wrong"},
                    ).status_code,
                    200,
                )
            self.assertEqual(
                client.post(
                    "/",
                    data={"profile_picker": "primary", "username": "user", "password": "wrong"},
                ).status_code,
                429,
            )

    def test_mysqlsh_configuration_pages_reject_session_without_system_user(self):
        app = Flask(__name__)
        app.secret_key = "test"
        register_mysqlsh_configuration_routes(
            app,
            {
                "login_required": lambda view: view,
                "render_dashboard": lambda *args, **kwargs: "unexpected",
                "is_local_admin_profile_session": lambda: False,
                "can_access_mysqlsh": lambda: False,
                "option_profile_store": "/tmp/no-option-profiles.json",
                "par_store": "/tmp/no-pars.json",
            },
        )
        client = app.test_client()
        self.assertEqual(client.get("/mysql-shell/option-profiles").status_code, 403)
        self.assertEqual(client.get("/mysql-shell/pars").status_code, 403)

    def test_mysqlsh_routes_reject_session_without_system_user(self):
        app = Flask(__name__)
        app.secret_key = "test"
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "can_access_mysqlsh": lambda: False,
                "render_dashboard": lambda *args, **kwargs: "unexpected",
            },
        )
        client = app.test_client()
        self.assertEqual(client.get("/mysql-shell/operations").status_code, 403)
        self.assertEqual(client.get("/mysql-shell/jobs").status_code, 403)
        self.assertEqual(client.get("/mysql-shell/validation").status_code, 403)

    def test_session_without_system_user_hides_the_mysqlsh_menu(self):
        service = (ROOT_DIR / "modules/session_services.py").read_text(encoding="utf-8")
        filter_start = service.index("def nav_groups_for_current_session")
        filter_end = service.index("def can_access_update_page", filter_start)
        filtered_block = service[filter_start:filter_end]
        self.assertIn('str(item["endpoint"]).startswith("mysqlsh_")', filtered_block)
        self.assertIn("can_access_mysqlsh", filtered_block)

    def test_inline_templates_use_nonces_and_shared_auto_submit(self):
        for template in (ROOT_DIR / "templates").glob("*.html"):
            content = template.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"<(?:script|style)(?![^>]*nonce)", content),
                f"{template.name} has an inline script/style without a CSP nonce",
            )
            self.assertNotIn("onchange=", content, f"{template.name} has an inline change handler")
        base = (ROOT_DIR / "templates/base.html").read_text(encoding="utf-8")
        self.assertIn("data-submit-on-change", base)

    def test_operations_renders_setup_state_when_object_storage_is_incomplete(self):
        app = Flask(__name__)
        app.secret_key = "test"
        captured = {}
        register_mysqlsh_routes(
            app,
            {
                "login_required": lambda view: view,
                "render_dashboard": lambda template, **context: captured.update(context) or "ok",
                "get_session_profile": lambda: {"name": "primary"},
                "get_session_credentials": lambda: {"username": "user", "password": "secret"},
                "get_server_session_id": lambda: "session",
                "get_session_username": lambda: "user",
                "is_local_admin_profile_session": lambda: False,
                "can_access_mysqlsh": lambda: True,
                "set_server_session_state": lambda key, value: None,
                "pop_server_session_state": lambda key: None,
                "load_object_storage_config": lambda: {"active_profile_name": "DEFAULT", "profiles": []},
                "select_object_storage_config": lambda value: {"profile_name": "DEFAULT"},
                "validate_object_storage_target": lambda value: (_ for _ in ()).throw(
                    ValueError("Object Storage region is required.")
                ),
                "test_instance_principal_access": Mock(),
                "option_profile_store": "/tmp/no-option-profiles.json",
                "par_store": "/tmp/no-pars.json",
            },
        )
        with patch("modules.mysqlsh_routes.get_mysqlsh_status", return_value={"available": False}):
            response = app.test_client().get("/mysql-shell/operations")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(captured["storage_configured"])

    def test_download_links_and_canonical_import_path_are_present(self):
        monitoring = (ROOT_DIR / "templates/monitoring_report.html").read_text(encoding="utf-8")
        db_admin = (ROOT_DIR / "templates/db_admin.html").read_text(encoding="utf-8")
        import_routes = (ROOT_DIR / "modules/mysql_import_routes.py").read_text(encoding="utf-8")
        self.assertIn("url_for(download_endpoint", monitoring)
        self.assertIn("url_for('db_admin_download'", db_admin)
        self.assertIn('@app.route("/mysql/import"', import_routes)
        self.assertIn('@app.route("/mysql/imprt"', import_routes)

    def test_setup_declares_durable_state_and_service_hardening(self):
        setup = (ROOT_DIR / "setup.sh").read_text(encoding="utf-8")
        for expected in (
            "StateDirectory=dbconsole",
            "StateDirectoryMode=0700",
            "RuntimeDirectoryPreserve=restart",
            "UMask=0077",
            "PrivateTmp=true",
            "ProtectSystem=full",
            "ProtectHome=read-only",
            "NoNewPrivileges=true",
            "CapabilityBoundingSet=CAP_NET_BIND_SERVICE",
        ):
            self.assertIn(expected, setup)

    def test_firewalld_absent_port_does_not_fall_through_to_other_firewalls(self):
        setup = (ROOT_DIR / "setup.sh").read_text(encoding="utf-8")
        absent_branch = (
            'if ! run_as_root_with_timeout 20 firewall-cmd --permanent '
            '--query-port="${port_value}/tcp" >/dev/null 2>&1; then'
        )
        branch_start = setup.index(absent_branch)
        branch_end = setup.index("elif run_as_root_with_timeout", branch_start)
        self.assertIn("return 0", setup[branch_start:branch_end])

    def test_terminal_update_polling_is_not_unconditional(self):
        template = (ROOT_DIR / "templates/update_dbconsole.html").read_text(encoding="utf-8")
        self.assertIn("activeStates.has(initialStatus.state)", template)
        self.assertIn("if (continuePolling)", template)


if __name__ == "__main__":
    unittest.main()
