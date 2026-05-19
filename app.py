import base64
import csv
import hmac
import io
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from modules import object_storage_util, profile_store, update_util
from modules.core_util import chmod_private_file, parse_iso_datetime as _parse_iso_datetime, utc_now_iso as _utc_now_iso
from modules.heatwave_pages import (
    build_dashboard_heatwave_summary as module_build_dashboard_heatwave_summary,
    build_heatwave_management_context as module_build_heatwave_management_context,
    build_heatwave_tables_context as module_build_heatwave_tables_context,
    build_heatwave_tables_export as module_build_heatwave_tables_export,
    handle_heatwave_management_action as module_handle_heatwave_management_action,
)
from modules.mysql_import import (
    build_mysql_import_page_state as module_build_mysql_import_page_state,
    build_mysql_import_plan as module_build_mysql_import_plan,
    delete_mysql_import_plan as module_delete_mysql_import_plan,
    load_mysql_import_plan as module_load_mysql_import_plan,
    run_mysql_import as module_run_mysql_import,
    save_mysql_import_plan as module_save_mysql_import_plan,
    validate_mysql_import_request as module_validate_mysql_import_request,
)
from modules.mysql_util import (
    DEFAULT_PROFILE,
    InterfaceError as MySQLInterfaceError,
    OperationalError as MySQLOperationalError,
    borrow_connection,
    close_cached_connection,
    normalize_profile,
    public_profile,
    public_profiles,
)
from modules.mysql_pages import (
    append_sql_workspace_history as module_append_sql_workspace_history,
    build_db_admin_context as module_build_db_admin_context,
    build_db_admin_export as module_build_db_admin_export,
    build_mysql_dashboard_context as module_build_mysql_dashboard_context,
    build_sql_workspace_context as module_build_sql_workspace_context,
    build_sql_workspace_explain_result as module_build_sql_workspace_explain_result,
    build_sql_workspace_history_entry as module_build_sql_workspace_history_entry,
    build_sql_workspace_result as module_build_sql_workspace_result,
    handle_db_admin_action as module_handle_db_admin_action,
)
from modules.monitoring_pages import (
    build_monitoring_charts_data as module_build_monitoring_charts_data,
    build_monitoring_charts_page_context as module_build_monitoring_charts_page_context,
    build_monitoring_dashboard_page_context as module_build_monitoring_dashboard_page_context,
    build_monitoring_locks_page_context as module_build_monitoring_locks_page_context,
    build_monitoring_report_download as module_build_monitoring_report_download,
    build_monitoring_report_page as module_build_monitoring_report_page,
)
from modules.status_variables import (
    build_empty_status_variable_page as module_build_empty_status_variable_page,
    fetch_grouped_status_variables as module_fetch_grouped_status_variables,
)

APP_TITLE = "MySQL DBConsole"
ROOT_DIR = Path(__file__).resolve().parent
PROFILE_STORE = ROOT_DIR / "profiles.json"
OBJECT_STORAGE_STORE = ROOT_DIR / "object_storage.json"
APP_VERSION_FILE = ROOT_DIR / "appver.json"
FLASK_SECRET_KEY_FILE = ROOT_DIR / ".flask_secret_key"
PROFILE_SSH_KEY_DIR = ROOT_DIR / "profile_ssh_keys"
IMPORT_CACHE_DIR = Path(tempfile.gettempdir()) / "dbconsole-import-cache"
DBCONSOLE_UPDATE_STATUS_FILE = Path(tempfile.gettempdir()) / "dbconsole-update-status.json"
DBCONSOLE_UPDATE_LOG_FILE = Path(tempfile.gettempdir()) / "dbconsole-update.log"
DBCONSOLE_UPDATE_WORKER = ROOT_DIR / "dbconsole_update_worker.py"
DBCONSOLE_UPDATE_MAX_LOG_LINES = 400
SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$]+$")
IMPORT_SQL_TYPE_RE = re.compile(r"^[A-Za-z]+(?: [A-Za-z]+)*(?:\([0-9, ]+\))?$")
DEFAULT_OBJECT_STORAGE = {
    "region": "",
    "namespace": "",
    "bucket_name": "",
    "bucket_prefix": "",
    "config_profile": "DEFAULT",
}
DBCONSOLE_SESSION_SCOPE_KEY = "_dbconsole_session_scope"
DBCONSOLE_SESSION_SCOPE_VALUE = "dbconsole"
DBCONSOLE_SESSION_VERSION_KEY = "_dbconsole_session_version"
DBCONSOLE_SESSION_VERSION = 1
DBCONSOLE_CREDENTIAL_SESSION_KEY = "dbconsole_server_session_id"
DBCONSOLE_VERSION_CHECK_SESSION_KEY = "dbconsole_version_check"
DBCONSOLE_UPDATE_POLL_TOKEN_SESSION_KEY = "dbconsole_update_poll_token"
DBCONSOLE_CSRF_SESSION_KEY = "dbconsole_csrf_token"
try:
    DBCONSOLE_CREDENTIAL_TTL_SECONDS = max(300, int(os.environ.get("DBCONSOLE_CREDENTIAL_TTL_SECONDS", "43200")))
except (TypeError, ValueError):
    DBCONSOLE_CREDENTIAL_TTL_SECONDS = 43200
DBCONSOLE_SESSION_COOKIE_NAME = os.environ.get("DBCONSOLE_SESSION_COOKIE_NAME", "dbconsole_session").strip() or "dbconsole_session"
DBCONSOLE_SESSION_COOKIE_PATH = os.environ.get("DBCONSOLE_SESSION_COOKIE_PATH", "/").strip() or "/"
DBCONSOLE_SESSION_COOKIE_SAMESITE = os.environ.get("DBCONSOLE_SESSION_COOKIE_SAMESITE", "Lax").strip() or "Lax"
DBCONSOLE_SESSION_COOKIE_SECURE_VALUE = os.environ.get("DBCONSOLE_SESSION_COOKIE_SECURE", "").strip().lower()
DBCONSOLE_SESSION_COOKIE_SECURE = DBCONSOLE_SESSION_COOKIE_SECURE_VALUE in {"1", "true", "yes", "on"}
SQL_WORKSPACE_HISTORY_SESSION_KEY = "sql_workspace_history"
ERROR_LOG_PRIORITY_OPTIONS = ("Note", "System", "Warning", "Error")
ERROR_LOG_PERIOD_OPTIONS = (
    {"value": "1h", "label": "1 hour", "hours": 1},
    {"value": "2h", "label": "2 hours", "hours": 2},
    {"value": "1d", "label": "1 day", "hours": 24},
    {"value": "all", "label": "ALL", "hours": None},
)
SQL_WORKSPACE_SECONDARY_ENGINE_OPTIONS = ("OFF", "ON", "FORCED")
DB_ADMIN_TABS = {"create", "select", "missing-primary-key", "event", "charset-collation"}
DB_ADMIN_DEFAULT_TAB = "select"
DB_ADMIN_TABLE_INFO_TABS = {"columns", "ddl", "indexes", "partitions", "preview", "modify-columns"}
DB_ADMIN_TABLE_INFO_DEFAULT_TAB = "columns"
DB_ADMIN_EVENT_OUTPUT_SESSION_KEY = "db_admin_event_output"
DBCONSOLE_UPDATE_RUNNING_STATES = {"starting", "running", "restarting"}
LOCAL_ADMIN_PROFILE_NAME = "local-admin-profile"
PROCESS_STARTED_AT = datetime.now(timezone.utc)
ACTIVE_DBCONSOLE_SESSIONS = {}
EVENT_SCHEDULE_OPTIONS = (
    {
        "value": "once",
        "label": "One Time",
        "schedule_type": "AT",
        "interval_value": "",
        "interval_field": "",
        "requires_at": True,
    },
    {
        "value": "every-minute",
        "label": "Every Minute",
        "schedule_type": "EVERY",
        "interval_value": 1,
        "interval_field": "MINUTE",
        "requires_at": False,
    },
    {
        "value": "every-hour",
        "label": "Every Hour",
        "schedule_type": "EVERY",
        "interval_value": 1,
        "interval_field": "HOUR",
        "requires_at": False,
    },
    {
        "value": "every-day",
        "label": "Every Day",
        "schedule_type": "EVERY",
        "interval_value": 1,
        "interval_field": "DAY",
        "requires_at": False,
    },
    {
        "value": "every-week",
        "label": "Every Week",
        "schedule_type": "EVERY",
        "interval_value": 1,
        "interval_field": "WEEK",
        "requires_at": False,
    },
    {
        "value": "every-month",
        "label": "Every Month",
        "schedule_type": "EVERY",
        "interval_value": 1,
        "interval_field": "MONTH",
        "requires_at": False,
    },
)
DEFAULT_EVENT_SCHEDULE_NAME = EVENT_SCHEDULE_OPTIONS[0]["value"]
DB_ADMIN_PREVIEW_MASKED_BASE_TYPES = {
    "binary",
    "bit",
    "blob",
    "geometry",
    "geometrycollection",
    "linestring",
    "longblob",
    "mediumblob",
    "multilinestring",
    "multipoint",
    "multipolygon",
    "point",
    "polygon",
    "tinyblob",
    "varbinary",
    "vector",
}
MONITORING_CHART_TAB_OPTIONS = (
    ("general", "General"),
    ("heatwave", "HeatWave"),
    ("replication", "Replication"),
)
IMPORT_TYPE_OPTIONS = [
    "BIGINT",
    "DOUBLE",
    "DECIMAL(18,6)",
    "TINYINT(1)",
    "VARCHAR(255)",
    "TEXT",
    "LONGTEXT",
    "DATE",
    "DATETIME",
    "JSON",
]
NAV_GROUPS = [
    {
        "label": "Admin",
        "items": [
            {"endpoint": "mysql_dashboard_page", "label": "Dashboard"},
            {"endpoint": "profile_page", "label": "Profile"},
            {"endpoint": "admin_status_variables_page", "label": "Status and Variables"},
            {"endpoint": "setup_object_storage_page", "label": "Setup Object Storage"},
            {"endpoint": "update_dbconsole_page", "label": "Auto-Update"},
        ],
    },
    {
        "label": "MySQL",
        "items": [
            {"endpoint": "db_admin_page", "label": "DB Admin"},
            {"endpoint": "sql_workspace_page", "label": "SQL Workspace"},
            {"endpoint": "mysql_import_page", "label": "Import"},
        ],
    },
    {
        "label": "HeatWave",
        "items": [
            {"endpoint": "hw_table_page", "label": "HW Table"},
            {"endpoint": "heatwave_management_page", "label": "HW Admin"},
            {"endpoint": "monitoring_performance_page", "label": "Performance Query"},
            {"endpoint": "monitoring_ml_page", "label": "ML Query"},
            {"endpoint": "monitoring_load_recovery_page", "label": "Table Load Recovery"},
        ],
    },
    {
        "label": "Monitoring",
        "items": [
            {"endpoint": "monitoring_dashboard_page", "label": "Dashboard"},
            {"endpoint": "monitoring_charts_page", "label": "Charts"},
            {"endpoint": "monitoring_locks_page", "label": "Locks"},
        ],
    },
]

STATUS_VARIABLE_SECTIONS = [
    {"key": "replication", "label": "Replication"},
    {"key": "performance_schema", "label": "Performance Schema"},
    {"key": "heatwave_rapid", "label": "HeatWave related"},
    {"key": "innodb", "label": "InnoDB"},
    {"key": "full_text", "label": "Full Text"},
    {"key": "mysqlx_specific", "label": "MySQLX Specific"},
    {"key": "security", "label": "Security"},
    {"key": "query_performance", "label": "Query Performance related"},
    {"key": "connection_threads", "label": "Connection & Threads"},
    {"key": "general", "label": "General"},
]


def load_flask_secret_key():
    configured_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if configured_secret:
        return configured_secret
    try:
        if FLASK_SECRET_KEY_FILE.exists():
            stored_secret = FLASK_SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
            if stored_secret:
                return stored_secret
        generated_secret = secrets.token_urlsafe(48)
        FLASK_SECRET_KEY_FILE.write_text(generated_secret + "\n", encoding="utf-8")
        try:
            FLASK_SECRET_KEY_FILE.chmod(0o600)
        except OSError:
            pass
        return generated_secret
    except OSError:
        return secrets.token_urlsafe(48)


app = Flask(__name__)
app.config["SECRET_KEY"] = load_flask_secret_key()
app.config["SESSION_COOKIE_NAME"] = DBCONSOLE_SESSION_COOKIE_NAME
app.config["SESSION_COOKIE_PATH"] = DBCONSOLE_SESSION_COOKIE_PATH
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = DBCONSOLE_SESSION_COOKIE_SAMESITE
app.config["SESSION_COOKIE_SECURE"] = DBCONSOLE_SESSION_COOKIE_SECURE


def _prime_dbconsole_session_scope():
    session[DBCONSOLE_SESSION_SCOPE_KEY] = DBCONSOLE_SESSION_SCOPE_VALUE
    session[DBCONSOLE_SESSION_VERSION_KEY] = DBCONSOLE_SESSION_VERSION


def _ensure_csrf_token():
    token = str(session.get(DBCONSOLE_CSRF_SESSION_KEY, "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
        token = secrets.token_urlsafe(32)
        session[DBCONSOLE_CSRF_SESSION_KEY] = token
    return token


def csrf_token():
    return _ensure_csrf_token()


@app.before_request
def ensure_dbconsole_session_scope():
    if (
        session.get(DBCONSOLE_SESSION_SCOPE_KEY) == DBCONSOLE_SESSION_SCOPE_VALUE
        and session.get(DBCONSOLE_SESSION_VERSION_KEY) == DBCONSOLE_SESSION_VERSION
    ):
        return
    session.clear()
    _prime_dbconsole_session_scope()


@app.before_request
def validate_csrf_token():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected_token = str(session.get(DBCONSOLE_CSRF_SESSION_KEY, "")).strip()
    supplied_token = str(
        request.form.get("_csrf_token", "")
        or request.headers.get("X-DBConsole-CSRF-Token", "")
    ).strip()
    if not expected_token or not supplied_token or not hmac.compare_digest(expected_token, supplied_token):
        abort(400, "Invalid or missing CSRF token.")


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": csrf_token}


@app.after_request
def add_authenticated_no_store_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if session.get("logged_in"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def ensure_profile_store():
    profile_store.ensure_profile_store(PROFILE_STORE)


def ensure_object_storage_store():
    object_storage_util.ensure_object_storage_store(OBJECT_STORAGE_STORE)


def _append_dbconsole_update_log(message):
    update_util.append_update_log(DBCONSOLE_UPDATE_LOG_FILE, message)


def _write_dbconsole_update_status(payload):
    return update_util.write_update_status(DBCONSOLE_UPDATE_STATUS_FILE, payload)


def _default_dbconsole_update_status():
    return update_util.default_update_status()


def _pid_is_alive(pid):
    return update_util.pid_is_alive(pid)


def _read_dbconsole_update_log_tail(max_lines=DBCONSOLE_UPDATE_MAX_LOG_LINES):
    return update_util.read_update_log_tail(DBCONSOLE_UPDATE_LOG_FILE, max_lines)


def _load_dbconsole_update_status_payload():
    return update_util.load_update_status_payload(DBCONSOLE_UPDATE_STATUS_FILE)


def _public_dbconsole_update_status(status):
    return update_util.public_update_status(status)


def _ensure_dbconsole_update_poll_token():
    token = str(session.get(DBCONSOLE_UPDATE_POLL_TOKEN_SESSION_KEY, "")).strip()
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        token = uuid4().hex
        session[DBCONSOLE_UPDATE_POLL_TOKEN_SESSION_KEY] = token
    return token


def _update_status_poll_token_is_valid(status):
    expected_token = str((status or {}).get("poll_token", "")).strip()
    supplied_token = str(request.headers.get("X-DBConsole-Update-Poll-Token", "")).strip()
    return bool(expected_token and supplied_token and hmac.compare_digest(expected_token, supplied_token))


def _maybe_finalize_dbconsole_update_status(status):
    return update_util.maybe_finalize_update_status(
        DBCONSOLE_UPDATE_STATUS_FILE,
        DBCONSOLE_UPDATE_LOG_FILE,
        PROCESS_STARTED_AT,
        status,
    )


def get_dbconsole_update_status():
    return update_util.get_update_status(
        DBCONSOLE_UPDATE_STATUS_FILE,
        DBCONSOLE_UPDATE_LOG_FILE,
        PROCESS_STARTED_AT,
        DBCONSOLE_UPDATE_MAX_LOG_LINES,
    )


def local_admin_profile_needs_bootstrap():
    profile = get_profile_by_name(LOCAL_ADMIN_PROFILE_NAME)
    if not profile:
        return True
    return not (
        profile.get("socket_enabled")
        and str(profile.get("socket_path") or "").strip()
        and not str(profile.get("host") or "").strip()
    )


def can_access_update_page():
    return is_local_admin_profile_session() or local_admin_profile_needs_bootstrap()


UPDATE_LOCAL_ADMIN_RESET_FIELDS = {
    "reset_local_mysql_admin_password",
    "confirm_reset_local_mysql_admin_password",
    "confirm_local_mysql_admin_reset",
}


def normalize_update_local_admin_bootstrap_credentials(form_payload, require_password=False):
    form_has_reset_fields = any(field_name in form_payload for field_name in UPDATE_LOCAL_ADMIN_RESET_FIELDS)
    if require_password and not form_has_reset_fields:
        return {}
    password = str(form_payload.get("reset_local_mysql_admin_password", "") or "")
    confirm_password = str(form_payload.get("confirm_reset_local_mysql_admin_password", "") or "")
    acknowledged = str(form_payload.get("confirm_local_mysql_admin_reset", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not password and not confirm_password:
        if require_password:
            raise ValueError("Enter and confirm the temporary localadmin password for first-time Auto-Update bootstrap.")
        return {}
    if not password:
        raise ValueError("Enter the new localadmin password.")
    if not confirm_password:
        raise ValueError("Confirm the new localadmin password.")
    if password != confirm_password:
        raise ValueError("Localadmin password confirmation does not match.")
    if not acknowledged:
        raise ValueError("Confirm that Auto-Update should set up the localadmin MySQL password.")

    profile = get_session_profile() if is_local_admin_profile_session() else {}
    username = str(profile.get("username") or "localadmin").strip() or "localadmin"
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}", username):
        raise ValueError("Localadmin MySQL username in the current profile is not valid for setup.")
    return {
        "LOCAL_MYSQL_ADMIN_USER": username,
        "LOCAL_MYSQL_ADMIN_PASSWORD": password,
        "LOCAL_MYSQL_PROFILE_NAME": LOCAL_ADMIN_PROFILE_NAME,
    }


def start_dbconsole_update_job(local_admin_password_reset=None):
    return update_util.start_update_job(
        repo_dir=ROOT_DIR,
        worker_script=DBCONSOLE_UPDATE_WORKER,
        status_file=DBCONSOLE_UPDATE_STATUS_FILE,
        log_file=DBCONSOLE_UPDATE_LOG_FILE,
        python_executable=sys.executable,
        service_pid=os.getpid(),
        poll_token=_ensure_dbconsole_update_poll_token(),
        process_started_at=PROCESS_STARTED_AT,
        max_log_lines=DBCONSOLE_UPDATE_MAX_LOG_LINES,
        local_admin_password_reset=local_admin_password_reset,
    )


def get_local_app_version():
    return update_util.get_local_app_version(APP_VERSION_FILE)


def _normalize_git_remote_url(remote_url):
    return update_util.normalize_git_remote_url(remote_url)


def infer_app_version_url():
    return update_util.infer_app_version_url(ROOT_DIR)


def normalize_repository_version_request_url(version_url):
    return update_util.normalize_repository_version_request_url(version_url)


def build_repository_version_ssl_context():
    return update_util.build_repository_version_ssl_context()


def read_repository_version_payload(response_body):
    return update_util.read_repository_version_payload(response_body)


def fetch_repository_app_version(timeout=2):
    return update_util.fetch_repository_app_version(ROOT_DIR, timeout=timeout)


def refresh_repo_version_check():
    local_version = get_local_app_version()
    repo_result = fetch_repository_app_version()
    repo_version = repo_result.get("repo_version") or "-"
    update_available = bool(repo_version != "-" and local_version != "-" and repo_version != local_version)
    version_check = {
        "local_version": local_version,
        "repo_version": repo_version,
        "update_available": update_available,
        "checked_at": _utc_now_iso(),
        "error": repo_result.get("error", ""),
        "version_url": repo_result.get("version_url", ""),
    }
    session[DBCONSOLE_VERSION_CHECK_SESSION_KEY] = version_check
    return version_check


def should_show_update_page_after_login(version_check):
    if not can_access_update_page():
        return False
    if version_check.get("update_available"):
        return True
    return bool(version_check.get("error"))


def ensure_import_cache_dir():
    IMPORT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def is_system_schema_name(schema_name):
    normalized_name = str(schema_name or "").strip().lower()
    return normalized_name in SYSTEM_SCHEMAS or normalized_name.startswith("mysql_")


def _normalize_int(value, default, minimum=None):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and normalized < minimum:
        return default
    return normalized


def load_profiles():
    return profile_store.load_profiles(PROFILE_STORE)


def save_profiles(profiles):
    profile_store.save_profiles(PROFILE_STORE, profiles)


def get_profile_by_name(profile_name):
    return profile_store.get_profile_by_name(PROFILE_STORE, profile_name)


def is_local_admin_profile_session():
    profile = get_session_profile()
    return (
        profile.get("name") == LOCAL_ADMIN_PROFILE_NAME
        and bool(profile.get("socket_enabled"))
        and bool(str(profile.get("socket_path") or "").strip())
    )


def local_admin_password_change_required():
    profile = get_session_profile()
    return bool(is_local_admin_profile_session() and profile.get("require_password_change"))


def clear_local_admin_password_change_required():
    profiles = load_profiles()
    changed = False
    updated_profiles = []
    for profile in profiles:
        if profile.get("name") == LOCAL_ADMIN_PROFILE_NAME and profile.get("require_password_change"):
            profile = dict(profile)
            profile["require_password_change"] = False
            changed = True
        updated_profiles.append(profile)
    if changed:
        save_profiles(updated_profiles)
        current_profile = get_session_profile()
        if current_profile.get("name") == LOCAL_ADMIN_PROFILE_NAME:
            current_profile["require_password_change"] = False
            set_session_profile(current_profile)


def nav_groups_for_current_session():
    if is_local_admin_profile_session():
        return NAV_GROUPS
    can_bootstrap_update = local_admin_profile_needs_bootstrap()
    filtered_groups = []
    for group in NAV_GROUPS:
        filtered_items = []
        for item in group["items"]:
            if item["endpoint"] == "profile_page":
                continue
            if item["endpoint"] == "update_dbconsole_page" and not can_bootstrap_update:
                continue
            filtered_items.append(item)
        filtered_groups.append({**group, "items": filtered_items})
    return filtered_groups


def _safe_profile_key_dir_name(profile_name):
    return profile_store.safe_profile_key_dir_name(profile_name)


def save_uploaded_profile_ssh_key(profile_name, upload_storage):
    return profile_store.save_uploaded_profile_ssh_key(PROFILE_SSH_KEY_DIR, profile_name, upload_storage)


def normalize_object_storage(payload):
    return object_storage_util.normalize_object_storage(payload)


def load_object_storage_config():
    return object_storage_util.load_object_storage_config(OBJECT_STORAGE_STORE)


def save_object_storage_config(payload):
    object_storage_util.save_object_storage_config(OBJECT_STORAGE_STORE, payload)


def fetch_setup_status():
    return object_storage_util.fetch_setup_status(OBJECT_STORAGE_STORE)


def get_session_profile():
    payload = session.get("connection_profile")
    if not payload:
        return normalize_profile(DEFAULT_PROFILE)
    return normalize_profile(payload)


def set_session_profile(profile):
    session["connection_profile"] = normalize_profile(profile)
    session["profile_name"] = normalize_profile(profile)["name"]


def _get_server_session_id():
    return str(session.get(DBCONSOLE_CREDENTIAL_SESSION_KEY, "")).strip()


def _cleanup_expired_server_sessions():
    now = datetime.now(timezone.utc)
    expired_session_ids = []
    for server_session_id, entry in list(ACTIVE_DBCONSOLE_SESSIONS.items()):
        created_at = _parse_iso_datetime((entry or {}).get("created_at"))
        if created_at is None or (now - created_at).total_seconds() > DBCONSOLE_CREDENTIAL_TTL_SECONDS:
            expired_session_ids.append(server_session_id)
    for server_session_id in expired_session_ids:
        entry = ACTIVE_DBCONSOLE_SESSIONS.pop(server_session_id, None)
        close_cached_connection(entry)


def _get_server_session_entry():
    _cleanup_expired_server_sessions()
    server_session_id = _get_server_session_id()
    if not server_session_id:
        return None
    entry = ACTIVE_DBCONSOLE_SESSIONS.get(server_session_id)
    return entry if isinstance(entry, dict) else None


def set_session_credentials(username, password):
    old_session_id = _get_server_session_id()
    if old_session_id:
        old_entry = ACTIVE_DBCONSOLE_SESSIONS.pop(old_session_id, None)
        close_cached_connection(old_entry)
    server_session_id = uuid4().hex
    ACTIVE_DBCONSOLE_SESSIONS[server_session_id] = {
        "username": str(username or "").strip(),
        "password": password or "",
        "created_at": _utc_now_iso(),
    }
    session[DBCONSOLE_CREDENTIAL_SESSION_KEY] = server_session_id


def get_session_credentials():
    entry = _get_server_session_entry()
    if entry is not None:
        return {
            "username": str(entry.get("username", "")).strip(),
            "password": entry.get("password", ""),
        }
    return {
        "username": "",
        "password": "",
    }


def get_session_username():
    return get_session_credentials()["username"]


def has_active_login_state():
    return bool(session.get("logged_in") and _get_server_session_entry())


def clear_login_state(keep_profile=True):
    server_session_id = _get_server_session_id()
    if server_session_id:
        entry = ACTIVE_DBCONSOLE_SESSIONS.pop(server_session_id, None)
        close_cached_connection(entry)
    profile = session.get("connection_profile") if keep_profile else None
    profile_name = session.get("profile_name") if keep_profile else None
    session.clear()
    _prime_dbconsole_session_scope()
    if keep_profile and profile:
        session["connection_profile"] = profile
        session["profile_name"] = profile_name


def _redirect_to_login_for_mysql_unavailable(error):
    profile_name = str(session.get("profile_name", "")).strip()
    clear_login_state(keep_profile=True)
    flash(f"MySQL connection is unavailable: {error}", "error")
    redirect_values = {"profile": profile_name} if profile_name else {}
    return redirect(url_for("login", **redirect_values))


def session_login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not has_active_login_state():
            flash("Log in to continue.", "error")
            clear_login_state(keep_profile=True)
            return redirect(url_for("login"))
        if local_admin_password_change_required() and request.endpoint != "local_admin_password_page":
            return redirect(url_for("local_admin_password_page"))
        return view(*args, **kwargs)

    return wrapped_view


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not has_active_login_state():
            flash("Log in to continue.", "error")
            clear_login_state(keep_profile=True)
            return redirect(url_for("login"))
        if local_admin_password_change_required() and request.endpoint != "local_admin_password_page":
            return redirect(url_for("local_admin_password_page"))
        try:
            with mysql_connection(connect_timeout=3):
                pass
        except Exception as error:
            return _redirect_to_login_for_mysql_unavailable(error)
        return view(*args, **kwargs)

    return wrapped_view


def quote_identifier(identifier):
    candidate = str(identifier or "").strip()
    if not IDENTIFIER_RE.fullmatch(candidate):
        raise ValueError(f"Invalid identifier: {candidate!r}")
    return f"`{candidate}`"


def quote_sql_string(value):
    return "'" + str(value or "").replace("\\", "\\\\").replace("'", "''") + "'"


def normalize_page_number(value):
    return _normalize_int(value, 1, minimum=1)


def normalize_db_admin_tab(value):
    normalized = str(value or "").strip().lower()
    if normalized not in DB_ADMIN_TABS:
        return DB_ADMIN_DEFAULT_TAB
    return normalized


def normalize_db_admin_table_info_tab(value):
    normalized = str(value or "").strip().lower()
    if normalized not in DB_ADMIN_TABLE_INFO_TABS:
        return DB_ADMIN_TABLE_INFO_DEFAULT_TAB
    return normalized


def normalize_event_schedule_name(value):
    normalized = str(value or "").strip().lower()
    if any(option["value"] == normalized for option in EVENT_SCHEDULE_OPTIONS):
        return normalized
    return DEFAULT_EVENT_SCHEDULE_NAME


def get_event_schedule_option(value):
    normalized = normalize_event_schedule_name(value)
    return next(
        option
        for option in EVENT_SCHEDULE_OPTIONS
        if option["value"] == normalized
    )


def normalize_sql_workspace_secondary_engine(value):
    normalized = str(value or "").strip().upper()
    if normalized not in SQL_WORKSPACE_SECONDARY_ENGINE_OPTIONS:
        return "ON"
    return normalized


def mysql_connection(database_override=None, connect_timeout=5, autocommit=True):
    return borrow_connection(
        profile=get_session_profile(),
        credentials=get_session_credentials(),
        session_entry=_get_server_session_entry(),
        database_override=database_override,
        connect_timeout=connect_timeout,
        autocommit=autocommit,
    )


@app.errorhandler(MySQLOperationalError)
def handle_mysql_operational_error(error):
    if not session.get("logged_in"):
        return f"MySQL operational error: {error}", 500
    return _redirect_to_login_for_mysql_unavailable(error)


@app.errorhandler(MySQLInterfaceError)
def handle_mysql_interface_error(error):
    if not session.get("logged_in"):
        return f"MySQL interface error: {error}", 500
    return _redirect_to_login_for_mysql_unavailable(error)


def _apply_query_session_options(cursor, *, use_secondary_engine=""):
    normalized_secondary_engine = normalize_sql_workspace_secondary_engine(use_secondary_engine) if use_secondary_engine else ""
    if normalized_secondary_engine:
        cursor.execute(f"SET SESSION use_secondary_engine = {normalized_secondary_engine}")


def execute_query(sql, params=None, *, database=None, use_secondary_engine=""):
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            _apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            return cursor.fetchall()


def execute_multi_result_query(sql, params=None, *, database=None, use_secondary_engine=""):
    result_sets = []
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            _apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)

            result_index = 1
            while True:
                columns = [item[0] for item in cursor.description] if cursor.description else []
                rows = cursor.fetchall() if columns else []
                if columns or rows:
                    result_sets.append(
                        {
                            "label": f"Result {result_index}",
                            "columns": columns,
                            "rows": rows,
                        }
                    )
                    result_index += 1
                if not cursor.nextset():
                    break
    return result_sets


def _collect_sql_workspace_cursor_results(cursor, sql, statement_index):
    result_sets = []
    result_index = 1
    while True:
        columns = [item[0] for item in cursor.description] if cursor.description else []
        if columns:
            rows = cursor.fetchall()
            label = f"Statement {statement_index}"
            if result_index > 1:
                label = f"Statement {statement_index}.{result_index}"
            result_sets.append(
                {
                    "label": label,
                    "columns": columns,
                    "rows": rows,
                    "statement": sql,
                }
            )
            result_index += 1
        else:
            rowcount = cursor.rowcount
            if rowcount is not None and rowcount >= 0:
                result_sets.append(
                    {
                        "label": f"Statement {statement_index}",
                        "kind": "message",
                        "message": f"Statement completed. Rows affected: {rowcount}.",
                        "statement": sql,
                    }
                )
        if not cursor.nextset():
            break

    if not result_sets:
        result_sets.append(
            {
                "label": f"Statement {statement_index}",
                "kind": "message",
                "message": "Statement completed without a result set.",
                "statement": sql,
            }
        )
    return result_sets


def execute_sql_workspace_statements(statements, *, database=None, use_secondary_engine=""):
    result_sets = []
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            _apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
            for statement_index, statement in enumerate(statements, start=1):
                cursor.execute(statement)
                result_sets.extend(_collect_sql_workspace_cursor_results(cursor, statement, statement_index))
    return result_sets


def execute_statement(sql, params=None, *, database=None):
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            rowcount = cursor.rowcount
            while cursor.nextset():
                pass
            return rowcount


def _normalize_sql_workspace_statement(sql_text):
    statement = str(sql_text or "").strip()
    if not statement:
        raise ValueError("Enter a SQL statement.")
    return statement


def split_sql_workspace_statements(sql_text, *, require_terminator=False):
    text = str(sql_text or "")
    statements = []
    current = []
    quote_char = ""
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    last_statement_terminated = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_line_comment:
            current.append(char)
            if char in "\r\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if quote_char:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                if next_char == quote_char:
                    current.append(next_char)
                    index += 2
                    continue
                quote_char = ""
            index += 1
            continue

        if in_backtick:
            current.append(char)
            if char == "`":
                if next_char == "`":
                    current.append(next_char)
                    index += 2
                    continue
                in_backtick = False
            index += 1
            continue

        if char == "-" and next_char == "-" and (index + 2 >= length or text[index + 2].isspace()):
            current.append(char)
            current.append(next_char)
            in_line_comment = True
            index += 2
            continue
        if char == "#":
            current.append(char)
            in_line_comment = True
            index += 1
            continue
        if char == "/" and next_char == "*":
            current.append(char)
            current.append(next_char)
            in_block_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote_char = char
            escaped = False
            index += 1
            continue
        if char == "`":
            current.append(char)
            in_backtick = True
            index += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            last_statement_terminated = True
            index += 1
            continue

        if not char.isspace():
            last_statement_terminated = False
        current.append(char)
        index += 1

    if quote_char or in_backtick or in_block_comment:
        raise ValueError("SQL text contains an unterminated quote, identifier, or block comment.")

    trailing_statement = "".join(current).strip()
    if trailing_statement:
        if require_terminator and not last_statement_terminated:
            raise ValueError("Every SQL statement must be terminated by ';'.")
        statements.append(trailing_statement)

    if not statements:
        raise ValueError("Enter a SQL statement.")
    return statements


def _normalize_sql_workspace_explain_statement(sql_text):
    statements = split_sql_workspace_statements(sql_text)
    if len(statements) != 1:
        raise ValueError("Explain supports one statement at a time.")
    statement = statements[0].rstrip().rstrip(";").strip()
    if not statement:
        raise ValueError("Enter a SQL statement.")
    if statement.lower().startswith("explain"):
        raise ValueError("Enter the SQL statement itself. Explain adds EXPLAIN automatically.")
    return statement


def fetch_scalar(sql, params=None, *, database=None, default=None):
    rows = execute_query(sql, params=params, database=database)
    if not rows:
        return default
    return next(iter(rows[0].values()))


def fetch_database_inventory():
    rows = execute_query(
        """
        SELECT
          s.schema_name AS database_name_value,
          COALESCE(table_stats.object_count, 0) AS object_count_value,
          COALESCE(table_stats.base_table_count, 0) AS base_table_count_value,
          COALESCE(table_stats.innodb_table_count, 0) AS innodb_table_count_value,
          COALESCE(table_stats.view_count, 0) AS view_count_value,
          COALESCE(table_stats.data_bytes, 0) AS data_bytes_value,
          COALESCE(table_stats.index_bytes, 0) AS index_bytes_value,
          COALESCE(table_stats.total_bytes, 0) AS total_bytes_value,
          COALESCE(routine_stats.routine_count, 0) AS routine_count_value
        FROM information_schema.schemata AS s
        LEFT JOIN (
          SELECT
            table_schema,
            COUNT(*) AS object_count,
            SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END) AS base_table_count,
            SUM(CASE WHEN UPPER(COALESCE(engine, '')) = 'INNODB' THEN 1 ELSE 0 END) AS innodb_table_count,
            SUM(CASE WHEN table_type = 'VIEW' THEN 1 ELSE 0 END) AS view_count,
            COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length ELSE 0 END), 0) AS data_bytes,
            COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN index_length ELSE 0 END), 0) AS index_bytes,
            COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length + index_length ELSE 0 END), 0) AS total_bytes
          FROM information_schema.tables
          GROUP BY table_schema
        ) AS table_stats
          ON table_stats.table_schema = s.schema_name
        LEFT JOIN (
          SELECT
            routine_schema,
            SUM(CASE WHEN routine_type IN ('PROCEDURE', 'FUNCTION') THEN 1 ELSE 0 END) AS routine_count
          FROM information_schema.routines
          GROUP BY routine_schema
        ) AS routine_stats
          ON routine_stats.routine_schema = s.schema_name
        ORDER BY s.schema_name
        """
    )
    inventory = []
    for row in rows:
        database_name = row["database_name_value"]
        total_bytes = row["total_bytes_value"] or 0
        inventory.append(
            {
                "database_name": database_name,
                "table_count": row["object_count_value"] or 0,
                "object_count": row["object_count_value"] or 0,
                "base_table_count": row["base_table_count_value"] or 0,
                "innodb_table_count": row["innodb_table_count_value"] or 0,
                "view_count": row["view_count_value"] or 0,
                "routine_count": row["routine_count_value"] or 0,
                "procedure_count": row["routine_count_value"] or 0,
                "data_bytes": row["data_bytes_value"] or 0,
                "index_bytes": row["index_bytes_value"] or 0,
                "total_bytes": total_bytes,
                "db_size_label": _format_bytes(total_bytes),
                "is_system": is_system_schema_name(database_name),
            }
        )
    return inventory


def fetch_dashboard_innodb_table_rows():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS table_name_value,
          engine AS engine_value,
          table_rows AS table_rows_value
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND UPPER(COALESCE(engine, '')) = 'INNODB'
          AND table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND table_schema NOT LIKE 'mysql@_%' ESCAPE '@'
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "table_name": row["table_name_value"],
            "engine": row["engine_value"] or "InnoDB",
            "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
        }
        for row in rows
    ]


def fetch_dashboard_view_rows():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS view_name_value
        FROM information_schema.views
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND table_schema NOT LIKE 'mysql@_%' ESCAPE '@'
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "view_name": row["view_name_value"],
        }
        for row in rows
    ]


