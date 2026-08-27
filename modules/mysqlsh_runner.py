"""DB Console owned MySQL Shell request construction.

OCI authorization is deliberately absent here: mysqlsh receives a short-lived PAR
URL created separately with DB Console's Instance Principal.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from modules.runtime_util import ensure_private_directory, get_runtime_directory


ALLOWED_FUNCTIONS = {"dump_instance", "dump_schemas", "load_dump"}
RESULT_START = "DBCONSOLE_MYSQLSH_RESULT_START"
RESULT_END = "DBCONSOLE_MYSQLSH_RESULT_END"
MYSQLSH_STATUS_TIMEOUT_SECONDS = 5


def resolve_mysqlsh_binary():
    configured = str(os.environ.get("DBCONSOLE_MYSQLSH", "")).strip()
    if configured:
        return configured
    return shutil.which("mysqlsh") or "mysqlsh"


def get_mysqlsh_status():
    binary = resolve_mysqlsh_binary()
    resolved = shutil.which(binary) if os.path.basename(binary) == binary else binary
    if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
        return {"available": False, "binary": binary, "version": "", "error": "mysqlsh was not found or is not executable; run DB Console setup."}
    try:
        result = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            env=mysqlsh_environment(),
            timeout=MYSQLSH_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "binary": resolved, "version": "", "error": "mysqlsh version check timed out."}
    except OSError as error:
        return {"available": False, "binary": resolved, "version": "", "error": f"mysqlsh is unavailable: {error}"}
    output = (result.stdout or result.stderr or "").strip()
    match = re.search(r"\b[0-9]+(?:\.[0-9]+){2}\b", output)
    version = match.group(0) if match else ""
    available = result.returncode == 0 and bool(version)
    return {
        "available": available,
        "binary": resolved,
        "version": version,
        "error": "" if available else output or "mysqlsh version could not be determined.",
    }


def mysqlsh_version_label():
    status = get_mysqlsh_status()
    if status["available"]:
        return status["version"]
    return status["error"] or "Not found"


def build_connection_options(profile, credentials):
    username = str((credentials or {}).get("username") or "").strip()
    if not username:
        raise ValueError("No active MySQL username is available for MySQL Shell.")
    if profile.get("socket_enabled"):
        socket_path = str(profile.get("socket_path") or "").strip()
        if not socket_path:
            raise ValueError("The active Unix socket profile has no socket path.")
        options = {"scheme": "mysql", "user": username, "password": str(credentials.get("password") or ""), "socket": socket_path}
    else:
        host = str(profile.get("host") or "").strip()
        if not host:
            raise ValueError("The active MySQL profile has no host.")
        options = {"scheme": "mysql", "user": username, "password": str(credentials.get("password") or ""), "host": host, "port": int(profile.get("port") or 3306)}
    database = str(profile.get("database") or "").strip()
    if database:
        options["schema"] = database
    ssl_mode = str(profile.get("ssl_mode") or "").strip()
    if ssl_mode:
        options["ssl-mode"] = ssl_mode
    for profile_key, mysqlsh_key in (("ssl_ca", "ssl-ca"), ("ssl_cert", "ssl-cert"), ("ssl_key", "ssl-key")):
        configured_path = os.path.expanduser(str(profile.get(profile_key) or "").strip())
        if configured_path:
            if not Path(configured_path).is_file():
                raise ValueError(f"The active MySQL profile references a missing {profile_key.replace('_', ' ')} file.")
            options[mysqlsh_key] = configured_path
    if profile.get("ssh_enabled"):
        ssh_host = str(profile.get("ssh_host") or "").strip()
        ssh_user = str(profile.get("ssh_user") or "").strip()
        ssh_key = os.path.expanduser(str(profile.get("ssh_key_path") or "").strip())
        if not ssh_host or not ssh_user or not ssh_key or not Path(ssh_key).is_file():
            raise ValueError("The active SSH profile requires a readable jump-host private key.")
        options["ssh"] = f"{ssh_user}@{ssh_host}:{int(profile.get('ssh_port') or 22)}"
        options["ssh-identity-file"] = ssh_key
    return options


def build_operation_request(operation, *, storage_url, schema_names=None, options=None):
    operation = str(operation or "").strip()
    if operation not in ALLOWED_FUNCTIONS:
        raise ValueError("Unsupported MySQL Shell operation.")
    url = str(storage_url or "").strip()
    if not url.startswith("https://"):
        raise ValueError("MySQL Shell requires an HTTPS Object Storage PAR URL.")
    normalized_options = dict(options or {})
    if operation == "dump_instance":
        args = [url, normalized_options]
    elif operation == "dump_schemas":
        schemas = [str(name).strip() for name in (schema_names or []) if str(name).strip()]
        if not schemas:
            raise ValueError("Choose at least one schema to dump.")
        if len(schemas) > 100 or any(len(name) > 64 for name in schemas):
            raise ValueError("Choose at most 100 schema names, each no longer than 64 characters.")
        args = [schemas, url, normalized_options]
    else:
        args = [url, normalized_options]
    return {"function_name": operation, "args": args, "kwargs": {}}


def build_execution_request(profile, credentials, operation_request):
    if operation_request.get("function_name") not in ALLOWED_FUNCTIONS:
        raise ValueError("Unsupported MySQL Shell function.")
    return {**operation_request, "connection_options": build_connection_options(profile, credentials)}


def build_command(binary, request_path):
    return [binary, "--py", "--no-wizard", "--pym", "modules.mysqlsh_python_runner", str(request_path)]


def mysqlsh_environment():
    root = Path(__file__).resolve().parent.parent
    config_home = ensure_private_directory(get_runtime_directory() / "mysqlsh" / "config")
    environment = os.environ.copy()
    pythonpath = [str(root)]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    environment["MYSQLSH_USER_CONFIG_HOME"] = str(config_home)
    environment.setdefault("TERM", "dumb")
    return environment


def redact_url(value):
    parsed = urlsplit(str(value or ""))
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/[redacted-par]", "", ""))


def operation_preview(operation_request):
    payload = dict(operation_request)
    args = list(payload.get("args") or [])
    if payload.get("function_name") == "dump_schemas" and len(args) > 1:
        args[1] = redact_url(args[1])
    elif args:
        args[0] = redact_url(args[0])
    payload["args"] = args
    return json.dumps(payload, indent=2, sort_keys=True)


def extract_result_payload(stdout_text):
    rendered = str(stdout_text or "")
    end = rendered.rfind(RESULT_END)
    if end < 0:
        return None
    start = rendered.rfind(RESULT_START, 0, end)
    if start < 0:
        return None
    raw = rendered[start + len(RESULT_START) : end].strip()
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def evaluate_execution(returncode, stdout_text, stderr_text):
    result = extract_result_payload(stdout_text)
    succeeded = int(returncode or 0) == 0 and (not isinstance(result, dict) or result.get("status") != "error")
    error = ""
    if isinstance(result, dict) and result.get("status") == "error":
        error = str(result.get("error") or "").strip()
    if not succeeded and not error:
        lines = [line.strip() for line in str(stderr_text or stdout_text or "").splitlines() if line.strip()]
        error = lines[-1] if lines else "mysqlsh exited with a non-zero status."
    return {"succeeded": succeeded, "error": error, "result": result}
