import base64
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request

from modules.core_util import chmod_private_file, parse_iso_datetime, utc_now_iso


RUNNING_STATES = {"starting", "running", "restarting"}
SECRET_ENV_KEYS = ("LOCAL_MYSQL_ADMIN_USER", "LOCAL_MYSQL_ADMIN_PASSWORD", "LOCAL_MYSQL_PROFILE_NAME")


def append_update_log(log_file, message):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(str(message or ""))
        if not str(message or "").endswith("\n"):
            handle.write("\n")
    chmod_private_file(log_file)


def default_update_status():
    return {
        "job_id": "",
        "state": "idle",
        "step": "Ready",
        "message": "No update has been started.",
        "completion_message": "",
        "started_at": "",
        "updated_at": "",
        "finished_at": "",
        "worker_pid": None,
        "service_names": [],
        "restart_requested_at": "",
    }


def write_update_status(status_file, payload):
    status_file.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["updated_at"] = utc_now_iso()
    temp_path = status_file.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    chmod_private_file(temp_path)
    temp_path.replace(status_file)
    chmod_private_file(status_file)
    return data


def pid_is_alive(pid):
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized_pid <= 0:
        return False
    try:
        os.kill(normalized_pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_update_log_tail(log_file, max_lines):
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if max_lines > 0:
        return lines[-max_lines:]
    return lines


def load_update_status_payload(status_file):
    if not status_file.exists():
        return default_update_status()
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_update_status()
    status = default_update_status()
    status.update(payload if isinstance(payload, dict) else {})
    service_names = status.get("service_names", [])
    if not isinstance(service_names, list):
        status["service_names"] = []
    return status


def public_update_status(status):
    public_status = dict(status or {})
    public_status.pop("poll_token", None)
    return public_status


def maybe_finalize_update_status(status_file, log_file, process_started_at, status):
    normalized_status = dict(status or default_update_status())
    restart_requested_at = parse_iso_datetime(normalized_status.get("restart_requested_at"))

    if normalized_status.get("state") == "restarting" and restart_requested_at:
        if process_started_at > restart_requested_at:
            append_update_log(log_file, "DBConsole service restart completed.")
            normalized_status["state"] = "completed"
            normalized_status["step"] = "Completed"
            normalized_status["message"] = normalized_status.get("completion_message") or "Repository refresh, setup, and service restart completed."
            normalized_status["finished_at"] = utc_now_iso()
            normalized_status = write_update_status(status_file, normalized_status)
        elif (parse_iso_datetime(utc_now_iso()) - restart_requested_at).total_seconds() > 120:
            append_update_log(log_file, "DBConsole service restart did not complete within the expected time window.")
            normalized_status["state"] = "error"
            normalized_status["step"] = "Failed"
            normalized_status["message"] = "The scheduled service restart did not complete within 120 seconds."
            normalized_status["finished_at"] = utc_now_iso()
            normalized_status = write_update_status(status_file, normalized_status)

    if normalized_status.get("state") in {"starting", "running"} and normalized_status.get("worker_pid"):
        if not pid_is_alive(normalized_status.get("worker_pid")):
            append_update_log(log_file, "The update worker stopped before reporting completion.")
            normalized_status["state"] = "error"
            normalized_status["step"] = "Failed"
            normalized_status["message"] = "The update worker stopped unexpectedly. Review the log output."
            normalized_status["finished_at"] = utc_now_iso()
            normalized_status = write_update_status(status_file, normalized_status)

    return normalized_status


def get_update_status(status_file, log_file, process_started_at, max_log_lines):
    status = maybe_finalize_update_status(status_file, log_file, process_started_at, load_update_status_payload(status_file))
    log_lines = read_update_log_tail(log_file, max_log_lines)
    status["log_lines"] = log_lines
    status["log_text"] = "\n".join(log_lines)
    status["can_start"] = status.get("state") not in RUNNING_STATES
    return status


def start_update_job(
    *,
    repo_dir,
    worker_script,
    status_file,
    log_file,
    python_executable,
    service_pid,
    poll_token,
    process_started_at,
    max_log_lines,
    local_admin_password_reset=None,
):
    current_status = get_update_status(status_file, log_file, process_started_at, max_log_lines)
    if not current_status.get("can_start"):
        raise ValueError("A DBConsole update is already in progress.")
    if not worker_script.exists():
        raise ValueError(f"Update worker script was not found at {worker_script}.")

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("", encoding="utf-8")

    status_payload = write_update_status(
        status_file,
        {
            "job_id": utc_now_iso().replace(":", "").replace("-", ""),
            "state": "starting",
            "step": "Starting",
            "message": "Launching the DBConsole update worker.",
            "completion_message": "",
            "started_at": utc_now_iso(),
            "finished_at": "",
            "worker_pid": None,
            "service_names": [],
            "restart_requested_at": "",
            "poll_token": poll_token,
        },
    )
    append_update_log(log_file, "Launching the DBConsole update worker.")

    worker_env = os.environ.copy()
    worker_env["PYTHONUNBUFFERED"] = "1"
    local_admin_password_reset = dict(local_admin_password_reset or {})
    for key in SECRET_ENV_KEYS:
        value = str(local_admin_password_reset.get(key, "") or "")
        if value:
            worker_env[key] = value
    if local_admin_password_reset:
        append_update_log(
            log_file,
            "Localadmin first-time bootstrap credentials were supplied for this update. The password is not logged or saved.",
        )
    worker = subprocess.Popen(
        [
            python_executable,
            str(worker_script),
            "--repo-dir",
            str(repo_dir),
            "--status-file",
            str(status_file),
            "--log-file",
            str(log_file),
            "--service-pid",
            str(service_pid),
        ],
        cwd=str(repo_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=worker_env,
    )
    status_payload["worker_pid"] = worker.pid
    status_payload["message"] = f"Update worker started with PID {worker.pid}."
    write_update_status(status_file, status_payload)
    return get_update_status(status_file, log_file, process_started_at, max_log_lines)


def get_local_app_version(app_version_file):
    if not app_version_file.exists():
        return "-"
    try:
        payload = json.loads(app_version_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "-"
    version = str(payload.get("version", "")).strip()
    return version or "-"


def normalize_git_remote_url(remote_url):
    remote = str(remote_url or "").strip()
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote[len("git@github.com:"):]
    if remote.startswith("https://github.com/"):
        if remote.endswith(".git"):
            remote = remote[:-4]
        if remote.count("/") >= 4:
            return remote
    return ""


def infer_app_version_url(repo_dir):
    configured_url = os.environ.get("DBCONSOLE_VERSION_URL", "").strip()
    if configured_url:
        return configured_url
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    remote_url = normalize_git_remote_url(result.stdout)
    if not remote_url:
        return ""
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        branch_name = ""
    else:
        branch_name = branch_result.stdout.strip()
    branch_name = branch_name or os.environ.get("DBCONSOLE_VERSION_BRANCH", "").strip() or "main"
    owner_repo = remote_url[len("https://github.com/"):].strip("/")
    return f"https://raw.githubusercontent.com/{owner_repo}/{branch_name}/appver.json"


def normalize_repository_version_request_url(version_url):
    raw_match = re.fullmatch(
        r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/appver\.json(?:\?.*)?",
        str(version_url or "").strip(),
    )
    if raw_match:
        owner_repo, branch_name = raw_match.groups()
        return f"https://api.github.com/repos/{owner_repo}/contents/appver.json?ref={branch_name}"

    github_raw_match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/raw/([^/]+)/appver\.json(?:\?.*)?",
        str(version_url or "").strip(),
    )
    if github_raw_match:
        owner_repo, branch_name = github_raw_match.groups()
        return f"https://api.github.com/repos/{owner_repo}/contents/appver.json?ref={branch_name}"

    return version_url


def build_repository_version_ssl_context():
    ca_bundle = os.environ.get("DBCONSOLE_VERSION_CA_BUNDLE", "").strip()
    if ca_bundle:
        return ssl.create_default_context(cafile=os.path.expanduser(ca_bundle))
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def read_repository_version_payload(response_body):
    payload = json.loads(response_body.decode("utf-8"))
    if "version" in payload:
        return payload
    encoded_content = payload.get("content")
    if encoded_content:
        decoded_body = base64.b64decode(str(encoded_content).encode("utf-8"))
        return json.loads(decoded_body.decode("utf-8"))
    return payload


def fetch_repository_app_version(repo_dir, timeout=2):
    version_url = infer_app_version_url(repo_dir)
    if not version_url:
        return {
            "repo_version": "-",
            "version_url": "",
            "error": "Set DBCONSOLE_VERSION_URL to enable repository version checks.",
        }
    try:
        request_url = normalize_repository_version_request_url(version_url)
        request_object = urllib.request.Request(
            request_url,
            headers={
                "Accept": "application/vnd.github.raw+json, application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        ssl_context = build_repository_version_ssl_context() if request_url.lower().startswith("https://") else None
        with urllib.request.urlopen(request_object, timeout=timeout, context=ssl_context) as response:
            payload = read_repository_version_payload(response.read())
    except ssl.SSLError as error:
        return {
            "repo_version": "-",
            "version_url": version_url,
            "error": f"TLS certificate verification failed. Set DBCONSOLE_VERSION_CA_BUNDLE to a valid CA bundle path. Details: {error}",
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        reason = getattr(error, "reason", None)
        if isinstance(reason, ssl.SSLError):
            error_message = (
                "TLS certificate verification failed. Set DBCONSOLE_VERSION_CA_BUNDLE to a valid CA bundle path. "
                f"Details: {reason}"
            )
        else:
            error_message = str(error)
        return {
            "repo_version": "-",
            "version_url": version_url,
            "error": error_message,
        }
    repo_version = str(payload.get("version", "")).strip() or "-"
    return {
        "repo_version": repo_version,
        "version_url": version_url,
        "error": "",
    }
