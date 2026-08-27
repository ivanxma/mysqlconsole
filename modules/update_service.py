import os
import sys

from flask import session

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
        version_check_session_key,
        is_local_admin_profile_session,
        active_job_count,
        max_log_lines=400,
    ):
        self.repo_dir = repo_dir
        self.app_version_file = app_version_file
        self.worker_script = worker_script
        self.status_file = status_file
        self.log_file = log_file
        self.process_started_at = process_started_at
        self.version_check_session_key = version_check_session_key
        self.is_local_admin_profile_session = is_local_admin_profile_session
        self.active_job_count = active_job_count
        self.max_log_lines = max_log_lines

    def public_status(self, status):
        return update_util.public_update_status(status)

    def poll_token_matches(self, candidate):
        return update_util.update_poll_token_matches(self.status_file, candidate)

    def get_status(self):
        return update_util.get_update_status(
            self.status_file,
            self.log_file,
            self.process_started_at,
            self.max_log_lines,
        )

    def start_job(self):
        active_count = self.active_job_count()
        if active_count:
            raise ValueError(
                f"Auto-update is blocked while {active_count} MySQL Shell job(s) are active. "
                "Wait for completion or cancel them first."
            )
        return update_util.start_update_job(
            repo_dir=self.repo_dir,
            worker_script=self.worker_script,
            status_file=self.status_file,
            log_file=self.log_file,
            python_executable=sys.executable,
            service_pid=os.getpid(),
            process_started_at=self.process_started_at,
            max_log_lines=self.max_log_lines,
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
        if not self.is_local_admin_profile_session():
            return False
        if version_check.get("update_available"):
            return True
        return bool(version_check.get("error"))