def fetch_dashboard_routine_rows():
    rows = execute_query(
        """
        SELECT
          routine_schema AS database_name_value,
          routine_type AS routine_type_value,
          routine_name AS routine_name_value
        FROM information_schema.routines
        WHERE routine_type IN ('PROCEDURE', 'FUNCTION')
          AND routine_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND routine_schema NOT LIKE 'mysql@_%' ESCAPE '@'
        ORDER BY routine_schema, routine_type, routine_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "routine_type": row["routine_type_value"] or "-",
            "routine_name": row["routine_name_value"],
        }
        for row in rows
    ]


def fetch_tables_for_database(database_name):
    if not database_name:
        return []
    rows = execute_query(
        """
        SELECT
          table_name AS table_name_value,
          engine AS engine_value,
          table_rows AS table_rows_value,
          table_comment AS table_comment_value,
          create_options AS create_options_value
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
        """,
        [database_name],
    )
    tables = []
    for row in rows:
        create_options = row["create_options_value"] or ""
        heatwave_configured = "SECONDARY_ENGINE=RAPID" in create_options.upper()
        tables.append(
            {
                "table_name": row["table_name_value"],
                "engine": row["engine_value"] or "-",
                "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "table_comment": row["table_comment_value"] or "",
                "create_options": create_options,
                "heatwave_configured": heatwave_configured,
            }
        )
    return tables


def fetch_charset_collation_options():
    rows = execute_query(
        """
        SELECT
          cs.character_set_name AS character_set_name_value,
          cs.default_collate_name AS default_collation_name_value,
          c.collation_name AS collation_name_value,
          c.is_default AS is_default_value
        FROM information_schema.character_sets AS cs
        JOIN information_schema.collations AS c
          ON c.character_set_name = cs.character_set_name
        ORDER BY cs.character_set_name, c.is_default DESC, c.collation_name
        """
    )
    charset_lookup = {}
    collations = []
    for row in rows:
        charset_name = row["character_set_name_value"]
        default_collation = row["default_collation_name_value"] or ""
        if charset_name not in charset_lookup:
            charset_lookup[charset_name] = {
                "charset_name": charset_name,
                "default_collation": default_collation,
            }
        collations.append(
            {
                "charset_name": charset_name,
                "collation_name": row["collation_name_value"],
                "is_default": str(row["is_default_value"] or "").upper() == "YES",
            }
        )
    return {
        "charsets": sorted(charset_lookup.values(), key=lambda item: item["charset_name"].lower()),
        "collations": collations,
    }


def _fetch_db_admin_charset_column_rows(database_name):
    if not database_name:
        return []
    rows = execute_query(
        """
        SELECT
          c.table_name AS table_name_value,
          c.column_name AS column_name_value,
          c.column_type AS column_type_value,
          c.character_set_name AS character_set_name_value,
          c.collation_name AS collation_name_value,
          c.column_key AS column_key_value,
          c.extra AS extra_value,
          c.ordinal_position AS ordinal_position_value,
          GROUP_CONCAT(
            DISTINCT CONCAT(
              k.constraint_name,
              ' -> ',
              k.referenced_table_schema,
              '.',
              k.referenced_table_name,
              '.',
              k.referenced_column_name
            )
            ORDER BY k.constraint_name
            SEPARATOR '; '
          ) AS outgoing_foreign_keys_value
        FROM information_schema.columns AS c
        LEFT JOIN information_schema.key_column_usage AS k
          ON k.table_schema = c.table_schema
         AND k.table_name = c.table_name
         AND k.column_name = c.column_name
         AND k.referenced_table_name IS NOT NULL
        WHERE c.table_schema = %s
          AND c.character_set_name IS NOT NULL
        GROUP BY
          c.table_name,
          c.column_name,
          c.column_type,
          c.character_set_name,
          c.collation_name,
          c.column_key,
          c.extra,
          c.ordinal_position
        ORDER BY c.table_name, c.ordinal_position
        """,
        [database_name],
    )
    return [
        {
            "table_name": row["table_name_value"],
            "column_name": row["column_name_value"],
            "column_type": row["column_type_value"],
            "charset_name": row["character_set_name_value"] or "",
            "collation_name": row["collation_name_value"] or "",
            "column_key": row["column_key_value"] or "",
            "extra": row["extra_value"] or "",
            "ordinal_position": row["ordinal_position_value"],
            "outgoing_foreign_keys": row["outgoing_foreign_keys_value"] or "",
            "has_outgoing_foreign_key": bool(row["outgoing_foreign_keys_value"]),
        }
        for row in rows
    ]


def _fetch_foreign_key_definitions(database_name, *, table_name="", referenced_table_name="", selected_columns=None):
    normalized_database = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()
    normalized_referenced_table = str(referenced_table_name or "").strip()
    if not normalized_database:
        return []

    selected_column_set = {
        str(column_name or "").strip()
        for column_name in selected_columns or []
        if str(column_name or "").strip()
    }
    sql = """
        SELECT
          k.constraint_schema AS constraint_schema_value,
          k.constraint_name AS constraint_name_value,
          k.table_schema AS table_schema_value,
          k.table_name AS table_name_value,
          k.column_name AS column_name_value,
          k.ordinal_position AS ordinal_position_value,
          k.referenced_table_schema AS referenced_table_schema_value,
          k.referenced_table_name AS referenced_table_name_value,
          k.referenced_column_name AS referenced_column_name_value,
          rc.update_rule AS update_rule_value,
          rc.delete_rule AS delete_rule_value
        FROM information_schema.key_column_usage AS k
        LEFT JOIN information_schema.referential_constraints AS rc
          ON rc.constraint_schema = k.constraint_schema
         AND rc.constraint_name = k.constraint_name
         AND rc.table_name = k.table_name
        WHERE k.referenced_table_name IS NOT NULL
    """
    params = []
    if normalized_table:
        sql += " AND k.table_schema = %s AND k.table_name = %s"
        params.extend([normalized_database, normalized_table])
    elif normalized_referenced_table:
        sql += " AND k.referenced_table_schema = %s AND k.referenced_table_name = %s"
        params.extend([normalized_database, normalized_referenced_table])
    else:
        sql += " AND k.table_schema = %s"
        params.append(normalized_database)
    sql += " ORDER BY k.table_schema, k.table_name, k.constraint_name, k.ordinal_position"

    grouped = {}
    ordered_keys = []
    for row in execute_query(sql, params):
        key = (
            row["table_schema_value"],
            row["table_name_value"],
            row["constraint_name_value"],
        )
        if key not in grouped:
            grouped[key] = {
                "constraint_schema": row["constraint_schema_value"],
                "constraint_name": row["constraint_name_value"],
                "table_schema": row["table_schema_value"],
                "table_name": row["table_name_value"],
                "referenced_table_schema": row["referenced_table_schema_value"],
                "referenced_table_name": row["referenced_table_name_value"],
                "update_rule": row["update_rule_value"] or "",
                "delete_rule": row["delete_rule_value"] or "",
                "columns": [],
                "referenced_columns": [],
            }
            ordered_keys.append(key)
        grouped[key]["columns"].append(row["column_name_value"])
        grouped[key]["referenced_columns"].append(row["referenced_column_name_value"])

    definitions = []
    for key in ordered_keys:
        definition = grouped[key]
        if selected_column_set and not any(column in selected_column_set for column in definition["columns"]):
            continue
        safe_table_schema = quote_identifier(definition["table_schema"])
        safe_table = quote_identifier(definition["table_name"])
        safe_referenced_schema = quote_identifier(definition["referenced_table_schema"])
        safe_referenced_table = quote_identifier(definition["referenced_table_name"])
        column_list = ", ".join(quote_identifier(column) for column in definition["columns"])
        referenced_column_list = ", ".join(quote_identifier(column) for column in definition["referenced_columns"])
        create_statement = (
            f"ALTER TABLE {safe_table_schema}.{safe_table} "
            f"ADD CONSTRAINT {_quote_existing_mysql_identifier(definition['constraint_name'])} "
            f"FOREIGN KEY ({column_list}) "
            f"REFERENCES {safe_referenced_schema}.{safe_referenced_table} ({referenced_column_list})"
        )
        if definition["delete_rule"]:
            create_statement += f" ON DELETE {definition['delete_rule']}"
        if definition["update_rule"]:
            create_statement += f" ON UPDATE {definition['update_rule']}"
        drop_statement = (
            f"ALTER TABLE {safe_table_schema}.{safe_table} "
            f"DROP FOREIGN KEY {_quote_existing_mysql_identifier(definition['constraint_name'])}"
        )
        definitions.append(
            {
                **definition,
                "column_list": ", ".join(definition["columns"]),
                "referenced_column_list": ", ".join(definition["referenced_columns"]),
                "drop_statement": drop_statement,
                "create_statement": create_statement,
            }
        )
    return definitions


def fetch_db_admin_charset_collation_report(database_name):
    normalized_database = str(database_name or "").strip()
    report = {
        "rows": [],
        "error": "",
        "table_count": 0,
        "text_column_count": 0,
        "column_difference_count": 0,
    }
    if not normalized_database:
        return report

    table_rows = execute_query(
        """
        SELECT
          t.table_name AS table_name_value,
          t.engine AS engine_value,
          t.table_rows AS table_rows_value,
          t.table_collation AS table_collation_value,
          co.character_set_name AS table_charset_value
        FROM information_schema.tables AS t
        LEFT JOIN information_schema.collations AS co
          ON co.collation_name = t.table_collation
        WHERE t.table_schema = %s
          AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
        """,
        [normalized_database],
    )
    column_rows = _fetch_db_admin_charset_column_rows(normalized_database)
    outgoing_foreign_keys = _fetch_foreign_key_definitions(normalized_database)
    outgoing_by_table = {}
    for definition in outgoing_foreign_keys:
        outgoing_by_table.setdefault(definition["table_name"], []).append(definition)
    columns_by_table = {}
    for column in column_rows:
        columns_by_table.setdefault(column["table_name"], []).append(column)

    for row in table_rows:
        table_name = row["table_name_value"]
        incoming_foreign_keys = _fetch_foreign_key_definitions(
            normalized_database,
            referenced_table_name=table_name,
        )
        table_charset = row["table_charset_value"] or ""
        table_collation = row["table_collation_value"] or ""
        columns = []
        difference_count = 0
        for column in columns_by_table.get(table_name, []):
            column_row = dict(column)
            column_row["differs_from_table"] = bool(
                table_collation
                and (
                    column_row["charset_name"] != table_charset
                    or column_row["collation_name"] != table_collation
                )
            )
            if column_row["differs_from_table"]:
                difference_count += 1
            columns.append(column_row)

        report["rows"].append(
            {
                "database_name": normalized_database,
                "table_name": table_name,
                "engine": row["engine_value"] or "-",
                "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "table_charset": table_charset or "-",
                "table_collation": table_collation or "-",
                "text_columns": columns,
                "text_column_count": len(columns),
                "column_difference_count": difference_count,
                "has_column_differences": difference_count > 0,
                "foreign_key_definitions": outgoing_by_table.get(table_name, []),
                "referenced_by_foreign_keys": incoming_foreign_keys,
            }
        )
        report["text_column_count"] += len(columns)
        report["column_difference_count"] += difference_count

    report["table_count"] = len(report["rows"])
    return report


def _validate_charset_collation_pair(charset_name, collation_name):
    normalized_charset = str(charset_name or "").strip()
    normalized_collation = str(collation_name or "").strip()
    if not normalized_charset:
        raise ValueError("Choose a target character set.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", normalized_charset):
        raise ValueError("Target character set is invalid.")
    if normalized_collation and not re.fullmatch(r"[A-Za-z0-9_]+", normalized_collation):
        raise ValueError("Target collation is invalid.")

    if not normalized_collation:
        normalized_collation = fetch_scalar(
            """
            SELECT default_collate_name
            FROM information_schema.character_sets
            WHERE character_set_name = %s
            """,
            [normalized_charset],
            default="",
        )
        normalized_collation = str(normalized_collation or "").strip()
    if not normalized_collation:
        raise ValueError(f"Character set `{normalized_charset}` was not found.")

    match_count = fetch_scalar(
        """
        SELECT COUNT(*)
        FROM information_schema.collations
        WHERE character_set_name = %s
          AND collation_name = %s
        """,
        [normalized_charset, normalized_collation],
        default=0,
    )
    if not match_count:
        raise ValueError(f"Collation `{normalized_collation}` does not belong to character set `{normalized_charset}`.")
    return normalized_charset, normalized_collation


def _parse_charset_column_selection(raw_values):
    selected_columns = []
    seen = set()
    for raw_value in raw_values or []:
        try:
            payload = json.loads(str(raw_value or ""))
        except json.JSONDecodeError as error:
            raise ValueError("One or more selected columns are invalid.") from error
        table_name = str(payload.get("table") or "").strip()
        column_name = str(payload.get("column") or "").strip()
        if not table_name or not column_name:
            raise ValueError("One or more selected columns are invalid.")
        quote_identifier(table_name)
        quote_identifier(column_name)
        key = (table_name, column_name)
        if key in seen:
            continue
        selected_columns.append({"table_name": table_name, "column_name": column_name})
        seen.add(key)
    return selected_columns


def _extract_column_definitions_from_create_statement(create_table_statement):
    definitions = {}
    for line in str(create_table_statement or "").splitlines():
        match = re.match(r"^\s*`([^`]+)`\s+(.*?)(?:,)?\s*$", line.rstrip())
        if match:
            definitions[match.group(1)] = match.group(2).strip()
    return definitions


def _strip_charset_collation_clauses(column_definition):
    text = str(column_definition or "").strip()
    text = re.sub(r"\s+CHARACTER\s+SET\s+`?[A-Za-z0-9_]+`?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+CHARSET\s+`?[A-Za-z0-9_]+`?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+COLLATE\s+`?[A-Za-z0-9_]+`?", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _build_column_charset_definition(current_definition, column_type, charset_name, collation_name):
    cleaned_definition = _strip_charset_collation_clauses(current_definition)
    normalized_column_type = str(column_type or "").strip()
    if not cleaned_definition or not normalized_column_type:
        raise ValueError("Unable to determine the current column definition.")
    if cleaned_definition[: len(normalized_column_type)].lower() != normalized_column_type.lower():
        raise ValueError(f"Unable to safely rewrite column definition `{cleaned_definition}`.")
    remainder = cleaned_definition[len(normalized_column_type) :].strip()
    new_definition = f"{normalized_column_type} CHARACTER SET {charset_name} COLLATE {collation_name}"
    if remainder:
        new_definition = f"{new_definition} {remainder}"
    return new_definition


def _fetch_outgoing_foreign_key_names(database_name, table_name, *, selected_columns=None):
    params = [database_name, table_name]
    selected_column_set = {
        str(column_name or "").strip()
        for column_name in selected_columns or []
        if str(column_name or "").strip()
    }
    rows = execute_query(
        """
        SELECT
          constraint_name AS constraint_name_value,
          column_name AS column_name_value
        FROM information_schema.key_column_usage
        WHERE table_schema = %s
          AND table_name = %s
          AND referenced_table_name IS NOT NULL
        ORDER BY constraint_name, ordinal_position
        """,
        params,
    )
    foreign_key_names = []
    seen = set()
    for row in rows:
        column_name = row["column_name_value"]
        if selected_column_set and column_name not in selected_column_set:
            continue
        constraint_name = row["constraint_name_value"]
        if constraint_name in seen:
            continue
        seen.add(constraint_name)
        foreign_key_names.append(constraint_name)
    return foreign_key_names


def _quote_existing_mysql_identifier(identifier):
    return "`" + str(identifier or "").replace("`", "``") + "`"


def build_db_admin_charset_collation_plan(database_name, payload):
    normalized_database = str(database_name or "").strip()
    if not normalized_database:
        raise ValueError("Choose a database before modifying charset or collation.")
    if is_system_schema_name(normalized_database):
        raise ValueError("System schemas cannot be changed here.")
    if payload is None or not hasattr(payload, "getlist"):
        raise ValueError("Charset/collation update payload is missing.")

    target_charset, target_collation = _validate_charset_collation_pair(
        payload.get("target_charset", ""),
        payload.get("target_collation", ""),
    )
    selected_tables = []
    seen_tables = set()
    for raw_table in payload.getlist("selected_charset_table"):
        table_name = str(raw_table or "").strip()
        if not table_name or table_name in seen_tables:
            continue
        quote_identifier(table_name)
        selected_tables.append(table_name)
        seen_tables.add(table_name)

    selected_columns = _parse_charset_column_selection(payload.getlist("selected_charset_column"))
    selected_columns = [
        row for row in selected_columns
        if row["table_name"] not in seen_tables
    ]
    if not selected_tables and not selected_columns:
        raise ValueError("Choose at least one table or column to modify.")

    available_tables = {row["table_name"] for row in fetch_tables_for_database(normalized_database)}
    missing_tables = [
        table_name
        for table_name in selected_tables + [row["table_name"] for row in selected_columns]
        if table_name not in available_tables
    ]
    if missing_tables:
        raise ValueError(f"Selected table was not found: `{missing_tables[0]}`.")

    drop_foreign_keys = str(payload.get("drop_foreign_keys", "")).strip().lower() in {"1", "true", "yes", "on"}
    disable_fk_checks = str(payload.get("foreign_key_checks", "on")).strip().lower() == "off"
    safe_database = quote_identifier(normalized_database)

    alter_statements = []
    foreign_key_definitions = []
    for table_name in selected_tables:
        safe_table = quote_identifier(table_name)
        if drop_foreign_keys:
            foreign_key_definitions.extend(
                _fetch_foreign_key_definitions(normalized_database, table_name=table_name)
            )
        alter_statements.append(
            f"ALTER TABLE {safe_database}.{safe_table} CONVERT TO CHARACTER SET {target_charset} COLLATE {target_collation}"
        )

    columns_by_table = {}
    for column in selected_columns:
        columns_by_table.setdefault(column["table_name"], []).append(column["column_name"])

    for table_name, column_names in columns_by_table.items():
        safe_table = quote_identifier(table_name)
        column_rows = {
            row["column_name"]: row
            for row in _fetch_db_admin_charset_column_rows(normalized_database)
            if row["table_name"] == table_name
        }
        ddl_statement = fetch_create_table_statement(normalized_database, table_name)
        definition_lookup = _extract_column_definitions_from_create_statement(ddl_statement)
        if drop_foreign_keys:
            foreign_key_definitions.extend(
                _fetch_foreign_key_definitions(
                    normalized_database,
                    table_name=table_name,
                    selected_columns=column_names,
                )
            )
        for column_name in column_names:
            column_row = column_rows.get(column_name)
            if not column_row:
                raise ValueError(f"Column `{table_name}.{column_name}` was not found or is not character-based.")
            current_definition = definition_lookup.get(column_name, "")
            new_definition = _build_column_charset_definition(
                current_definition,
                column_row["column_type"],
                target_charset,
                target_collation,
            )
            alter_statements.append(
                f"ALTER TABLE {safe_database}.{safe_table} MODIFY COLUMN {quote_identifier(column_name)} {new_definition}"
            )

    if not alter_statements:
        raise ValueError("No charset/collation changes were submitted.")

    deduped_foreign_keys = []
    seen_foreign_keys = set()
    for definition in foreign_key_definitions:
        key = (
            definition["table_schema"],
            definition["table_name"],
            definition["constraint_name"],
        )
        if key in seen_foreign_keys:
            continue
        seen_foreign_keys.add(key)
        if not definition.get("drop_statement") or not definition.get("create_statement"):
            raise ValueError(
                f"Unable to generate full drop/recreate SQL for foreign key `{definition.get('constraint_name')}`."
            )
        deduped_foreign_keys.append(definition)

    drop_statements = [definition["drop_statement"] for definition in deduped_foreign_keys]
    recreate_statements = [definition["create_statement"] for definition in deduped_foreign_keys]
    changed_parts = []
    if selected_tables:
        changed_parts.append(f"{len(selected_tables)} table(s)")
    if selected_columns:
        changed_parts.append(f"{len(selected_columns)} column(s)")

    return {
        "database_name": normalized_database,
        "target_charset": target_charset,
        "target_collation": target_collation,
        "selected_table_count": len(selected_tables),
        "selected_column_count": len(selected_columns),
        "changed_parts": changed_parts,
        "disable_fk_checks": disable_fk_checks,
        "drop_foreign_keys": drop_foreign_keys,
        "drop_statements": drop_statements,
        "alter_statements": alter_statements,
        "recreate_statements": recreate_statements,
        "foreign_key_definitions": deduped_foreign_keys,
    }


def preview_db_admin_charset_collation(database_name, payload):
    return build_db_admin_charset_collation_plan(database_name, payload)


def build_db_admin_charset_collation_script(plan):
    lines = [
        "-- DBConsole charset/collation change script",
        f"-- Database: {plan['database_name']}",
        f"-- Target character set: {plan['target_charset']}",
        f"-- Target collation: {plan['target_collation']}",
        f"-- Selected tables: {plan['selected_table_count']}",
        f"-- Selected columns: {plan['selected_column_count']}",
        "",
    ]
    if plan["disable_fk_checks"]:
        lines.extend([
            "-- Foreign key checks disabled for this script.",
            "SET FOREIGN_KEY_CHECKS = 0;",
            "",
        ])
    if plan["drop_statements"]:
        lines.append("-- Drop foreign keys before charset/collation changes.")
        lines.extend(f"{statement};" for statement in plan["drop_statements"])
        lines.append("")
    lines.append("-- Apply charset/collation changes.")
    lines.extend(f"{statement};" for statement in plan["alter_statements"])
    lines.append("")
    if plan["recreate_statements"]:
        lines.append("-- Recreate foreign keys after charset/collation changes.")
        lines.extend(f"{statement};" for statement in plan["recreate_statements"])
        lines.append("")
    if plan["disable_fk_checks"]:
        lines.extend([
            "-- Restore foreign key checks.",
            "SET FOREIGN_KEY_CHECKS = 1;",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def modify_db_admin_charset_collation(database_name, payload):
    plan = build_db_admin_charset_collation_plan(database_name, payload)
    executed_drops = 0
    executed_recreates = 0
    with mysql_connection(database_override=plan["database_name"]) as connection:
        with connection.cursor() as cursor:
            if plan["disable_fk_checks"]:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for statement in plan["drop_statements"]:
                    cursor.execute(statement)
                    executed_drops += 1
                for statement in plan["alter_statements"]:
                    cursor.execute(statement)
                for statement in plan["recreate_statements"]:
                    cursor.execute(statement)
                    executed_recreates += 1
            finally:
                if plan["disable_fk_checks"]:
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    message = (
        f"Updated charset/collation to `{plan['target_charset']}` / `{plan['target_collation']}` "
        f"for {' and '.join(plan['changed_parts'])} in `{plan['database_name']}`."
    )
    if executed_drops:
        message += f" Dropped and recreated {executed_recreates} of {executed_drops} outgoing foreign key constraint(s)."
    if plan["disable_fk_checks"]:
        message += " FOREIGN_KEY_CHECKS was disabled for this execution and restored afterward."
    return {
        "message": message,
        "database_name": plan["database_name"],
    }


def _format_datetime_label(value, *, empty="-"):
    if not value:
        return empty
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _summarize_identifier_list(items, *, max_items=3):
    normalized_items = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not normalized_items:
        return ""
    if len(normalized_items) <= max_items:
        return ", ".join(normalized_items)
    remaining_count = len(normalized_items) - max_items
    return ", ".join(normalized_items[:max_items]) + f", and {remaining_count} more"


def _build_event_schedule_label(
    *,
    event_type="",
    execute_at=None,
    interval_value=None,
    interval_field="",
    starts=None,
    ends=None,
):
    normalized_event_type = str(event_type or "").strip().upper()
    if normalized_event_type == "ONE TIME":
        execute_at_label = _format_datetime_label(execute_at, empty="")
        return f"At {execute_at_label}" if execute_at_label else "One Time"

    interval_field_label = str(interval_field or "").strip().replace("_", " ").lower()
    try:
        interval_number = int(interval_value)
    except (TypeError, ValueError):
        interval_number = 0
    if not interval_field_label:
        schedule_label = "Recurring"
    elif interval_number == 1:
        schedule_label = f"Every 1 {interval_field_label}"
    else:
        plural_label = interval_field_label if interval_field_label.endswith("s") else f"{interval_field_label}s"
        schedule_label = f"Every {interval_number or interval_value} {plural_label}"

    starts_label = _format_datetime_label(starts, empty="")
    ends_label = _format_datetime_label(ends, empty="")
    if starts_label:
        schedule_label += f" starting {starts_label}"
    if ends_label:
        schedule_label += f" until {ends_label}"
    return schedule_label


def _parse_event_schedule_at(raw_value):
    normalized_value = str(raw_value or "").strip()
    if not normalized_value:
        raise ValueError("Choose a date and time for one-time event schedules.")
    try:
        schedule_at = datetime.fromisoformat(normalized_value)
    except ValueError as error:
        raise ValueError("Choose a valid date and time for one-time event schedules.") from error
    return schedule_at.strftime("%Y-%m-%d %H:%M:%S")


def _parse_selected_event_keys(raw_values):
    event_keys = []
    seen_keys = set()
    for raw_value in raw_values or []:
        database_name, separator, event_name = str(raw_value or "").strip().partition(".")
        if not separator or not database_name or not event_name:
            raise ValueError("One or more selected events are invalid.")
        quote_identifier(database_name)
        quote_identifier(event_name)
        event_key = (database_name, event_name)
        if event_key in seen_keys:
            continue
        seen_keys.add(event_key)
        event_keys.append(event_key)
    return event_keys


def fetch_db_admin_event_rows():
    rows = execute_query(
        """
        SELECT
          event_schema AS database_name_value,
          event_name AS event_name_value,
          status AS status_value,
          event_type AS event_type_value,
          execute_at AS execute_at_value,
          interval_value AS interval_value_value,
          interval_field AS interval_field_value,
          starts AS starts_value,
          ends AS ends_value,
          on_completion AS on_completion_value,
          definer AS definer_value,
          created AS created_value,
          last_altered AS last_altered_value,
          last_executed AS last_executed_value,
          COALESCE(created, last_altered, starts, execute_at) AS sort_created_value
        FROM information_schema.events
        WHERE event_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND event_schema NOT LIKE 'mysql@_%%' ESCAPE '@'
        ORDER BY COALESCE(created, last_altered, starts, execute_at) DESC, event_schema, event_name
        """
    )
    event_rows = []
    for row in rows:
        database_name = row["database_name_value"]
        event_name = row["event_name_value"]
        status = str(row["status_value"] or "").strip().upper()
        event_rows.append(
            {
                "database_name": database_name,
                "event_name": event_name,
                "event_key": f"{database_name}.{event_name}",
                "status": status,
                "status_label": status.replace("_", " ").title() if status else "-",
                "is_enabled": status == "ENABLED",
                "event_type": row["event_type_value"] or "-",
                "schedule_label": _build_event_schedule_label(
                    event_type=row["event_type_value"],
                    execute_at=row["execute_at_value"],
                    interval_value=row["interval_value_value"],
                    interval_field=row["interval_field_value"],
                    starts=row["starts_value"],
                    ends=row["ends_value"],
                ),
                "created": _format_datetime_label(row["created_value"]),
                "last_altered": _format_datetime_label(row["last_altered_value"]),
                "last_executed": _format_datetime_label(row["last_executed_value"]),
                "on_completion": row["on_completion_value"] or "-",
                "definer": row["definer_value"] or "-",
                "sort_created_value": _format_datetime_label(row["sort_created_value"], empty=""),
            }
        )
    return event_rows


def create_db_admin_event(database_name, event_name, schedule_name, schedule_at, body_sql):
    normalized_database = str(database_name or "").strip()
    normalized_event_name = str(event_name or "").strip()
    normalized_body_sql = str(body_sql or "").strip()
    normalized_body_statement = normalized_body_sql.rstrip().rstrip(";").strip()

    if not normalized_database:
        raise ValueError("Choose a database for the event.")
    if is_system_schema_name(normalized_database):
        raise ValueError("System schemas cannot be changed here.")
    if not fetch_database_exists(normalized_database):
        raise ValueError(f"Database `{normalized_database}` was not found.")
    if not normalized_event_name:
        raise ValueError("Event name is required.")
    if not normalized_body_statement:
        raise ValueError("Event content is required.")

    schedule_option = get_event_schedule_option(schedule_name)
    schedule_label = schedule_option["label"]
    if schedule_option["requires_at"]:
        schedule_at_value = _parse_event_schedule_at(schedule_at)
        schedule_clause = f"AT TIMESTAMP('{schedule_at_value}')"
        schedule_label = f"{schedule_option['label']} at {schedule_at_value}"
    else:
        schedule_clause = (
            f"EVERY {int(schedule_option['interval_value'])} {schedule_option['interval_field']} "
            "STARTS CURRENT_TIMESTAMP"
        )

    safe_database = quote_identifier(normalized_database)
    safe_event_name = quote_identifier(normalized_event_name)
    statement = (
        f"CREATE EVENT {safe_database}.{safe_event_name} "
        f"ON SCHEDULE {schedule_clause} "
        "ON COMPLETION PRESERVE "
        "ENABLE "
        f"DO {normalized_body_statement}"
    )
    execute_statement(statement, database=normalized_database)

    message = f"Created event `{normalized_database}.{normalized_event_name}` with schedule {schedule_label}."
    return {
        "flash_category": "success",
        "flash_message": message,
        "redirect_endpoint": "db_admin_page",
        "redirect_values": {
            "db_admin_tab": "event",
            "database": normalized_database,
            "focus_event_database": normalized_database,
            "focus_event_name": normalized_event_name,
        },
        "event_action_output": {
            "title": "Create Event",
            "category": "success",
            "message": message,
        },
    }


def set_db_admin_events_enabled(selected_event_keys, *, enabled):
    event_keys = _parse_selected_event_keys(selected_event_keys)
    if not event_keys:
        action_label = "enable" if enabled else "disable"
        raise ValueError(f"Select at least one event to {action_label}.")

    status_keyword = "ENABLE" if enabled else "DISABLE"
    action_label = "enabled" if enabled else "disabled"
    qualified_event_names = []
    for database_name, event_name in event_keys:
        if is_system_schema_name(database_name):
            raise ValueError("System schema events cannot be changed here.")
        execute_statement(
            f"ALTER EVENT {quote_identifier(database_name)}.{quote_identifier(event_name)} {status_keyword}",
            database=database_name,
        )
        qualified_event_names.append(f"`{database_name}.{event_name}`")

    message = f"{action_label.title()} {len(event_keys)} event(s): {_summarize_identifier_list(qualified_event_names)}."
    redirect_values = {"db_admin_tab": "event"}
    if len(event_keys) == 1:
        redirect_values["database"] = event_keys[0][0]
        redirect_values["focus_event_database"] = event_keys[0][0]
        redirect_values["focus_event_name"] = event_keys[0][1]

    return {
        "flash_category": "success",
        "flash_message": message,
        "redirect_endpoint": "db_admin_page",
        "redirect_values": redirect_values,
        "event_action_output": {
            "title": "Event Status",
            "category": "success",
            "message": message,
        },
    }


def delete_db_admin_events(selected_event_keys):
    event_keys = _parse_selected_event_keys(selected_event_keys)
    if not event_keys:
        raise ValueError("Select at least one event to delete.")

    qualified_event_names = []
    for database_name, event_name in event_keys:
        if is_system_schema_name(database_name):
            raise ValueError("System schema events cannot be changed here.")
        execute_statement(
            f"DROP EVENT {quote_identifier(database_name)}.{quote_identifier(event_name)}",
            database=database_name,
        )
        qualified_event_names.append(f"`{database_name}.{event_name}`")

    message = f"Deleted {len(event_keys)} event(s): {_summarize_identifier_list(qualified_event_names)}."
    redirect_values = {"db_admin_tab": "event"}
    if len(event_keys) == 1:
        redirect_values["database"] = event_keys[0][0]

    return {
        "flash_category": "success",
        "flash_message": message,
        "redirect_endpoint": "db_admin_page",
        "redirect_values": redirect_values,
        "event_action_output": {
            "title": "Delete Event",
            "category": "success",
            "message": message,
        },
    }


def _fetch_primary_key_status_rows(*, database_name="", table_name="", only_missing_primary_key):
    sql = """
        SELECT
          t.table_schema AS database_name_value,
          t.table_name AS table_name_value,
          t.engine AS engine_value,
          t.table_rows AS table_rows_value,
          COALESCE(primary_keys.has_primary_key, 0) AS has_primary_key_value,
          COALESCE(auto_increment_columns.auto_increment_column_name, '') AS auto_increment_column_name_value,
          COALESCE(row_id_columns.has_my_row_id, 0) AS has_my_row_id_value
        FROM information_schema.tables AS t
        LEFT JOIN (
          SELECT
            table_schema,
            table_name,
            1 AS has_primary_key
          FROM information_schema.statistics
          WHERE index_name = 'PRIMARY'
          GROUP BY table_schema, table_name
        ) AS primary_keys
          ON primary_keys.table_schema = t.table_schema
         AND primary_keys.table_name = t.table_name
        LEFT JOIN (
          SELECT
            table_schema,
            table_name,
            MIN(column_name) AS auto_increment_column_name
          FROM information_schema.columns
          WHERE LOWER(COALESCE(extra, '')) LIKE '%%auto_increment%%'
          GROUP BY table_schema, table_name
        ) AS auto_increment_columns
          ON auto_increment_columns.table_schema = t.table_schema
         AND auto_increment_columns.table_name = t.table_name
        LEFT JOIN (
          SELECT
            table_schema,
            table_name,
            MAX(CASE WHEN LOWER(column_name) = 'my_row_id' THEN 1 ELSE 0 END) AS has_my_row_id
          FROM information_schema.columns
          GROUP BY table_schema, table_name
        ) AS row_id_columns
          ON row_id_columns.table_schema = t.table_schema
         AND row_id_columns.table_name = t.table_name
        WHERE t.table_type = 'BASE TABLE'
          AND t.table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND t.table_schema NOT LIKE 'mysql@_%%' ESCAPE '@'
    """
    params = []
    normalized_database = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()
    if normalized_database:
        sql += " AND t.table_schema = %s"
        params.append(normalized_database)
    if normalized_table:
        sql += " AND t.table_name = %s"
        params.append(normalized_table)
    if only_missing_primary_key:
        sql += " AND COALESCE(primary_keys.has_primary_key, 0) = 0"
    sql += " ORDER BY t.table_schema, t.table_name"

    rows = execute_query(sql, params or None)
    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "database_name": row["database_name_value"],
                "table_name": row["table_name_value"],
                "engine": row["engine_value"] or "-",
                "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "has_primary_key": bool(row["has_primary_key_value"]),
                "auto_increment_column_name": row["auto_increment_column_name_value"] or "",
                "has_my_row_id": bool(row["has_my_row_id_value"]),
            }
        )
    return normalized_rows


def fetch_tables_without_primary_key():
    return _fetch_primary_key_status_rows(only_missing_primary_key=True)


def fetch_table_primary_key_status(database_name, table_name):
    rows = _fetch_primary_key_status_rows(
        database_name=database_name,
        table_name=table_name,
        only_missing_primary_key=False,
    )
    if not rows:
        return None
    return rows[0]


def fix_table_without_primary_key(database_name, table_name):
    normalized_database = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()
    if not normalized_database or not normalized_table:
        raise ValueError("Choose both a database and table before applying the primary key fix.")
    if is_system_schema_name(normalized_database):
        raise ValueError("System schemas cannot be changed here.")

    primary_key_status = fetch_table_primary_key_status(normalized_database, normalized_table)
    if primary_key_status is None:
        raise ValueError(f"Table `{normalized_database}.{normalized_table}` was not found.")
    if primary_key_status["has_primary_key"]:
        return {
            "status": "already_has_primary_key",
            "strategy": "none",
            "message": f"Table `{normalized_database}.{normalized_table}` already has a primary key.",
        }

    safe_database = quote_identifier(normalized_database)
    safe_table = quote_identifier(normalized_table)
    auto_increment_column_name = primary_key_status["auto_increment_column_name"]
    if auto_increment_column_name:
        execute_statement(
            f"ALTER TABLE {safe_database}.{safe_table} "
            f"ADD PRIMARY KEY ({quote_identifier(auto_increment_column_name)})"
        )
        return {
            "status": "fixed",
            "strategy": "use_auto_increment",
            "message": (
                f"Added PRIMARY KEY on `{normalized_database}.{normalized_table}` "
                f"using existing AUTO_INCREMENT column `{auto_increment_column_name}`."
            ),
        }

    row_id_column = quote_identifier("my_row_id")
    if primary_key_status["has_my_row_id"]:
        raise ValueError(
            f"Table `{normalized_database}.{normalized_table}` already contains `my_row_id`, "
            "so the automatic invisible-column fix cannot be applied."
        )

    execute_statement(
        f"ALTER TABLE {safe_database}.{safe_table} "
        f"ADD COLUMN {row_id_column} BIGINT UNSIGNED NOT NULL AUTO_INCREMENT INVISIBLE, "
        f"ADD PRIMARY KEY ({row_id_column})"
    )
    return {
        "status": "fixed",
        "strategy": "add_invisible_my_row_id",
        "message": (
            f"Added invisible AUTO_INCREMENT column `my_row_id` and PRIMARY KEY on "
            f"`{normalized_database}.{normalized_table}`."
        ),
    }


def fetch_full_table_report(schema_name, table_name, *, order_by_candidates=None, limit=None):
    column_names = fetch_table_column_names(schema_name, table_name)
    if not column_names:
        raise ValueError(f"No columns found for {schema_name}.{table_name}.")

    column_lookup = {column_name.lower(): column_name for column_name in column_names}
    selected_columns = [
        f"{quote_identifier(column_name)} AS {quote_identifier(column_name)}"
        for column_name in column_names
    ]
    sql = (
        f"SELECT {', '.join(selected_columns)} "
        f"FROM {quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    )

    order_clauses = []
    for candidate in order_by_candidates or []:
        actual_name = column_lookup.get(str(candidate or "").strip().lower())
        if actual_name:
            order_clauses.append(quote_identifier(actual_name))
    if order_clauses:
        sql += " ORDER BY " + ", ".join(order_clauses)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return run_report_query(sql)


def fetch_heatwave_status_variable_report():
    return run_report_query("SHOW GLOBAL STATUS LIKE 'rapid%status'")


def fetch_heatwave_nodes_report():
    return fetch_full_table_report(
        "performance_schema",
        "rpd_nodes",
        order_by_candidates=[
            "node_name",
            "node_id",
            "host_name",
            "hostname",
            "host",
            "address",
            "ip_address",
        ],
        limit=200,
    )


def fetch_heatwave_inventory_report():
    table_id_columns = fetch_table_column_names("performance_schema", "rpd_table_id")
    tables_columns = fetch_table_column_names("performance_schema", "rpd_tables")
    if not table_id_columns:
        raise ValueError("No columns found for performance_schema.rpd_table_id.")
    if not tables_columns:
        raise ValueError("No columns found for performance_schema.rpd_tables.")

    table_id_lookup = {column_name.lower(): column_name for column_name in table_id_columns}
    tables_lookup = {column_name.lower(): column_name for column_name in tables_columns}
    join_pairs = [
        ("id", "id"),
        ("table_id", "table_id"),
        ("name", "name"),
    ]
    join_pair = next(
        (
            (table_id_lookup[left_name], tables_lookup[right_name])
            for left_name, right_name in join_pairs
            if left_name in table_id_lookup and right_name in tables_lookup
        ),
        None,
    )
    if join_pair is None:
        raise ValueError("Unable to determine a join key between performance_schema.rpd_table_id and performance_schema.rpd_tables.")

    selected_columns = []
    table_id_aliases = []
    tables_aliases = []

    for column_name in table_id_columns:
        alias = f"rpd_table_id__{column_name}"
        selected_columns.append(f"tid.{quote_identifier(column_name)} AS {quote_identifier(alias)}")
        table_id_aliases.append(alias)

    for column_name in tables_columns:
        alias = f"rpd_tables__{column_name}"
        selected_columns.append(f"rt.{quote_identifier(column_name)} AS {quote_identifier(alias)}")
        tables_aliases.append(alias)

    sql = """
        SELECT {columns}
        FROM performance_schema.rpd_table_id AS tid
        LEFT JOIN performance_schema.rpd_tables AS rt
          ON tid.{table_id_key} = rt.{tables_key}
    """.format(
        columns=", ".join(selected_columns),
        table_id_key=quote_identifier(join_pair[0]),
        tables_key=quote_identifier(join_pair[1]),
    )

    order_clauses = []
    for candidate in ("schema_name", "database_name", "table_schema", "name", "table_name", "id"):
        actual_name = table_id_lookup.get(candidate)
        if actual_name:
            order_clauses.append(f"tid.{quote_identifier(actual_name)}")
    if order_clauses:
        sql += " ORDER BY " + ", ".join(order_clauses)

    report = run_report_query(sql)
    report["table_id_columns"] = table_id_aliases
    report["tables_columns"] = tables_aliases
    return report


def fetch_heatwave_defined_secondary_engine_tables():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS table_name_value,
          table_rows AS row_count_value,
          create_options AS create_options_value
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND UPPER(COALESCE(create_options, '')) LIKE '%%SECONDARY_ENGINE=RAPID%%'
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "table_name": row["table_name_value"],
            "row_count": row["row_count_value"] if row["row_count_value"] is not None else "-",
            "create_options": row["create_options_value"] or "",
        }
        for row in rows
    ]


def fetch_lakehouse_engine_tables():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS table_name_value,
          engine AS engine_value,
          create_options AS create_options_value
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND (
            UPPER(COALESCE(engine, '')) LIKE '%%LAKEHOUSE%%'
            OR UPPER(COALESCE(create_options, '')) LIKE '%%LAKEHOUSE%%'
          )
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "table_name": row["table_name_value"],
            "engine": row["engine_value"] or "-",
            "create_options": row["create_options_value"] or "",
        }
        for row in rows
    ]


def fetch_dashboard_heatwave_summary():
    return module_build_dashboard_heatwave_summary(
        fetch_heatwave_inventory_report=fetch_heatwave_inventory_report,
        fetch_heatwave_defined_secondary_engine_tables=fetch_heatwave_defined_secondary_engine_tables,
        fetch_lakehouse_engine_tables=fetch_lakehouse_engine_tables,
        is_system_schema_name=is_system_schema_name,
    )


def fetch_table_columns(database_name, table_name):
    if not database_name or not table_name:
        return []
    rows = execute_query(
        """
        SELECT
          column_name AS column_name_value,
          column_type AS column_type_value,
          is_nullable AS is_nullable_value,
          column_key AS column_key_value,
          extra AS extra_value,
          column_comment AS column_comment_value
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        [database_name, table_name],
    )
    return [
        {
            "column_name": row["column_name_value"],
            "column_type": row["column_type_value"],
            "is_nullable": row["is_nullable_value"],
            "column_key": row["column_key_value"],
            "extra": row["extra_value"],
            "column_comment": row["column_comment_value"] or "",
        }
        for row in rows
    ]


