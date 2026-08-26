import threading
import unittest
from unittest.mock import patch

try:
    import mysql.connector  # noqa: F401
except ModuleNotFoundError:  # Allow lock-only unit tests to run without optional local runtime dependencies.
    import sys
    from types import ModuleType, SimpleNamespace

    mysql_module = ModuleType("mysql")
    connector_module = ModuleType("mysql.connector")
    constants_module = ModuleType("mysql.connector.constants")
    connector_module.OperationalError = type("OperationalError", (Exception,), {})
    connector_module.InterfaceError = type("InterfaceError", (Exception,), {})
    connector_module.ProgrammingError = type("ProgrammingError", (Exception,), {})
    connector_module.connect = lambda **kwargs: None
    constants_module.ClientFlag = SimpleNamespace(SSL=0)
    mysql_module.connector = connector_module
    sys.modules["mysql"] = mysql_module
    sys.modules["mysql.connector"] = connector_module
    sys.modules["mysql.connector.constants"] = constants_module

from modules.mysql_util import (
    DEFAULT_PROFILE,
    MYSQL_CONNECTION_CACHE_KEY,
    borrow_connection,
    close_cached_connection,
    profile_signature,
)


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.autocommit_values = []

    def set_autocommit(self, enabled):
        self.autocommit_values.append(bool(enabled))

    def close(self):
        self.closed = True


class SessionConnectionIsolationTests(unittest.TestCase):
    def setUp(self):
        self.profile = {**DEFAULT_PROFILE, "name": "test", "host": "db.example"}
        self.credentials = {"username": "dbuser", "password": "password"}

    def session_entry(self):
        return {
            MYSQL_CONNECTION_CACHE_KEY: {
                "signature": profile_signature(self.profile, self.credentials["username"]),
                "connection": FakeConnection(),
                "tunnel": None,
            }
        }

    def borrow(self, entry):
        return borrow_connection(self.profile, self.credentials, entry)

    def test_same_session_connection_is_serialized(self):
        entry = self.session_entry()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors = []

        def first_request():
            try:
                with self.borrow(entry):
                    first_entered.set()
                    release_first.wait(2)
            except Exception as error:  # pragma: no cover - assertions surface failures
                errors.append(error)

        def second_request():
            try:
                with self.borrow(entry):
                    second_entered.set()
            except Exception as error:  # pragma: no cover - assertions surface failures
                errors.append(error)

        with patch("modules.mysql_util.prepare_connection_for_use"):
            first_thread = threading.Thread(target=first_request)
            second_thread = threading.Thread(target=second_request)
            first_thread.start()
            self.assertTrue(first_entered.wait(1))
            second_thread.start()
            self.assertFalse(second_entered.wait(0.15))
            release_first.set()
            self.assertTrue(second_entered.wait(1))
            first_thread.join(1)
            second_thread.join(1)

        self.assertFalse(errors)

    def test_different_sessions_do_not_block_each_other(self):
        first_entry = self.session_entry()
        second_entry = self.session_entry()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors = []

        def first_request():
            try:
                with self.borrow(first_entry):
                    first_entered.set()
                    release_first.wait(2)
            except Exception as error:  # pragma: no cover - assertions surface failures
                errors.append(error)

        def second_request():
            try:
                with self.borrow(second_entry):
                    second_entered.set()
            except Exception as error:  # pragma: no cover - assertions surface failures
                errors.append(error)

        with patch("modules.mysql_util.prepare_connection_for_use"):
            first_thread = threading.Thread(target=first_request)
            second_thread = threading.Thread(target=second_request)
            first_thread.start()
            self.assertTrue(first_entered.wait(1))
            second_thread.start()
            self.assertTrue(second_entered.wait(1))
            release_first.set()
            first_thread.join(1)
            second_thread.join(1)

        self.assertFalse(errors)

    def test_close_waits_for_an_active_session_request(self):
        entry = self.session_entry()
        connection = entry[MYSQL_CONNECTION_CACHE_KEY]["connection"]
        request_entered = threading.Event()
        release_request = threading.Event()
        close_finished = threading.Event()

        def active_request():
            with self.borrow(entry):
                request_entered.set()
                release_request.wait(2)

        def close_request():
            close_cached_connection(entry)
            close_finished.set()

        with patch("modules.mysql_util.prepare_connection_for_use"):
            request_thread = threading.Thread(target=active_request)
            close_thread = threading.Thread(target=close_request)
            request_thread.start()
            self.assertTrue(request_entered.wait(1))
            close_thread.start()
            self.assertFalse(close_finished.wait(0.15))
            self.assertFalse(connection.closed)
            release_request.set()
            self.assertTrue(close_finished.wait(1))
            request_thread.join(1)
            close_thread.join(1)

        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
