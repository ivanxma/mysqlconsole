import os
from contextlib import contextmanager

import mysql.connector
from mysql.connector.constants import ClientFlag

try:
    from sshtunnel import SSHTunnelForwarder
except ImportError:  # pragma: no cover - optional dependency at runtime
    SSHTunnelForwarder = None


DEFAULT_PROFILE = {
    "name": "",
    "host": "",
    "port": 3306,
    "database": "mysql",
    "username": "",
    "ssl_mode": "REQUIRED",
    "ssl_ca": "",
    "ssl_cert": "",
    "ssl_key": "",
    "socket_enabled": False,
    "socket_path": "",
    "ssh_enabled": False,
    "ssh_host": "",
    "ssh_port": 22,
    "ssh_user": "",
    "ssh_key_path": "",
    "require_password_change": False,
}
MYSQL_SSL_MODES = {"DISABLED", "REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"}
MYSQL_CONNECTION_CACHE_KEY = "mysql_connection_cache"

OperationalError = mysql.connector.OperationalError
InterfaceError = mysql.connector.InterfaceError
ProgrammingError = mysql.connector.ProgrammingError


def _normalize_int(value, default, minimum=None):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and normalized < minimum:
        return default
    return normalized


def normalize_mysql_ssl_mode(value):
    normalized = str(value or "").strip().upper().replace("-", "_")
    aliases = {
        "VERIFY_CA": "VERIFY_CA",
        "VERIFYCA": "VERIFY_CA",
        "VERIFY_IDENTITY": "VERIFY_IDENTITY",
        "VERIFYIDENTITY": "VERIFY_IDENTITY",
        "VERIFY_ID": "VERIFY_IDENTITY",
        "REQUIRED": "REQUIRED",
        "REQUIRE": "REQUIRED",
        "DISABLED": "DISABLED",
        "DISABLE": "DISABLED",
        "OFF": "DISABLED",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in MYSQL_SSL_MODES:
        return DEFAULT_PROFILE["ssl_mode"]
    return normalized


def normalize_profile(payload):
    return {
        "name": str(payload.get("name", "")).strip(),
        "host": str(payload.get("host", "")).strip(),
        "port": _normalize_int(payload.get("port"), DEFAULT_PROFILE["port"], minimum=1),
        "database": str(payload.get("database", "")).strip() or DEFAULT_PROFILE["database"],
        "username": str(payload.get("username", "")).strip(),
        "ssl_mode": normalize_mysql_ssl_mode(payload.get("ssl_mode", DEFAULT_PROFILE["ssl_mode"])),
        "ssl_ca": str(payload.get("ssl_ca", "")).strip(),
        "ssl_cert": str(payload.get("ssl_cert", "")).strip(),
        "ssl_key": str(payload.get("ssl_key", "")).strip(),
        "socket_enabled": str(payload.get("socket_enabled", "")).strip().lower() in {"1", "true", "yes", "on"},
        "socket_path": str(payload.get("socket_path", "")).strip(),
        "ssh_enabled": str(payload.get("ssh_enabled", "")).strip().lower() in {"1", "true", "yes", "on"},
        "ssh_host": str(payload.get("ssh_host", "")).strip(),
        "ssh_port": _normalize_int(payload.get("ssh_port"), DEFAULT_PROFILE["ssh_port"], minimum=1),
        "ssh_user": str(payload.get("ssh_user", "")).strip(),
        "ssh_key_path": str(payload.get("ssh_key_path", "")).strip(),
        "require_password_change": str(payload.get("require_password_change", "")).strip().lower()
        in {"1", "true", "yes", "on"},
    }


def public_profile(profile):
    payload = normalize_profile(profile)
    payload["ssh_key_path"] = ""
    payload["ssh_key_uploaded"] = bool(normalize_profile(profile).get("ssh_key_path"))
    return payload


def public_profiles(profiles):
    return [public_profile(profile) for profile in profiles]


def profile_signature(profile, username):
    normalized = normalize_profile(profile)
    return {
        "username": str(username or "").strip(),
        "profile_name": normalized["name"],
        "host": normalized["host"],
        "port": normalized["port"],
        "ssl_mode": normalized["ssl_mode"],
        "ssl_ca": normalized["ssl_ca"],
        "ssl_cert": normalized["ssl_cert"],
        "ssl_key": normalized["ssl_key"],
        "socket_enabled": normalized["socket_enabled"],
        "socket_path": normalized["socket_path"],
        "ssh_enabled": normalized["ssh_enabled"],
        "ssh_host": normalized["ssh_host"],
        "ssh_port": normalized["ssh_port"],
        "ssh_user": normalized["ssh_user"],
        "ssh_key_path": normalized["ssh_key_path"],
    }


class MySQLConnectionAdapter:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return self._connection.cursor(dictionary=True, buffered=True)

    def close(self):
        return self._connection.close()

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def select_db(self, database_name):
        self._connection.database = database_name

    def set_autocommit(self, enabled):
        self._connection.autocommit = bool(enabled)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def apply_mysql_ssl_profile(connect_kwargs, profile):
    ssl_mode = normalize_mysql_ssl_mode(profile.get("ssl_mode", DEFAULT_PROFILE["ssl_mode"]))
    if ssl_mode == "DISABLED":
        connect_kwargs["ssl_disabled"] = True
        return

    connect_kwargs["ssl_disabled"] = False
    connect_kwargs["client_flags"] = [ClientFlag.SSL]
    ssl_ca = str(profile.get("ssl_ca") or "").strip()
    ssl_cert = str(profile.get("ssl_cert") or "").strip()
    ssl_key = str(profile.get("ssl_key") or "").strip()
    if ssl_ca:
        connect_kwargs["ssl_ca"] = os.path.expanduser(ssl_ca)
    if ssl_cert:
        connect_kwargs["ssl_cert"] = os.path.expanduser(ssl_cert)
    if ssl_key:
        connect_kwargs["ssl_key"] = os.path.expanduser(ssl_key)
    if ssl_mode in {"VERIFY_CA", "VERIFY_IDENTITY"}:
        connect_kwargs["ssl_verify_cert"] = True
    if ssl_mode == "VERIFY_IDENTITY":
        connect_kwargs["ssl_verify_identity"] = True


def close_cached_connection(entry):
    if not isinstance(entry, dict):
        return
    cache = entry.pop(MYSQL_CONNECTION_CACHE_KEY, None)
    if not isinstance(cache, dict):
        return
    connection = cache.get("connection")
    tunnel = cache.get("tunnel")
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass
    if tunnel is not None:
        try:
            tunnel.stop()
        except Exception:
            pass


def test_connection(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def prepare_connection_for_use(connection, database_name, autocommit):
    connection.set_autocommit(autocommit)
    test_connection(connection)
    if database_name:
        connection.select_db(database_name)


def build_connect_kwargs(profile, credentials, database_name, target_host, target_port, connect_timeout, autocommit):
    connect_kwargs = {
        "user": credentials["username"],
        "password": credentials["password"],
        "database": database_name,
        "connection_timeout": connect_timeout,
        "charset": "utf8mb4",
        "autocommit": autocommit,
    }
    if profile.get("socket_enabled") and profile.get("socket_path"):
        connect_kwargs["unix_socket"] = os.path.expanduser(profile["socket_path"])
    else:
        connect_kwargs["host"] = target_host
        connect_kwargs["port"] = target_port
        apply_mysql_ssl_profile(connect_kwargs, profile)
    return connect_kwargs


def open_connection(profile, credentials, database_name, connect_timeout, autocommit):
    use_unix_socket = bool(profile.get("socket_enabled") and profile.get("socket_path"))
    tunnel = None
    target_host = profile["host"]
    target_port = profile["port"]

    if profile["ssh_enabled"]:
        if SSHTunnelForwarder is None:
            raise RuntimeError("SSH tunneling requires the `sshtunnel` package.")
        if not profile["ssh_host"] or not profile["ssh_user"] or not profile["ssh_key_path"]:
            raise ValueError("SSH-enabled profiles require jump host, SSH user, and private key path.")
        tunnel = SSHTunnelForwarder(
            (profile["ssh_host"], profile["ssh_port"]),
            ssh_username=profile["ssh_user"],
            ssh_pkey=os.path.expanduser(profile["ssh_key_path"]),
            remote_bind_address=(profile["host"], profile["port"]),
        )
        tunnel.start()
        target_host = "127.0.0.1"
        target_port = tunnel.local_bind_port

    try:
        connect_kwargs = build_connect_kwargs(
            profile,
            credentials,
            database_name,
            target_host,
            target_port,
            connect_timeout,
            autocommit,
        )
        return MySQLConnectionAdapter(mysql.connector.connect(**connect_kwargs)), tunnel
    except Exception:
        if tunnel is not None:
            try:
                tunnel.stop()
            except Exception:
                pass
        raise


@contextmanager
def borrow_connection(profile, credentials, session_entry, database_override=None, connect_timeout=5, autocommit=True):
    if not credentials["username"]:
        raise ValueError("No active MySQL login is available in the current session.")
    if session_entry is None:
        raise ValueError("No active MySQL login is available in the current session.")
    use_unix_socket = bool(profile.get("socket_enabled") and profile.get("socket_path"))
    if not use_unix_socket and not profile["host"]:
        raise ValueError("The selected profile does not have a MySQL host configured.")
    if use_unix_socket and profile["ssh_enabled"]:
        raise ValueError("Unix socket profiles cannot also use SSH tunneling.")

    signature = profile_signature(profile, credentials["username"])
    requested_database = database_override or profile["database"] or None
    cache = session_entry.get(MYSQL_CONNECTION_CACHE_KEY)
    if not isinstance(cache, dict) or cache.get("signature") != signature:
        close_cached_connection(session_entry)
        cache = None
    connection = cache.get("connection") if isinstance(cache, dict) else None
    try:
        if connection is not None:
            prepare_connection_for_use(connection, requested_database, autocommit)
        else:
            connection, tunnel = open_connection(
                profile=profile,
                credentials=credentials,
                database_name=requested_database,
                connect_timeout=connect_timeout,
                autocommit=autocommit,
            )
            session_entry[MYSQL_CONNECTION_CACHE_KEY] = {
                "signature": signature,
                "connection": connection,
                "tunnel": tunnel,
            }
            test_connection(connection)
    except Exception:
        close_cached_connection(session_entry)
        connection, tunnel = open_connection(
            profile=profile,
            credentials=credentials,
            database_name=requested_database,
            connect_timeout=connect_timeout,
            autocommit=autocommit,
        )
        session_entry[MYSQL_CONNECTION_CACHE_KEY] = {
            "signature": signature,
            "connection": connection,
            "tunnel": tunnel,
        }
        test_connection(connection)
    try:
        yield connection
    except Exception:
        if not autocommit:
            try:
                connection.rollback()
            except Exception:
                close_cached_connection(session_entry)
        raise
    finally:
        if connection is not None:
            try:
                connection.set_autocommit(True)
            except Exception:
                close_cached_connection(session_entry)


def quote_sql_string_literal(value):
    if value is None:
        normalized = ""
    else:
        normalized = str(value)
    escaped_value = (
        normalized
        .replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\x1a", "\\Z")
        .replace("'", "\\'")
        .replace('"', '\\"')
    )
    return f"'{escaped_value}'"
