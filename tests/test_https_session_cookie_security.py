import unittest
from pathlib import Path

from modules.session_services import session_cookie_secure_for_transport


ROOT_DIR = Path(__file__).resolve().parents[1]


class HttpsSessionCookieSecurityTests(unittest.TestCase):
    def test_https_listener_always_sets_secure_cookie(self):
        for configured_value in ("", "0", "false", "no", "off"):
            self.assertTrue(session_cookie_secure_for_transport(configured_value, "https"))

    def test_non_https_listener_retains_explicit_configuration(self):
        self.assertTrue(session_cookie_secure_for_transport("true", "http"))
        self.assertFalse(session_cookie_secure_for_transport("false", "http"))

    def test_https_start_script_overrides_stale_runtime_environment(self):
        script = (ROOT_DIR / "start_https.sh").read_text(encoding="utf-8")
        self.assertIn("DBCONSOLE_LISTENER_SCHEME=https", script)
        self.assertIn("DBCONSOLE_SESSION_COOKIE_SECURE=1", script)
        self.assertIn("DBCONSOLE_LISTENER_SCHEME DBCONSOLE_SESSION_COOKIE_SECURE", script)

    def test_http_start_script_clears_shared_https_cookie_setting(self):
        script = (ROOT_DIR / "start_http.sh").read_text(encoding="utf-8")
        self.assertIn("DBCONSOLE_LISTENER_SCHEME=http", script)
        self.assertIn("DBCONSOLE_SESSION_COOKIE_SECURE=0", script)


if __name__ == "__main__":
    unittest.main()
