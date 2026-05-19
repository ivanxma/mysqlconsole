import base64
import csv
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
from pathlib import Path
from uuid import uuid4

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from modules import object_storage_util, profile_store, update_util
from modules.admin_routes import register_admin_routes
from modules.auth_routes import register_auth_routes
from modules.core_util import chmod_private_file, parse_iso_datetime as _parse_iso_datetime, utc_now_iso as _utc_now_iso
from modules.dashboard_queries import (
    _normalize_checkbox,
    _normalize_error_log_priorities,
    configure_dashboard_queries,
    fetch_all_show_variable_rows,
    fetch_dashboard_heatwave_summary,
    fetch_database_inventory,
    fetch_heatwave_defined_secondary_engine_tables,
    fetch_heatwave_inventory_report,
    fetch_heatwave_nodes_report,
    fetch_heatwave_status_variable_report,
    fetch_lakehouse_engine_tables,
    fetch_server_overview,
    fetch_tables_for_database,
    normalize_error_log_code,
    normalize_error_log_message_like,
    normalize_error_log_period,
)
from modules.dashboard_routes import register_dashboard_routes
from modules.db_admin_queries import (
    build_db_admin_charset_collation_script,
    configure_db_admin_queries,
    create_db_admin_event,
    delete_db_admin_events,
    empty_table_preview,
    fetch_charset_collation_options,
    fetch_create_table_statement,
    fetch_db_admin_charset_collation_report,
    fetch_db_admin_event_rows,
    fetch_full_table_report,
    fetch_table_columns,
    fetch_table_indexes,
    fetch_table_partitions,
    fetch_table_preview,
    fetch_tables_without_primary_key,
    fix_table_without_primary_key,
    modify_db_admin_charset_collation,
    preview_db_admin_charset_collation,
    set_db_admin_events_enabled,
)
from modules.db_admin_routes import register_db_admin_routes
from modules.heatwave_pages import (
    build_dashboard_heatwave_summary as module_build_dashboard_heatwave_summary,
)
from modules.heatwave_routes import register_heatwave_routes
from modules.mysql_import import (
    build_mysql_import_page_state as module_build_mysql_import_page_state,
    build_mysql_import_plan as module_build_mysql_import_plan,
    delete_mysql_import_plan as module_delete_mysql_import_plan,
    load_mysql_import_plan as module_load_mysql_import_plan,
    run_mysql_import as module_run_mysql_import,
    save_mysql_import_plan as module_save_mysql_import_plan,
    validate_mysql_import_request as module_validate_mysql_import_request,
)
from modules.mysql_import_routes import register_mysql_import_routes
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
    build_db_admin_context as module_build_db_admin_context,
    build_db_admin_export as module_build_db_admin_export,
    handle_db_admin_action as module_handle_db_admin_action,
)
from modules.monitoring_routes import register_monitoring_routes
from modules.monitoring_queries import (
    build_monitoring_chart_snapshot,
    build_monitoring_dashboard_context,
    build_monitoring_locks_context,
    configure_monitoring_queries,
    empty_replication_overview_info,
    fetch_monitoring_load_recovery,
    fetch_monitoring_ml_queries,
    fetch_monitoring_performance_queries,
    fetch_replication_overview_info,
    fetch_table_column_lookup,
    fetch_table_column_names,
    run_report_query,
)
from modules.status_variables import (
    build_empty_status_variable_page as module_build_empty_status_variable_page,
    fetch_grouped_status_variables as module_fetch_grouped_status_variables,
)
from modules.sql_workspace import (
    apply_query_session_options as _apply_query_session_options,
    normalize_sql_workspace_secondary_engine,
    register_sql_workspace_routes,
)
from modules.session_util import SessionManager
from modules.update_routes import register_update_routes

