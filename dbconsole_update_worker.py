#!/usr/bin/env python3
import argparse
import grp
import json
import os
import platform
import pwd
import shlex
import shutil
import signal
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


HTTP_SERVICE = "dbconsole-http.service"
HTTPS_SERVICE = "dbconsole-https.service"
DEFAULT_ALLOWED_UPDATE_REMOTE_URL = "https://github.com/ivanxma/mysqlconsole.git"
DEFAULT_ALLOWED_UPDATE_BRANCH = "main"
ALLOWED_LOCAL_STATE_PATHS = {
    ".flask_secret_key",
    ".runtime.env",
    "etc/my.cnf",
    "object_storage.json",
    "profiles.json",
    "security_vulnerability_report.html",
    "security_vulnerability_review.html",
}
ALLOWED_LOCAL_STATE_SUFFIXES = (
    "_security_report.html",
    "_vulnerability_report.html",
)
ALLOWED_LOCAL_STATE_PREFIXES = (
    ".data/",
    ".embedded/",
    "pip-audit-report.",
    "profile_ssh_keys/",
    "security_review",
    "security_vulnerability_report",
    "vulnerability_review",
    "tls/",
)


def normalize_git_remote_url(remote_url):
    remote = str(remote_url or "").strip()
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote[len("git@github.com:"):]
    if remote.endswith(".git"):
        remote = remote[:-4]
    return remote.rstrip("/")


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class UpdateWorker:
    def __init__(self, repo_dir, status_file, log_file, service_pid=None):
        self.repo_dir = Path(repo_dir).resolve()
        self.status_file = Path(status_file).resolve()
        self.log_file = Path(log_file).resolve()
        self.service_pid = self.normalize_pid(service_pid)
        self.status = self.load_status()

    @staticmethod
    def normalize_pid(value):
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 else None

    def load_status(self):
        if not self.status_file.exists():
            return {}
        try:
            payload = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_status(self, **updates):
        self.status.update(updates)
        self.status["updated_at"] = utc_now_iso()
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.status_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(self.status, indent=2, ensure_ascii=False), encoding="utf-8")
        self.chmod_path(temp_file, 0o600)
        temp_file.replace(self.status_file)
        self.chmod_path(self.status_file, 0o600)
        return self.status

    def append_log(self, message):
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(str(message or ""))
            if not str(message or "").endswith("\n"):
                handle.write("\n")
        self.chmod_path(self.log_file, 0o600)

    @staticmethod
    def chmod_path(path, mode):
        try:
            Path(path).chmod(mode)
        except OSError:
            pass

    def log_step(self, step, message):
        self.write_status(state="running", step=step, message=message)
        self.append_log(f"[{utc_now_iso()}] {message}")

    def run_command(self, command, *, cwd=None, env=None):
        display_command = shlex.join(command)
        self.append_log(f"$ {display_command}")
        process = subprocess.Popen(
            command,
            cwd=str(cwd or self.repo_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.append_log(line.rstrip("\n"))
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"Command failed with exit code {return_code}: {display_command}")

    def run_capture(self, command, *, cwd=None):
        result = subprocess.run(
            command,
            cwd=str(cwd or self.repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error_output = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(error_output or f"Command failed: {shlex.join(command)}")
        return result.stdout

    def verify_update_source(self, branch_name):
        allowed_remote = os.environ.get("DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL", DEFAULT_ALLOWED_UPDATE_REMOTE_URL).strip()
        allowed_branch = os.environ.get("DBCONSOLE_UPDATE_ALLOWED_BRANCH", DEFAULT_ALLOWED_UPDATE_BRANCH).strip()
        origin_url = self.run_capture(["git", "remote", "get-url", "origin"], cwd=self.repo_dir).strip()

        normalized_origin = normalize_git_remote_url(origin_url)
        normalized_allowed = normalize_git_remote_url(allowed_remote)
        if normalized_allowed and normalized_origin != normalized_allowed:
            raise RuntimeError(
                "Update source mismatch. "
                f"origin is {normalized_origin or origin_url!r}, expected {normalized_allowed!r}. "
                "Set DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL only after verifying the repository source."
            )

        if allowed_branch and branch_name != allowed_branch:
            raise RuntimeError(
                f"Update branch mismatch. Current branch is {branch_name!r}, expected {allowed_branch!r}. "
                "Set DBCONSOLE_UPDATE_ALLOWED_BRANCH only after verifying the deployment branch."
            )

        self.append_log(f"Verified update source: {normalized_origin or origin_url} on branch {branch_name}.")

    def detect_os_family(self):
        if platform.system() == "Darwin":
            return "macos"

        os_release = Path("/etc/os-release")
        if not os_release.exists():
            raise RuntimeError("Unable to detect the operating system for setup.sh.")

        fields = {}
        for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip().strip('"')

        distro_id = fields.get("ID", "").lower()
        version_major = fields.get("VERSION_ID", "").split(".", 1)[0]
        if (distro_id in {"ol", "oraclelinux"}) and version_major == "8":
            return "ol8"
        if (distro_id in {"ol", "oraclelinux"}) and version_major == "9":
            return "ol9"
        if distro_id == "ubuntu":
            return "ubuntu"
        raise RuntimeError(f"Unsupported operating system for setup.sh: {distro_id or 'unknown'} {version_major or ''}".strip())

    def load_runtime_env(self):
        runtime_env = {}
        runtime_env_file = self.repo_dir / ".runtime.env"
        if not runtime_env_file.exists():
            return runtime_env

        for raw_line in runtime_env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            runtime_env[key.strip()] = value.strip()
        return runtime_env

    def systemctl_state(self, service_name, command):
        if not shutil.which("systemctl"):
            return False
        result = subprocess.run(
            ["systemctl", command, service_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def detect_deploy_mode_and_services(self, runtime_env):
        active_services = []
        http_enabled = self.systemctl_state(HTTP_SERVICE, "is-enabled") or self.systemctl_state(HTTP_SERVICE, "is-active")
        https_enabled = self.systemctl_state(HTTPS_SERVICE, "is-enabled") or self.systemctl_state(HTTPS_SERVICE, "is-active")
        if http_enabled:
            active_services.append(HTTP_SERVICE)
        if https_enabled:
            active_services.append(HTTPS_SERVICE)

        if http_enabled and https_enabled:
            return "both", active_services
        if https_enabled:
            return "https", active_services
        if http_enabled:
            return "http", active_services

        env_mode = str(runtime_env.get("DEPLOY_MODE", "")).strip().lower()
        if env_mode in {"http", "https", "both", "none"}:
            if env_mode == "http":
                return env_mode, [HTTP_SERVICE] if shutil.which("systemctl") else []
            if env_mode == "https":
                return env_mode, [HTTPS_SERVICE] if shutil.which("systemctl") else []
            if env_mode == "both":
                services = [service for service in (HTTP_SERVICE, HTTPS_SERVICE) if shutil.which("systemctl")]
                return env_mode, services
            return env_mode, []

        if runtime_env.get("SSL_CERT_FILE") and runtime_env.get("SSL_KEY_FILE"):
            return "https", [HTTPS_SERVICE] if shutil.which("systemctl") else []
        return "http", [HTTP_SERVICE] if shutil.which("systemctl") else []

    def ensure_clean_worktree(self):
        status_lines = [
            line
            for line in self.run_capture(["git", "status", "--porcelain"], cwd=self.repo_dir).splitlines()
            if line.strip()
        ]
        blocking_lines = []
        ignored_lines = []
        for line in status_lines:
            path = self.status_line_path(line)
            if self.is_allowed_local_state_path(path):
                ignored_lines.append(line)
            else:
                blocking_lines.append(line)
        if ignored_lines:
            self.append_log("Ignoring local DBConsole state files during worktree validation:")
            for line in ignored_lines:
                self.append_log(line)
        if blocking_lines:
            for line in blocking_lines:
                self.append_log(line)
            raise RuntimeError("Repository has local changes. Commit or stash them before running Update DBConsole.")

    def path_is_tracked(self, path):
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=str(self.repo_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def preserve_allowed_local_state_for_pull(self):
        status_lines = [
            line
            for line in self.run_capture(["git", "status", "--porcelain"], cwd=self.repo_dir).splitlines()
            if line.strip()
        ]
        paths = []
        for line in status_lines:
            path = self.status_line_path(line)
            if self.is_allowed_local_state_path(path) and self.path_is_tracked(path):
                paths.append(path)

        if not paths:
            return None

        backup_dir = Path(tempfile.mkdtemp(prefix="dbconsole-update-local-state-"))
        preserved = []
        for path in sorted(set(paths)):
            source = self.repo_dir / path
            backup_path = backup_dir / path
            if source.is_dir():
                shutil.copytree(source, backup_path)
                existed = True
            elif source.exists():
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup_path)
                existed = True
            else:
                existed = False

            preserved.append({"path": path, "backup_path": backup_path, "existed": existed})
            self.append_log(f"Preserving local DBConsole state before pull: {path}")
            self.run_command(["git", "restore", "--staged", "--worktree", "--", path], cwd=self.repo_dir)

        return {"backup_dir": backup_dir, "items": preserved}

    def restore_allowed_local_state_after_pull(self, preserved_state):
        if not preserved_state:
            return

        for item in preserved_state["items"]:
            path = item["path"]
            target = self.repo_dir / path
            backup_path = item["backup_path"]
            if not item["existed"]:
                continue
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if backup_path.is_dir():
                shutil.copytree(backup_path, target)
            else:
                shutil.copy2(backup_path, target)
            self.append_log(f"Restored local DBConsole state after pull: {path}")

        shutil.rmtree(preserved_state["backup_dir"], ignore_errors=True)

    @staticmethod
    def status_line_path(line):
        path = str(line or "")[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        return path

    @staticmethod
    def is_allowed_local_state_path(path):
        normalized = str(path or "").strip()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return (
            normalized in ALLOWED_LOCAL_STATE_PATHS
            or any(normalized.startswith(prefix) for prefix in ALLOWED_LOCAL_STATE_PREFIXES)
            or any(normalized.endswith(suffix) for suffix in ALLOWED_LOCAL_STATE_SUFFIXES)
        )

    def harden_local_deployment_state(self):
        file_modes = {
            ".flask_secret_key": 0o600,
            ".runtime.env": 0o600,
            "object_storage.json": 0o600,
            "profiles.json": 0o600,
        }
        for relative_path, mode in file_modes.items():
            path = self.repo_dir / relative_path
            if path.is_file():
                self.chmod_path(path, mode)
                self.append_log(f"Hardened local file permissions: {relative_path} -> {oct(mode)}")

        profile_key_dir = self.repo_dir / "profile_ssh_keys"
        if profile_key_dir.is_dir():
            self.chmod_path(profile_key_dir, 0o700)
            for path in profile_key_dir.rglob("*"):
                if path.is_dir():
                    self.chmod_path(path, 0o700)
                elif path.is_file():
                    self.chmod_path(path, 0o600)
            self.append_log("Hardened uploaded SSH private key storage permissions.")

        tls_dir = self.repo_dir / "tls"
        if tls_dir.is_dir():
            self.chmod_path(tls_dir, 0o700)
            private_suffixes = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}
            for path in tls_dir.rglob("*"):
                if path.is_dir():
                    self.chmod_path(path, 0o700)
                elif path.is_file():
                    mode = 0o600 if path.suffix.lower() in private_suffixes else 0o644
                    self.chmod_path(path, mode)
            self.append_log("Hardened TLS directory permissions.")

        mysql_data_dir = self.repo_dir / ".data"
        if mysql_data_dir.is_dir():
            self.chmod_path(mysql_data_dir, 0o700)
            self.append_log("Hardened local MySQL data directory permissions.")

        mysql_config = self.repo_dir / "etc" / "my.cnf"
        if mysql_config.is_file():
            self.chmod_path(mysql_config, 0o600)
            self.append_log("Hardened local MySQL config file permissions.")

    def current_user_group(self):
        try:
            user_name = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            user_name = ""
        try:
            group_name = grp.getgrgid(os.getgid()).gr_name
        except KeyError:
            group_name = ""
        return user_name, group_name

    def run_setup(self, os_family, deploy_mode, runtime_env, *, skip_privileged_setup=False):
        setup_env = os.environ.copy()
        setup_env["RUNTIME_ENV_FILE"] = str(self.repo_dir / ".runtime.env")
        user_name, group_name = self.current_user_group()
        if user_name:
            setup_env["SERVICE_USER"] = user_name
        if group_name:
            setup_env["SERVICE_GROUP"] = group_name
        if skip_privileged_setup:
            setup_env["SKIP_PRIVILEGED_SETUP"] = "1"

        host_value = runtime_env.get("HOST", "")
        http_port = runtime_env.get("DEFAULT_HTTP_PORT", "")
        https_port = runtime_env.get("DEFAULT_HTTPS_PORT", "")
        ssl_cert_file = runtime_env.get("SSL_CERT_FILE", "")
        ssl_key_file = runtime_env.get("SSL_KEY_FILE", "")
        passthrough_env_keys = (
            "DBCONSOLE_MYSQLSH",
            "DBCONSOLE_PYTHON_BIN",
            "DBCONSOLE_PYTHON_MIN_VERSION",
            "LOCAL_MYSQL_AUTOSTART",
            "LOCAL_MYSQL_SOCKET",
            "LOCAL_MYSQL_SERVICE",
            "LOCAL_MYSQL_BASEDIR",
            "LOCAL_MYSQL_DATADIR",
            "LOCAL_MYSQL_PROFILE_NAME",
            "LOCAL_MYSQL_ADMIN_USER",
            "LOCAL_MYSQL_ADMIN_PASSWORD",
            "LOCAL_MYSQL_INIT_FILE_PROVISIONING",
            "LOCAL_MYSQL_RESET_UNKNOWN_ROOT",
            "EMBEDDED_MYSQL_SHELL_DIR",
            "EMBEDDED_MYSQL_SERVER_DIR",
            "MYSQL_SERVER_VERSION",
            "MYSQL_SERVER_DOWNLOAD_PAGE",
            "MYSQL_SERVER_VENDOR_DOWNLOAD_BASE",
            "MYSQL_SERVER_EMBEDDED_URL",
            "MYSQL_SERVER_EMBEDDED_PACKAGE",
            "MYSQL_SERVER_MACOS_PACKAGE_TAG",
            "DBCONSOLE_DEPENDENCY_AUDIT",
            "DBCONSOLE_DEPENDENCY_AUDIT_STRICT",
            "DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL",
            "DBCONSOLE_UPDATE_ALLOWED_BRANCH",
        )

        if host_value:
            setup_env["HOST"] = host_value
        if http_port:
            setup_env["HTTP_PORT"] = http_port
        if https_port:
            setup_env["HTTPS_PORT"] = https_port
        if ssl_cert_file:
            setup_env["SSL_CERT_FILE"] = ssl_cert_file
        if ssl_key_file:
            setup_env["SSL_KEY_FILE"] = ssl_key_file
        for key in passthrough_env_keys:
            value = runtime_env.get(key, "") or os.environ.get(key, "")
            if value:
                setup_env[key] = value

        command = ["/bin/bash", str(self.repo_dir / "setup.sh"), os_family, deploy_mode]
        if http_port:
            command.extend(["--http-port", http_port])
        if https_port:
            command.extend(["--https-port", https_port])
        self.run_command(command, cwd=self.repo_dir, env=setup_env)

    def passwordless_sudo_available(self):
        if os.geteuid() == 0:
            return True, ""
        if not shutil.which("sudo"):
            return False, "sudo is not installed."
        true_command = "/bin/true" if Path("/bin/true").exists() else (shutil.which("true") or "true")
        result = subprocess.run(
            ["sudo", "-n", true_command],
            cwd=str(self.repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True, ""
        output = (result.stderr or result.stdout or "").strip()
        return False, output or "sudo -n true failed."

    def begin_restart_wait(self, service_names, completion_message):
        restart_requested_at = utc_now_iso()
        self.write_status(
            state="restarting",
            step="Restarting",
            message=f"Waiting for {' and '.join(service_names)} to restart.",
            restart_requested_at=restart_requested_at,
            service_names=service_names,
            completion_message=completion_message,
        )
        return restart_requested_at

    def schedule_service_restart(self, service_names, completion_message):
        if not service_names:
            return
        if not shutil.which("systemctl"):
            raise RuntimeError("systemctl is required to restart the DBConsole service.")

        privilege_prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]

        restart_requested_at = self.begin_restart_wait(service_names, completion_message)
        self.append_log(f"[{restart_requested_at}] Scheduling service restart for {', '.join(service_names)}.")

        if shutil.which("systemd-run"):
            transient_unit_name = f"dbconsole-self-update-{os.getpid()}"
            restart_command = "sleep 2 && /bin/systemctl restart " + " ".join(
                shlex.quote(service_name) for service_name in service_names
            )
            self.run_command(
                privilege_prefix
                + [
                    "systemd-run",
                    "--unit",
                    transient_unit_name,
                    "--collect",
                    "/bin/sh",
                    "-lc",
                    restart_command,
                ],
                cwd=self.repo_dir,
            )
            self.append_log(f"Restart scheduled in transient unit {transient_unit_name}.")
            return

        self.run_command(privilege_prefix + ["systemctl", "restart", *service_names], cwd=self.repo_dir)
        completed_at = utc_now_iso()
        self.write_status(
            state="completed",
            step="Completed",
            message=completion_message,
            completion_message=completion_message,
            finished_at=completed_at,
        )
        self.append_log(f"[{completed_at}] Service restart completed.")

    def schedule_self_restart(self, service_names, completion_message):
        if not service_names:
            raise RuntimeError("Unable to restart DBConsole automatically because no active systemd service was detected.")
        if not self.service_pid:
            raise RuntimeError("Unable to restart DBConsole automatically because the running service PID is unknown.")

        restart_requested_at = self.begin_restart_wait(service_names, completion_message)
        self.append_log(
            f"[{restart_requested_at}] Scheduling service self-restart by terminating PID {self.service_pid}."
        )
        subprocess.Popen(
            ["/bin/sh", "-lc", f"sleep 2 && kill -{signal.SIGKILL.value} {self.service_pid}"],
            cwd=str(self.repo_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        self.append_log(
            "Restart will be triggered by sending SIGKILL to the current DBConsole service process so systemd restarts it."
        )

    def run(self):
        self.write_status(
            state="running",
            step="Starting",
            message="DBConsole update worker is running.",
            started_at=self.status.get("started_at") or utc_now_iso(),
            finished_at="",
            worker_pid=os.getpid(),
        )
        self.append_log(f"[{utc_now_iso()}] DBConsole update worker started.")

        os_family = self.detect_os_family()
        runtime_env = self.load_runtime_env()
        deploy_mode, service_names = self.detect_deploy_mode_and_services(runtime_env)
        self.write_status(service_names=service_names)
        self.log_step("Inspecting", f"Detected OS family `{os_family}` with deploy mode `{deploy_mode}`.")

        self.log_step("Hardening deployment", "Repairing local deployment file permissions before repository validation.")
        self.harden_local_deployment_state()

        self.log_step("Checking repository", "Validating the git worktree.")
        self.ensure_clean_worktree()

        branch_name = self.run_capture(["git", "branch", "--show-current"], cwd=self.repo_dir).strip() or "detached"
        self.verify_update_source(branch_name)
        self.append_log(f"Updating branch {branch_name}.")

        self.log_step("Pulling repository", "Fetching the latest repository changes.")
        self.run_command(["git", "fetch", "--all", "--prune"], cwd=self.repo_dir)
        preserved_state = self.preserve_allowed_local_state_for_pull()
        try:
            self.run_command(["git", "pull", "--ff-only"], cwd=self.repo_dir)
        except Exception:
            self.restore_allowed_local_state_after_pull(preserved_state)
            self.harden_local_deployment_state()
            raise
        self.restore_allowed_local_state_after_pull(preserved_state)
        self.harden_local_deployment_state()

        full_completion_message = "Repository refresh, setup, and service restart completed."
        limited_completion_message = (
            "Repository refresh, Python dependencies, and service restart completed. "
            "Privileged setup changes, including the MySQL Shell Innovation package upgrade, were skipped because passwordless sudo was unavailable from the running service."
        )
        sudo_ready, sudo_error = self.passwordless_sudo_available()

        if sudo_ready:
            self.log_step(
                "Running setup",
                "Rerunning setup.sh to refresh dependencies, upgrade MySQL Shell Innovation, and update service wiring.",
            )
            self.run_setup(os_family, deploy_mode, runtime_env)
        else:
            self.log_step(
                "Running setup",
                "Passwordless sudo is unavailable from the running DBConsole service. "
                "Refreshing the repository and Python environment in unprivileged mode.",
            )
            if sudo_error:
                self.append_log(f"Passwordless sudo check failed: {sudo_error}")
            self.append_log(
                "setup.sh will skip privileged steps such as Linux package installation, firewall changes, and systemd unit rewrites."
            )
            self.append_log(
                "Re-run ./setup.sh from an SSH shell if you need the MySQL Shell Innovation package upgrade, firewall changes, or refreshed systemd units."
            )
            self.run_setup(os_family, deploy_mode, runtime_env, skip_privileged_setup=True)
        self.harden_local_deployment_state()

        if service_names:
            if sudo_ready:
                self.schedule_service_restart(service_names, full_completion_message)
            else:
                self.schedule_self_restart(service_names, limited_completion_message)
            return

        completion_time = utc_now_iso()
        self.write_status(
            state="completed",
            step="Completed",
            message=(
                "Repository refresh and setup completed. No systemd service restart was required."
                if sudo_ready
                else "Repository refresh and Python dependencies completed. Restart DBConsole manually to load the new code."
            ),
            completion_message="",
            finished_at=completion_time,
            restart_requested_at="",
            service_names=[],
        )
        self.append_log(f"[{completion_time}] Update completed without a service restart.")


def main():
    parser = argparse.ArgumentParser(description="Update DBConsole from the repository and rerun setup.")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--service-pid")
    args = parser.parse_args()

    worker = UpdateWorker(args.repo_dir, args.status_file, args.log_file, service_pid=args.service_pid)
    try:
        worker.run()
    except Exception as error:
        failed_at = utc_now_iso()
        worker.append_log(f"[{failed_at}] ERROR: {error}")
        worker.write_status(
            state="error",
            step="Failed",
            message=str(error),
            finished_at=failed_at,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
