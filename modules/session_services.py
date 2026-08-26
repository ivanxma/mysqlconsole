import os
import secrets

from flask import flash, session

from modules.core_util import chmod_private_file
from modules.session_util import SessionManager


_TRUE_VALUES = {"1", "true", "yes", "on"}


def session_cookie_secure_for_transport(value, listener_scheme=""):
    """Resolve the cookie flag without permitting HTTPS to be downgraded."""
    if str(listener_scheme or "").strip().lower() == "https":
        return True
    return str(value or "").strip().lower() in _TRUE_VALUES


def load_flask_secret_key(secret_key_file):
    configured_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if configured_secret:
        return configured_secret
    try:
        if secret_key_file.exists():
            stored_secret = secret_key_file.read_text(encoding="utf-8").strip()
            if stored_secret:
                return stored_secret
        generated_secret = secrets.token_urlsafe(48)
        secret_key_file.write_text(generated_secret + "\n", encoding="utf-8")
        chmod_private_file(secret_key_file)
        return generated_secret
    except OSError:
        return secrets.token_urlsafe(48)


def normalize_credential_ttl_seconds(value, default=43200, minimum=300):
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


class DbConsoleSessionService:
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
        profile_service,
        local_admin_profile_name,
        nav_groups,
    ):
        self.profile_service = profile_service
        self.local_admin_profile_name = local_admin_profile_name
        self.nav_groups = nav_groups
        self.manager = SessionManager(
            default_profile=default_profile,
            normalize_profile=normalize_profile,
            close_cached_connection=close_cached_connection,
            parse_iso_datetime=parse_iso_datetime,
            utc_now_iso=utc_now_iso,
            credential_ttl_seconds=credential_ttl_seconds,
            scope_key=scope_key,
            scope_value=scope_value,
            version_key=version_key,
            version=version,
            credential_session_key=credential_session_key,
            csrf_session_key=csrf_session_key,
        )

    def configure_auth_callbacks(self, *, mysql_connection):
        self.manager.configure_auth_callbacks(
            mysql_connection=mysql_connection,
            local_admin_password_change_required=self.local_admin_password_change_required,
        )

    def csrf_token(self):
        return self.manager.ensure_csrf_token()

    def ensure_scope(self):
        return self.manager.ensure_scope()

    def validate_csrf_request(self):
        return self.manager.validate_csrf_request()

    def add_authenticated_no_store_headers(self, response):
        return self.manager.add_authenticated_no_store_headers(response)

    def get_session_profile(self):
        return self.manager.get_session_profile()

    def set_session_profile(self, profile):
        self.manager.set_session_profile(profile)

    def get_server_session_id(self):
        return self.manager.get_server_session_id()

    def cleanup_expired_server_sessions(self):
        self.manager.cleanup_expired_server_sessions()

    def get_server_session_entry(self):
        return self.manager.get_server_session_entry()

    def set_session_credentials(self, username, password):
        self.manager.set_session_credentials(username, password)

    def get_session_credentials(self):
        return self.manager.get_session_credentials()

    def get_session_username(self):
        return self.manager.get_session_username()

    def has_active_login_state(self):
        return self.manager.has_active_login_state()

    def clear_login_state(self, keep_profile=True):
        self.manager.clear_login_state(keep_profile=keep_profile)

    def redirect_to_login_for_mysql_unavailable(self, error):
        return self.manager.redirect_to_login_for_mysql_unavailable(error)

    def session_login_required(self, view):
        return self.manager.session_login_required(view)

    def login_required(self, view):
        return self.manager.login_required(view)

    def set_logged_in(self, value):
        session["logged_in"] = bool(value)

    def is_local_admin_profile_session(self):
        profile = self.get_session_profile()
        return (
            profile.get("name") == self.local_admin_profile_name
            and bool(profile.get("socket_enabled"))
            and bool(str(profile.get("socket_path") or "").strip())
        )

    def local_admin_password_change_required(self):
        profile = self.get_session_profile()
        return bool(self.is_local_admin_profile_session() and profile.get("require_password_change"))

    def clear_local_admin_password_change_required(self):
        profiles = self.profile_service.load_profiles()
        changed = False
        updated_profiles = []
        for profile in profiles:
            if profile.get("name") == self.local_admin_profile_name and profile.get("require_password_change"):
                profile = dict(profile)
                profile["require_password_change"] = False
                changed = True
            updated_profiles.append(profile)
        if changed:
            self.profile_service.save_profiles(updated_profiles)
            current_profile = self.get_session_profile()
            if current_profile.get("name") == self.local_admin_profile_name:
                current_profile["require_password_change"] = False
                self.set_session_profile(current_profile)

    def nav_groups_for_current_session(self):
        if self.is_local_admin_profile_session():
            return self.nav_groups
        filtered_groups = []
        for group in self.nav_groups:
            filtered_items = []
            for item in group["items"]:
                if item["endpoint"] in {"profile_page", "update_dbconsole_page"}:
                    continue
                filtered_items.append(item)
            filtered_groups.append({**group, "items": filtered_items})
        return filtered_groups

    def can_access_update_page(self):
        return self.is_local_admin_profile_session()

    def require_local_admin_profile_session(self):
        if not self.is_local_admin_profile_session():
            flash(f"Use `{self.local_admin_profile_name}` for this action.", "error")
            return False
        return True