APP_TITLE = "MySQL DBConsole"
ROOT_DIR = Path(__file__).resolve().parent
PROFILE_STORE = ROOT_DIR / "profiles.json"
OBJECT_STORAGE_STORE = ROOT_DIR / "object_storage.json"
APP_VERSION_FILE = ROOT_DIR / "appver.json"
FLASK_SECRET_KEY_FILE = ROOT_DIR / ".flask_secret_key"
PROFILE_SSH_KEY_DIR = ROOT_DIR / "profile_ssh_keys"
DBCONSOLE_UPDATE_STATUS_FILE = Path(tempfile.gettempdir()) / "dbconsole-update-status.json"
DBCONSOLE_UPDATE_LOG_FILE = Path(tempfile.gettempdir()) / "dbconsole-update.log"
DBCONSOLE_UPDATE_WORKER = ROOT_DIR / "dbconsole_update_worker.py"
DBCONSOLE_UPDATE_MAX_LOG_LINES = 400
SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$]+$")
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

DB_ADMIN_TABS = {"create", "select", "missing-primary-key", "event", "charset-collation"}
DB_ADMIN_DEFAULT_TAB = "select"
DB_ADMIN_TABLE_INFO_TABS = {"columns", "ddl", "indexes", "partitions", "preview", "modify-columns"}
DB_ADMIN_TABLE_INFO_DEFAULT_TAB = "columns"
DB_ADMIN_EVENT_OUTPUT_SESSION_KEY = "db_admin_event_output"
DBCONSOLE_UPDATE_RUNNING_STATES = {"starting", "running", "restarting"}
LOCAL_ADMIN_PROFILE_NAME = "local-admin-profile"
PROCESS_STARTED_AT = datetime.now(timezone.utc)
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

SESSION_MANAGER = SessionManager(
    default_profile=DEFAULT_PROFILE,
    normalize_profile=normalize_profile,
    close_cached_connection=close_cached_connection,
    parse_iso_datetime=_parse_iso_datetime,
    utc_now_iso=_utc_now_iso,
    credential_ttl_seconds=DBCONSOLE_CREDENTIAL_TTL_SECONDS,
    scope_key=DBCONSOLE_SESSION_SCOPE_KEY,
    scope_value=DBCONSOLE_SESSION_SCOPE_VALUE,
    version_key=DBCONSOLE_SESSION_VERSION_KEY,
    version=DBCONSOLE_SESSION_VERSION,
    credential_session_key=DBCONSOLE_CREDENTIAL_SESSION_KEY,
    csrf_session_key=DBCONSOLE_CSRF_SESSION_KEY,
)


def csrf_token():
    return SESSION_MANAGER.ensure_csrf_token()


@app.before_request
def ensure_dbconsole_session_scope():
    return SESSION_MANAGER.ensure_scope()


@app.before_request
def validate_csrf_token():
    return SESSION_MANAGER.validate_csrf_request()


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": csrf_token}


@app.after_request
def add_authenticated_no_store_headers(response):
    return SESSION_MANAGER.add_authenticated_no_store_headers(response)


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
    return SESSION_MANAGER.get_session_profile()


def set_session_profile(profile):
    SESSION_MANAGER.set_session_profile(profile)


def _get_server_session_id():
    return SESSION_MANAGER.get_server_session_id()


def _cleanup_expired_server_sessions():
    SESSION_MANAGER.cleanup_expired_server_sessions()


def _get_server_session_entry():
    return SESSION_MANAGER.get_server_session_entry()


def set_session_credentials(username, password):
    SESSION_MANAGER.set_session_credentials(username, password)


def get_session_credentials():
    return SESSION_MANAGER.get_session_credentials()


def get_session_username():
    return SESSION_MANAGER.get_session_username()


def has_active_login_state():
    return SESSION_MANAGER.has_active_login_state()


def clear_login_state(keep_profile=True):
    SESSION_MANAGER.clear_login_state(keep_profile=keep_profile)


def _redirect_to_login_for_mysql_unavailable(error):
    return SESSION_MANAGER.redirect_to_login_for_mysql_unavailable(error)


def session_login_required(view):
    return SESSION_MANAGER.session_login_required(view)


def login_required(view):
    return SESSION_MANAGER.login_required(view)


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


def mysql_connection(database_override=None, connect_timeout=5, autocommit=True):
    return borrow_connection(
        profile=get_session_profile(),
        credentials=get_session_credentials(),
        session_entry=_get_server_session_entry(),
        database_override=database_override,
        connect_timeout=connect_timeout,
        autocommit=autocommit,
    )


