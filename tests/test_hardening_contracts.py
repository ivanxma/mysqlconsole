import unittest
from functools import wraps
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, abort, session

from modules import object_storage_util, oci_util
from modules.update_routes import register_update_routes


ROOT_DIR = Path(__file__).resolve().parents[1]


class HardeningContractTests(unittest.TestCase):
    def test_update_status_requires_an_authenticated_server_session(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"

        def session_login_required(view):
            @wraps(view)
            def guarded(*args, **kwargs):
                if not session.get("allowed"):
                    abort(401)
                return view(*args, **kwargs)
            return guarded

        is_local_admin = {"value": False}
        active_login = {"value": False}
        register_update_routes(
            app,
            {
                "session_login_required": session_login_required,
                "render_dashboard": lambda *args, **kwargs: "unused",
                "is_local_admin_profile_session": lambda: is_local_admin["value"],
                "has_active_login_state": lambda: active_login["value"],
                "update_poll_token_matches": lambda candidate: candidate == "job-token",
                "get_dbconsole_update_status": lambda: {
                    "state": "running",
                    "log_text": "private log",
                    "poll_token": "job-token",
                },
                "public_dbconsole_update_status": lambda status: {
                    key: value for key, value in status.items() if key != "poll_token"
                },
            },
        )
        client = app.test_client()
        self.assertEqual(client.get("/admin/update-dbconsole/status").status_code, 401)
        token_response = client.get(
            "/admin/update-dbconsole/status",
            headers={"X-DBConsole-Update-Poll-Token": "job-token"},
        )
        self.assertEqual(token_response.status_code, 200)
        self.assertNotIn(b"job-token", token_response.data)
        with client.session_transaction() as client_session:
            client_session["allowed"] = True
        active_login["value"] = True
        self.assertEqual(client.get("/admin/update-dbconsole/status").status_code, 403)
        is_local_admin["value"] = True
        response = client.get("/admin/update-dbconsole/status")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"private log", response.data)

    def test_systemd_service_declares_private_runtime_directory(self):
        setup = (ROOT_DIR / "setup.sh").read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=dbconsole", setup)
        self.assertIn("RuntimeDirectoryMode=0700", setup)
        self.assertIn("Environment=DBCONSOLE_RUNTIME_DIR=/run/dbconsole", setup)
        self.assertIn("NoNewPrivileges=true", setup)
        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE", setup)

    def test_launchers_default_to_the_populated_virtual_environment(self):
        for launcher_name in ("start_http.sh", "start_https.sh"):
            launcher = (ROOT_DIR / launcher_name).read_text(encoding="utf-8")
            self.assertIn('PYTHON_BIN="${PYTHON_BIN_INPUT:-$SCRIPT_DIR/.venv/bin/python}"', launcher)
            self.assertNotIn('PYTHON_BIN_INPUT:-${DBCONSOLE_PYTHON_BIN', launcher)
            self.assertIn('XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$DBCONSOLE_RUNTIME_DIR}"', launcher)

    def test_socket_only_mysql_enables_mysql_shell_load_dump(self):
        setup = (ROOT_DIR / "setup.sh").read_text(encoding="utf-8")
        self.assertIn('echo "skip-networking"', setup)
        self.assertIn('echo "mysqlx=0"', setup)
        self.assertIn('echo "local_infile=ON"', setup)

    def test_legacy_object_storage_alias_is_rejected(self):
        old_payload = {"oci_region": "uk-london-1"}
        with self.assertRaisesRegex(ValueError, "Legacy Object Storage settings"):
            object_storage_util.normalize_object_storage(old_payload)

    def test_json_fallback_has_a_bounded_memory_limit(self):
        self.assertEqual(oci_util.STDLIB_JSON_FALLBACK_MAX_BYTES, 16 * 1024 * 1024)
        self.assertLess(oci_util.STDLIB_JSON_FALLBACK_MAX_BYTES, oci_util.DEFAULT_MAX_UPLOAD_BYTES)

    def test_streaming_json_parser_path_reads_complete_document(self):
        observed = {"bytes": b""}

        def parse(stream):
            observed["bytes"] = stream.read()
            yield "", "start_map", None
            yield "name", "string", "Ada"
            yield "", "end_map", None

        with patch.dict("sys.modules", {"ijson": SimpleNamespace(parse=parse)}):
            oci_util._validate_json_stream(BytesIO(b'{"name":"Ada"}'), 14)
        self.assertEqual(observed["bytes"], b'{"name":"Ada"}')


if __name__ == "__main__":
    unittest.main()