def fetch_table_indexes(database_name, table_name):
    if not database_name or not table_name:
        return []
    rows = execute_query(
        """
        SELECT
          index_name AS index_name_value,
          non_unique AS non_unique_value,
          index_type AS index_type_value,
          seq_in_index AS seq_in_index_value,
          column_name AS column_name_value,
          sub_part AS sub_part_value,
          cardinality AS cardinality_value,
          index_comment AS index_comment_value,
          is_visible AS is_visible_value
        FROM information_schema.statistics
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY index_name, seq_in_index
        """,
        [database_name, table_name],
    )
    index_lookup = {}
    ordered_indexes = []
    for row in rows:
        index_name = row["index_name_value"]
        if index_name not in index_lookup:
            index_lookup[index_name] = {
                "index_name": index_name,
                "is_unique": row["non_unique_value"] == 0,
                "index_type": row["index_type_value"] or "-",
                "is_visible": row["is_visible_value"] or "-",
                "cardinality": row["cardinality_value"] if row["cardinality_value"] is not None else "-",
                "index_comment": row["index_comment_value"] or "-",
                "columns": [],
            }
            ordered_indexes.append(index_lookup[index_name])
        column_name = row["column_name_value"] or "-"
        if row["sub_part_value"] is not None:
            column_name = f"{column_name}({row['sub_part_value']})"
        index_lookup[index_name]["columns"].append(column_name)
    return ordered_indexes


def fetch_table_partitions(database_name, table_name):
    if not database_name or not table_name:
        return {
            "is_partitioned": False,
            "partition_method": "",
            "partition_expression": "",
            "subpartition_method": "",
            "subpartition_expression": "",
            "partition_count": 0,
            "rows": [],
        }
    rows = execute_query(
        """
        SELECT
          partition_name AS partition_name_value,
          subpartition_name AS subpartition_name_value,
          partition_method AS partition_method_value,
          partition_expression AS partition_expression_value,
          subpartition_method AS subpartition_method_value,
          subpartition_expression AS subpartition_expression_value,
          partition_description AS partition_description_value,
          partition_ordinal_position AS partition_ordinal_position_value,
          subpartition_ordinal_position AS subpartition_ordinal_position_value,
          table_rows AS table_rows_value,
          data_length AS data_length_value,
          index_length AS index_length_value,
          data_free AS data_free_value
        FROM information_schema.partitions
        WHERE table_schema = %s
          AND table_name = %s
          AND partition_name IS NOT NULL
        ORDER BY partition_ordinal_position, subpartition_ordinal_position
        """,
        [database_name, table_name],
    )
    if not rows:
        return {
            "is_partitioned": False,
            "partition_method": "",
            "partition_expression": "",
            "subpartition_method": "",
            "subpartition_expression": "",
            "partition_count": 0,
            "rows": [],
        }

    first_row = rows[0]
    partitions = []
    partition_names = set()
    for row in rows:
        partition_name = row["partition_name_value"] or "-"
        partition_names.add(partition_name)
        partitions.append(
            {
                "partition_name": partition_name,
                "subpartition_name": row["subpartition_name_value"] or "-",
                "partition_description": row["partition_description_value"] or "-",
                "table_rows": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "data_length": row["data_length_value"] if row["data_length_value"] is not None else "-",
                "index_length": row["index_length_value"] if row["index_length_value"] is not None else "-",
                "data_free": row["data_free_value"] if row["data_free_value"] is not None else "-",
            }
        )

    return {
        "is_partitioned": True,
        "partition_method": first_row["partition_method_value"] or "-",
        "partition_expression": first_row["partition_expression_value"] or "-",
        "subpartition_method": first_row["subpartition_method_value"] or "-",
        "subpartition_expression": first_row["subpartition_expression_value"] or "-",
        "partition_count": len(partition_names),
        "rows": partitions,
    }


def _normalize_mysql_base_type(column_type):
    normalized = str(column_type or "").strip().lower()
    if not normalized:
        return ""
    return normalized.split("(", 1)[0].split()[0]


def _build_table_preview_select_list(column_definitions):
    select_clauses = []
    masked_columns = []
    for column in column_definitions or []:
        column_name = column["column_name"]
        column_type = column.get("column_type", "")
        safe_column_name = quote_identifier(column_name)
        base_type = _normalize_mysql_base_type(column_type)
        if base_type in DB_ADMIN_PREVIEW_MASKED_BASE_TYPES:
            placeholder = f"[{base_type.upper()}]"
            select_clauses.append(f"CAST('{placeholder}' AS CHAR(32)) AS {safe_column_name}")
            masked_columns.append({"column_name": column_name, "column_type": column_type})
            continue
        select_clauses.append(safe_column_name)
    return select_clauses, masked_columns


def fetch_table_preview(database_name, table_name, page=1, page_size=25):
    if not database_name or not table_name:
        return {
            "columns": [],
            "rows": [],
            "page": 1,
            "page_size": page_size,
            "total_rows": 0,
            "has_previous": False,
            "has_next": False,
            "masked_columns": [],
        }
    safe_database = quote_identifier(database_name)
    safe_table = quote_identifier(table_name)
    page = normalize_page_number(page)
    offset = (page - 1) * page_size
    total_rows = fetch_scalar(f"SELECT COUNT(*) FROM {safe_database}.{safe_table}", default=0)
    column_definitions = fetch_table_columns(database_name, table_name)
    select_clauses, masked_columns = _build_table_preview_select_list(column_definitions)
    with mysql_connection(database_override=database_name) as connection:
        with connection.cursor() as cursor:
            select_list_sql = ", ".join(select_clauses) if select_clauses else "*"
            cursor.execute(
                f"SELECT {select_list_sql} FROM {safe_database}.{safe_table} LIMIT %s OFFSET %s",
                [page_size, offset],
            )
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
    return {
        "columns": columns,
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows or 0,
        "has_previous": page > 1,
        "has_next": offset + len(rows) < (total_rows or 0),
        "masked_columns": masked_columns,
    }


def fetch_create_table_statement(database_name, table_name):
    if not database_name or not table_name:
        return ""
    safe_table = quote_identifier(table_name)
    with mysql_connection(database_override=database_name) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW CREATE TABLE {safe_table}")
            row = cursor.fetchone() or {}
    return row.get("Create Table", "")


def empty_table_preview(page_size=25):
    return {
        "columns": [],
        "rows": [],
        "page": 1,
        "page_size": page_size,
        "total_rows": 0,
        "has_previous": False,
        "has_next": False,
        "masked_columns": [],
    }


def _import_cache_path(plan_id):
    candidate = str(plan_id or "").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", candidate):
        return None
    return IMPORT_CACHE_DIR / f"{candidate}.json"


def save_mysql_import_plan(plan):
    ensure_import_cache_dir()
    plan_payload = dict(plan)
    plan_payload["plan_id"] = uuid4().hex
    cache_path = _import_cache_path(plan_payload["plan_id"])
    cache_path.write_text(json.dumps(plan_payload, ensure_ascii=False), encoding="utf-8")
    return plan_payload


def load_mysql_import_plan(plan_id):
    cache_path = _import_cache_path(plan_id)
    if cache_path is None or not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def delete_mysql_import_plan(plan_id):
    cache_path = _import_cache_path(plan_id)
    if cache_path is None or not cache_path.exists():
        return
    cache_path.unlink(missing_ok=True)