SESSION_MANAGER.configure_auth_callbacks(
    mysql_connection=mysql_connection,
    local_admin_password_change_required=local_admin_password_change_required,
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


def execute_query(sql, params=None, *, database=None, use_secondary_engine=""):
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            _apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            return cursor.fetchall()


configure_monitoring_queries(
    execute_query=execute_query,
    quote_identifier=quote_identifier,
    mysql_connection=mysql_connection,
)


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


def fetch_scalar(sql, params=None, *, database=None, default=None):
    rows = execute_query(sql, params=params, database=database)
    if not rows:
        return default
    return next(iter(rows[0].values()))


configure_db_admin_queries(
    execute_query=execute_query,
    execute_statement=execute_statement,
    fetch_scalar=fetch_scalar,
    fetch_table_column_lookup=fetch_table_column_lookup,
    get_event_schedule_option=get_event_schedule_option,
    quote_identifier=quote_identifier,
    quote_sql_string=quote_sql_string,
    mysql_connection=mysql_connection,
    db_admin_preview_masked_base_types=DB_ADMIN_PREVIEW_MASKED_BASE_TYPES,
)

configure_dashboard_queries(
    execute_query=execute_query,
    fetch_scalar=fetch_scalar,
    fetch_table_column_lookup=fetch_table_column_lookup,
    fetch_table_column_names=fetch_table_column_names,
    fetch_full_table_report=fetch_full_table_report,
    run_report_query=run_report_query,
    fetch_replication_overview_info=fetch_replication_overview_info,
    empty_replication_overview_info=empty_replication_overview_info,
    quote_identifier=quote_identifier,
    is_system_schema_name=is_system_schema_name,
    build_dashboard_heatwave_summary=module_build_dashboard_heatwave_summary,
    get_session_profile=get_session_profile,
)


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


register_auth_routes(
    app,
    {
        "app_title": APP_TITLE,
        "default_profile": DEFAULT_PROFILE,
        "local_admin_profile_name": LOCAL_ADMIN_PROFILE_NAME,
        "session_login_required": session_login_required,
        "normalize_profile": normalize_profile,
        "public_profile": public_profile,
        "public_profiles": public_profiles,
        "load_profiles": load_profiles,
        "get_profile_by_name": get_profile_by_name,
        "get_session_profile": get_session_profile,
        "set_session_profile": set_session_profile,
        "set_logged_in": lambda value: session.__setitem__("logged_in", bool(value)),
        "set_session_credentials": set_session_credentials,
        "clear_login_state": clear_login_state,
        "mysql_connection": mysql_connection,
        "local_admin_password_change_required": local_admin_password_change_required,
        "is_local_admin_profile_session": is_local_admin_profile_session,
        "local_admin_profile_needs_bootstrap": local_admin_profile_needs_bootstrap,
        "refresh_repo_version_check": refresh_repo_version_check,
        "should_show_update_page_after_login": should_show_update_page_after_login,
        "change_local_admin_profile_password": change_local_admin_profile_password,
        "clear_local_admin_password_change_required": clear_local_admin_password_change_required,
    },
)

register_admin_routes(
    app,
    {
        "login_required": login_required,
        "session_login_required": session_login_required,
        "render_dashboard": render_dashboard,
        "default_profile": DEFAULT_PROFILE,
        "local_admin_profile_name": LOCAL_ADMIN_PROFILE_NAME,
        "normalize_profile": normalize_profile,
        "public_profile": public_profile,
        "load_profiles": load_profiles,
        "save_profiles": save_profiles,
        "get_profile_by_name": get_profile_by_name,
        "get_session_profile": get_session_profile,
        "set_session_profile": set_session_profile,
        "clear_login_state": clear_login_state,
        "is_local_admin_profile_session": is_local_admin_profile_session,
        "change_local_admin_profile_password": change_local_admin_profile_password,
        "clear_local_admin_password_change_required": clear_local_admin_password_change_required,
        "save_uploaded_profile_ssh_key": save_uploaded_profile_ssh_key,
        "load_object_storage_config": load_object_storage_config,
        "normalize_object_storage": normalize_object_storage,
        "save_object_storage_config": save_object_storage_config,
        "build_empty_status_variable_page": module_build_empty_status_variable_page,
        "fetch_grouped_status_variables": module_fetch_grouped_status_variables,
        "execute_query": execute_query,
    },
)

register_update_routes(
    app,
    {
        "session_login_required": session_login_required,
        "render_dashboard": render_dashboard,
        "local_admin_profile_name": LOCAL_ADMIN_PROFILE_NAME,
        "version_check_session_key": DBCONSOLE_VERSION_CHECK_SESSION_KEY,
        "update_local_admin_reset_fields": UPDATE_LOCAL_ADMIN_RESET_FIELDS,
        "local_admin_profile_needs_bootstrap": local_admin_profile_needs_bootstrap,
        "is_local_admin_profile_session": is_local_admin_profile_session,
        "normalize_update_local_admin_bootstrap_credentials": normalize_update_local_admin_bootstrap_credentials,
        "start_dbconsole_update_job": start_dbconsole_update_job,
        "refresh_repo_version_check": refresh_repo_version_check,
        "public_dbconsole_update_status": _public_dbconsole_update_status,
        "get_dbconsole_update_status": get_dbconsole_update_status,
        "ensure_dbconsole_update_poll_token": _ensure_dbconsole_update_poll_token,
        "get_session_profile": get_session_profile,
        "get_local_app_version": get_local_app_version,
        "infer_app_version_url": infer_app_version_url,
        "update_status_poll_token_is_valid": _update_status_poll_token_is_valid,
    },
)


register_dashboard_routes(
    app,
    {
        "login_required": login_required,
        "render_dashboard": render_dashboard,
        "app_title": APP_TITLE,
        "fetch_server_overview": fetch_server_overview,
        "fetch_database_inventory": fetch_database_inventory,
        "fetch_dashboard_heatwave_summary": fetch_dashboard_heatwave_summary,
        "normalize_error_log_priorities": _normalize_error_log_priorities,
        "normalize_error_log_period": normalize_error_log_period,
        "normalize_error_log_code": normalize_error_log_code,
        "normalize_error_log_message_like": normalize_error_log_message_like,
        "get_session_profile": get_session_profile,
        "fetch_all_show_variable_rows": fetch_all_show_variable_rows,
        "get_local_app_version": get_local_app_version,
        "get_session_username": get_session_username,
        "fetch_setup_status": fetch_setup_status,
    },
)


register_sql_workspace_routes(
    app,
    {
        "login_required": login_required,
        "render_dashboard": render_dashboard,
        "get_session_profile": get_session_profile,
        "is_system_schema_name": is_system_schema_name,
        "fetch_database_inventory": fetch_database_inventory,
        "execute_query": execute_query,
        "mysql_connection": mysql_connection,
    },
)

register_mysql_import_routes(
    app,
    {
        "login_required": login_required,
        "render_dashboard": render_dashboard,
        "fetch_database_inventory": fetch_database_inventory,
        "load_mysql_import_plan": module_load_mysql_import_plan,
        "build_mysql_import_page_state": module_build_mysql_import_page_state,
        "fetch_table_exists": fetch_table_exists,
        "delete_mysql_import_plan": module_delete_mysql_import_plan,
        "save_mysql_import_plan": module_save_mysql_import_plan,
        "build_mysql_import_plan": module_build_mysql_import_plan,
        "quote_identifier": quote_identifier,
        "validate_mysql_import_request": module_validate_mysql_import_request,
        "fetch_database_exists": fetch_database_exists,
        "run_mysql_import": module_run_mysql_import,
        "execute_statement": execute_statement,
        "mysql_connection": mysql_connection,
    },
)

register_db_admin_routes(
    app,
    {
        "login_required": login_required,
        "render_dashboard": render_dashboard,
        "build_csv_response": build_csv_response,
        "normalize_page_number": normalize_page_number,
        "normalize_db_admin_tab": normalize_db_admin_tab,
        "normalize_db_admin_table_info_tab": normalize_db_admin_table_info_tab,
        "db_admin_default_tab": DB_ADMIN_DEFAULT_TAB,
        "db_admin_table_info_default_tab": DB_ADMIN_TABLE_INFO_DEFAULT_TAB,
        "db_admin_event_output_session_key": DB_ADMIN_EVENT_OUTPUT_SESSION_KEY,
        "system_schemas": SYSTEM_SCHEMAS,
        "event_schedule_options": EVENT_SCHEDULE_OPTIONS,
        "quote_identifier": quote_identifier,
        "execute_statement": execute_statement,
        "build_db_admin_context": module_build_db_admin_context,
        "build_db_admin_export": module_build_db_admin_export,
        "handle_db_admin_action": module_handle_db_admin_action,
        "fetch_database_inventory": fetch_database_inventory,
        "fetch_tables_for_database": fetch_tables_for_database,
        "empty_table_preview": empty_table_preview,
        "fetch_table_preview": fetch_table_preview,
        "fetch_create_table_statement": fetch_create_table_statement,
        "fetch_table_columns": fetch_table_columns,
        "fetch_table_indexes": fetch_table_indexes,
        "fetch_table_partitions": fetch_table_partitions,
        "fetch_tables_without_primary_key": fetch_tables_without_primary_key,
        "fix_table_without_primary_key": fix_table_without_primary_key,
        "fetch_db_admin_event_rows": fetch_db_admin_event_rows,
        "create_db_admin_event": create_db_admin_event,
        "set_db_admin_events_enabled": set_db_admin_events_enabled,
        "delete_db_admin_events": delete_db_admin_events,
        "fetch_db_admin_charset_collation_report": fetch_db_admin_charset_collation_report,
        "fetch_charset_collation_options": fetch_charset_collation_options,
        "preview_charset_collation": preview_db_admin_charset_collation,
        "build_charset_collation_script": build_db_admin_charset_collation_script,
        "modify_db_admin_charset_collation": modify_db_admin_charset_collation,
    },
)


register_heatwave_routes(
    app,
    {
        "login_required": login_required,
        "render_dashboard": render_dashboard,
        "build_csv_response": build_csv_response,
        "fetch_heatwave_inventory_report": fetch_heatwave_inventory_report,
        "fetch_heatwave_status_variable_report": fetch_heatwave_status_variable_report,
        "fetch_heatwave_nodes_report": fetch_heatwave_nodes_report,
        "fetch_heatwave_defined_secondary_engine_tables": fetch_heatwave_defined_secondary_engine_tables,
        "fetch_lakehouse_engine_tables": fetch_lakehouse_engine_tables,
        "quote_identifier": quote_identifier,
        "execute_statement": execute_statement,
        "execute_multi_result_query": execute_multi_result_query,
        "fetch_table_columns": fetch_table_columns,
        "fetch_create_table_statement": fetch_create_table_statement,
        "fetch_database_inventory": fetch_database_inventory,
        "fetch_tables_for_database": fetch_tables_for_database,
        "execute_query": execute_query,
    },
)

register_monitoring_routes(
    app,
    {
        "login_required": login_required,
        "render_dashboard": render_dashboard,
        "build_csv_response": build_csv_response,
        "normalize_checkbox": _normalize_checkbox,
        "monitoring_chart_tab_options": MONITORING_CHART_TAB_OPTIONS,
        "build_monitoring_dashboard_context": build_monitoring_dashboard_context,
        "build_monitoring_chart_snapshot": build_monitoring_chart_snapshot,
        "build_monitoring_locks_context": build_monitoring_locks_context,
        "fetch_monitoring_performance_queries": fetch_monitoring_performance_queries,
        "fetch_monitoring_ml_queries": fetch_monitoring_ml_queries,
        "fetch_monitoring_load_recovery": fetch_monitoring_load_recovery,
    },
)


if __name__ == "__main__":
    ensure_profile_store()
    ensure_object_storage_store()
    app.run(debug=True, host="127.0.0.1", port=5001)
