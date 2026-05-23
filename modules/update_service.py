import hmac
import os
import re
import sys
from uuid import uuid4

from flask import request, session

from modules import update_util
from modules.core_util import utc_now_iso


class DbConsoleUpdateService:
    def __init__(
        self,
        *,
        repo_dir,
        app_version_file,
        worker_script,
        status_file,
        log_file,
        process_started_at,
        poll_token_session_key,
        version_check_session_key,
        local_admin_profile_name,
        can_access_update_page,
        is_local_admin_profile_session,
        get_session_profile,
        max_log_lines=400,
    ):
        self.repo_dir = repo_dir
        self.app_version_file = app_version_file
        self.worker_script = worker_script
        self.status_file = status_file
        self.log_file = log_file
        self.process_started_at = process_started_at
        self.poll_token_session_key = poll_token_session_key
        self.version_check_session_key = version_check_session_key
        self.local_admin_profile_name = local_admin_profile_name
        self.can_access_update_page = can_access_update_page
        self.is_local_admin_profile_session = is_local_admin_profile_session
        self.get_session_profile = get_session_profile
        self.max_log_lines = max_log_lines
        self.update_local_admin_reset_fields = {
            "reset_local_mysql_admin_password",
            "confirm_reset_local_mysql_admin_password",
            "confirm_local_mysql_admin_reset",
        }

    def public_status(self, status):
        return update_util.public_update_status(status)

    def ensure_poll_token(self):
        token = str(session.get(self.poll_token_session_key, "")).strip()
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            token = uuid4().hex
            session[self.poll_token_session_key] = token
        return token

    def status_poll_token_is_valid(self, status):
        expected_token = str((status or {}).get("poll_token", "")).strip()
        supplied_token = str(request.headers.get("X-DBConsole-Update-Poll-Token", "")).strip()
        return bool(expected_token and supplied_token and hmac.compare_digest(expected_token, supplied_token))

    def get_status(self):
        return update_util.get_update_status(
            self.status_file,
            self.log_file,
            self.process_started_at,
            self.max_log_lines,
        )

    def start_job(self, local_admin_password_reset=None):
        return update_util.start_update_job(
            repo_dir=self.repo_dir,
            worker_script=self.worker_script,
            status_file=self.status_file,
            log_file=self.log_file,
            python_executable=sys.executable,
            service_pid=os.getpid(),
            poll_token=self.ensure_poll_token(),
            process_started_at=self.process_started_at,
            max_log_lines=self.max_log_lines,
            local_admin_password_reset=local_admin_password_reset,
        )

    def get_local_app_version(self):
        return update_util.get_local_app_version(self.app_version_file)

    def infer_app_version_url(self):
        return update_util.infer_app_version_url(self.repo_dir)

    def fetch_repository_app_version(self, timeout=2):
        return update_util.fetch_repository_app_version(self.repo_dir, timeout=timeout)

    def refresh_repo_version_check(self):
        local_version = self.get_local_app_version()
        repo_result = self.fetch_repository_app_version()
        repo_version = repo_result.get("repo_version") or "-"
        update_available = bool(repo_version != "-" and local_version != "-" and repo_version != local_version)
        version_check = {
            "local_version": local_version,
            "repo_version": repo_version,
            "update_available": update_available,
            "checked_at": utc_now_iso(),
            "error": repo_result.get("error", ""),
            "version_url": repo_result.get("version_url", ""),
        }
        session[self.version_check_session_key] = version_check
        return version_check

    def should_show_update_page_after_login(self, version_check):
        if not self.can_access_update_page():
            return False
        if version_check.get("update_available"):
            return True
        return bool(version_check.get("error"))

    def normalize_local_admin_bootstrap_credentials(self, form_payload, require_password=False):
        form_has_reset_fields = any(field_name in form_payload for field_name in self.update_local_admin_reset_fields)
        if require_password and not form_has_reset_fields:
            return {}
        password = str(form_payload.get("reset_local_mysql_admin_password", "") or "")
        confirm_password = str(form_payload.get("confirm_reset_local_mysql_admin_password", "") or "")
        acknowledged = str(form_payload.get("confirm_local_mysql_admin_reset", "") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
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

        profile = self.get_session_profile() if self.is_local_admin_profile_session() else {}
        username = str(profile.get("username") or "localadmin").strip() or "localadmin"
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}", username):
            raise ValueError("Localadmin MySQL username in the current profile is not valid for setup.")
        return {
            "LOCAL_MYSQL_ADMIN_USER": username,
            "LOCAL_MYSQL_ADMIN_PASSWORD": password,
            "LOCAL_MYSQL_PROFILE_NAME": self.local_admin_profile_name,
        }