def sanitize_import_identifier(value, prefix="column"):
    cleaned = re.sub(r"[^A-Za-z0-9_$]+", "_", str(value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:64]


def lowercase_import_identifier(value, prefix="column"):
    return sanitize_import_identifier(value, prefix).lower()


def _make_unique_labels(values, prefix):
    labels = []
    seen = set()
    for index, value in enumerate(values, start=1):
        base_label = str(value or "").strip() or f"{prefix}_{index}"
        candidate = base_label
        suffix = 2
        while candidate.lower() in seen:
            candidate = f"{base_label}_{suffix}"
            suffix += 1
        labels.append(candidate)
        seen.add(candidate.lower())
    return labels


def derive_import_table_name(filename):
    return lowercase_import_identifier(Path(str(filename or "import_table")).stem, "import_table")


def _normalize_upload_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _preview_import_value(value, max_length=120):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def _normalize_json_row(item):
    if isinstance(item, dict):
        return {
            str(key or f"column_{index + 1}"): _normalize_upload_value(value)
            for index, (key, value) in enumerate(item.items())
        }
    if isinstance(item, list):
        return {
            f"value_{index + 1}": _normalize_upload_value(value)
            for index, value in enumerate(item)
        }
    return {"value": _normalize_upload_value(item)}


def parse_json_upload(text):
    payload = json.loads(text)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if len(payload) == 1:
            only_value = next(iter(payload.values()))
            items = only_value if isinstance(only_value, list) else [payload]
        else:
            items = [payload]
    else:
        items = [payload]

    rows = []
    column_order = []
    for item in items:
        row = _normalize_json_row(item)
        for column_name in row:
            if column_name not in column_order:
                column_order.append(column_name)
        rows.append(row)

    if not column_order:
        raise ValueError("The JSON file did not contain tabular rows.")

    normalized_rows = [{column_name: row.get(column_name) for column_name in column_order} for row in rows]
    return {"file_format": "json", "column_order": column_order, "rows": normalized_rows}


def parse_csv_upload(text):
    sample = text[:4096] or "column_1\n"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = True

    stream = io.StringIO(text, newline="")
    reader = list(csv.reader(stream, dialect))
    if not reader:
        raise ValueError("The CSV file is empty.")

    if has_header:
        raw_headers = _make_unique_labels(reader[0], "column")
        data_rows = reader[1:]
    else:
        raw_headers = []
        data_rows = reader

    max_columns = max((len(row) for row in ([reader[0]] + data_rows)), default=0)
    if not raw_headers:
        raw_headers = [f"column_{index + 1}" for index in range(max_columns)]
    elif len(raw_headers) < max_columns:
        raw_headers.extend([f"column_{index + 1}" for index in range(len(raw_headers), max_columns)])

    rows = []
    for row_values in data_rows:
        if not row_values or all(str(value or "").strip() == "" for value in row_values):
            continue
        padded_values = list(row_values) + [""] * (len(raw_headers) - len(row_values))
        rows.append(
            {
                header: _normalize_upload_value(padded_values[index] if index < len(padded_values) else None)
                for index, header in enumerate(raw_headers)
            }
        )

    return {"file_format": "csv", "column_order": raw_headers, "rows": rows}


def parse_import_upload(upload_storage):
    filename = Path(str(getattr(upload_storage, "filename", "") or "")).name
    if not filename:
        raise ValueError("Choose a CSV or JSON file to upload.")

    payload = upload_storage.read()
    if not payload:
        raise ValueError("The uploaded file is empty.")

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Upload files must be UTF-8 encoded.") from error

    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        parsed = parse_csv_upload(text)
    elif suffix == ".json":
        parsed = parse_json_upload(text)
    else:
        raise ValueError("Only CSV and JSON files are supported.")

    parsed["source_filename"] = filename
    return parsed


def _is_bool_like(value):
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return str(value).strip().lower() in {"true", "false", "yes", "no", "on", "off"}
    return False


def _is_int_like(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return bool(re.fullmatch(r"[+-]?\d+", value.strip()))
    return False


def _is_float_like(value):
    if _is_int_like(value):
        return True
    if isinstance(value, float):
        return True
    if isinstance(value, str):
        return bool(re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", value.strip()))
    return False


def _is_date_like(value):
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return False
    try:
        datetime.strptime(stripped, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _is_datetime_like(value):
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if "T" not in stripped and " " not in stripped:
        return False
    candidate = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return False


def infer_import_column_type(values):
    non_null_values = [value for value in values if value is not None]
    if not non_null_values:
        return "VARCHAR(255)"
    if all(isinstance(value, (dict, list)) for value in non_null_values):
        return "JSON"
    if all(_is_bool_like(value) for value in non_null_values):
        return "TINYINT(1)"
    if all(_is_int_like(value) for value in non_null_values):
        return "BIGINT"
    if all(_is_float_like(value) for value in non_null_values):
        return "DOUBLE"
    if all(_is_datetime_like(value) for value in non_null_values):
        return "DATETIME"
    if all(_is_date_like(value) for value in non_null_values):
        return "DATE"

    max_length = max(len(_preview_import_value(value, max_length=1000000)) for value in non_null_values)
    if max_length > 65535:
        return "LONGTEXT"
    if max_length > 255:
        return "TEXT"
    return "VARCHAR(255)"


def build_import_column_definitions(rows, column_order):
    definitions = []
    seen_names = set()
    for index, source_name in enumerate(column_order, start=1):
        suggested_name = lowercase_import_identifier(source_name, f"column_{index}")
        candidate_name = suggested_name
        suffix = 2
        while candidate_name.lower() in seen_names:
            candidate_name = lowercase_import_identifier(f"{suggested_name}_{suffix}", f"column_{index}")
            suffix += 1
        seen_names.add(candidate_name.lower())
        column_values = [row.get(source_name) for row in rows]
        sample_values = []
        for value in column_values:
            if value is None:
                continue
            sample_values.append(_preview_import_value(value))
            if len(sample_values) >= 3:
                break
        definitions.append(
            {
                "source_name": source_name,
                "column_name": candidate_name,
                "data_type": infer_import_column_type(column_values),
                "allow_null": any(value is None for value in column_values) or not rows,
                "sample_values": sample_values,
            }
        )
    return definitions


def build_import_sample_rows(rows, column_order, limit=10):
    sample_rows = []
    for row in rows[:limit]:
        sample_rows.append({column_name: _preview_import_value(row.get(column_name)) for column_name in column_order})
    return sample_rows


def _extract_mysql_import_state(payload):
    return {
        "create_database": _normalize_checkbox(payload.get("create_database", "")),
        "selected_database": str(payload.get("selected_database", "")).strip(),
        "new_database_name": str(payload.get("new_database_name", "")).strip(),
        "table_name": str(payload.get("table_name", "")).strip().lower(),
        "replace_existing_table": _normalize_checkbox(payload.get("replace_existing_table", "")),
    }


def _effective_import_database_name(import_state):
    return import_state["new_database_name"] if import_state["create_database"] else import_state["selected_database"]


def build_mysql_import_plan(upload_storage, payload, database_inventory):
    parsed_upload = parse_import_upload(upload_storage)
    import_state = _extract_mysql_import_state(payload)
    target_database = _effective_import_database_name(import_state)
    available_database_names = {row["database_name"] for row in database_inventory}

    if not target_database:
        raise ValueError("Choose a database, or enable Create DB and enter a database name.")
    quote_identifier(target_database)
    if not import_state["create_database"] and target_database not in available_database_names:
        raise ValueError(f"Database `{target_database}` was not found.")
    if not parsed_upload["column_order"]:
        raise ValueError("The uploaded file did not contain any columns to import.")

    return {
        "source_filename": parsed_upload["source_filename"],
        "file_format": parsed_upload["file_format"],
        "rows": parsed_upload["rows"],
        "row_count": len(parsed_upload["rows"]),
        "column_order": parsed_upload["column_order"],
        "sample_columns": parsed_upload["column_order"],
        "sample_rows": build_import_sample_rows(parsed_upload["rows"], parsed_upload["column_order"]),
        "column_definitions": build_import_column_definitions(parsed_upload["rows"], parsed_upload["column_order"]),
        "selected_database": import_state["selected_database"],
        "create_database": import_state["create_database"],
        "new_database_name": import_state["new_database_name"],
        "table_name": derive_import_table_name(parsed_upload["source_filename"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _hydrate_import_column_definitions(plan, payload=None):
    column_definitions = []
    for index, definition in enumerate(plan.get("column_definitions", [])):
        if payload is None:
            column_name = definition.get("column_name", "")
            data_type = definition.get("data_type", "")
            allow_null = bool(definition.get("allow_null"))
        else:
            column_name = str(payload.get(f"column_name_{index}", definition.get("column_name", ""))).strip().lower()
            data_type = str(payload.get(f"column_type_{index}", definition.get("data_type", ""))).strip()
            allow_null = _normalize_checkbox(payload.get(f"column_allow_null_{index}", ""))
        column_definitions.append(
            {
                "source_name": definition.get("source_name", ""),
                "column_name": column_name,
                "data_type": data_type,
                "allow_null": allow_null,
                "sample_values": definition.get("sample_values", []),
            }
        )
    return column_definitions


def fetch_database_exists(database_name):
    if not database_name:
        return False
    return bool(
        fetch_scalar(
            """
            SELECT COUNT(*) AS database_count_value
            FROM information_schema.schemata
            WHERE schema_name = %s
            """,
            [database_name],
            default=0,
        )
    )


def fetch_table_exists(database_name, table_name):
    if not database_name or not table_name:
        return False
    return bool(
        fetch_scalar(
            """
            SELECT COUNT(*) AS table_count_value
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
            """,
            [database_name, table_name],
            default=0,
        )
    )


def build_mysql_import_page_state(plan, database_inventory, payload=None):
    available_database_names = {row["database_name"] for row in database_inventory}
    if payload is None:
        import_state = {
            "create_database": bool(plan.get("create_database")) if plan else False,
            "selected_database": plan.get("selected_database", "") if plan else "",
            "new_database_name": plan.get("new_database_name", "") if plan else "",
            "table_name": plan.get("table_name", "") if plan else "",
            "replace_existing_table": False,
        }
    else:
        import_state = _extract_mysql_import_state(payload)

    state = {
        "database_inventory": database_inventory,
        "import_type_options": IMPORT_TYPE_OPTIONS,
        "plan_loaded": bool(plan),
        "plan_id": plan.get("plan_id", "") if plan else "",
        "source_filename": plan.get("source_filename", "") if plan else "",
        "file_format": str(plan.get("file_format", "")).upper() if plan else "",
        "row_count": plan.get("row_count", 0) if plan else 0,
        "sample_columns": plan.get("sample_columns", []) if plan else [],
        "sample_rows": plan.get("sample_rows", []) if plan else [],
        "column_definitions": _hydrate_import_column_definitions(plan, payload) if plan else [],
        "create_database": import_state["create_database"],
        "selected_database": import_state["selected_database"],
        "new_database_name": import_state["new_database_name"],
        "table_name": import_state["table_name"] or (plan.get("table_name", "") if plan else ""),
        "replace_existing_table": import_state["replace_existing_table"],
        "database_exists": False,
        "table_exists": False,
        "effective_database_name": "",
    }
    state["effective_database_name"] = _effective_import_database_name(state)
    if state["effective_database_name"] in available_database_names:
        state["database_exists"] = True
        if state["table_name"]:
            state["table_exists"] = fetch_table_exists(state["effective_database_name"], state["table_name"])
    return state


def _normalize_import_type(data_type):
    normalized = re.sub(r"\s+", " ", str(data_type or "").strip().upper())
    if not normalized:
        raise ValueError("Each import column must have a data type.")
    if not IMPORT_SQL_TYPE_RE.fullmatch(normalized):
        raise ValueError(f"Invalid data type `{data_type}`.")
    return normalized


def validate_mysql_import_request(payload, plan, database_inventory):
    import_state = _extract_mysql_import_state(payload)
    target_database = _effective_import_database_name(import_state)
    available_database_names = {row["database_name"] for row in database_inventory}

    if not target_database:
        raise ValueError("Choose a database, or enable Create DB and enter a database name.")
    quote_identifier(target_database)
    if not import_state["create_database"] and target_database not in available_database_names:
        raise ValueError(f"Database `{target_database}` was not found.")

    table_name = import_state["table_name"] or derive_import_table_name(plan.get("source_filename", "import_table"))
    quote_identifier(table_name)

    column_definitions = []
    seen_column_names = set()
    for index, definition in enumerate(_hydrate_import_column_definitions(plan, payload), start=1):
        column_name = str(definition.get("column_name", "")).strip()
        if not column_name:
            raise ValueError(f"Column name {index} cannot be empty.")
        quote_identifier(column_name)
        column_key = column_name.lower()
        if column_key in seen_column_names:
            raise ValueError(f"Duplicate import column name `{column_name}` is not allowed.")
        seen_column_names.add(column_key)
        column_definitions.append(
            {
                "source_name": definition.get("source_name", ""),
                "column_name": column_name,
                "data_type": _normalize_import_type(definition.get("data_type", "")),
                "allow_null": bool(definition.get("allow_null")),
            }
        )

    table_exists = fetch_table_exists(target_database, table_name) if fetch_database_exists(target_database) else False
    if table_exists and not import_state["replace_existing_table"]:
        raise ValueError(f"Table `{target_database}.{table_name}` already exists. Choose Replace Table or change the table name.")

    return {
        "create_database": import_state["create_database"],
        "replace_existing_table": import_state["replace_existing_table"],
        "effective_database_name": target_database,
        "table_name": table_name,
        "column_definitions": column_definitions,
    }


def _coerce_import_cell_value(value, column_definition):
    data_type = str(column_definition["data_type"]).upper()
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            if data_type.startswith(("VARCHAR", "TEXT", "LONGTEXT")):
                return ""
            return None
    if data_type.startswith("JSON"):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return json.dumps(value)
        if isinstance(value, (int, float)):
            return json.dumps(value)
        return str(value)
    if data_type.startswith("TINYINT(1)"):
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return int(value)
        lowered = str(value).strip().lower()
        bool_map = {"true": 1, "false": 0, "yes": 1, "no": 0, "on": 1, "off": 0}
        if lowered in bool_map:
            return bool_map[lowered]
        return int(lowered)
    if data_type.startswith(("BIGINT", "INT", "INTEGER", "SMALLINT", "MEDIUMINT", "TINYINT")):
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return int(value)
        return int(str(value).strip())
    if data_type.startswith(("DOUBLE", "FLOAT", "REAL")):
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip())
    if data_type.startswith(("DECIMAL", "NUMERIC")):
        return str(value).strip() if isinstance(value, str) else str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def run_mysql_import(plan, import_request):
    target_database = import_request["effective_database_name"]
    table_name = import_request["table_name"]
    column_definitions = import_request["column_definitions"]
    safe_database = quote_identifier(target_database)
    safe_table = quote_identifier(table_name)

    if import_request["create_database"]:
        execute_statement(f"CREATE DATABASE IF NOT EXISTS {safe_database}")

    create_columns_sql = ", ".join(
        f"{quote_identifier(column['column_name'])} {column['data_type']} {'NULL' if column['allow_null'] else 'NOT NULL'}"
        for column in column_definitions
    )
    insert_columns_sql = ", ".join(quote_identifier(column["column_name"]) for column in column_definitions)
    insert_placeholders = ", ".join(["%s"] * len(column_definitions))
    insert_sql = f"INSERT INTO {safe_database}.{safe_table} ({insert_columns_sql}) VALUES ({insert_placeholders})"

    with mysql_connection(database_override=target_database, autocommit=False) as connection:
        try:
            with connection.cursor() as cursor:
                if import_request["replace_existing_table"]:
                    cursor.execute(f"DROP TABLE IF EXISTS {safe_database}.{safe_table}")
                cursor.execute(
                    f"CREATE TABLE {safe_database}.{safe_table} ({create_columns_sql}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                )
                pending_rows = []
                for raw_row in plan.get("rows", []):
                    pending_rows.append(
                        [
                            _coerce_import_cell_value(raw_row.get(column["source_name"]), column)
                            for column in column_definitions
                        ]
                    )
                    if len(pending_rows) >= 500:
                        cursor.executemany(insert_sql, pending_rows)
                        pending_rows = []
                if pending_rows:
                    cursor.executemany(insert_sql, pending_rows)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


SECURITY_COMPONENT_KEYWORDS = (
    "audit",
    "authentication",
    "connection_control",
    "firewall",
    "keyring",
    "password",
    "security",
)


def _is_security_related_name(name):
    lowered_name = str(name or "").strip().lower()
    return any(token in lowered_name for token in SECURITY_COMPONENT_KEYWORDS)


def _component_name_from_urn(component_urn):
    normalized_urn = str(component_urn or "").strip()
    if not normalized_urn:
        return "-"
    return normalized_urn.rsplit("/", 1)[-1]


def fetch_installed_component_rows():
    rows = execute_query(
        """
        SELECT
          component_urn AS component_urn_value
        FROM mysql.component
        ORDER BY component_urn
        """
    )
    component_rows = []
    for row in rows:
        component_urn = row["component_urn_value"] or "-"
        component_name = _component_name_from_urn(component_urn)
        component_rows.append(
            {
                "component_name": component_name,
                "component_urn": component_urn,
                "is_security_related": _is_security_related_name(component_name),
            }
        )
    component_rows.sort(key=lambda item: (not item["is_security_related"], item["component_name"].lower()))
    return component_rows


def fetch_security_feature_rows(installed_components):
    security_rows = []
    seen_keys = set()

    for row in installed_components:
        if not row["is_security_related"]:
            continue
        row_key = ("component", row["component_name"].lower())
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        security_rows.append(
            {
                "feature_type": "Component",
                "feature_name": row["component_name"],
                "status_label": "Installed",
                "is_enabled": True,
                "details": row["component_urn"],
            }
        )

    plugin_rows = execute_query(
        """
        SELECT
          plugin_name AS plugin_name_value,
          plugin_status AS plugin_status_value,
          load_option AS load_option_value
        FROM information_schema.plugins
        ORDER BY plugin_name
        """
    )
    for row in plugin_rows:
        plugin_name = row["plugin_name_value"] or "-"
        if not _is_security_related_name(plugin_name):
            continue
        row_key = ("plugin", str(plugin_name).lower())
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        plugin_status = row["plugin_status_value"] or "-"
        security_rows.append(
            {
                "feature_type": "Plugin",
                "feature_name": plugin_name,
                "status_label": plugin_status,
                "is_enabled": str(plugin_status).strip().upper() in {"ACTIVE", "ENABLED"},
                "details": row["load_option_value"] or "-",
            }
        )

    security_rows.sort(key=lambda item: (not item["is_enabled"], item["feature_name"].lower()))
    return security_rows


def _normalize_show_variable_row(row):
    return {
        "name": (
            row.get("Variable_name")
            or row.get("variable_name")
            or row.get("VARIABLE_NAME")
            or row.get("Name")
            or row.get("name")
            or "-"
        ),
        "value": (
            row.get("Value")
            if "Value" in row
            else row.get("value")
            if "value" in row
            else row.get("VARIABLE_VALUE")
            if "VARIABLE_VALUE" in row
            else row.get("variable_value", "-")
        ),
    }


def fetch_show_variable_rows(kind, patterns):
    rows = []
    seen_names = set()
    for pattern in patterns:
        for row in execute_query(f"SHOW GLOBAL {kind} LIKE %s", [pattern]):
            normalized_row = _normalize_show_variable_row(row)
            row_key = str(normalized_row["name"]).lower()
            if row_key in seen_names:
                continue
            seen_names.add(row_key)
            rows.append(normalized_row)
    rows.sort(key=lambda item: str(item["name"]).lower())
    return rows


def fetch_all_show_variable_rows(kind):
    normalized_kind = str(kind or "").strip().upper()
    if normalized_kind not in {"VARIABLES", "STATUS"}:
        raise ValueError("Unsupported SHOW GLOBAL kind.")
    rows = [_normalize_show_variable_row(row) for row in execute_query(f"SHOW GLOBAL {normalized_kind}")]
    rows.sort(key=lambda item: str(item["name"]).lower())
    return rows


def _is_on_value(value):
    return str(value or "").strip().upper() in {"1", "ON", "YES", "TRUE", "ENABLED", "ACTIVE", "FORCE"}


def _dynamic_table_rows(schema_name, table_name, column_candidates, *, order_by_candidates=None, limit=50):
    column_lookup = fetch_table_column_lookup(schema_name, table_name)
    if not column_lookup:
        return []

    selected_columns = []
    output_columns = []
    for output_name, candidates in column_candidates:
        actual_column = _first_available_column(column_lookup, candidates)
        if not actual_column:
            continue
        selected_columns.append(f"{quote_identifier(actual_column)} AS {quote_identifier(output_name)}")
        output_columns.append(output_name)

    if not selected_columns:
        return []

    safe_schema = quote_identifier(schema_name)
    safe_table = quote_identifier(table_name)
    sql = f"SELECT {', '.join(selected_columns)} FROM {safe_schema}.{safe_table}"
    order_columns = []
    for candidate in order_by_candidates or []:
        actual_column = column_lookup.get(str(candidate).lower())
        if actual_column:
            order_columns.append(quote_identifier(actual_column))
    if order_columns:
        sql += " ORDER BY " + ", ".join(order_columns)
    sql += f" LIMIT {int(limit)}"

    result_rows = []
    for row in execute_query(sql):
        result_rows.append({column: row.get(column) if row.get(column) is not None else "-" for column in output_columns})
    return result_rows


def fetch_audit_security_info(security_features):
    info = {
        "enabled_label": "Off",
        "variables": [],
        "status_rows": [],
        "filter_rows": [],
        "user_rows": [],
        "errors": [],
    }
    try:
        info["variables"] = fetch_show_variable_rows("VARIABLES", ["audit_log%"])
        info["status_rows"] = fetch_show_variable_rows("STATUS", ["audit_log%"])
    except Exception as error:  # pragma: no cover - depends on privileges / server features
        info["errors"].append(str(error))

    try:
        info["filter_rows"] = _dynamic_table_rows(
            "mysql",
            "audit_log_filter",
            [
                ("filter_name", ["name", "filter_name"]),
                ("filter_rule", ["filter", "rule", "definition"]),
            ],
            order_by_candidates=["name", "filter_name"],
            limit=25,
        )
    except Exception as error:  # pragma: no cover - Enterprise Audit only
        info["errors"].append(str(error))

    try:
        info["user_rows"] = _dynamic_table_rows(
            "mysql",
            "audit_log_user",
            [
                ("user", ["user", "username"]),
                ("host", ["host"]),
                ("filter_name", ["filtername", "filter_name", "name"]),
            ],
            order_by_candidates=["user", "host"],
            limit=50,
        )
    except Exception as error:  # pragma: no cover - Enterprise Audit only
        info["errors"].append(str(error))

    audit_feature_enabled = any("audit" in row["feature_name"].lower() and row["is_enabled"] for row in security_features)
    audit_variable_enabled = any(_is_on_value(row["value"]) for row in info["variables"] if str(row["name"]).lower() in {"audit_log", "audit_log_filter_id"})
    if audit_feature_enabled or audit_variable_enabled or info["filter_rows"] or info["user_rows"]:
        info["enabled_label"] = "On"
    return info


def empty_audit_security_info():
    return {
        "enabled_label": "-",
        "variables": [],
        "status_rows": [],
        "filter_rows": [],
        "user_rows": [],
        "errors": [],
    }


def fetch_firewall_security_info(security_features):
    info = {
        "enabled_label": "Off",
        "variables": [],
        "status_rows": [],
        "user_rows": [],
        "rule_rows": [],
        "errors": [],
    }
    try:
        info["variables"] = fetch_show_variable_rows("VARIABLES", ["mysql_firewall%"])
        info["status_rows"] = fetch_show_variable_rows("STATUS", ["mysql_firewall%"])
    except Exception as error:  # pragma: no cover - depends on privileges / server features
        info["errors"].append(str(error))

    try:
        info["user_rows"] = _dynamic_table_rows(
            "mysql",
            "firewall_users",
            [
                ("user_host", ["userhost", "user_host"]),
                ("mode", ["mode"]),
            ],
            order_by_candidates=["userhost", "user_host"],
            limit=50,
        )
    except Exception as error:  # pragma: no cover - Enterprise Firewall only
        info["errors"].append(str(error))
    if not info["user_rows"]:
        try:
            info["user_rows"] = _dynamic_table_rows(
                "information_schema",
                "MYSQL_FIREWALL_USERS",
                [
                    ("user_host", ["userhost", "user_host"]),
                    ("mode", ["mode"]),
                ],
                order_by_candidates=["userhost", "user_host"],
                limit=50,
            )
        except Exception as error:  # pragma: no cover - Enterprise Firewall only
            info["errors"].append(str(error))

    try:
        info["rule_rows"] = _dynamic_table_rows(
            "mysql",
            "firewall_whitelist",
            [
                ("user_host", ["userhost", "user_host"]),
                ("rule", ["rule"]),
            ],
            order_by_candidates=["userhost", "user_host"],
            limit=50,
        )
    except Exception as error:  # pragma: no cover - Enterprise Firewall only
        info["errors"].append(str(error))
    if not info["rule_rows"]:
        try:
            info["rule_rows"] = _dynamic_table_rows(
                "information_schema",
                "MYSQL_FIREWALL_WHITELIST",
                [
                    ("user_host", ["userhost", "user_host"]),
                    ("rule", ["rule"]),
                ],
                order_by_candidates=["userhost", "user_host"],
                limit=50,
            )
        except Exception as error:  # pragma: no cover - Enterprise Firewall only
            info["errors"].append(str(error))

    firewall_feature_enabled = any("firewall" in row["feature_name"].lower() and row["is_enabled"] for row in security_features)
    firewall_variable_enabled = any(_is_on_value(row["value"]) for row in info["variables"] if str(row["name"]).lower() in {"mysql_firewall_mode", "mysql_firewall_trace"})
    if firewall_feature_enabled or firewall_variable_enabled or info["user_rows"] or info["rule_rows"]:
        info["enabled_label"] = "On"
    return info


def empty_firewall_security_info():
    return {
        "enabled_label": "-",
        "variables": [],
        "status_rows": [],
        "user_rows": [],
        "rule_rows": [],
        "errors": [],
    }


def fetch_password_security_info(security_features):
    info = {
        "enabled_label": "Off",
        "variables": [],
        "status_rows": [],
        "errors": [],
    }
    password_variable_patterns = [
        "validate_password%",
        "default_password_lifetime",
        "disconnect_on_expired_password",
        "password_history",
        "password_reuse_interval",
        "password_require_current",
    ]
    try:
        info["variables"] = fetch_show_variable_rows("VARIABLES", password_variable_patterns)
        info["status_rows"] = fetch_show_variable_rows("STATUS", ["validate_password%"])
    except Exception as error:  # pragma: no cover - depends on privileges / server features
        info["errors"].append(str(error))

    password_feature_enabled = any(
        ("password" in row["feature_name"].lower() or "validate_password" in row["feature_name"].lower())
        and row["is_enabled"]
        for row in security_features
    )
    policy_variable_enabled = any(
        str(row["name"]).lower().startswith("validate_password")
        for row in info["variables"]
    )
    if password_feature_enabled or policy_variable_enabled:
        info["enabled_label"] = "On"
    return info


def empty_password_security_info():
    return {
        "enabled_label": "-",
        "variables": [],
        "status_rows": [],
        "errors": [],
    }


def normalize_error_log_period(value):
    candidate = str(value or "").strip().lower()
    allowed = {option["value"]: option for option in ERROR_LOG_PERIOD_OPTIONS}
    return allowed.get(candidate, allowed["1d"])


def fetch_recent_error_log_rows(hours=24, limit=50, priorities=None, error_code="", message_like=""):
    normalized_priorities = _normalize_error_log_priorities(priorities)
    normalized_error_code = normalize_error_log_code(error_code)
    error_code_filter = parse_error_log_code_filter(normalized_error_code)
    error_code_values = error_code_filter["codes"]
    normalized_message_like = normalize_error_log_message_like(message_like)
    column_lookup = fetch_table_column_lookup("performance_schema", "error_log")
    if not column_lookup:
        raise ValueError("performance_schema.error_log is not available on this server.")

    logged_column = column_lookup.get("logged")
    prio_column = column_lookup.get("prio") or column_lookup.get("priority")
    error_code_column = column_lookup.get("error_code")
    subsystem_column = column_lookup.get("subsystem")
    data_column = column_lookup.get("data") or column_lookup.get("message")

    if not logged_column or not data_column:
        raise ValueError("Unable to determine required columns for performance_schema.error_log.")

    selected_columns = [f"{quote_identifier(logged_column)} AS logged_value"]
    if prio_column:
        selected_columns.append(f"{quote_identifier(prio_column)} AS priority_value")
    if error_code_column:
        selected_columns.append(f"{quote_identifier(error_code_column)} AS error_code_value")
    if subsystem_column:
        selected_columns.append(f"{quote_identifier(subsystem_column)} AS subsystem_value")
    selected_columns.append(f"{quote_identifier(data_column)} AS message_value")

    sql = (
        "SELECT {columns} "
        "FROM performance_schema.error_log "
        "WHERE 1 = 1"
    ).format(
        columns=", ".join(selected_columns),
    )
    params = []
    if hours is not None:
        sql += " AND {logged_column} >= NOW() - INTERVAL %s HOUR".format(
            logged_column=quote_identifier(logged_column),
        )
        params.append(int(hours))
    if prio_column and normalized_priorities:
        sql += " AND UPPER(COALESCE({priority_column}, '')) IN ({placeholders})".format(
            priority_column=quote_identifier(prio_column),
            placeholders=", ".join(["%s"] * len(normalized_priorities)),
        )
        params.extend(priority.upper() for priority in normalized_priorities)
    if error_code_column and error_code_values:
        sql += " AND CAST({error_code_column} AS CHAR) {operator} ({placeholders})".format(
            error_code_column=quote_identifier(error_code_column),
            operator=error_code_filter["operator"],
            placeholders=", ".join(["%s"] * len(error_code_values)),
        )
        params.extend(error_code_values)
    if normalized_message_like:
        sql += " AND {data_column} LIKE %s".format(
            data_column=quote_identifier(data_column),
        )
        params.append(f"%{normalized_message_like}%")
    sql += " ORDER BY {logged_column} DESC LIMIT {limit}".format(
        logged_column=quote_identifier(logged_column),
        limit=int(limit),
    )

    rows = execute_query(sql, params)
    return [
        {
            "logged": row["logged_value"],
            "priority": str(row.get("priority_value") or "-"),
            "error_code": row.get("error_code_value") or "-",
            "subsystem": row.get("subsystem_value") or "-",
            "message": row.get("message_value") or "-",
        }
        for row in rows
    ]


def fetch_mysql_shell_version():
    package_checks = [
        (["rpm", "-q", "--qf", "%{VERSION}", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
        (["dpkg-query", "-W", "-f=${Version}", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
        (["brew", "list", "--cask", "--versions", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
        (["brew", "list", "--formula", "--versions", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
    ]
    for command, version_pattern in package_checks:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        package_output = (result.stdout or result.stderr or "").strip()
        version_match = re.search(version_pattern, package_output)
        if version_match:
            return version_match.group(0)

    mysqlsh_command = (
        os.environ.get("DBCONSOLE_MYSQLSH")
        or os.environ.get("MYSQLSH")
        or "mysqlsh"
    )
    try:
        mysqlsh_timeout = max(1, int(os.environ.get("DBCONSOLE_MYSQLSH_TIMEOUT", "5")))
    except (TypeError, ValueError):
        mysqlsh_timeout = 5
    try:
        result = subprocess.run(
            [mysqlsh_command, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=mysqlsh_timeout,
            check=False,
        )
    except FileNotFoundError:
        return "Not found"
    except subprocess.TimeoutExpired:
        return f"Timed out after {mysqlsh_timeout}s"
    except Exception as error:  # pragma: no cover - depends on host runtime
        return f"Unavailable: {error}"

    version_output = (result.stdout or result.stderr or "").strip()
    if not version_output:
        return "Unavailable"
    version_match = re.search(r"\b[0-9]+(?:\.[0-9]+){2}\b", version_output)
    if version_match:
        return version_match.group(0)
    return version_output.splitlines()[0]


def fetch_server_overview(
    recent_error_log_priorities=None,
    recent_error_log_period=None,
    recent_error_log_code="",
    recent_error_log_message_like="",
    sections=None,
):
    selected_sections = set(sections or {"server-database", "logs", "security", "heatwave", "replication"})
    selected_error_log_priorities = _normalize_error_log_priorities(recent_error_log_priorities)
    selected_error_log_period = normalize_error_log_period(recent_error_log_period)
    selected_error_log_code = normalize_error_log_code(recent_error_log_code)
    selected_error_log_message_like = normalize_error_log_message_like(recent_error_log_message_like)
    version = fetch_scalar("SELECT VERSION()", default="-")
    hostname = fetch_scalar("SELECT @@hostname", default="-")
    include_server_database = "server-database" in selected_sections
    include_logs = "logs" in selected_sections
    include_security = "security" in selected_sections
    include_heatwave = "heatwave" in selected_sections
    include_replication = "replication" in selected_sections

    mysql_shell_version = fetch_mysql_shell_version() if include_server_database else "-"
    current_user = default_database = global_time_zone = session_time_zone = system_time_zone = "-"
    global_sql_mode = session_sql_mode = server_charset = server_collation = "-"
    max_connections = current_connection_count = database_count = 0
    table_totals = {}
    total_size_bytes = 0
    innodb_table_rows = []
    view_rows = []
    routine_rows = []
    time_zone_name_count = 0
    time_zone_tables_populated = False
    time_zone_tables_label = "-"
    time_zone_tables_error = ""

    if include_server_database:
        current_user = fetch_scalar("SELECT CURRENT_USER()", default="-")
        default_database = fetch_scalar("SELECT DATABASE()", default="-")
        global_time_zone = fetch_scalar("SELECT @@GLOBAL.time_zone", default="-")
        session_time_zone = fetch_scalar("SELECT @@SESSION.time_zone", default="-")
        system_time_zone = fetch_scalar("SELECT @@system_time_zone", default="-")
        global_sql_mode = fetch_scalar("SELECT @@GLOBAL.sql_mode", default="-")
        session_sql_mode = fetch_scalar("SELECT @@SESSION.sql_mode", default="-")
        server_charset = fetch_scalar("SELECT @@character_set_server", default="-")
        server_collation = fetch_scalar("SELECT @@collation_server", default="-")
        max_connections = fetch_scalar("SELECT @@max_connections", default=0)
        threads_connected_rows = execute_query("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
        if threads_connected_rows:
            thread_row = threads_connected_rows[0]
            current_connection_count = int(
                thread_row.get("Value")
                or thread_row.get("value")
                or thread_row.get("VARIABLE_VALUE")
                or thread_row.get("variable_value")
                or 0
            )
        database_count = fetch_scalar(
            """
            SELECT COUNT(*) AS database_count_value
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
              AND schema_name NOT LIKE 'mysql@_%' ESCAPE '@'
            """,
            default=0,
        )
        table_totals = execute_query(
            """
            SELECT
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END), 0) AS base_table_count_value,
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length ELSE 0 END), 0) AS data_bytes_value,
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN index_length ELSE 0 END), 0) AS index_bytes_value,
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length + index_length ELSE 0 END), 0) AS total_bytes_value
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
              AND table_schema NOT LIKE 'mysql@_%' ESCAPE '@'
            """,
        )
        table_totals = table_totals[0] if table_totals else {}
        total_size_bytes = table_totals.get("total_bytes_value") or 0
        innodb_table_rows = fetch_dashboard_innodb_table_rows()
        view_rows = fetch_dashboard_view_rows()
        routine_rows = fetch_dashboard_routine_rows()

        try:
            time_zone_name_count = fetch_scalar("SELECT COUNT(*) FROM mysql.time_zone_name", default=0)
            time_zone_tables_populated = bool(time_zone_name_count)
            time_zone_tables_label = f"Yes ({time_zone_name_count} rows)" if time_zone_name_count else "No"
            time_zone_tables_error = ""
        except Exception as error:  # pragma: no cover - depends on privileges / server setup
            time_zone_name_count = 0
            time_zone_tables_populated = False
            time_zone_tables_label = "Unavailable"
            time_zone_tables_error = str(error)

    replication_info = fetch_replication_overview_info() if include_replication else empty_replication_overview_info()
    try:
        rapid_status_rows = execute_query("SHOW GLOBAL STATUS LIKE 'rapid%status'") if include_heatwave else []
    except Exception as error:  # pragma: no cover - depends on server features
        rapid_status_rows = [{"Variable_name": "rapid_status_error", "Value": str(error)}]

    installed_components = []
    installed_components_error = ""
    security_features = []
    security_features_error = ""
    audit_info = empty_audit_security_info()
    firewall_info = empty_firewall_security_info()
    password_info = empty_password_security_info()
    if include_security:
        try:
            installed_components = fetch_installed_component_rows()
            installed_components_error = ""
        except Exception as error:  # pragma: no cover - depends on server features
            installed_components = []
            installed_components_error = str(error)

        try:
            security_features = fetch_security_feature_rows(installed_components)
            security_features_error = ""
        except Exception as error:  # pragma: no cover - depends on server features
            security_features = []
            security_features_error = str(error)

        audit_info = fetch_audit_security_info(security_features)
        firewall_info = fetch_firewall_security_info(security_features)
        password_info = fetch_password_security_info(security_features)

    recent_error_log_rows = []
    recent_error_log_error = ""
    if include_logs:
        try:
            recent_error_log_rows = fetch_recent_error_log_rows(
                hours=selected_error_log_period["hours"],
                limit=50,
                priorities=selected_error_log_priorities,
                error_code=selected_error_log_code,
                message_like=selected_error_log_message_like,
            )
            recent_error_log_error = ""
        except Exception as error:  # pragma: no cover - depends on server features
            recent_error_log_rows = []
            recent_error_log_error = str(error)

    return {
        "server_version": version,
        "server_hostname": hostname,
        "mysql_shell_version": mysql_shell_version,
        "current_user": current_user,
        "default_database": default_database,
        "global_time_zone": global_time_zone,
        "session_time_zone": session_time_zone,
        "system_time_zone": system_time_zone,
        "time_zone_tables_populated": time_zone_tables_populated,
        "time_zone_tables_label": time_zone_tables_label,
        "time_zone_table_row_count": time_zone_name_count,
        "time_zone_tables_error": time_zone_tables_error,
        "global_sql_mode": global_sql_mode,
        "session_sql_mode": session_sql_mode,
        "server_charset": server_charset,
        "server_collation": server_collation,
        "max_connections": max_connections or 0,
        "current_connection_count": current_connection_count,
        "database_count": database_count,
        "table_count": table_totals.get("base_table_count_value") or 0,
        "innodb_table_count": len(innodb_table_rows),
        "view_count": len(view_rows),
        "routine_count": len(routine_rows),
        "procedure_count": len(routine_rows),
        "data_bytes": table_totals.get("data_bytes_value") or 0,
        "index_bytes": table_totals.get("index_bytes_value") or 0,
        "total_size_bytes": total_size_bytes,
        "total_size_label": _format_bytes(total_size_bytes),
        "innodb_table_rows": innodb_table_rows,
        "view_rows": view_rows,
        "routine_rows": routine_rows,
        "replication_info": replication_info,
        "rapid_status_rows": rapid_status_rows[:10],
        "installed_components": installed_components,
        "installed_components_error": installed_components_error,
        "installed_component_count": len(installed_components),
        "security_features": security_features,
        "security_features_error": security_features_error,
        "security_feature_count": len(security_features),
        "enabled_security_feature_count": sum(1 for row in security_features if row["is_enabled"]),
        "audit_info": audit_info,
        "firewall_info": firewall_info,
        "password_info": password_info,
        "error_log_priority_options": list(ERROR_LOG_PRIORITY_OPTIONS),
        "error_log_period_options": list(ERROR_LOG_PERIOD_OPTIONS),
        "selected_error_log_priorities": selected_error_log_priorities,
        "selected_error_log_priority_label": ", ".join(selected_error_log_priorities) if selected_error_log_priorities else "All",
        "selected_error_log_period": selected_error_log_period,
        "selected_error_log_code": selected_error_log_code,
        "selected_error_log_message_like": selected_error_log_message_like,
        "recent_error_log_rows": recent_error_log_rows,
        "recent_error_log_error": recent_error_log_error,
        "recent_error_log_count": len(recent_error_log_rows),
        "connection_endpoint": f"{get_session_profile()['host']}:{get_session_profile()['port']}",
    }


def _empty_status_variable_page(active_tab):
    normalized_tab = "variables" if str(active_tab or "").strip().lower() == "variables" else "status"
    return {
        "tab": normalized_tab,
        "tab_label": "Global Variables" if normalized_tab == "variables" else "Global Status",
        "show_source_details": normalized_tab == "variables",
        "total_count": 0,
        "non_empty_count": 0,
        "sections": [
            {
                "key": section["key"],
                "label": section["label"],
                "rows": [],
                "row_count": 0,
                "open_by_default": False,
            }
            for section in STATUS_VARIABLE_SECTIONS
        ],
    }


def _format_status_variable_source(raw_source):
    source = str(raw_source or "").strip()
    if not source:
        return ""
    return source.replace("_", " ").title()


def _normalize_status_variable_row(row):
    name = str(
        row.get("Variable_name")
        or row.get("variable_name")
        or row.get("metric_name")
        or row.get("variable_name_value")
        or ""
    ).strip()
    raw_value = (
        row.get("Value")
        if "Value" in row
        else row.get("value")
        if "value" in row
        else row.get("metric_value")
        if "metric_value" in row
        else row.get("variable_value")
    )
    raw_source = (
        row.get("variable_source")
        if "variable_source" in row
        else row.get("variable_source_value")
        if "variable_source_value" in row
        else row.get("source")
    )
    raw_path = (
        row.get("variable_path")
        if "variable_path" in row
        else row.get("variable_path_value")
        if "variable_path_value" in row
        else row.get("path")
    )
    return {
        "name": name,
        "value": "" if raw_value is None else str(raw_value),
        "source": _format_status_variable_source(raw_source),
        "path": str(raw_path or "").strip(),
    }


def _classify_status_variable(name):
    lowered = str(name or "").strip().lower()
    if not lowered:
        return "general"
    if lowered.startswith(("innodb_ft_", "ft_", "fts_")) or "_fts_" in lowered:
        return "full_text"
    if lowered.startswith("performance_schema") or "performance_schema" in lowered:
        return "performance_schema"
    if lowered.startswith(
        (
            "audit",
            "admin",
            "ssl_",
            "tls_",
            "admin_ssl_",
            "admin_tls_",
            "validate_password",
            "caching_sha2_password",
            "sha256_password",
            "sha256",
            "authentication_",
            "keyring_",
            "component_keyring_",
            "mysql_firewall_",
            "enterprise_encryption",
            "password_",
            "secure_",
        )
    ) or lowered in {
        "auto_generate_certs",
        "default_authentication_plugin",
        "default_password_lifetime",
        "disconnect_on_expired_password",
        "generated_random_password_length",
        "have_openssl",
        "have_ssl",
        "require_secure_transport",
        "table_encryption_privilege_check",
    } or any(
        token in lowered
        for token in (
            "audit",
            "password",
            "_sha2",
            "ssl",
            "tls",
            "encryption",
            "keyring",
            "wallet",
            "tde",
            "encrypt",
            "openssl",
            "kerberos",
            "ldap",
            "private_key",
            "public_key",
            "master_key",
            "key_path",
            "key_file",
            "_cert",
            "_crl",
            "rsa",
        )
    ):
        return "security"
    if lowered.startswith("mysqlx_"):
        return "mysqlx_specific"
    if lowered.startswith(
        ("rapid_", "heatwave_", "secondary_engine", "use_secondary_engine", "lakehouse_", "lakehouse")
    ) or "lakehouse" in lowered:
        return "heatwave_rapid"
    if lowered.startswith(("group_replication", "gr_")):
        return "replication"
    if lowered.startswith(
        (
            "replica",
            "slave",
            "source_",
            "replication_",
            "rpl_",
            "relay_log",
            "log_bin",
            "sync_relay_log",
            "master_",
            "binlog",
            "gtid_",
            "log_replica_updates",
            "log_slave_updates",
        )
    ) or lowered in {"read_only", "super_read_only"}:
        return "replication"
    if lowered.startswith(("innodb_", "innobase_", "have_innodb")):
        return "innodb"
    if lowered.startswith(
        (
            "join_buffer",
            "sort_buffer",
            "read_buffer",
            "read_rnd_buffer",
            "bulk_insert_buffer",
            "preload_buffer_size",
            "query_alloc_block",
            "query_prealloc_size",
            "query_cache",
            "optimizer_",
            "max_execution",
            "flush",
            "transaction_",
            "temptable_",
            "tmp_table_size",
            "max_heap_table_size",
            "table_open_cache",
            "table_definition_cache",
            "stored_program_cache",
            "host_cache_size",
            "range_alloc_block_size",
            "range_optimizer_",
            "parser_max_mem_size",
            "select_",
            "sort_",
            "handler_",
            "created_tmp_",
            "opened_",
            "queries",
            "slow_",
        )
    ) or lowered in {
        "eq_range_index_dive_limit",
        "flush_time",
        "lock_wait_timeout",
        "long_query_time",
        "max_seeks_for_key",
        "max_sort_length",
        "open_files_limit",
        "optimizer_prune_level",
        "optimizer_search_depth",
        "optimizer_trace_limit",
        "optimizer_trace_max_mem_size",
        "optimizer_trace_offset",
        "optimizer_trace_features",
        "sql_buffer_result",
        "sql_select_limit",
        "table_open_cache_instances",
        "table_open_cache_triggers",
        "transaction_alloc_block_size",
        "transaction_prealloc_size",
    } or any(
        token in lowered
        for token in (
            "join_buffer",
            "key_buffer",
            "key_cache",
            "max_execution",
            "optimizer",
            "transaction",
            "flush",
            "tmp_table",
            "table_open_cache",
            "table_definition_cache",
            "stored_program_cache",
            "query_cache",
            "query_alloc",
            "prealloc",
            "_instances",
        )
    ):
        return "query_performance"
    if lowered.startswith(
        (
            "threads_",
            "thread_",
            "connection_",
            "connections",
            "connection_errors_",
            "max_used_connections",
            "aborted_",
            "bytes_received",
            "bytes_sent",
            "socket_",
            "tcp_",
            "net_",
        )
    ) or lowered in {
        "connections",
        "aborted_clients",
        "aborted_connects",
        "locked_connects",
        "max_used_connections",
    }:
        return "connection_threads"
    return "general"


def _group_status_variables(rows, active_tab):
    grouped = _empty_status_variable_page(active_tab)
    section_lookup = {section["key"]: section for section in grouped["sections"]}
    total_count = 0

    for raw_row in rows:
        row = _normalize_status_variable_row(raw_row)
        if not row["name"]:
            continue
        section_key = _classify_status_variable(row["name"])
        section_lookup[section_key]["rows"].append(row)
        total_count += 1

    first_open_key = next(
        (
            section["key"]
            for section in grouped["sections"]
            if section["rows"]
        ),
        grouped["sections"][0]["key"] if grouped["sections"] else "",
    )

    non_empty_count = 0
    for section in grouped["sections"]:
        section["rows"].sort(key=lambda item: item["name"].lower())
        section["row_count"] = len(section["rows"])
        if section["row_count"]:
            non_empty_count += 1
        section["open_by_default"] = section["key"] == first_open_key

    grouped["total_count"] = total_count
    grouped["non_empty_count"] = non_empty_count
    return grouped


def fetch_grouped_variable_rows():
    try:
        global_columns = fetch_table_column_lookup("performance_schema", "global_variables")
        info_columns = fetch_table_column_lookup("performance_schema", "variables_info")
        global_name_column = _first_available_column(global_columns, ["variable_name"])
        global_value_column = _first_available_column(global_columns, ["variable_value"])
        info_name_column = _first_available_column(info_columns, ["variable_name"])
        info_source_column = _first_available_column(info_columns, ["variable_source"])
        info_path_column = _first_available_column(info_columns, ["variable_path"])

        if global_name_column and global_value_column and info_name_column and (info_source_column or info_path_column):
            selected_columns = [
                f"gv.{quote_identifier(global_name_column)} AS variable_name_value",
                f"gv.{quote_identifier(global_value_column)} AS variable_value",
            ]
            if info_source_column:
                selected_columns.append(f"vi.{quote_identifier(info_source_column)} AS variable_source_value")
            if info_path_column:
                selected_columns.append(f"vi.{quote_identifier(info_path_column)} AS variable_path_value")
            return execute_query(
                """
                SELECT
                  {selected_columns}
                FROM performance_schema.global_variables AS gv
                LEFT JOIN performance_schema.variables_info AS vi
                  ON gv.{global_name_column} = vi.{info_name_column}
                ORDER BY gv.{global_name_column}
                """.format(
                    selected_columns=",\n                  ".join(selected_columns),
                    global_name_column=quote_identifier(global_name_column),
                    info_name_column=quote_identifier(info_name_column),
                ),
                database="performance_schema",
            )
    except Exception:
        pass
    return execute_query("SHOW GLOBAL VARIABLES")


def fetch_grouped_status_variables(active_tab):
    normalized_tab = "variables" if str(active_tab or "").strip().lower() == "variables" else "status"
    if normalized_tab == "variables":
        rows = fetch_grouped_variable_rows()
    else:
        rows = execute_query("SHOW GLOBAL STATUS")
    return _group_status_variables(rows, normalized_tab)


def run_report_query(sql, params=None, *, database=None):
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
    return {"columns": columns, "rows": rows}


def fetch_monitoring_performance_queries():
    return run_report_query(
        """
        SELECT
          QUERY_ID AS query_id,
          QUERY_TEXT AS query_text,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryStartTime", '$[0]')),
            '%%Y-%%m-%%d %%H:%%i:%%s.%%f'
          ) AS query_start,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.qexecStartTime", '$[0]')),
            '%%Y-%%m-%%d %%H:%%i:%%s.%%f'
          ) AS rapid_start,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.timeBetweenMakePushedJoinAndRpdExecMsec", '$[0]') AS queue_wait_ms,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryEndTime", '$[0]')),
            '%%Y-%%m-%%d %%H:%%i:%%s.%%f'
          ) AS query_end,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.executionTime", '$[0]') AS execution_ms,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.sessionId", '$[0]') AS connection_id
        FROM performance_schema.rpd_query_stats
        WHERE query_text NOT LIKE 'ML_%%'
        ORDER BY query_id DESC
        LIMIT 200
        """
    )


def fetch_monitoring_ml_queries(current_ml_connection_only=False):
    connection_filter = ""
    if current_ml_connection_only:
        connection_filter = """
          AND connection_id = (
            SELECT id
            FROM performance_schema.processlist
            WHERE info LIKE 'SET rapid_ml_operation%%'
            LIMIT 1
          )
        """
    return run_report_query(
        """
        SELECT
          QEXEC_TEXT->>"$.startTime" AS start_time,
          query_text,
          QEXEC_TEXT->>"$.status" AS status,
          QEXEC_TEXT->>"$.totalRunTime" AS total_run_time,
          QEXEC_TEXT->>"$.details.operation" AS operation,
          QEXEC_TEXT->>"$.completionPercentage" AS completion_percentage,
          query_id,
          connection_id
        FROM performance_schema.rpd_query_stats
        WHERE query_text LIKE 'ML_%%'
        {connection_filter}
        ORDER BY start_time DESC
        LIMIT 200
        """.format(connection_filter=connection_filter)
    )


def fetch_monitoring_load_recovery():
    return run_report_query(
        """
        SELECT
          rpd_table_id.id AS table_id,
          rpd_table_id.name AS table_name,
          rpd_tables.size_bytes AS size_bytes,
          rpd_tables.query_count AS query_count,
          rpd_tables.recovery_source AS recovery_source,
          rpd_tables.load_start_timestamp AS load_start_timestamp,
          TIME_TO_SEC(TIMEDIFF(rpd_tables.load_end_timestamp, rpd_tables.load_start_timestamp)) AS duration_seconds
        FROM performance_schema.rpd_tables
        JOIN performance_schema.rpd_table_id
          ON rpd_tables.id = rpd_table_id.id
        ORDER BY rpd_tables.size_bytes DESC
        LIMIT 200
        """
    )


def _empty_report():
    return {"columns": [], "rows": [], "error": ""}


def _safe_report(fetcher, *args, **kwargs):
    try:
        report = fetcher(*args, **kwargs)
        report["error"] = ""
        return report
    except Exception as error:
        return {"columns": [], "rows": [], "error": str(error)}


def _format_bytes(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    unit_index = 0
    while number >= 1024 and unit_index < len(units) - 1:
        number /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(number)} {units[unit_index]}"
    return f"{number:.1f} {units[unit_index]}"


def _coerce_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_numeric(value, default=None):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _parse_mysql_size_to_bytes(value):
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*([KMGTPE]?)(?:I?B?)?\s*", text)
    if not match:
        return _extract_numeric(text, None)
    number = float(match.group(1))
    suffix = match.group(2).upper()
    power_map = {
        "": 0,
        "K": 1,
        "M": 2,
        "G": 3,
        "T": 4,
        "P": 5,
        "E": 6,
    }
    return number * (1024 ** power_map.get(suffix, 0))


def _estimate_temp_tablespace_bytes_from_path(temp_data_file_path):
    file_specs = [segment.strip() for segment in str(temp_data_file_path or "").split(";") if segment.strip()]
    total_bytes = 0
    found_size = False
    for file_spec in file_specs:
        parts = [part.strip() for part in file_spec.split(":") if part.strip()]
        if len(parts) < 2:
            continue
        size_bytes = _parse_mysql_size_to_bytes(parts[1])
        if size_bytes is None:
            continue
        total_bytes += size_bytes
        found_size = True
    if not found_size:
        return None
    return total_bytes


def _format_count(value):
    number = _extract_numeric(value, None)
    if number is None:
        return "-"
    if float(number).is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def _format_milliseconds(value):
    number = _extract_numeric(value, None)
    if number is None:
        return "-"
    if number >= 60000:
        return f"{number / 60000.0:.1f} min"
    if number >= 1000:
        return f"{number / 1000.0:.1f} s"
    return f"{number:.0f} ms"


def _duration_value_to_ms(column_name, value):
    number = _extract_numeric(value, None)
    if number is None:
        return None
    lowered = str(column_name or "").lower()
    if "nanosecond" in lowered or lowered.endswith("_ns"):
        return number / 1_000_000.0
    if "microsecond" in lowered or lowered.endswith("_us"):
        return number / 1000.0
    if (lowered.endswith("_sec") or lowered.endswith("_secs") or lowered.endswith("_seconds")) and not lowered.endswith("_ms"):
        return number * 1000.0
    return number


def _report_row_map(report, key_column, value_column):
    mapping = {}
    for row in report.get("rows", []):
        key = row.get(key_column)
        if key is None:
            continue
        mapping[str(key)] = row.get(value_column)
    return mapping


def _first_available_column(column_lookup, candidates):
    for candidate in candidates:
        actual_name = column_lookup.get(candidate.lower())
        if actual_name:
            return actual_name
    return None


def _chart_card(card_id, title, subtitle, kind, *, unit="count", series=None, bars=None, details=None, error=""):
    return {
        "id": card_id,
        "title": title,
        "subtitle": subtitle,
        "kind": kind,
        "unit": unit,
        "series": series or [],
        "bars": bars or [],
        "details": details or [],
        "error": error,
    }


def _sum_report_column(report, column_name):
    total = 0
    found = False
    for row in report.get("rows", []):
        value = _coerce_int(row.get(column_name), None)
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def fetch_monitoring_global_status():
    return run_report_query(
        """
        SELECT
          variable_name AS metric_name,
          variable_value AS metric_value
        FROM performance_schema.global_status
        WHERE variable_name IN (
          'Threads_connected',
          'Threads_running',
          'Created_tmp_tables',
          'Created_tmp_disk_tables',
          'Created_tmp_files'
        )
        ORDER BY variable_name
        """
    )


def fetch_monitoring_user_processlist():
    return run_report_query(
        """
        SELECT
          id AS connection_id,
          user AS user_name,
          host AS host_name,
          db AS database_name,
          command AS command_name,
          time AS elapsed_seconds,
          state AS state_name,
          LEFT(info, 240) AS current_sql
        FROM performance_schema.processlist
        WHERE user IS NOT NULL
          AND user NOT IN ('event_scheduler', 'system user', 'mysql.session')
        ORDER BY time DESC, id DESC
        LIMIT 100
        """
    )


def fetch_monitoring_current_connections():
    return run_report_query(
        """
        SELECT
          COALESCE(user, '(internal)') AS user_name,
          SUBSTRING_INDEX(COALESCE(host, ''), ':', 1) AS host_name,
          COALESCE(db, '') AS database_name,
          COUNT(*) AS connection_count,
          SUM(CASE WHEN command <> 'Sleep' THEN 1 ELSE 0 END) AS active_count,
          MAX(time) AS max_age_seconds
        FROM performance_schema.processlist
        GROUP BY COALESCE(user, '(internal)'), SUBSTRING_INDEX(COALESCE(host, ''), ':', 1), COALESCE(db, '')
        ORDER BY connection_count DESC, active_count DESC, user_name
        LIMIT 100
        """
    )


def fetch_monitoring_innodb_memory_usage():
    return run_report_query(
        """
        SELECT
          REPLACE(event_name, 'memory/innodb/', '') AS event_name,
          current_count_used AS allocation_count,
          current_number_of_bytes_used AS current_bytes,
          high_number_of_bytes_used AS high_bytes
        FROM performance_schema.memory_summary_global_by_event_name
        WHERE event_name LIKE 'memory/innodb/%%'
        ORDER BY current_number_of_bytes_used DESC
        LIMIT 25
        """
    )


def fetch_monitoring_lock_waits():
    return run_report_query(
        """
        SELECT
          COALESCE(waiting_lock.object_schema, blocking_lock.object_schema) AS object_schema,
          COALESCE(waiting_lock.object_name, blocking_lock.object_name) AS object_name,
          waiting_thread.processlist_id AS waiting_connection_id,
          waiting_thread.processlist_user AS waiting_user,
          waiting_thread.processlist_time AS waiting_seconds,
          waiting_lock.lock_type AS waiting_lock_type,
          waiting_lock.lock_mode AS waiting_lock_mode,
          blocking_thread.processlist_id AS blocking_connection_id,
          blocking_thread.processlist_user AS blocking_user,
          blocking_thread.processlist_time AS blocking_seconds,
          blocking_lock.lock_type AS blocking_lock_type,
          blocking_lock.lock_mode AS blocking_lock_mode
        FROM performance_schema.data_lock_waits AS waits
        JOIN performance_schema.data_locks AS waiting_lock
          ON waits.requesting_engine_lock_id = waiting_lock.engine_lock_id
        JOIN performance_schema.data_locks AS blocking_lock
          ON waits.blocking_engine_lock_id = blocking_lock.engine_lock_id
        LEFT JOIN performance_schema.threads AS waiting_thread
          ON waiting_lock.thread_id = waiting_thread.thread_id
        LEFT JOIN performance_schema.threads AS blocking_thread
          ON blocking_lock.thread_id = blocking_thread.thread_id
        ORDER BY object_schema, object_name, waiting_seconds DESC
        LIMIT 100
        """
    )


def fetch_monitoring_lock_table_detail(lock_schema, lock_table):
    return run_report_query(
        """
        SELECT
          object_schema,
          object_name,
          index_name,
          lock_type,
          lock_mode,
          lock_status,
          lock_data,
          thread.processlist_id AS connection_id,
          thread.processlist_user AS user_name,
          thread.processlist_db AS database_name,
          thread.processlist_time AS elapsed_seconds
        FROM performance_schema.data_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.thread_id = thread.thread_id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
        ORDER BY connection_id, index_name, lock_mode
        LIMIT 200
        """,
        [lock_schema, lock_table],
    )


def fetch_monitoring_lock_connection_detail(connection_id):
    return run_report_query(
        """
        SELECT
          thread.processlist_id AS connection_id,
          thread.processlist_user AS user_name,
          thread.processlist_db AS database_name,
          thread.processlist_state AS state_name,
          thread.processlist_time AS elapsed_seconds,
          locks.object_schema,
          locks.object_name,
          locks.index_name,
          locks.lock_type,
          locks.lock_mode,
          locks.lock_status,
          locks.lock_data
        FROM performance_schema.data_locks AS locks
        JOIN performance_schema.threads AS thread
          ON locks.thread_id = thread.thread_id
        WHERE thread.processlist_id = %s
        ORDER BY locks.object_schema, locks.object_name, locks.index_name
        LIMIT 200
        """,
        [connection_id],
    )


def fetch_monitoring_metadata_locks():
    return run_report_query(
        """
        SELECT
          object_type,
          object_schema,
          object_name,
          lock_type,
          lock_duration,
          lock_status,
          source,
          owner_thread_id,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE object_schema IS NOT NULL
        ORDER BY CASE WHEN lock_status = 'PENDING' THEN 0 ELSE 1 END, object_schema, object_name
        LIMIT 200
        """
    )


def fetch_monitoring_metadata_object_detail(lock_schema, lock_name):
    return run_report_query(
        """
        SELECT
          object_type,
          object_schema,
          object_name,
          lock_type,
          lock_duration,
          lock_status,
          source,
          owner_thread_id,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
        ORDER BY CASE WHEN lock_status = 'PENDING' THEN 0 ELSE 1 END, owner_connection_id
        LIMIT 200
        """,
        [lock_schema, lock_name],
    )


def fetch_monitoring_metadata_connection_detail(connection_id):
    return run_report_query(
        """
        SELECT
          object_type,
          object_schema,
          object_name,
          lock_type,
          lock_duration,
          lock_status,
          source,
          owner_thread_id,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE thread.processlist_id = %s
        ORDER BY CASE WHEN lock_status = 'PENDING' THEN 0 ELSE 1 END, object_schema, object_name
        LIMIT 200
        """,
        [connection_id],
    )


def fetch_monitoring_process_connection_detail(connection_id):
    return run_report_query(
        """
        SELECT
          id AS connection_id,
          user AS user_name,
          host AS host_name,
          db AS database_name,
          command AS command_name,
          time AS elapsed_seconds,
          state AS state_name,
          LEFT(info, 500) AS current_sql
        FROM performance_schema.processlist
        WHERE id = %s
        LIMIT 1
        """,
        [connection_id],
    )


def fetch_monitoring_row_lock_source_detail(lock_schema, lock_table, blocking_connection_id):
    return run_report_query(
        """
        SELECT
          waits.blocking_connection_id,
          waits.blocking_user,
          waits.blocking_seconds,
          waits.blocking_lock_type,
          waits.blocking_lock_mode,
          held_locks.index_name,
          held_locks.lock_type AS held_lock_type,
          held_locks.lock_mode AS held_lock_mode,
          held_locks.lock_status AS held_lock_status,
          held_locks.lock_data
        FROM (
          SELECT
            COALESCE(waiting_lock.object_schema, blocking_lock.object_schema) AS object_schema,
            COALESCE(waiting_lock.object_name, blocking_lock.object_name) AS object_name,
            blocking_thread.processlist_id AS blocking_connection_id,
            blocking_thread.processlist_user AS blocking_user,
            blocking_thread.processlist_time AS blocking_seconds,
            blocking_lock.lock_type AS blocking_lock_type,
            blocking_lock.lock_mode AS blocking_lock_mode,
            blocking_lock.thread_id AS blocking_thread_id
          FROM performance_schema.data_lock_waits AS lock_waits
          JOIN performance_schema.data_locks AS waiting_lock
            ON lock_waits.requesting_engine_lock_id = waiting_lock.engine_lock_id
          JOIN performance_schema.data_locks AS blocking_lock
            ON lock_waits.blocking_engine_lock_id = blocking_lock.engine_lock_id
          LEFT JOIN performance_schema.threads AS blocking_thread
            ON blocking_lock.thread_id = blocking_thread.thread_id
        ) AS waits
        LEFT JOIN performance_schema.data_locks AS held_locks
          ON waits.blocking_thread_id = held_locks.thread_id
         AND held_locks.object_schema = waits.object_schema
         AND held_locks.object_name = waits.object_name
        WHERE waits.object_schema = %s
          AND waits.object_name = %s
          AND waits.blocking_connection_id = %s
        ORDER BY held_locks.index_name, held_locks.lock_mode
        LIMIT 200
        """,
        [lock_schema, lock_table, blocking_connection_id],
    )


def fetch_monitoring_row_lock_impacted_detail(lock_schema, lock_table, waiting_connection_id):
    return run_report_query(
        """
        SELECT
          waits.waiting_connection_id,
          waits.waiting_user,
          waits.waiting_seconds,
          waits.waiting_lock_type,
          waits.waiting_lock_mode,
          process.command AS waiting_command,
          process.state AS waiting_state,
          LEFT(process.info, 500) AS waiting_sql
        FROM (
          SELECT
            COALESCE(waiting_lock.object_schema, blocking_lock.object_schema) AS object_schema,
            COALESCE(waiting_lock.object_name, blocking_lock.object_name) AS object_name,
            waiting_thread.processlist_id AS waiting_connection_id,
            waiting_thread.processlist_user AS waiting_user,
            waiting_thread.processlist_time AS waiting_seconds,
            waiting_lock.lock_type AS waiting_lock_type,
            waiting_lock.lock_mode AS waiting_lock_mode
          FROM performance_schema.data_lock_waits AS lock_waits
          JOIN performance_schema.data_locks AS waiting_lock
            ON lock_waits.requesting_engine_lock_id = waiting_lock.engine_lock_id
          JOIN performance_schema.data_locks AS blocking_lock
            ON lock_waits.blocking_engine_lock_id = blocking_lock.engine_lock_id
          LEFT JOIN performance_schema.threads AS waiting_thread
            ON waiting_lock.thread_id = waiting_thread.thread_id
        ) AS waits
        LEFT JOIN performance_schema.processlist AS process
          ON waits.waiting_connection_id = process.id
        WHERE waits.object_schema = %s
          AND waits.object_name = %s
          AND waits.waiting_connection_id = %s
        ORDER BY waits.waiting_seconds DESC
        LIMIT 50
        """,
        [lock_schema, lock_table, waiting_connection_id],
    )


def fetch_monitoring_metadata_source_detail(lock_schema, lock_name, owner_connection_id):
    return run_report_query(
        """
        SELECT
          locks.object_type,
          locks.object_schema,
          locks.object_name,
          locks.lock_type,
          locks.lock_duration,
          locks.lock_status,
          locks.source,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_state AS owner_state,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
          AND thread.processlist_id = %s
        ORDER BY locks.lock_status, locks.lock_type
        LIMIT 200
        """,
        [lock_schema, lock_name, owner_connection_id],
    )


def fetch_monitoring_metadata_impacted_detail(lock_schema, lock_name):
    return run_report_query(
        """
        SELECT
          locks.object_type,
          locks.object_schema,
          locks.object_name,
          locks.lock_type,
          locks.lock_duration,
          locks.lock_status,
          thread.processlist_id AS connection_id,
          thread.processlist_user AS user_name,
          thread.processlist_db AS database_name,
          thread.processlist_state AS state_name,
          thread.processlist_time AS elapsed_seconds,
          process.command AS command_name,
          LEFT(process.info, 500) AS current_sql
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        LEFT JOIN performance_schema.processlist AS process
          ON thread.processlist_id = process.id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
          AND locks.lock_status = 'PENDING'
        ORDER BY elapsed_seconds DESC, connection_id
        LIMIT 200
        """,
        [lock_schema, lock_name],
    )


def fetch_monitoring_innodb_storage_usage():
    return run_report_query(
        """
        SELECT
          table_schema,
          COUNT(*) AS table_count,
          SUM(data_length) AS data_bytes,
          SUM(index_length) AS index_bytes,
          SUM(data_length + index_length) AS total_bytes
        FROM information_schema.tables
        WHERE engine = 'InnoDB'
        GROUP BY table_schema
        ORDER BY total_bytes DESC
        LIMIT 100
        """
    )


def fetch_monitoring_temp_storage_usage():
    return run_report_query(
        """
        SELECT
          variable_name AS setting_name,
          variable_value AS setting_value
        FROM performance_schema.global_variables
        WHERE variable_name IN (
          'tmp_table_size',
          'max_heap_table_size',
          'temptable_max_ram',
          'innodb_temp_data_file_path'
        )
        ORDER BY variable_name
        """
    )


def fetch_monitoring_temp_table_usage():
    return run_dynamic_projection_report(
        "information_schema",
        "innodb_temp_table_info",
        [
            ("table_id", "table_id"),
            ("name", "name"),
            ("n_cols", "column_count"),
            ("space", "tablespace_id"),
            ("per_table_tablespace", "per_table_tablespace"),
            ("is_compressed", "is_compressed"),
        ],
        order_by=["table_id"],
        limit=100,
    )


def fetch_table_column_names(schema_name, table_name):
    rows = execute_query(
        """
        SELECT
          column_name AS column_name_value
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        [schema_name, table_name],
        database="information_schema",
    )
    return [row["column_name_value"] for row in rows]


def fetch_table_column_lookup(schema_name, table_name):
    return {
        column_name.lower(): column_name
        for column_name in fetch_table_column_names(schema_name, table_name)
    }


def run_dynamic_projection_report(schema_name, table_name, projections, *, order_by=None, limit=None):
    available_columns = fetch_table_column_names(schema_name, table_name)
    available_column_lookup = {column_name.lower(): column_name for column_name in available_columns}
    selected_columns = []
    output_columns = []

    for source_name, alias in projections:
        actual_source_name = available_column_lookup.get(str(source_name).lower())
        if not actual_source_name:
            continue
        safe_source = quote_identifier(actual_source_name)
        safe_alias = quote_identifier(alias)
        selected_columns.append(f"{safe_source} AS {safe_alias}")
        output_columns.append(alias)

    if not selected_columns:
        raise ValueError(f"No expected columns were found on {schema_name}.{table_name}.")

    sql = f"SELECT {', '.join(selected_columns)} FROM {quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    if order_by:
        order_clauses = []
        for column_name in order_by:
            if column_name in output_columns:
                order_clauses.append(quote_identifier(column_name))
        if order_clauses:
            sql += " ORDER BY " + ", ".join(order_clauses)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return run_report_query(sql)


def _join_non_empty_labels(values, empty_label="-"):
    labels = []
    seen = set()
    for value in values:
        label = str(value if value is not None else "").strip()
        if not label or label == "-":
            continue
        if label not in seen:
            labels.append(label)
            seen.add(label)
    return ", ".join(labels) if labels else empty_label


def _parse_mysql_datetime(value):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text or text.startswith("0000-00-00"):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _milliseconds_between_timestamps(start_value, end_value):
    start_dt = _parse_mysql_datetime(start_value)
    end_dt = _parse_mysql_datetime(end_value)
    if not start_dt or not end_dt:
        return None
    return max((end_dt - start_dt).total_seconds() * 1000.0, 0.0)


def fetch_monitoring_replication_connection_status():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_connection_status",
        [
            ("channel_name", "channel_name"),
            ("service_state", "service_state"),
            ("thread_id", "thread_id"),
            ("received_transaction_set", "received_transaction_set"),
            ("last_heartbeat_timestamp", "last_heartbeat_timestamp"),
            ("last_error_number", "last_error_number"),
            ("last_error_message", "last_error_message"),
        ],
        order_by=["channel_name"],
    )


def fetch_monitoring_replication_applier_coordinator():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_applier_status_by_coordinator",
        [
            ("channel_name", "channel_name"),
            ("thread_id", "thread_id"),
            ("service_state", "service_state"),
            ("last_processed_transaction", "last_processed_transaction"),
            ("last_processed_transaction_original_commit_timestamp", "last_processed_transaction_original_commit_timestamp"),
            ("last_processed_transaction_immediate_commit_timestamp", "last_processed_transaction_immediate_commit_timestamp"),
            ("last_processed_transaction_start_buffer_timestamp", "last_processed_transaction_start_buffer_timestamp"),
            ("last_processed_transaction_end_buffer_timestamp", "last_processed_transaction_end_buffer_timestamp"),
            ("last_processed_transaction_start_apply_timestamp", "last_processed_transaction_start_apply_timestamp"),
            ("last_processed_transaction_end_apply_timestamp", "last_processed_transaction_end_apply_timestamp"),
            ("last_error_number", "last_error_number"),
            ("last_error_message", "last_error_message"),
        ],
        order_by=["channel_name"],
    )


def fetch_monitoring_replication_applier_workers():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_applier_status_by_worker",
        [
            ("channel_name", "channel_name"),
            ("worker_id", "worker_id"),
            ("thread_id", "thread_id"),
            ("service_state", "service_state"),
            ("last_applied_transaction", "last_applied_transaction"),
            ("last_applied_transaction_original_commit_timestamp", "last_applied_transaction_original_commit_timestamp"),
            ("last_applied_transaction_immediate_commit_timestamp", "last_applied_transaction_immediate_commit_timestamp"),
            ("last_applied_transaction_start_apply_timestamp", "last_applied_transaction_start_apply_timestamp"),
            ("last_applied_transaction_end_apply_timestamp", "last_applied_transaction_end_apply_timestamp"),
            ("applying_transaction", "applying_transaction"),
            ("applying_transaction_original_commit_timestamp", "applying_transaction_original_commit_timestamp"),
            ("applying_transaction_immediate_commit_timestamp", "applying_transaction_immediate_commit_timestamp"),
            ("last_error_number", "last_error_number"),
            ("last_error_message", "last_error_message"),
        ],
        order_by=["channel_name", "worker_id"],
        limit=200,
    )


def fetch_group_replication_member_rows():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_group_members",
        [
            ("channel_name", "channel_name"),
            ("member_id", "member_id"),
            ("member_host", "member_host"),
            ("member_port", "member_port"),
            ("member_state", "member_state"),
            ("member_role", "member_role"),
            ("member_version", "member_version"),
            ("member_communication_stack", "member_communication_stack"),
        ],
        order_by=["member_host", "member_port"],
        limit=100,
    )


def fetch_group_replication_member_stats_rows():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_group_member_stats",
        [
            ("channel_name", "channel_name"),
            ("member_id", "member_id"),
            ("count_transactions_in_queue", "count_transactions_in_queue"),
            ("count_transactions_checked", "count_transactions_checked"),
            ("count_conflicts_detected", "count_conflicts_detected"),
            ("count_transactions_rows_validating", "count_transactions_rows_validating"),
            ("transactions_committed_all_members", "transactions_committed_all_members"),
            ("last_conflict_free_transaction", "last_conflict_free_transaction"),
        ],
        order_by=["member_id"],
        limit=100,
    )


def fetch_replication_overview_info():
    def fetch_replica_status_report():
        rows = fetch_replica_status_rows()
        columns = list(rows[0].keys()) if rows else []
        return {"columns": columns, "rows": rows}

    replica_status_report = _safe_report(fetch_replica_status_report)
    replication_connection = _safe_report(fetch_monitoring_replication_connection_status)
    replication_applier = _safe_report(fetch_monitoring_replication_applier_coordinator)
    replication_workers = _safe_report(fetch_monitoring_replication_applier_workers)
    group_members = _safe_report(fetch_group_replication_member_rows)
    group_member_stats = _safe_report(fetch_group_replication_member_stats_rows)

    replica_rows = replica_status_report.get("rows", [])
    io_running_values = []
    sql_running_values = []
    lag_values = []
    for row in replica_rows:
        io_running_values.append(row.get("Replica_IO_Running") or row.get("Slave_IO_Running") or "-")
        sql_running_values.append(row.get("Replica_SQL_Running") or row.get("Slave_SQL_Running") or "-")
        lag_values.append(row.get("Seconds_Behind_Source") if "Seconds_Behind_Source" in row else row.get("Seconds_Behind_Master", "-"))

    replication_connection_rows = replication_connection.get("rows", []) if not replication_connection.get("error") else []
    replication_applier_rows = replication_applier.get("rows", []) if not replication_applier.get("error") else []
    replication_worker_rows = replication_workers.get("rows", []) if not replication_workers.get("error") else []
    group_member_rows = group_members.get("rows", []) if not group_members.get("error") else []

    if not io_running_values:
        io_running_values = [
            row.get("service_state") or "-"
            for row in replication_connection_rows
        ]
    if not sql_running_values:
        sql_running_values = [
            row.get("service_state") or "-"
            for row in replication_applier_rows
        ] or [
            row.get("service_state") or "-"
            for row in replication_worker_rows
        ]
    if not io_running_values and not sql_running_values and group_member_rows:
        io_running_values = ["Group Replication"]
        sql_running_values = [
            row.get("member_state") or "-"
            for row in group_member_rows
        ]

    replica_channel_count = len(replica_rows) or len(replication_connection_rows)
    performance_schema_channel_count = len(replication_connection_rows) if not replication_connection.get("error") else "-"
    group_member_count = len(group_member_rows) if not group_members.get("error") else "-"

    return {
        "replica_status": replica_status_report,
        "replication_connection": replication_connection,
        "replication_applier": replication_applier,
        "replication_workers": replication_workers,
        "group_members": group_members,
        "group_member_stats": group_member_stats,
        "replica_channel_count": replica_channel_count,
        "replica_io_running_label": _join_non_empty_labels(io_running_values),
        "replica_sql_running_label": _join_non_empty_labels(sql_running_values),
        "replica_lag_label": _join_non_empty_labels(lag_values),
        "performance_schema_channel_count": performance_schema_channel_count,
        "group_member_count": group_member_count,
    }


def empty_replication_report():
    return {"columns": [], "rows": [], "error": ""}


def empty_replication_overview_info():
    return {
        "replica_status": empty_replication_report(),
        "replication_connection": empty_replication_report(),
        "replication_applier": empty_replication_report(),
        "replication_workers": empty_replication_report(),
        "group_members": empty_replication_report(),
        "group_member_stats": empty_replication_report(),
        "replica_channel_count": "-",
        "replica_io_running_label": "-",
        "replica_sql_running_label": "-",
        "replica_lag_label": "-",
        "performance_schema_channel_count": "-",
        "group_member_count": "-",
    }


def fetch_monitoring_storage_totals():
    rows = execute_query(
        """
        SELECT
          COALESCE(SUM(data_length), 0) AS data_bytes,
          COALESCE(SUM(index_length), 0) AS index_bytes,
          COUNT(*) AS table_count,
          COUNT(DISTINCT table_schema) AS schema_count
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
        """
    )
    return rows[0] if rows else {"data_bytes": 0, "index_bytes": 0, "table_count": 0, "schema_count": 0}


def fetch_monitoring_temp_tablespace_summary():
    column_lookup = fetch_table_column_lookup("information_schema", "files")
    allocated_size_column = _first_available_column(
        column_lookup,
        [
            "allocated_size",
            "file_size",
            "data_length",
            "max_data_length",
            "initial_size",
            "maximum_size",
        ],
    )
    total_extents_column = _first_available_column(column_lookup, ["total_extents"])
    free_extents_column = _first_available_column(column_lookup, ["free_extents"])
    extent_size_column = _first_available_column(column_lookup, ["extent_size"])
    tablespace_column = _first_available_column(column_lookup, ["tablespace_name"])
    file_name_column = _first_available_column(column_lookup, ["file_name"])

    size_expression = ""
    size_source = ""
    if allocated_size_column:
        size_expression = f"COALESCE({quote_identifier(allocated_size_column)}, 0)"
        size_source = f"information_schema.files.{allocated_size_column}"
    elif total_extents_column and free_extents_column and extent_size_column:
        size_expression = (
            "GREATEST("
            f"COALESCE({quote_identifier(total_extents_column)}, 0) - "
            f"COALESCE({quote_identifier(free_extents_column)}, 0), "
            "0"
            ") * "
            f"COALESCE({quote_identifier(extent_size_column)}, 0)"
        )
        size_source = (
            f"information_schema.files.({total_extents_column}-{free_extents_column})*{extent_size_column}"
        )
    elif total_extents_column and extent_size_column:
        size_expression = (
            f"COALESCE({quote_identifier(total_extents_column)}, 0) * "
            f"COALESCE({quote_identifier(extent_size_column)}, 0)"
        )
        size_source = f"information_schema.files.{total_extents_column}*{extent_size_column}"

    conditions = []
    if tablespace_column:
        safe_tablespace = quote_identifier(tablespace_column)
        conditions.append(
            "("
            f"LOWER({safe_tablespace}) IN ('innodb_temporary', 'innodb_temp') "
            f"OR LOWER({safe_tablespace}) LIKE 'innodb_temporary%%' "
            f"OR LOWER({safe_tablespace}) LIKE '%%ibtmp%%'"
            ")"
        )
    if file_name_column:
        safe_file_name = quote_identifier(file_name_column)
        conditions.append(
            "("
            f"LOWER({safe_file_name}) LIKE '%%ibtmp%%' "
            f"OR LOWER({safe_file_name}) LIKE '%%#innodb_temp%%'"
            ")"
        )

    if size_expression and conditions:
        rows = execute_query(
            """
            SELECT
              COALESCE(SUM({size_expression}), 0) AS temp_bytes
            FROM information_schema.files
            WHERE {conditions}
            """.format(size_expression=size_expression, conditions=" OR ".join(conditions)),
            database="information_schema",
        )
        temp_bytes = _extract_numeric(rows[0].get("temp_bytes"), None) if rows else None
        if temp_bytes is not None:
            return {"temp_bytes": temp_bytes, "source": size_source}

    temp_settings = _report_row_map(fetch_monitoring_temp_storage_usage(), "setting_name", "setting_value")
    estimated_bytes = _estimate_temp_tablespace_bytes_from_path(temp_settings.get("innodb_temp_data_file_path"))
    if estimated_bytes is not None:
        return {
            "temp_bytes": estimated_bytes,
            "source": "performance_schema.global_variables.innodb_temp_data_file_path",
            "estimated": True,
        }
    return {"temp_bytes": 0, "source": "unavailable", "estimated": True}


def fetch_show_binary_logs_summary():
    with mysql_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW BINARY LOGS")
            rows = cursor.fetchall()
    total_bytes = 0
    for row in rows:
        total_bytes += _extract_numeric(row.get("File_size") or row.get("file_size"), 0) or 0
    return {
        "file_count": len(rows),
        "total_bytes": total_bytes,
    }


def fetch_replica_status_rows():
    errors = []
    for sql in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
        try:
            with mysql_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    return cursor.fetchall()
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise ValueError(errors[0])
    return []


def fetch_replication_channel_lag_rows():
    channels = []
    try:
        replica_rows = fetch_replica_status_rows()
    except Exception:
        replica_rows = []
    for index, row in enumerate(replica_rows, start=1):
        channel_name = (
            row.get("Channel_Name")
            or row.get("Channel_name")
            or row.get("Connection_name")
            or row.get("Source_Host")
            or f"Channel {index}"
        )
        lag_seconds = _extract_numeric(row.get("Seconds_Behind_Source"), None)
        if lag_seconds is None:
            lag_seconds = _extract_numeric(row.get("Seconds_Behind_Master"), 0)
        relay_space = _extract_numeric(row.get("Relay_Log_Space"), 0) or 0
        channels.append(
            {
                "label": str(channel_name).strip() or f"Channel {index}",
                "lag_ms": (lag_seconds or 0) * 1000.0,
                "relay_log_bytes": relay_space,
            }
        )
    if channels:
        return channels

    fallback_channels = {}
    applier_report = _safe_report(fetch_monitoring_replication_applier_coordinator)
    worker_report = _safe_report(fetch_monitoring_replication_applier_workers)
    for row in applier_report.get("rows", []) if not applier_report.get("error") else []:
        channel_name = str(row.get("channel_name") or "default").strip() or "default"
        lag_ms = _milliseconds_between_timestamps(
            row.get("last_processed_transaction_original_commit_timestamp"),
            row.get("last_processed_transaction_end_apply_timestamp"),
        )
        fallback_channels[channel_name] = {
            "label": channel_name,
            "lag_ms": lag_ms or 0.0,
            "relay_log_bytes": 0,
        }
    for row in worker_report.get("rows", []) if not worker_report.get("error") else []:
        channel_name = str(row.get("channel_name") or "default").strip() or "default"
        lag_ms = _milliseconds_between_timestamps(
            row.get("last_applied_transaction_original_commit_timestamp"),
            row.get("last_applied_transaction_end_apply_timestamp"),
        )
        existing = fallback_channels.get(channel_name)
        if not existing or (lag_ms or 0.0) > existing["lag_ms"]:
            fallback_channels[channel_name] = {
                "label": channel_name,
                "lag_ms": lag_ms or 0.0,
                "relay_log_bytes": 0,
            }
    if fallback_channels:
        return sorted(fallback_channels.values(), key=lambda item: item["label"])
    return channels


def fetch_heatwave_load_distribution():
    def normalize_progress(value):
        numeric = _extract_numeric(value, None)
        if numeric is None:
            return None
        if 0.0 <= numeric <= 1.0:
            return numeric * 100.0
        return numeric

    column_lookup = fetch_table_column_lookup("performance_schema", "rpd_tables")
    progress_column = _first_available_column(
        column_lookup,
        [
            "load_progress",
            "load_percentage",
            "load_percent",
            "percent_loaded",
            "load_pct",
            "availability_percentage",
            "availability_percent",
        ],
    )
    if progress_column:
        rows = execute_query(
            f"SELECT {quote_identifier(progress_column)} AS progress_value FROM performance_schema.rpd_tables"
        )
        loaded = partial = not_loaded = 0
        for row in rows:
            progress_value = normalize_progress(row.get("progress_value"))
            if progress_value is None:
                not_loaded += 1
                continue
            if progress_value >= 99.999:
                loaded += 1
            elif progress_value > 0:
                partial += 1
            else:
                not_loaded += 1
        return {
            "loaded": loaded,
            "partial": partial,
            "not_loaded": not_loaded,
            "total_tables": loaded + partial + not_loaded,
            "source": progress_column,
        }

    status_column = _first_available_column(
        column_lookup,
        [
            "load_status",
            "status",
            "recovery_status",
            "availability_status",
        ],
    )
    if status_column:
        rows = execute_query(
            f"SELECT {quote_identifier(status_column)} AS status_value FROM performance_schema.rpd_tables"
        )
        loaded = partial = not_loaded = 0
        for row in rows:
            status_value = str(row.get("status_value") or "").strip().lower()
            numeric_status = normalize_progress(status_value)
            if numeric_status is not None:
                if numeric_status >= 99.999:
                    loaded += 1
                elif numeric_status > 0:
                    partial += 1
                else:
                    not_loaded += 1
                continue
            if any(token in status_value for token in ("not loaded", "unloaded", "pending", "init")):
                not_loaded += 1
            elif any(token in status_value for token in ("partial", "loading", "recover", "progress", "sync")):
                partial += 1
            elif any(token in status_value for token in ("loaded", "complete", "available", "active")):
                loaded += 1
            else:
                not_loaded += 1
        return {
            "loaded": loaded,
            "partial": partial,
            "not_loaded": not_loaded,
            "total_tables": loaded + partial + not_loaded,
            "source": status_column,
        }

    start_column = _first_available_column(column_lookup, ["load_start_timestamp"])
    end_column = _first_available_column(column_lookup, ["load_end_timestamp"])
    if start_column or end_column:
        selected_columns = []
        if start_column:
            selected_columns.append(f"{quote_identifier(start_column)} AS load_start_value")
        if end_column:
            selected_columns.append(f"{quote_identifier(end_column)} AS load_end_value")
        rows = execute_query(
            "SELECT {columns} FROM performance_schema.rpd_tables".format(columns=", ".join(selected_columns))
        )
        loaded = partial = not_loaded = 0
        for row in rows:
            if row.get("load_end_value") not in (None, ""):
                loaded += 1
            elif row.get("load_start_value") not in (None, ""):
                partial += 1
            else:
                not_loaded += 1
        return {
            "loaded": loaded,
            "partial": partial,
            "not_loaded": not_loaded,
            "total_tables": loaded + partial + not_loaded,
            "source": "load timestamps",
        }

    raise ValueError("Unable to determine HeatWave load state columns from performance_schema.rpd_tables.")


def fetch_heatwave_node_memory_rows():
    column_lookup = fetch_table_column_lookup("performance_schema", "rpd_nodes")
    node_id_column = _first_available_column(column_lookup, ["id", "node_id"])
    ip_column = _first_available_column(column_lookup, ["ip", "ip_address", "address", "host", "hostname", "host_name"])
    port_column = _first_available_column(column_lookup, ["port"])
    memory_usage_column = _first_available_column(
        column_lookup,
        [
            "memory_usage",
            "memory_used_bytes",
            "used_memory_bytes",
            "current_memory_bytes",
            "memory_bytes",
            "allocated_memory_bytes",
            "alloc_pool_memory_bytes",
            "memory",
        ],
    )
    memory_total_column = _first_available_column(
        column_lookup,
        [
            "memory_total",
            "total_memory_bytes",
            "memory_total_bytes",
            "configured_memory_bytes",
            "node_memory_bytes",
        ],
    )
    baserel_memory_usage_column = _first_available_column(
        column_lookup,
        [
            "baserel_memory_usage",
            "base_rel_memory_usage",
        ],
    )
    status_column = _first_available_column(column_lookup, ["status"])
    if not memory_usage_column:
        raise ValueError("Unable to determine MEMORY_USAGE from performance_schema.rpd_nodes.")

    selected_columns = []
    if node_id_column:
        selected_columns.append(f"{quote_identifier(node_id_column)} AS node_id_value")
    if ip_column:
        selected_columns.append(f"{quote_identifier(ip_column)} AS ip_value")
    if port_column:
        selected_columns.append(f"{quote_identifier(port_column)} AS port_value")
    selected_columns.append(f"{quote_identifier(memory_usage_column)} AS memory_usage_value")
    if memory_total_column:
        selected_columns.append(f"{quote_identifier(memory_total_column)} AS memory_total_value")
    if baserel_memory_usage_column:
        selected_columns.append(f"{quote_identifier(baserel_memory_usage_column)} AS baserel_memory_usage_value")
    if status_column:
        selected_columns.append(f"{quote_identifier(status_column)} AS status_value")

    order_clauses = []
    if node_id_column:
        order_clauses.append(quote_identifier(node_id_column))
    if ip_column:
        order_clauses.append(quote_identifier(ip_column))
    if port_column:
        order_clauses.append(quote_identifier(port_column))
    if not order_clauses:
        order_clauses.append(f"{quote_identifier(memory_usage_column)} DESC")

    rows = execute_query(
        """
        SELECT {columns}
        FROM performance_schema.rpd_nodes
        ORDER BY {order_by}
        LIMIT 200
        """.format(
            columns=", ".join(selected_columns),
            order_by=", ".join(order_clauses),
        )
    )
    normalized_rows = []
    for index, row in enumerate(rows, start=1):
        node_id_value = _coerce_int(row.get("node_id_value"), None)
        ip_value = str(row.get("ip_value") or "").strip()
        port_value = _coerce_int(row.get("port_value"), None)
        memory_usage_value = _extract_numeric(row.get("memory_usage_value"), 0) or 0
        memory_total_value = _extract_numeric(row.get("memory_total_value"), None)
        baserel_memory_usage_value = _extract_numeric(row.get("baserel_memory_usage_value"), None)
        status_value = str(row.get("status_value") or "").strip()

        node_label = f"Node {node_id_value}" if node_id_value is not None else f"Node {index}"
        if ip_value and port_value is not None:
            node_label = f"{node_label} ({ip_value}:{port_value})"
        elif ip_value:
            node_label = f"{node_label} ({ip_value})"

        normalized_rows.append(
            {
                "label": node_label,
                "memory_usage_bytes": memory_usage_value,
                "memory_total_bytes": memory_total_value,
                "baserel_memory_usage_bytes": baserel_memory_usage_value,
                "status": status_value,
            }
        )
    return normalized_rows


def fetch_heatwave_query_timing_summary():
    rows = execute_query(
        """
        SELECT
          QUERY_ID AS query_id_value,
          QUERY_TEXT AS query_text_value,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryStartTime", '$[0]')),
            '%Y-%m-%d %H:%i:%s.%f'
          ) AS query_start_value,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.qexecStartTime", '$[0]')),
            '%Y-%m-%d %H:%i:%s.%f'
          ) AS rpd_start_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.timeBetweenMakePushedJoinAndRpdExecMsec", '$[0]') AS queue_wait_ms_value,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryEndTime", '$[0]')),
            '%Y-%m-%d %H:%i:%s.%f'
          ) AS query_end_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.changePropagationSync.msec", '$[0]') AS change_propagation_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.waitTime", '$[0]') AS total_wait_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.executionTime", '$[0]') AS total_exec_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.optimizationTime", '$[0]') AS total_opt_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.rpdExec.msec", '$[0]') AS rpd_exec_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.getResults.msec", '$[0]') AS get_result_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.sessionId", '$[0]') AS connection_id_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.qkrnActualRows[*].actRows", '$[0]') AS act_rows_value
        FROM performance_schema.rpd_query_stats
        WHERE query_text NOT LIKE 'ML_%'
        ORDER BY query_id DESC
        LIMIT 60
        """
    )

    metric_values = {
        "queue_wait_ms": [],
        "total_wait_ms": [],
        "total_exec_ms": [],
        "total_opt_ms": [],
        "rpd_exec_ms": [],
        "get_result_ms": [],
        "change_propagation_ms": [],
    }

    for row in rows:
        for metric_name in metric_values:
            value = _extract_numeric(row.get(f"{metric_name}_value"), None)
            if value is not None:
                metric_values[metric_name].append(value)

    latest_row = rows[0] if rows else {}
    latest_query_text = " ".join(str(latest_row.get("query_text_value") or "").split())
    if len(latest_query_text) > 120:
        latest_query_text = latest_query_text[:117].rstrip() + "..."

    latest_query_start = latest_row.get("query_start_value")
    latest_query_end = latest_row.get("query_end_value")
    latest_elapsed_ms = None
    if latest_query_start and latest_query_end:
        try:
            latest_elapsed_ms = max((latest_query_end - latest_query_start).total_seconds() * 1000.0, 0.0)
        except TypeError:
            latest_elapsed_ms = None

    return {
        "sample_count": len(rows),
        "latest_query_id": latest_row.get("query_id_value", ""),
        "latest_connection_id": _extract_numeric(latest_row.get("connection_id_value"), None),
        "latest_query_text": latest_query_text,
        "latest_act_rows": _extract_numeric(latest_row.get("act_rows_value"), None),
        "latest_elapsed_ms": latest_elapsed_ms,
        "avg_queue_wait_ms": sum(metric_values["queue_wait_ms"]) / len(metric_values["queue_wait_ms"])
        if metric_values["queue_wait_ms"]
        else 0,
        "avg_total_wait_ms": sum(metric_values["total_wait_ms"]) / len(metric_values["total_wait_ms"])
        if metric_values["total_wait_ms"]
        else 0,
        "avg_total_exec_ms": sum(metric_values["total_exec_ms"]) / len(metric_values["total_exec_ms"])
        if metric_values["total_exec_ms"]
        else 0,
        "avg_total_opt_ms": sum(metric_values["total_opt_ms"]) / len(metric_values["total_opt_ms"])
        if metric_values["total_opt_ms"]
        else 0,
        "avg_rpd_exec_ms": sum(metric_values["rpd_exec_ms"]) / len(metric_values["rpd_exec_ms"])
        if metric_values["rpd_exec_ms"]
        else 0,
        "avg_get_result_ms": sum(metric_values["get_result_ms"]) / len(metric_values["get_result_ms"])
        if metric_values["get_result_ms"]
        else 0,
        "avg_change_propagation_ms": sum(metric_values["change_propagation_ms"])
        / len(metric_values["change_propagation_ms"])
        if metric_values["change_propagation_ms"]
        else 0,
        "max_queue_wait_ms": max(metric_values["queue_wait_ms"]) if metric_values["queue_wait_ms"] else 0,
        "max_total_wait_ms": max(metric_values["total_wait_ms"]) if metric_values["total_wait_ms"] else 0,
        "max_total_exec_ms": max(metric_values["total_exec_ms"]) if metric_values["total_exec_ms"] else 0,
        "max_total_opt_ms": max(metric_values["total_opt_ms"]) if metric_values["total_opt_ms"] else 0,
        "max_rpd_exec_ms": max(metric_values["rpd_exec_ms"]) if metric_values["rpd_exec_ms"] else 0,
        "max_get_result_ms": max(metric_values["get_result_ms"]) if metric_values["get_result_ms"] else 0,
        "max_change_propagation_ms": max(metric_values["change_propagation_ms"])
        if metric_values["change_propagation_ms"]
        else 0,
    }


def build_monitoring_connections_chart_card():
    title = "Connections"
    subtitle = "Active connections and currently running processes."
    try:
        status_map = _report_row_map(fetch_monitoring_global_status(), "metric_name", "metric_value")
        active_connections = _extract_numeric(status_map.get("Threads_connected"), 0) or 0
        running_processes = _extract_numeric(status_map.get("Threads_running"), 0) or 0
        return _chart_card(
            "connections",
            title,
            subtitle,
            "timeseries",
            unit="count",
            series=[
                {
                    "key": "active_connections",
                    "label": "Active Connections",
                    "color": "#a93a1a",
                    "value": active_connections,
                    "display": _format_count(active_connections),
                },
                {
                    "key": "running_processes",
                    "label": "Running Processes",
                    "color": "#1d4e89",
                    "value": running_processes,
                    "display": _format_count(running_processes),
                },
            ],
            details=[
                f"Threads_connected: {_format_count(active_connections)}",
                f"Threads_running: {_format_count(running_processes)}",
            ],
        )
    except Exception as error:
        return _chart_card("connections", title, subtitle, "timeseries", unit="count", error=str(error))


def build_monitoring_locks_chart_card():
    title = "Locks"
    subtitle = "Current row lock waits and pending metadata locks."
    try:
        row_lock_waits = fetch_monitoring_lock_waits()
        metadata_locks = fetch_monitoring_metadata_locks()
        row_wait_count = len(row_lock_waits.get("rows", []))
        pending_metadata_count = sum(
            1
            for row in metadata_locks.get("rows", [])
            if str(row.get("lock_status") or "").strip().upper() == "PENDING"
        )
        return _chart_card(
            "locks",
            title,
            subtitle,
            "timeseries",
            unit="count",
            series=[
                {
                    "key": "row_lock_waits",
                    "label": "Row Lock Waits",
                    "color": "#8f2d56",
                    "value": row_wait_count,
                    "display": _format_count(row_wait_count),
                },
                {
                    "key": "pending_metadata_locks",
                    "label": "Pending Metadata Locks",
                    "color": "#3d5a80",
                    "value": pending_metadata_count,
                    "display": _format_count(pending_metadata_count),
                },
            ],
            details=[
                f"Row lock wait rows: {_format_count(row_wait_count)}",
                f"Pending metadata locks: {_format_count(pending_metadata_count)}",
            ],
        )
    except Exception as error:
        return _chart_card("locks", title, subtitle, "timeseries", unit="count", error=str(error))


def build_monitoring_storage_chart_card():
    title = "DB Size and Index Size"
    subtitle = "Total data and index bytes across non-system schemas."
    try:
        totals = fetch_monitoring_storage_totals()
        data_bytes = _extract_numeric(totals.get("data_bytes"), 0) or 0
        index_bytes = _extract_numeric(totals.get("index_bytes"), 0) or 0
        table_count = _extract_numeric(totals.get("table_count"), 0) or 0
        schema_count = _extract_numeric(totals.get("schema_count"), 0) or 0
        return _chart_card(
            "storage",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "data_bytes",
                    "label": "Data Bytes",
                    "color": "#355070",
                    "value": data_bytes,
                    "display": _format_bytes(data_bytes),
                },
                {
                    "key": "index_bytes",
                    "label": "Index Bytes",
                    "color": "#bc6c25",
                    "value": index_bytes,
                    "display": _format_bytes(index_bytes),
                },
            ],
            details=[
                f"Tables counted: {_format_count(table_count)}",
                f"Schemas counted: {_format_count(schema_count)}",
                f"Total footprint: {_format_bytes(data_bytes + index_bytes)}",
            ],
        )
    except Exception as error:
        return _chart_card("storage", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_innodb_memory_chart_card():
    title = "InnoDB Memory Usage"
    subtitle = "Current and peak instrumented InnoDB memory usage."
    try:
        report = fetch_monitoring_innodb_memory_usage()
        current_bytes = _sum_report_column(report, "current_bytes") or 0
        high_bytes = _sum_report_column(report, "high_bytes") or 0
        top_consumer = report.get("rows", [{}])[0]
        top_consumer_name = top_consumer.get("event_name") or "-"
        return _chart_card(
            "innodb_memory",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "current_bytes",
                    "label": "Current Bytes",
                    "color": "#588157",
                    "value": current_bytes,
                    "display": _format_bytes(current_bytes),
                },
                {
                    "key": "high_bytes",
                    "label": "Peak Bytes",
                    "color": "#a68a64",
                    "value": high_bytes,
                    "display": _format_bytes(high_bytes),
                },
            ],
            details=[
                f"Top consumer: {top_consumer_name}",
                f"Instrument rows: {_format_count(len(report.get('rows', [])))}",
            ],
        )
    except Exception as error:
        return _chart_card("innodb_memory", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_temp_space_chart_card():
    title = "Temp Table Space Usage"
    subtitle = "InnoDB temp tablespace bytes against the configured temp RAM ceiling."
    try:
        temp_summary = fetch_monitoring_temp_tablespace_summary()
        temp_settings = _report_row_map(fetch_monitoring_temp_storage_usage(), "setting_name", "setting_value")
        temp_table_report = _safe_report(fetch_monitoring_temp_table_usage)
        temp_bytes = _extract_numeric(temp_summary.get("temp_bytes"), 0) or 0
        configured_max_ram = _extract_numeric(temp_settings.get("temptable_max_ram"), 0) or 0
        temp_table_count = len(temp_table_report.get("rows", [])) if not temp_table_report.get("error") else 0
        details = [
            f"Active temp tables: {_format_count(temp_table_count)}",
            f"innodb_temp_data_file_path: {temp_settings.get('innodb_temp_data_file_path') or '-'}",
        ]
        if temp_summary.get("source"):
            source_label = temp_summary["source"]
            if temp_summary.get("estimated"):
                source_label += " (estimated)"
            details.append(f"Temp space source: {source_label}")
        return _chart_card(
            "temp_space",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "temp_space_bytes",
                    "label": "Temp Tablespace Bytes",
                    "color": "#2a9d8f",
                    "value": temp_bytes,
                    "display": _format_bytes(temp_bytes),
                },
                {
                    "key": "temptable_max_ram",
                    "label": "Temp Max RAM",
                    "color": "#264653",
                    "value": configured_max_ram,
                    "display": _format_bytes(configured_max_ram),
                },
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("temp_space", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_binlog_relay_chart_card():
    title = "Binlog and Relay Log Usage"
    subtitle = "Current binary log footprint and relay log space from replica channels."
    try:
        binlog_summary = fetch_show_binary_logs_summary()
        replica_status_error = ""
        try:
            replica_rows = fetch_replica_status_rows()
        except Exception as error:
            replica_rows = []
            replica_status_error = str(error)
        fallback_channels = fetch_replication_channel_lag_rows() if not replica_rows else []
        binlog_bytes = _extract_numeric(binlog_summary.get("total_bytes"), 0) or 0
        relay_bytes = sum(_extract_numeric(row.get("Relay_Log_Space"), 0) or 0 for row in replica_rows)
        channel_count = len(replica_rows) or len(fallback_channels)
        details = [
            f"Binary log files: {_format_count(binlog_summary.get('file_count', 0))}",
            f"Replica channels: {_format_count(channel_count)}",
        ]
        if replica_status_error and fallback_channels:
            details.append("Relay log bytes unavailable from SHOW REPLICA STATUS; channel count uses Performance Schema.")
        return _chart_card(
            "binlog_relay",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "binlog_bytes",
                    "label": "Binlog Bytes",
                    "color": "#6d597a",
                    "value": binlog_bytes,
                    "display": _format_bytes(binlog_bytes),
                },
                {
                    "key": "relay_log_bytes",
                    "label": "Relay Log Bytes",
                    "color": "#e76f51",
                    "value": relay_bytes,
                    "display": _format_bytes(relay_bytes),
                },
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("binlog_relay", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_replication_latency_chart_card():
    title = "Replication Channel Latency"
    subtitle = "Current lag per replica channel."
    try:
        channels = fetch_replication_channel_lag_rows()
        bars = [
            {
                "label": row["label"],
                "value": row["lag_ms"],
                "display": _format_milliseconds(row["lag_ms"]),
                "color": "#457b9d",
            }
            for row in channels[:12]
        ]
        details = []
        if channels:
            max_lag_ms = max(row["lag_ms"] for row in channels)
            details.append(f"Max lag: {_format_milliseconds(max_lag_ms)}")
            details.append(f"Channels: {_format_count(len(channels))}")
        else:
            details.append("No replica channels were returned.")
        return _chart_card(
            "replication_latency",
            title,
            subtitle,
            "bars",
            unit="ms",
            bars=bars,
            details=details,
        )
    except Exception as error:
        return _chart_card("replication_latency", title, subtitle, "bars", unit="ms", error=str(error))


def build_heatwave_load_state_chart_card():
    title = "HeatWave Load State"
    subtitle = "Loaded, partial, and not-loaded HeatWave tables."
    try:
        distribution = fetch_heatwave_load_distribution()
        return _chart_card(
            "heatwave_load_state",
            title,
            subtitle,
            "bars",
            unit="count",
            bars=[
                {
                    "label": "Loaded (100%)",
                    "value": distribution["loaded"],
                    "display": _format_count(distribution["loaded"]),
                    "color": "#2a9d8f",
                },
                {
                    "label": "Partial (>0 <100%)",
                    "value": distribution["partial"],
                    "display": _format_count(distribution["partial"]),
                    "color": "#f4a261",
                },
                {
                    "label": "Not Loaded (0%)",
                    "value": distribution["not_loaded"],
                    "display": _format_count(distribution["not_loaded"]),
                    "color": "#e76f51",
                },
            ],
            details=[
                f"Tracked tables: {_format_count(distribution['total_tables'])}",
                f"Source field: {distribution['source']}",
            ],
        )
    except Exception as error:
        return _chart_card("heatwave_load_state", title, subtitle, "bars", unit="count", error=str(error))


def build_heatwave_node_memory_chart_card():
    title = "HeatWave Node Memory"
    subtitle = "Current MEMORY_USAGE by HeatWave node from performance_schema.rpd_nodes."
    try:
        rows = fetch_heatwave_node_memory_rows()
        total_used_bytes = sum(row["memory_usage_bytes"] for row in rows)
        total_capacity_bytes = sum((row["memory_total_bytes"] or 0) for row in rows)
        total_baserel_bytes = sum((row["baserel_memory_usage_bytes"] or 0) for row in rows)
        unavailable_nodes = [
            row for row in rows if row["status"] and not str(row["status"]).strip().upper().startswith("AVAIL_")
        ]
        highest_node = max(rows, key=lambda item: item["memory_usage_bytes"], default=None)

        details = [
            f"Nodes returned: {_format_count(len(rows))}",
            "Source: performance_schema.rpd_nodes",
        ]
        if total_capacity_bytes > 0:
            cluster_usage_pct = (total_used_bytes / total_capacity_bytes) * 100.0
            details.append(
                f"Cluster memory usage: {_format_bytes(total_used_bytes)} of {_format_bytes(total_capacity_bytes)} ({cluster_usage_pct:.1f}%)"
            )
        else:
            details.append(f"Cluster memory usage: {_format_bytes(total_used_bytes)}")
        if total_baserel_bytes > 0:
            details.append(f"Base relation memory usage: {_format_bytes(total_baserel_bytes)}")
        if highest_node:
            details.append(
                f"Top node: {highest_node['label']} at {_format_bytes(highest_node['memory_usage_bytes'])}"
            )
        if unavailable_nodes:
            details.append(f"Nodes with non-AVAIL status: {_format_count(len(unavailable_nodes))}")
        else:
            details.append("All node statuses are AVAIL_.")

        return _chart_card(
            "heatwave_node_memory",
            title,
            subtitle,
            "bars",
            unit="bytes",
            bars=[
                {
                    "label": row["label"],
                    "value": row["memory_usage_bytes"],
                    "display": (
                        f"{_format_bytes(row['memory_usage_bytes'])} of {_format_bytes(row['memory_total_bytes'])} "
                        f"({(row['memory_usage_bytes'] / row['memory_total_bytes']) * 100.0:.1f}%)"
                        if row["memory_total_bytes"]
                        else _format_bytes(row["memory_usage_bytes"])
                    ),
                    "color": "#2a9d8f"
                    if str(row["status"]).strip().upper().startswith("AVAIL_")
                    else "#e76f51",
                }
                for row in rows
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("heatwave_node_memory", title, subtitle, "bars", unit="bytes", error=str(error))


def build_heatwave_query_timing_chart_card():
    title = "HeatWave Query Timing"
    subtitle = "Recent queue, execution, wait, and RPD timings from performance_schema.rpd_query_stats."
    try:
        summary = fetch_heatwave_query_timing_summary()
        details = [
            f"Recent samples: {_format_count(summary['sample_count'])}",
            "Source: performance_schema.rpd_query_stats",
        ]
        if summary["latest_query_id"] not in (None, ""):
            details.append(f"Latest query id: {summary['latest_query_id']}")
        if summary["latest_connection_id"] is not None:
            details.append(f"Latest connection id: {_format_count(summary['latest_connection_id'])}")
        if summary["latest_query_text"]:
            details.append(f"Latest query: {summary['latest_query_text']}")
        if summary["latest_act_rows"] is not None:
            details.append(f"Latest actual rows: {_format_count(summary['latest_act_rows'])}")
        if summary["latest_elapsed_ms"] is not None:
            details.append(f"Latest end-to-end: {_format_milliseconds(summary['latest_elapsed_ms'])}")
        details.append(f"Avg change propagation: {_format_milliseconds(summary['avg_change_propagation_ms'])}")
        details.append(f"Avg get results: {_format_milliseconds(summary['avg_get_result_ms'])}")
        details.append(f"Peak queue wait: {_format_milliseconds(summary['max_queue_wait_ms'])}")
        details.append(f"Peak total execution: {_format_milliseconds(summary['max_total_exec_ms'])}")
        details.append(f"Peak total wait: {_format_milliseconds(summary['max_total_wait_ms'])}")
        details.append(f"Peak RPD execution: {_format_milliseconds(summary['max_rpd_exec_ms'])}")
        details.append(f"Peak optimization: {_format_milliseconds(summary['max_total_opt_ms'])}")
        details.append(f"Peak get results: {_format_milliseconds(summary['max_get_result_ms'])}")
        details.append(f"Peak change propagation: {_format_milliseconds(summary['max_change_propagation_ms'])}")
        return _chart_card(
            "heatwave_query_timing",
            title,
            subtitle,
            "timeseries",
            unit="ms",
            series=[
                {
                    "key": "avg_queue_wait_ms",
                    "label": "Avg Queue Wait",
                    "color": "#6d597a",
                    "value": summary["avg_queue_wait_ms"],
                    "display": _format_milliseconds(summary["avg_queue_wait_ms"]),
                },
                {
                    "key": "avg_total_exec_ms",
                    "label": "Avg Total Exec",
                    "color": "#b56576",
                    "value": summary["avg_total_exec_ms"],
                    "display": _format_milliseconds(summary["avg_total_exec_ms"]),
                },
                {
                    "key": "avg_total_wait_ms",
                    "label": "Avg Total Wait",
                    "color": "#355070",
                    "value": summary["avg_total_wait_ms"],
                    "display": _format_milliseconds(summary["avg_total_wait_ms"]),
                },
                {
                    "key": "avg_total_opt_ms",
                    "label": "Avg Optimization",
                    "color": "#a68a64",
                    "value": summary["avg_total_opt_ms"],
                    "display": _format_milliseconds(summary["avg_total_opt_ms"]),
                },
                {
                    "key": "avg_rpd_exec_ms",
                    "label": "Avg RPD Exec",
                    "color": "#2a9d8f",
                    "value": summary["avg_rpd_exec_ms"],
                    "display": _format_milliseconds(summary["avg_rpd_exec_ms"]),
                },
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("heatwave_query_timing", title, subtitle, "timeseries", unit="ms", error=str(error))


def build_monitoring_chart_snapshot():
    tab_key_by_id = {
        "connections": "general",
        "locks": "general",
        "storage": "general",
        "innodb_memory": "general",
        "temp_space": "general",
        "binlog_relay": "replication",
        "replication_latency": "replication",
        "heatwave_load_state": "heatwave",
        "heatwave_node_memory": "heatwave",
        "heatwave_query_timing": "heatwave",
    }
    cards = [
        build_monitoring_connections_chart_card(),
        build_monitoring_locks_chart_card(),
        build_monitoring_storage_chart_card(),
        build_monitoring_innodb_memory_chart_card(),
        build_monitoring_temp_space_chart_card(),
        build_monitoring_binlog_relay_chart_card(),
        build_monitoring_replication_latency_chart_card(),
        build_heatwave_load_state_chart_card(),
        build_heatwave_node_memory_chart_card(),
        build_heatwave_query_timing_chart_card(),
    ]
    for card in cards:
        card["tab_key"] = tab_key_by_id.get(card.get("id"), "general")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cards": cards,
    }


def build_monitoring_dashboard_context():
    global_status = _safe_report(fetch_monitoring_global_status)
    user_processes = _safe_report(fetch_monitoring_user_processlist)
    current_connections = _safe_report(fetch_monitoring_current_connections)
    innodb_memory = _safe_report(fetch_monitoring_innodb_memory_usage)
    innodb_storage = _safe_report(fetch_monitoring_innodb_storage_usage)
    temp_storage = _safe_report(fetch_monitoring_temp_storage_usage)
    temp_tables = _safe_report(fetch_monitoring_temp_table_usage)
    replication_connection = _safe_report(fetch_monitoring_replication_connection_status)
    replication_applier = _safe_report(fetch_monitoring_replication_applier_coordinator)
    replication_workers = _safe_report(fetch_monitoring_replication_applier_workers)

    global_status_map = {
        row["metric_name"]: row["metric_value"]
        for row in global_status.get("rows", [])
        if row.get("metric_name") is not None
    }
    metrics = [
        {
            "label": "User Processes",
            "value": len(user_processes.get("rows", [])) if not user_processes.get("error") else "-",
            "subtitle": "Top 100 non-system processlist rows",
        },
        {
            "label": "Current Connections",
            "value": global_status_map.get("Threads_connected", "-"),
            "subtitle": f"Threads running: {global_status_map.get('Threads_running', '-')}",
        },
        {
            "label": "InnoDB Memory",
            "value": _format_bytes(_sum_report_column(innodb_memory, "current_bytes")),
            "subtitle": "Total current bytes from memory/innodb instruments",
        },
        {
            "label": "Temp Disk Tables",
            "value": global_status_map.get("Created_tmp_disk_tables", "-"),
            "subtitle": f"Created tmp tables: {global_status_map.get('Created_tmp_tables', '-')}",
        },
        {
            "label": "InnoDB Storage",
            "value": _format_bytes(_sum_report_column(innodb_storage, "total_bytes")),
            "subtitle": "Summed across InnoDB schemas",
        },
        {
            "label": "Replica Channels",
            "value": len(replication_connection.get("rows", [])) if not replication_connection.get("error") else "-",
            "subtitle": "performance_schema replication_connection_status",
        },
    ]

    return {
        "metrics": metrics,
        "global_status": global_status,
        "user_processes": user_processes,
        "current_connections": current_connections,
        "innodb_memory": innodb_memory,
        "innodb_storage": innodb_storage,
        "temp_storage": temp_storage,
        "temp_tables": temp_tables,
        "replication_connection": replication_connection,
        "replication_applier": replication_applier,
        "replication_workers": replication_workers,
    }


def build_monitoring_locks_context():
    row_lock_schema = str(request.args.get("row_lock_schema", "")).strip()
    row_lock_table = str(request.args.get("row_lock_table", "")).strip()
    row_blocking_connection_id = _coerce_int(request.args.get("row_blocking_connection_id", ""))
    row_waiting_connection_id = _coerce_int(request.args.get("row_waiting_connection_id", ""))
    mdl_schema = str(request.args.get("mdl_schema", "")).strip()
    mdl_name = str(request.args.get("mdl_name", "")).strip()
    mdl_owner_connection_id = _coerce_int(request.args.get("mdl_owner_connection_id", ""))
    lock_focus = str(request.args.get("lock_focus", "row")).strip().lower()
    if lock_focus not in {"row", "meta"}:
        lock_focus = "row"

    row_locks = _safe_report(fetch_monitoring_lock_waits)
    metadata_locks = _safe_report(fetch_monitoring_metadata_locks)
    row_lock_source = _empty_report()
    row_lock_source_process = _empty_report()
    row_lock_impacted = _empty_report()
    row_lock_impacted_process = _empty_report()
    metadata_lock_source = _empty_report()
    metadata_lock_source_process = _empty_report()
    metadata_lock_impacted = _empty_report()

    if row_lock_schema and row_lock_table and row_blocking_connection_id is not None:
        row_lock_source = _safe_report(
            fetch_monitoring_row_lock_source_detail,
            row_lock_schema,
            row_lock_table,
            row_blocking_connection_id,
        )
        row_lock_source_process = _safe_report(fetch_monitoring_process_connection_detail, row_blocking_connection_id)

    if row_lock_schema and row_lock_table and row_waiting_connection_id is not None:
        row_lock_impacted = _safe_report(
            fetch_monitoring_row_lock_impacted_detail,
            row_lock_schema,
            row_lock_table,
            row_waiting_connection_id,
        )
        row_lock_impacted_process = _safe_report(fetch_monitoring_process_connection_detail, row_waiting_connection_id)

    if mdl_schema and mdl_name and mdl_owner_connection_id is not None:
        metadata_lock_source = _safe_report(
            fetch_monitoring_metadata_source_detail,
            mdl_schema,
            mdl_name,
            mdl_owner_connection_id,
        )
        metadata_lock_source_process = _safe_report(fetch_monitoring_process_connection_detail, mdl_owner_connection_id)

    if mdl_schema and mdl_name:
        metadata_lock_impacted = _safe_report(fetch_monitoring_metadata_impacted_detail, mdl_schema, mdl_name)

    return {
        "lock_focus": lock_focus,
        "row_locks": row_locks,
        "metadata_locks": metadata_locks,
        "row_lock_source": row_lock_source,
        "row_lock_source_process": row_lock_source_process,
        "row_lock_impacted": row_lock_impacted,
        "row_lock_impacted_process": row_lock_impacted_process,
        "metadata_lock_source": metadata_lock_source,
        "metadata_lock_source_process": metadata_lock_source_process,
        "metadata_lock_impacted": metadata_lock_impacted,
        "selected_row_lock_schema": row_lock_schema,
        "selected_row_lock_table": row_lock_table,
        "selected_row_blocking_connection_id": row_blocking_connection_id,
        "selected_row_waiting_connection_id": row_waiting_connection_id,
        "selected_mdl_schema": mdl_schema,
        "selected_mdl_name": mdl_name,
        "selected_mdl_owner_connection_id": mdl_owner_connection_id,
    }


def build_csv_response(filename, columns, rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in rows:
        if isinstance(row, dict):
            writer.writerow([row.get(column, "") for column in columns])
        else:
            writer.writerow(list(row))
    return Response(
        stream.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _normalize_checkbox(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_error_log_priorities(values):
    raw_values = [str(value or "").strip() for value in values or []]
    if any(value.lower() == "all" for value in raw_values):
        return []
    allowed_lookup = {
        str(option).strip().lower(): str(option)
        for option in ERROR_LOG_PRIORITY_OPTIONS
    }
    normalized = []
    seen = set()
    for value in raw_values:
        normalized_value = allowed_lookup.get(str(value or "").strip().lower())
        if not normalized_value or normalized_value in seen:
            continue
        normalized.append(normalized_value)
        seen.add(normalized_value)
    return normalized


def normalize_error_log_code(value):
    return str(value or "").strip()


def parse_error_log_code_filter(value):
    text = normalize_error_log_code(value)
    if not text:
        return {"operator": "IN", "codes": []}
    operator = "IN"
    match = re.match(r"(?is)^\s*(not\s+in|in)\s*\((.*)\)\s*$", text)
    if match:
        operator = "NOT IN" if re.sub(r"\s+", " ", match.group(1).strip().upper()) == "NOT IN" else "IN"
        text = match.group(2)
    raw_items = _split_error_log_code_items(text)
    codes = []
    seen = set()
    for item in raw_items:
        code = _unquote_error_log_code_item(item)
        if not code or code in seen:
            continue
        codes.append(code)
        seen.add(code)
    return {"operator": operator, "codes": codes}


def _split_error_log_code_items(value):
    items = []
    current = []
    quote_char = ""
    escaped = False
    for char in str(value or ""):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote_char:
            current.append(char)
            escaped = True
            continue
        if quote_char:
            current.append(char)
            if char == quote_char:
                quote_char = ""
            continue
        if char in {"'", '"'}:
            quote_char = char
            current.append(char)
            continue
        if char == ",":
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    items.append("".join(current).strip())
    return [item for item in items if item]


def _unquote_error_log_code_item(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.replace("\\'", "'").replace('\\"', '"').strip()


def normalize_error_log_message_like(value):
    return str(value or "").strip()


def change_local_admin_profile_password(new_password):
    profile = get_session_profile()
    credentials = get_session_credentials()
    if profile.get("name") != LOCAL_ADMIN_PROFILE_NAME:
        raise ValueError(f"Password changes here are only available for `{LOCAL_ADMIN_PROFILE_NAME}`.")
    username = str(profile.get("username") or credentials.get("username") or "").strip()
    if not username:
        raise ValueError("The local admin profile does not have a MySQL username.")
    if credentials.get("username") and credentials["username"] != username:
        raise ValueError(f"Log in as `{username}` before changing the local admin profile password.")
    normalized_password = str(new_password or "")
    if not normalized_password:
        raise ValueError("Enter a new password for the local admin profile.")

    user_literal = quote_sql_string(username)
    password_literal = quote_sql_string(normalized_password)
    account_hosts = ("localhost",)
    with mysql_connection(database_override="mysql") as connection:
        with connection.cursor() as cursor:
            for host in account_hosts:
                cursor.execute(
                    f"CREATE USER IF NOT EXISTS {user_literal}@{quote_sql_string(host)} IDENTIFIED BY {password_literal}"
                )
                cursor.execute(
                    f"ALTER USER {user_literal}@{quote_sql_string(host)} IDENTIFIED BY {password_literal}"
                )
            cursor.execute("FLUSH PRIVILEGES")
    set_session_credentials(username, normalized_password)


def render_dashboard(template_name, **context):
    profile = get_session_profile()
    overview = context.pop("server_overview", None)
    if session.get("logged_in") and overview is None:
        try:
            overview = fetch_server_overview()
        except Exception:
            overview = None
    return render_template(
        template_name,
        app_title=APP_TITLE,
        logged_in=bool(session.get("logged_in")),
        current_user=get_session_username(),
        current_profile_name=session.get("profile_name", ""),
        connection_summary=f"{profile['host'] or '-'}:{profile['port']}" if profile else "-",
        nav_groups=nav_groups_for_current_session(),
        current_endpoint=request.endpoint or "",
        session_profile=profile,
        can_use_auto_update=can_access_update_page(),
        setup_status=fetch_setup_status(),
        server_overview=overview,
        app_version=get_local_app_version(),
        version_check=session.get(DBCONSOLE_VERSION_CHECK_SESSION_KEY, {}),
        **context,
    )


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        picked_profile = get_profile_by_name(request.form.get("profile_picker", ""))
        if picked_profile:
            profile_payload = dict(picked_profile)
        else:
            profile_payload = normalize_profile(DEFAULT_PROFILE)
        profile = normalize_profile(profile_payload)
        username = str(request.form.get("username", "")).strip()
        if username:
            profile["username"] = username
        password = request.form.get("password", "")
        if not profile.get("socket_enabled") and not profile["host"]:
            flash("Choose a saved profile.", "error")
        elif not username:
            flash("MySQL username is required.", "error")
        else:
            try:
                clear_login_state(keep_profile=False)
                session["connection_profile"] = profile
                session["profile_name"] = profile["name"]
                set_session_credentials(username, password)
                with mysql_connection(connect_timeout=5):
                    pass
                session["logged_in"] = True
                flash("Connected to MySQL.", "success")
                if local_admin_password_change_required():
                    return redirect(url_for("local_admin_password_page"))
                version_check = refresh_repo_version_check()
                if version_check.get("update_available"):
                    if is_local_admin_profile_session():
                        flash(
                            f"DBConsole update available: {version_check.get('local_version')} -> {version_check.get('repo_version')}.",
                            "success",
                        )
                    elif local_admin_profile_needs_bootstrap():
                        flash(
                            "DBConsole update available. Use Auto-Update to complete first-time local-admin-profile bootstrap with a temporary localadmin password.",
                            "success",
                        )
                    else:
                        flash(
                            "DBConsole update available. Sign in with local-admin-profile to run Auto-Update.",
                            "success",
                        )
                elif version_check.get("error"):
                    if is_local_admin_profile_session():
                        flash(
                            "Repository version check could not complete. Review the Auto-Update page for details.",
                            "error",
                        )
                    else:
                        flash("Repository version check could not complete.", "error")
                if should_show_update_page_after_login(version_check):
                    return redirect(url_for("update_dbconsole_page"))
                return redirect(url_for("mysql_dashboard_page"))
            except Exception as error:
                clear_login_state(keep_profile=True)
                flash(f"Unable to connect: {error}", "error")

    selected_name = str(request.args.get("profile", "")).strip()
    selected_profile = get_profile_by_name(selected_name) or get_session_profile()
    visible_profiles = public_profiles(load_profiles())
    return render_template(
        "login.html",
        app_title=APP_TITLE,
        page_title="Login",
        logged_in=False,
        profiles=visible_profiles,
        selected_profile=public_profile(selected_profile),
        selected_profile_name=selected_name or selected_profile.get("name", ""),
    )


@app.route("/logout", methods=["POST"])
def logout():
    clear_login_state(keep_profile=False)
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/admin/local-admin-password", methods=["GET", "POST"])
@session_login_required
def local_admin_password_page():
    if not is_local_admin_profile_session():
        abort(403)
    if request.method == "POST":
        new_password = request.form.get("new_local_admin_password", "")
        confirm_password = request.form.get("confirm_local_admin_password", "")
        if new_password != confirm_password:
            flash("Local admin profile password confirmation does not match.", "error")
        else:
            try:
                change_local_admin_profile_password(new_password)
                clear_local_admin_password_change_required()
                clear_login_state(keep_profile=False)
                flash("Password changed. Sign in again.", "success")
                return redirect(url_for("login"))
            except Exception as error:
                flash(str(error), "error")
    return render_template(
        "local_admin_password.html",
        app_title=APP_TITLE,
        page_title="Change Local Admin Password",
        logged_in=False,
        local_admin_profile_name=LOCAL_ADMIN_PROFILE_NAME,
    )


@app.route("/admin/profile", methods=["GET", "POST"])
@session_login_required
def profile_page():
    if not is_local_admin_profile_session():
        abort(403)
    profiles = load_profiles()
    selected_name = str(request.values.get("selected_profile", "")).strip()
    editing_profile = get_profile_by_name(selected_name) or get_session_profile()

    if request.method == "POST":
        action = str(request.form.get("profile_action", "")).strip()
        profile_payload = normalize_profile(request.form)
        profile_name = profile_payload["name"]
        if action == "change_local_admin_password":
            new_password = request.form.get("new_local_admin_password", "")
            confirm_password = request.form.get("confirm_local_admin_password", "")
            if new_password != confirm_password:
                flash("Local admin profile password confirmation does not match.", "error")
            else:
                try:
                    change_local_admin_profile_password(new_password)
                    clear_local_admin_password_change_required()
                    clear_login_state(keep_profile=False)
                    flash("Password changed. Sign in again.", "success")
                    return redirect(url_for("login"))
                except Exception as error:
                    flash(str(error), "error")
        elif action == "save":
            if not profile_name:
                flash("Profile name is required.", "error")
            elif not profile_payload["socket_enabled"] and not profile_payload["host"]:
                flash("Profile host is required unless Unix socket is enabled.", "error")
            elif profile_payload["socket_enabled"] and not profile_payload["socket_path"]:
                flash("Unix socket path is required when Unix socket is enabled.", "error")
            elif profile_payload["socket_enabled"] and profile_payload["ssh_enabled"]:
                flash("Unix socket profiles cannot use SSH tunneling.", "error")
            else:
                existing_profile = get_profile_by_name(profile_name)
                if existing_profile and not request.files.get("ssh_key_file"):
                    profile_payload["ssh_key_path"] = existing_profile.get("ssh_key_path", "")
                try:
                    uploaded_ssh_key_path = save_uploaded_profile_ssh_key(profile_name, request.files.get("ssh_key_file"))
                except Exception as error:
                    flash(str(error), "error")
                    editing_profile = profile_payload
                    profiles = load_profiles()
                    return render_dashboard(
                        "profile.html",
                        page_title="Profile",
                        profiles=profiles,
                        selected_profile_name=selected_name,
                        editing_profile=public_profile(editing_profile),
                        local_admin_profile_name=LOCAL_ADMIN_PROFILE_NAME,
                        can_change_local_admin_password=is_local_admin_profile_session(),
                    )
                if uploaded_ssh_key_path:
                    profile_payload["ssh_key_path"] = uploaded_ssh_key_path
                if profile_payload["ssh_enabled"] and (
                    not profile_payload["ssh_host"] or not profile_payload["ssh_user"] or not profile_payload["ssh_key_path"]
                ):
                    flash("SSH profiles require jump host, SSH user, and an uploaded private key.", "error")
                    editing_profile = profile_payload
                    profiles = load_profiles()
                    return render_dashboard(
                        "profile.html",
                        page_title="Profile",
                        profiles=profiles,
                        selected_profile_name=selected_name,
                        editing_profile=public_profile(editing_profile),
                        local_admin_profile_name=LOCAL_ADMIN_PROFILE_NAME,
                        can_change_local_admin_password=is_local_admin_profile_session(),
                    )
                remaining = [row for row in profiles if row["name"].lower() != profile_name.lower()]
                remaining.append(profile_payload)
                save_profiles(remaining)
                if get_session_profile()["name"].lower() == profile_name.lower():
                    set_session_profile(profile_payload)
                flash(f"Profile `{profile_name}` saved.", "success")
                return redirect(url_for("profile_page", selected_profile=profile_name))
        elif action == "delete":
            if not profile_name:
                flash("Choose a profile to delete.", "error")
            else:
                remaining = [row for row in profiles if row["name"].lower() != profile_name.lower()]
                if len(remaining) == len(profiles):
                    flash("Profile not found.", "error")
                else:
                    save_profiles(remaining)
                    if get_session_profile()["name"].lower() == profile_name.lower():
                        session["connection_profile"] = normalize_profile(DEFAULT_PROFILE)
                        session["profile_name"] = ""
                    flash(f"Profile `{profile_name}` deleted.", "success")
                    return redirect(url_for("profile_page"))
        editing_profile = profile_payload
        profiles = load_profiles()

    return render_dashboard(
        "profile.html",
        page_title="Profile",
        profiles=profiles,
        selected_profile_name=selected_name,
        editing_profile=public_profile(editing_profile),
        local_admin_profile_name=LOCAL_ADMIN_PROFILE_NAME,
        can_change_local_admin_password=is_local_admin_profile_session(),
    )


@app.route("/admin/setup-object-storage", methods=["GET", "POST"])
@login_required
def setup_object_storage_page():
    config = load_object_storage_config()
    if request.method == "POST":
        config = normalize_object_storage(request.form)
        save_object_storage_config(config)
        flash("Object Storage configuration saved.", "success")
        return redirect(url_for("setup_object_storage_page"))
    return render_dashboard(
        "setup_object_storage.html",
        page_title="Setup Object Storage",
        object_storage_config=config,
    )


@app.route("/admin/status-variables")
@login_required
def admin_status_variables_page():
    active_tab = "variables" if str(request.args.get("tab", "")).strip().lower() == "variables" else "status"
    status_variable_page = module_build_empty_status_variable_page(active_tab)
    error_message = ""
    try:
        status_variable_page = module_fetch_grouped_status_variables(active_tab, execute_query=execute_query)
    except Exception as error:
        error_message = str(error)
    return render_dashboard(
        "status_variables.html",
        page_title="Status and Variables",
        active_tab=active_tab,
        status_variable_page=status_variable_page,
        error_message=error_message,
    )


@app.route("/admin/update-dbconsole", methods=["GET", "POST"])
@session_login_required
def update_dbconsole_page():
    bootstrap_required = local_admin_profile_needs_bootstrap()
    update_start_allowed = is_local_admin_profile_session() or bootstrap_required
    if not update_start_allowed:
        abort(403)
    if request.method == "POST":
        action = str(request.form.get("update_action", "")).strip().lower()
        if action == "start":
            if not update_start_allowed:
                abort(403)
            try:
                if bootstrap_required:
                    local_admin_password_reset = normalize_update_local_admin_bootstrap_credentials(
                        request.form,
                        require_password=True,
                    )
                elif any(field_name in request.form for field_name in UPDATE_LOCAL_ADMIN_RESET_FIELDS):
                    raise ValueError(
                        "Use the local-admin password change page to change an existing localadmin password."
                    )
                else:
                    local_admin_password_reset = {}
                start_dbconsole_update_job(local_admin_password_reset=local_admin_password_reset)
                flash("Auto-update started.", "success")
            except Exception as error:
                flash(str(error), "error")
        elif action == "retrieve-version":
            version_check = refresh_repo_version_check()
            if version_check.get("error"):
                flash(f"Repository version check failed: {version_check['error']}", "error")
            elif version_check.get("update_available"):
                flash(
                    f"Repository version {version_check.get('repo_version')} differs from local version {version_check.get('local_version')}.",
                    "success",
                )
            else:
                flash("Repository version matches the local app version.", "success")
        return redirect(url_for("update_dbconsole_page"))

    return render_dashboard(
        "update_dbconsole.html",
        page_title="Auto-Update",
        update_status=_public_dbconsole_update_status(get_dbconsole_update_status()),
        update_poll_token=_ensure_dbconsole_update_poll_token(),
        local_admin_profile_name=LOCAL_ADMIN_PROFILE_NAME,
        local_admin_mysql_user=str(
            (get_session_profile().get("username") if is_local_admin_profile_session() else "") or "localadmin"
        ),
        bootstrap_required=bootstrap_required,
        update_start_allowed=update_start_allowed,
        app_version_info=session.get(DBCONSOLE_VERSION_CHECK_SESSION_KEY)
        or {
            "local_version": get_local_app_version(),
            "repo_version": "-",
            "update_available": False,
            "checked_at": "",
            "error": "",
            "version_url": infer_app_version_url(),
        },
    )


@app.route("/admin/update-dbconsole/status")
def update_dbconsole_status():
    update_status = get_dbconsole_update_status()
    if not session.get("logged_in") and not _update_status_poll_token_is_valid(update_status):
        return jsonify({"error": "Log in to continue."}), 401
    return jsonify(_public_dbconsole_update_status(update_status))


@app.route("/mysql/dashboard")
@login_required
def mysql_dashboard_page():
    dashboard_tab = str(request.args.get("dashboard_tab", "server-database")).strip().lower()
    if dashboard_tab == "error-log":
        dashboard_tab = "logs"
    if dashboard_tab not in {"server-database", "logs", "security", "heatwave", "replication"}:
        dashboard_tab = "server-database"
    selected_error_log_priorities = _normalize_error_log_priorities(request.args.getlist("error_prio"))
    selected_error_log_period = normalize_error_log_period(request.args.get("error_period"))
    selected_error_log_code = normalize_error_log_code(request.args.get("error_code"))
    selected_error_log_message_like = normalize_error_log_message_like(request.args.get("message_like"))
    dashboard_context = build_mysql_dashboard_snapshot_context(
        selected_error_log_priorities,
        selected_error_log_period,
        selected_error_log_code,
        selected_error_log_message_like,
        sections={dashboard_tab},
    )
    return render_dashboard(
        "mysql_dashboard.html",
        page_title="Admin Dashboard",
        dashboard_tab=dashboard_tab,
        **dashboard_context,
    )


def build_mysql_dashboard_snapshot_context(
    selected_error_log_priorities,
    selected_error_log_period,
    selected_error_log_code,
    selected_error_log_message_like,
    *,
    sections=None,
):
    return module_build_mysql_dashboard_context(
        fetch_server_overview=lambda: fetch_server_overview(
            recent_error_log_priorities=selected_error_log_priorities,
            recent_error_log_period=selected_error_log_period["value"],
            recent_error_log_code=selected_error_log_code,
            recent_error_log_message_like=selected_error_log_message_like,
            sections=sections,
        ),
        fetch_database_inventory=fetch_database_inventory,
        fetch_dashboard_heatwave_summary=fetch_dashboard_heatwave_summary,
        include_inventory=sections is None or "server-database" in sections or "heatwave" in sections,
        include_heatwave=sections is None or "heatwave" in sections,
    )


@app.route("/mysql/dashboard/download")
@login_required
def mysql_dashboard_download_page():
    selected_error_log_priorities = _normalize_error_log_priorities(request.args.getlist("error_prio"))
    selected_error_log_period = normalize_error_log_period(request.args.get("error_period"))
    selected_error_log_code = normalize_error_log_code(request.args.get("error_code"))
    selected_error_log_message_like = normalize_error_log_message_like(request.args.get("message_like"))
    dashboard_context = build_mysql_dashboard_snapshot_context(
        selected_error_log_priorities,
        selected_error_log_period,
        selected_error_log_code,
        selected_error_log_message_like,
        sections={"server-database", "logs", "security", "heatwave", "replication"},
    )
    generated_at = datetime.now(timezone.utc)
    profile = get_session_profile()
    global_variables = []
    global_variables_error = ""
    global_status = []
    global_status_error = ""
    try:
        global_variables = fetch_all_show_variable_rows("VARIABLES")
    except Exception as error:  # pragma: no cover - depends on server privileges
        global_variables_error = str(error)
    try:
        global_status = fetch_all_show_variable_rows("STATUS")
    except Exception as error:  # pragma: no cover - depends on server privileges
        global_status_error = str(error)
    html = render_template(
        "mysql_dashboard_export.html",
        app_title=APP_TITLE,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        app_version=get_local_app_version(),
        current_user=get_session_username(),
        current_profile_name=session.get("profile_name", ""),
        connection_summary=f"{profile['host'] or '-'}:{profile['port']}" if profile else "-",
        setup_status=fetch_setup_status(),
        global_variables=global_variables,
        global_variables_error=global_variables_error,
        global_status=global_status,
        global_status_error=global_status_error,
        **dashboard_context,
    )
    filename = f"admin-dashboard-{generated_at.strftime('%Y%m%d-%H%M%S')}.html"
    return Response(
        html,
        mimetype="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/mysql/sql-workspace", methods=["GET", "POST"])
@login_required
def sql_workspace_page():
    workspace_output_tab = str(request.values.get("output_tab", "execution-result")).strip().lower()
    if workspace_output_tab not in {"execution-result", "history"}:
        workspace_output_tab = "execution-result"
    use_secondary_engine = normalize_sql_workspace_secondary_engine(
        request.values.get("use_secondary_engine", "ON")
    )
    default_database = str(get_session_profile().get("database", "") or "").strip()
    if is_system_schema_name(default_database):
        default_database = ""

    selected_database = str(
        request.values.get("database", default_database if request.method == "GET" else "")
    ).strip()
    sql_text = str(request.values.get("sql_text", ""))
    history_rows = session.get(SQL_WORKSPACE_HISTORY_SESSION_KEY, [])
    if not isinstance(history_rows, list):
        history_rows = []

    last_result = None
    if request.method == "POST":
        action = str(request.form.get("workspace_action", "execute")).strip().lower()
        selected_database = str(request.form.get("database", "")).strip()
        sql_text = str(request.form.get("sql_text", ""))
        use_secondary_engine = normalize_sql_workspace_secondary_engine(
            request.form.get("use_secondary_engine", "ON")
        )
        if action == "clear_history":
            session[SQL_WORKSPACE_HISTORY_SESSION_KEY] = []
            flash("SQL workspace history cleared.", "success")
            redirect_values = {"output_tab": "history", "use_secondary_engine": use_secondary_engine}
            if selected_database:
                redirect_values["database"] = selected_database
            if sql_text:
                redirect_values["sql_text"] = sql_text
            return redirect(url_for("sql_workspace_page", **redirect_values))

        started_at = perf_counter()
        workspace_output_tab = "execution-result"

        if action == "explain":
            normalized_statement = sql_text
            try:
                normalized_statement = _normalize_sql_workspace_explain_statement(sql_text)
                text_rows = execute_query(
                    f"EXPLAIN {normalized_statement}",
                    database=selected_database or None,
                    use_secondary_engine=use_secondary_engine,
                )
                json_rows = []
                json_error = ""
                try:
                    json_rows = execute_query(
                        f"EXPLAIN FORMAT=JSON {normalized_statement}",
                        database=selected_database or None,
                        use_secondary_engine=use_secondary_engine,
                    )
                except Exception as error:  # pragma: no cover - depends on server features
                    json_error = str(error)
                duration_ms = (perf_counter() - started_at) * 1000
                last_result = module_build_sql_workspace_explain_result(
                    normalized_statement,
                    selected_database,
                    text_rows,
                    json_rows,
                    duration_ms,
                    use_secondary_engine=use_secondary_engine,
                    json_error=json_error,
                )
                history_rows = module_append_sql_workspace_history(
                    history_rows,
                    module_build_sql_workspace_history_entry(
                        "Explain",
                        selected_database,
                        normalized_statement,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        status="success" if not json_error else "partial",
                        error_message=json_error,
                    ),
                )
            except Exception as error:
                duration_ms = (perf_counter() - started_at) * 1000
                last_result = module_build_sql_workspace_result(
                    "Explain",
                    normalized_statement,
                    selected_database,
                    [],
                    duration_ms,
                    use_secondary_engine=use_secondary_engine,
                    error_message=str(error),
                )
                history_rows = module_append_sql_workspace_history(
                    history_rows,
                    module_build_sql_workspace_history_entry(
                        "Explain",
                        selected_database,
                        normalized_statement,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        status="error",
                        error_message=str(error),
                    ),
                )
                flash(str(error), "error")
        else:
            normalized_statement = sql_text
            result_sets = []
            try:
                normalized_statement = _normalize_sql_workspace_statement(sql_text)
                statements = split_sql_workspace_statements(normalized_statement)
                result_sets = execute_sql_workspace_statements(
                    statements,
                    database=selected_database or None,
                    use_secondary_engine=use_secondary_engine,
                )
                duration_ms = (perf_counter() - started_at) * 1000
                last_result = module_build_sql_workspace_result(
                    "Execute",
                    normalized_statement,
                    selected_database,
                    result_sets,
                    duration_ms,
                    use_secondary_engine=use_secondary_engine,
                )
                history_rows = module_append_sql_workspace_history(
                    history_rows,
                    module_build_sql_workspace_history_entry(
                        "Execute",
                        selected_database,
                        normalized_statement,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        status="success",
                    ),
                )
            except Exception as error:
                duration_ms = (perf_counter() - started_at) * 1000
                last_result = module_build_sql_workspace_result(
                    "Execute",
                    normalized_statement,
                    selected_database,
                    result_sets,
                    duration_ms,
                    use_secondary_engine=use_secondary_engine,
                    error_message=str(error),
                )
                history_rows = module_append_sql_workspace_history(
                    history_rows,
                    module_build_sql_workspace_history_entry(
                        "Execute",
                        selected_database,
                        normalized_statement,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        status="error",
                        error_message=str(error),
                    ),
                )
                flash(str(error), "error")

        session[SQL_WORKSPACE_HISTORY_SESSION_KEY] = history_rows

    page_context = module_build_sql_workspace_context(
        selected_database,
        sql_text,
        last_result,
        history_rows,
        fetch_database_inventory=fetch_database_inventory,
    )
    return render_dashboard(
        "sql_workspace.html",
        page_title="SQL Workspace",
        workspace_output_tab=workspace_output_tab,
        use_secondary_engine=use_secondary_engine,
        secondary_engine_modes=SQL_WORKSPACE_SECONDARY_ENGINE_OPTIONS,
        **page_context,
    )


@app.route("/mysql/imprt", methods=["GET", "POST"])
@login_required
def mysql_import_page():
    database_inventory = [row for row in fetch_database_inventory() if not row["is_system"]]
    existing_plan_id = str(session.get("mysql_import_plan_id", "")).strip()
    plan = module_load_mysql_import_plan(existing_plan_id) if existing_plan_id else None
    if existing_plan_id and plan is None:
        session.pop("mysql_import_plan_id", None)

    page_state = module_build_mysql_import_page_state(
        plan,
        database_inventory,
        fetch_table_exists=fetch_table_exists,
    )

    if request.method == "POST":
        action = str(request.form.get("import_action", "")).strip()

        if action == "clear":
            if existing_plan_id:
                module_delete_mysql_import_plan(existing_plan_id)
            session.pop("mysql_import_plan_id", None)
            flash("Import draft cleared.", "success")
            return redirect(url_for("mysql_import_page"))

        if action == "preview":
            upload_storage = request.files.get("import_file")
            try:
                plan = module_save_mysql_import_plan(
                    module_build_mysql_import_plan(
                        upload_storage,
                        request.form,
                        database_inventory,
                        quote_identifier=quote_identifier,
                    )
                )
                session["mysql_import_plan_id"] = plan["plan_id"]
                if existing_plan_id and existing_plan_id != plan["plan_id"]:
                    module_delete_mysql_import_plan(existing_plan_id)
                flash(f"Loaded {plan['row_count']} rows from `{plan['source_filename']}`.", "success")
                return redirect(url_for("mysql_import_page"))
            except Exception as error:
                page_state = module_build_mysql_import_page_state(
                    plan,
                    database_inventory,
                    fetch_table_exists=fetch_table_exists,
                    payload=request.form,
                )
                flash(str(error), "error")

        elif action == "import":
            if plan is None:
                flash("Upload a CSV or JSON file to preview before importing.", "error")
                return redirect(url_for("mysql_import_page"))
            try:
                import_request = module_validate_mysql_import_request(
                    request.form,
                    plan,
                    database_inventory,
                    quote_identifier=quote_identifier,
                    fetch_table_exists=fetch_table_exists,
                    fetch_database_exists=fetch_database_exists,
                )
                module_run_mysql_import(
                    plan,
                    import_request,
                    quote_identifier=quote_identifier,
                    execute_statement=execute_statement,
                    mysql_connection=mysql_connection,
                )
                if existing_plan_id:
                    module_delete_mysql_import_plan(existing_plan_id)
                session.pop("mysql_import_plan_id", None)
                flash(
                    f"Imported {plan.get('row_count', 0)} rows into "
                    f"`{import_request['effective_database_name']}.{import_request['table_name']}`.",
                    "success",
                )
                return redirect(
                    url_for(
                        "db_admin_page",
                        database=import_request["effective_database_name"],
                        table=import_request["table_name"],
                        )
                )
            except Exception as error:
                page_state = module_build_mysql_import_page_state(
                    plan,
                    database_inventory,
                    fetch_table_exists=fetch_table_exists,
                    payload=request.form,
                )
                flash(str(error), "error")
        else:
            flash("Unsupported import action.", "error")

    return render_dashboard(
        "mysql_import.html",
        page_title="Import",
        import_page=page_state,
    )


@app.route("/mysql/db-admin", methods=["GET", "POST"])
@login_required
def db_admin_page():
    db_admin_tab = normalize_db_admin_tab(request.values.get("db_admin_tab", DB_ADMIN_DEFAULT_TAB))
    selected_database = str(request.values.get("database", "")).strip()
    selected_table = str(request.values.get("table", "")).strip()
    focus_event_database = str(request.args.get("focus_event_database", "")).strip()
    focus_event_name = str(request.args.get("focus_event_name", "")).strip()
    preview_page = normalize_page_number(request.args.get("page", "1"))
    table_info_tab = normalize_db_admin_table_info_tab(request.values.get("table_info_tab", DB_ADMIN_TABLE_INFO_DEFAULT_TAB))
    if str(request.args.get("dialog", "")).strip() == "modify-columns":
        table_info_tab = "modify-columns"
    db_admin_edit_payload = None
    db_admin_event_form_payload = None
    db_admin_charset_collation_payload = None
    charset_collation_preview = None
    event_action_output = session.pop(DB_ADMIN_EVENT_OUTPUT_SESSION_KEY, None)

    if request.method == "POST":
        action = str(request.form.get("db_action", "")).strip()
        db_admin_tab = normalize_db_admin_tab(request.form.get("db_admin_tab", db_admin_tab))
        selected_database = str(request.form.get("database_name", selected_database)).strip()
        selected_table = str(request.form.get("table_name", selected_table)).strip()
        if action == "create_event":
            selected_database = str(request.form.get("event_database_name", selected_database)).strip()
        if action == "download_charset_collation_script":
            try:
                plan = preview_db_admin_charset_collation(selected_database, request.form)
                script_text = build_db_admin_charset_collation_script(plan)
                filename_database = re.sub(r"[^A-Za-z0-9_.-]+", "_", plan["database_name"]).strip("._") or "database"
                response = Response(script_text, mimetype="application/sql")
                response.headers["Content-Disposition"] = (
                    f"attachment; filename={filename_database}-charset-collation-plan.sql"
                )
                return response
            except Exception as error:
                flash(str(error), "error")
                db_admin_charset_collation_payload = request.form
                return redirect(
                    url_for(
                        "db_admin_page",
                        db_admin_tab="charset-collation",
                        database=selected_database,
                    )
                )
        try:
            action_result = module_handle_db_admin_action(
                action,
                request.form.get("database_name", ""),
                table_name=request.form.get("table_name", ""),
                payload=request.form,
                quote_identifier=quote_identifier,
                execute_statement=execute_statement,
                system_schemas=SYSTEM_SCHEMAS,
                fetch_create_table_statement=fetch_create_table_statement,
                fetch_table_columns=fetch_table_columns,
                fetch_tables_for_database=fetch_tables_for_database,
                fetch_missing_primary_key_rows=fetch_tables_without_primary_key,
                fix_missing_primary_key_table=fix_table_without_primary_key,
                create_db_event=create_db_admin_event,
                set_db_events_enabled=set_db_admin_events_enabled,
                delete_db_events=delete_db_admin_events,
                modify_charset_collation=modify_db_admin_charset_collation,
                preview_charset_collation=preview_db_admin_charset_collation,
            )
            if action_result.get("charset_collation_preview"):
                db_admin_charset_collation_payload = request.form
                charset_collation_preview = action_result["charset_collation_preview"]
                flash(action_result["flash_message"], action_result["flash_category"])
            else:
                if action_result.get("event_action_output"):
                    session[DB_ADMIN_EVENT_OUTPUT_SESSION_KEY] = action_result["event_action_output"]
                flash(action_result["flash_message"], action_result["flash_category"])
                redirect_values = dict(action_result["redirect_values"])
                redirect_values.setdefault("db_admin_tab", DB_ADMIN_DEFAULT_TAB)
                return redirect(url_for(action_result["redirect_endpoint"], **redirect_values))
        except Exception as error:
            flash(str(error), "error")
            if action == "modify_table_columns":
                table_info_tab = "modify-columns"
                db_admin_edit_payload = request.form
            elif action == "create_event":
                db_admin_event_form_payload = request.form
                event_action_output = {
                    "title": "Create Event",
                    "category": "error",
                    "message": str(error),
                }
            elif action in {"enable_events", "disable_events", "delete_events"}:
                event_action_output = {
                    "title": "Delete Event" if action == "delete_events" else "Event Status",
                    "category": "error",
                    "message": str(error),
                }
            elif action in {"modify_charset_collation", "preview_charset_collation"}:
                db_admin_charset_collation_payload = request.form

    page_context = module_build_db_admin_context(
        selected_database,
        selected_table,
        preview_page,
        db_admin_tab=db_admin_tab,
        table_info_tab=table_info_tab,
        fetch_database_inventory=fetch_database_inventory,
        fetch_tables_for_database=fetch_tables_for_database,
        empty_table_preview=empty_table_preview,
        fetch_table_preview=fetch_table_preview,
        fetch_create_table_statement=fetch_create_table_statement,
        fetch_table_columns=fetch_table_columns,
        fetch_table_indexes=fetch_table_indexes,
        fetch_table_partitions=fetch_table_partitions,
        fetch_missing_primary_key_rows=fetch_tables_without_primary_key,
        column_edit_payload=db_admin_edit_payload,
        fetch_event_rows=fetch_db_admin_event_rows,
        event_form_payload=db_admin_event_form_payload,
        event_schedule_options=EVENT_SCHEDULE_OPTIONS,
        focused_event_database=focus_event_database,
        focused_event_name=focus_event_name,
        fetch_charset_collation_report=fetch_db_admin_charset_collation_report,
        fetch_charset_collation_options=fetch_charset_collation_options,
        charset_collation_payload=db_admin_charset_collation_payload,
    )
    if page_context.get("redirect_endpoint"):
        flash(page_context["flash_message"], page_context["flash_category"])
        redirect_values = dict(page_context["redirect_values"])
        redirect_values.setdefault("db_admin_tab", DB_ADMIN_DEFAULT_TAB)
        return redirect(url_for(page_context["redirect_endpoint"], **redirect_values))

    return render_dashboard(
        "db_admin.html",
        page_title="DB Admin",
        db_admin_tab=db_admin_tab,
        table_info_tab=table_info_tab,
        event_schedule_options=EVENT_SCHEDULE_OPTIONS,
        event_action_output=event_action_output,
        charset_collation_preview=charset_collation_preview,
        **page_context,
    )


@app.route("/mysql/db-admin/download")
@login_required
def db_admin_download():
    selected_database = str(request.args.get("database", "")).strip()
    db_admin_tab = normalize_db_admin_tab(request.args.get("db_admin_tab", DB_ADMIN_DEFAULT_TAB))
    export_payload = module_build_db_admin_export(
        selected_database,
        db_admin_tab=db_admin_tab,
        fetch_tables_for_database=fetch_tables_for_database,
        fetch_missing_primary_key_rows=fetch_tables_without_primary_key,
    )
    return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])


@app.route("/heatwave/hw-table")
@login_required
def hw_table_page():
    active_tab = "lakehouse" if str(request.args.get("tab", "")).strip().lower() == "lakehouse" else "heatwave"
    report = module_build_heatwave_tables_context(
        fetch_heatwave_inventory_report=fetch_heatwave_inventory_report,
        fetch_heatwave_status_variable_report=fetch_heatwave_status_variable_report,
        fetch_heatwave_nodes_report=fetch_heatwave_nodes_report,
        fetch_heatwave_defined_secondary_engine_tables=fetch_heatwave_defined_secondary_engine_tables,
        fetch_lakehouse_engine_tables=fetch_lakehouse_engine_tables,
    )
    return render_dashboard(
        "hw_table.html",
        page_title="HW Table",
        active_tab=active_tab,
        **report,
    )


@app.route("/heatwave/hw-table/download")
@login_required
def hw_table_download():
    report = module_build_heatwave_tables_context(
        fetch_heatwave_inventory_report=fetch_heatwave_inventory_report,
        fetch_heatwave_status_variable_report=fetch_heatwave_status_variable_report,
        fetch_heatwave_nodes_report=fetch_heatwave_nodes_report,
        fetch_heatwave_defined_secondary_engine_tables=fetch_heatwave_defined_secondary_engine_tables,
        fetch_lakehouse_engine_tables=fetch_lakehouse_engine_tables,
    )
    export_payload = module_build_heatwave_tables_export(report)
    return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])


@app.route("/heatwave/management", methods=["GET", "POST"])
@login_required
def heatwave_management_page():
    active_tab = "table" if str(request.values.get("tab", "")).strip().lower() == "table" else "db"
    selected_database = str(request.values.get("database", "")).strip()
    selected_table = str(request.values.get("table", "")).strip()
    management_open_dialog = ""
    management_popup_result = None

    if request.method == "POST":
        active_tab = "table" if str(request.form.get("tab", "")).strip().lower() == "table" else "db"
        action = str(request.form.get("management_action", "")).strip()
        selected_database = str(request.form.get("database", "")).strip()
        selected_table = str(request.form.get("table", "")).strip()
        try:
            action_result = module_handle_heatwave_management_action(
                action,
                selected_database,
                selected_table,
                excluded_columns=request.form.getlist("excluded_columns"),
                quote_identifier=quote_identifier,
                execute_statement=execute_statement,
                execute_multi_result_query=execute_multi_result_query,
                fetch_table_columns=fetch_table_columns,
                fetch_create_table_statement=fetch_create_table_statement,
            )
            flash(action_result["flash_message"], action_result["flash_category"])
            selected_database = str(action_result["redirect_values"].get("database", selected_database)).strip()
            selected_table = str(action_result["redirect_values"].get("table", selected_table)).strip()
            active_tab = "table" if str(action_result["redirect_values"].get("tab", active_tab)).strip().lower() == "table" else "db"
            if action_result.get("render_popup"):
                management_open_dialog = str(action_result.get("open_dialog", "")).strip()
                management_popup_result = action_result.get("popup_result")
            else:
                return redirect(url_for("heatwave_management_page", **action_result["redirect_values"]))
        except Exception as error:
            flash(str(error), "error")
            if action == "exclude_columns_update":
                management_open_dialog = "exclude-columns-dialog"

    page_context = module_build_heatwave_management_context(
        selected_database,
        selected_table,
        active_tab,
        fetch_database_inventory=fetch_database_inventory,
        fetch_tables_for_database=fetch_tables_for_database,
        fetch_table_columns=fetch_table_columns,
        fetch_create_table_statement=fetch_create_table_statement,
        fetch_heatwave_inventory_report=fetch_heatwave_inventory_report,
        fetch_heatwave_defined_secondary_engine_tables=fetch_heatwave_defined_secondary_engine_tables,
        execute_query=execute_query,
    )
    return render_dashboard(
        "heatwave_management.html",
        page_title="HW Admin",
        management_open_dialog=management_open_dialog,
        management_popup_result=management_popup_result,
        **page_context,
    )


@app.route("/monitoring/dashboard")
@login_required
def monitoring_dashboard_page():
    return render_dashboard(
        "monitoring_dashboard.html",
        page_title="Monitoring Dashboard",
        **module_build_monitoring_dashboard_page_context(
            build_monitoring_dashboard_context=build_monitoring_dashboard_context,
        ),
    )


@app.route("/monitoring/charts")
@login_required
def monitoring_charts_page():
    monitoring_chart_tab = str(request.args.get("chart_tab", "general")).strip().lower()
    allowed_chart_tabs = {option[0] for option in MONITORING_CHART_TAB_OPTIONS}
    if monitoring_chart_tab not in allowed_chart_tabs:
        monitoring_chart_tab = "general"
    return render_dashboard(
        "monitoring_charts.html",
        page_title="Monitoring Charts",
        monitoring_chart_tab=monitoring_chart_tab,
        monitoring_chart_tabs=[{"key": key, "label": label} for key, label in MONITORING_CHART_TAB_OPTIONS],
        **module_build_monitoring_charts_page_context(
            build_monitoring_chart_snapshot=build_monitoring_chart_snapshot,
            charts_data_url=url_for("monitoring_charts_data"),
        ),
    )


@app.route("/monitoring/charts/data")
@login_required
def monitoring_charts_data():
    return jsonify(module_build_monitoring_charts_data(build_monitoring_chart_snapshot=build_monitoring_chart_snapshot))


@app.route("/monitoring/locks")
@login_required
def monitoring_locks_page():
    return render_dashboard(
        "monitoring_locks.html",
        page_title="Locks",
        **module_build_monitoring_locks_page_context(
            build_monitoring_locks_context=build_monitoring_locks_context,
        ),
    )


@app.route("/monitoring/performance-query")
@login_required
def monitoring_performance_page():
    return render_dashboard(
        "monitoring_report.html",
        **module_build_monitoring_report_page(
            fetch_monitoring_performance_queries,
            page_title="Performance Query",
            report_title="HeatWave Performance Query",
            report_description="Direct monitoring view for HeatWave query activity from performance_schema.",
            download_endpoint="monitoring_performance_download",
        ),
    )


@app.route("/monitoring/performance-query/download")
@login_required
def monitoring_performance_download():
    export_payload = module_build_monitoring_report_download(
        fetch_monitoring_performance_queries,
        "monitoring-performance-query.csv",
    )
    return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])


@app.route("/monitoring/ml-query")
@login_required
def monitoring_ml_page():
    current_ml_connection_only = _normalize_checkbox(request.args.get("current_ml_connection_only", ""))
    return render_dashboard(
        "monitoring_report.html",
        **module_build_monitoring_report_page(
            fetch_monitoring_ml_queries,
            page_title="ML Query",
            report_title="HeatWave ML Query",
            report_description="Direct monitoring view for HeatWave ML jobs from performance_schema.",
            download_endpoint="monitoring_ml_download",
            fetch_kwargs={"current_ml_connection_only": current_ml_connection_only},
            extra_context={"current_ml_connection_only": current_ml_connection_only},
        ),
    )


@app.route("/monitoring/ml-query/download")
@login_required
def monitoring_ml_download():
    current_ml_connection_only = _normalize_checkbox(request.args.get("current_ml_connection_only", ""))
    export_payload = module_build_monitoring_report_download(
        fetch_monitoring_ml_queries,
        "monitoring-ml-query.csv",
        fetch_kwargs={"current_ml_connection_only": current_ml_connection_only},
    )
    return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])


@app.route("/monitoring/table-load-recovery")
@login_required
def monitoring_load_recovery_page():
    return render_dashboard(
        "monitoring_report.html",
        **module_build_monitoring_report_page(
            fetch_monitoring_load_recovery,
            page_title="Table Load Recovery",
            report_title="HeatWave Table Load Recovery",
            report_description="Direct monitoring view for HeatWave table load and recovery state.",
            download_endpoint="monitoring_load_recovery_download",
        ),
    )


@app.route("/monitoring/table-load-recovery/download")
@login_required
def monitoring_load_recovery_download():
    export_payload = module_build_monitoring_report_download(
        fetch_monitoring_load_recovery,
        "monitoring-table-load-recovery.csv",
    )
    return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])


if __name__ == "__main__":
    ensure_profile_store()
    ensure_object_storage_store()
    app.run(debug=True, host="127.0.0.1", port=5001)
