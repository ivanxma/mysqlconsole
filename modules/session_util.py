import hmac
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from threading import Event, RLock, Thread
from uuid import uuid4

from flask import abort, flash, g, redirect, request, session, url_for


class SessionManager:
    def __init__(
        self,
        *,
        default_profile,
        normalize_profile,
        close_cached_connection,
        parse_iso_datetime,
        utc_now_iso,
        credential_ttl_seconds,
        scope_key,
        scope_value,
        version_key,
        version,
        credential_session_key,
        csrf_session_key,
    ):
        self.default_profile = default_profile
        self.normalize_profile = normalize_profile
        self.close_cached_connection = close_cached_connection
        self.parse_iso_datetime = parse_iso_datetime
        self.utc_now_iso = utc_now_iso
        self.credential_ttl_seconds = credential_ttl_seconds
        self.scope_key = scope_key
        self.scope_value = scope_value
        self.version_key = version_key
        self.version = version
        self.credential_session_key = credential_session_key
        self.csrf_session_key = csrf_session_key
        self.active_sessions = {}
        self.active_sessions_lock = RLock()
        self.mysql_connection = None
        self.local_admin_password_change_required = None
        self.cleanup_stop_event = Event()
        self.cleanup_thread = None

    def configure_auth_callbacks(self, *, mysql_connection, local_admin_password_change_required):
        self.mysql_connection = mysql_connection
        self.local_admin_password_change_required = local_admin_password_change_required

    def prime_scope(self):
        session[self.scope_key] = self.scope_value
        session[self.version_key] = self.version

    def ensure_scope(self):
        if session.get(self.scope_key) == self.scope_value and session.get(self.version_key) == self.version:
            return
        session.clear()
        self.prime_scope()

    def ensure_csrf_token(self):
        token = str(session.get(self.csrf_session_key, "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
            token = secrets.token_urlsafe(32)
            session[self.csrf_session_key] = token
        return token

    def validate_csrf_request(self):
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        expected_token = str(session.get(self.csrf_session_key, "")).strip()
        supplied_token = str(
            request.form.get("_csrf_token", "")
            or request.headers.get("X-DBConsole-CSRF-Token", "")
        ).strip()
        if not expected_token or not supplied_token or not hmac.compare_digest(expected_token, supplied_token):
            abort(400, "Invalid or missing CSRF token.")

    def add_authenticated_no_store_headers(self, response):
        nonce = self.csp_nonce()
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
            "form-action 'self'; img-src 'self' data:; connect-src 'self'; "
            f"style-src 'self' 'nonce-{nonce}'; script-src 'self' 'nonce-{nonce}'",
        )
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if session.get("logged_in"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @staticmethod
    def csp_nonce():
        nonce = getattr(g, "dbconsole_csp_nonce", "")
        if not nonce:
            nonce = secrets.token_urlsafe(24)
            g.dbconsole_csp_nonce = nonce
        return nonce

    def get_session_profile(self):
        payload = session.get("connection_profile")
        if not payload:
            return self.normalize_profile(self.default_profile)
        return self.normalize_profile(payload)

    def set_session_profile(self, profile):
        normalized_profile = self.normalize_profile(profile)
        session["connection_profile"] = normalized_profile
        session["profile_name"] = normalized_profile["name"]

    def get_server_session_id(self):
        return str(session.get(self.credential_session_key, "")).strip()

    def cleanup_expired_server_sessions(self):
        now = datetime.now(timezone.utc)
        expired_entries = []
        with self.active_sessions_lock:
            expired_session_ids = []
            for server_session_id, entry in list(self.active_sessions.items()):
                last_activity = self.parse_iso_datetime(
                    (entry or {}).get("last_activity_at") or (entry or {}).get("created_at")
                )
                if last_activity is None or (now - last_activity).total_seconds() > self.credential_ttl_seconds:
                    expired_session_ids.append(server_session_id)
            for server_session_id in expired_session_ids:
                entry = self.active_sessions.pop(server_session_id, None)
                if entry is not None:
                    expired_entries.append(entry)
        for entry in expired_entries:
            self.close_cached_connection(entry)

    def get_server_session_entry(self):
        self.cleanup_expired_server_sessions()
        server_session_id = self.get_server_session_id()
        if not server_session_id:
            return None
        with self.active_sessions_lock:
            entry = self.active_sessions.get(server_session_id)
            if isinstance(entry, dict):
                entry["last_activity_at"] = self.utc_now_iso()
        return entry if isinstance(entry, dict) else None

    def set_server_session_state(self, key, value):
        server_session_id = self.get_server_session_id()
        if not server_session_id:
            raise ValueError("No active DB Console server session is available.")
        with self.active_sessions_lock:
            entry = self.active_sessions.get(server_session_id)
            if not isinstance(entry, dict):
                raise ValueError("The DB Console server session has expired.")
            state = entry.setdefault("feature_state", {})
            state[str(key)] = value

    def pop_server_session_state(self, key):
        server_session_id = self.get_server_session_id()
        if not server_session_id:
            return None
        with self.active_sessions_lock:
            entry = self.active_sessions.get(server_session_id)
            if not isinstance(entry, dict):
                return None
            state = entry.get("feature_state")
            if not isinstance(state, dict):
                return None
            return state.pop(str(key), None)

    def set_session_credentials(self, username, password):
        old_session_id = self.get_server_session_id()
        old_entry = None
        server_session_id = uuid4().hex
        session_entry = {
            "username": str(username or "").strip(),
            "password": password or "",
            "created_at": self.utc_now_iso(),
            "last_activity_at": self.utc_now_iso(),
            "mysql_connection_lock": RLock(),
        }
        with self.active_sessions_lock:
            if old_session_id:
                old_entry = self.active_sessions.pop(old_session_id, None)
            self.active_sessions[server_session_id] = session_entry
        if old_entry is not None:
            self.close_cached_connection(old_entry)
        session[self.credential_session_key] = server_session_id

    def get_session_credentials(self):
        entry = self.get_server_session_entry()
        if entry is not None:
            return {
                "username": str(entry.get("username", "")).strip(),
                "password": entry.get("password", ""),
            }
        return {
            "username": "",
            "password": "",
        }

    def get_session_username(self):
        return self.get_session_credentials()["username"]

    def has_active_login_state(self):
        return bool(session.get("logged_in") and self.get_server_session_entry())

    def clear_login_state(self, keep_profile=True):
        server_session_id = self.get_server_session_id()
        entry = None
        if server_session_id:
            with self.active_sessions_lock:
                entry = self.active_sessions.pop(server_session_id, None)
        if entry is not None:
            self.close_cached_connection(entry)
        profile = session.get("connection_profile") if keep_profile else None
        profile_name = session.get("profile_name") if keep_profile else None
        session.clear()
        self.prime_scope()
        if keep_profile and profile:
            session["connection_profile"] = profile
            session["profile_name"] = profile_name

    def close_all_server_sessions(self):
        with self.active_sessions_lock:
            entries = list(self.active_sessions.values())
            self.active_sessions.clear()
        for entry in entries:
            self.close_cached_connection(entry)

    def start_cleanup_worker(self, interval_seconds=60):
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            return
        self.cleanup_stop_event.clear()

        def cleanup_loop():
            while not self.cleanup_stop_event.wait(interval_seconds):
                self.cleanup_expired_server_sessions()

        self.cleanup_thread = Thread(target=cleanup_loop, name="dbconsole-session-cleanup", daemon=True)
        self.cleanup_thread.start()

    def stop_cleanup_worker(self):
        self.cleanup_stop_event.set()
        self.close_all_server_sessions()

    def redirect_to_login_for_mysql_unavailable(self, error):
        profile_name = str(session.get("profile_name", "")).strip()
        self.clear_login_state(keep_profile=True)
        flash(f"MySQL connection is unavailable: {error}", "error")
        redirect_values = {"profile": profile_name} if profile_name else {}
        return redirect(url_for("login", **redirect_values))

    def session_login_required(self, view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not self.has_active_login_state():
                flash("Log in to continue.", "error")
                self.clear_login_state(keep_profile=True)
                return redirect(url_for("login"))
            if self.local_admin_password_change_required() and request.endpoint != "local_admin_password_page":
                return redirect(url_for("local_admin_password_page"))
            return view(*args, **kwargs)

        return wrapped_view

    def login_required(self, view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not self.has_active_login_state():
                flash("Log in to continue.", "error")
                self.clear_login_state(keep_profile=True)
                return redirect(url_for("login"))
            if self.local_admin_password_change_required() and request.endpoint != "local_admin_password_page":
                return redirect(url_for("local_admin_password_page"))
            try:
                with self.mysql_connection(connect_timeout=3):
                    pass
            except Exception as error:
                return self.redirect_to_login_for_mysql_unavailable(error)
            return view(*args, **kwargs)

        return wrapped_view
