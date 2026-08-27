"""Private, owner-scoped background job management for DB Console mysqlsh work."""
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.mysqlsh_runner import build_execution_request, get_mysqlsh_status, operation_preview
from modules import oci_util
from modules.mysqlsh_par_store import remove_par_by_oci_id
from modules.runtime_util import (
    atomic_write_private_text,
    ensure_private_directory,
    ensure_private_regular_file,
    get_runtime_directory,
    get_state_directory,
)


FINAL_STATUSES = {"succeeded", "failed", "canceled"}
ACTIVE_STATUSES = {"submitted", "running", "finalizing", "cancel_requested"}
MAX_ACTIVE_JOBS_GLOBAL = 4
MAX_ACTIVE_JOBS_PER_OWNER = 2
MAX_RETAINED_JOBS_PER_OWNER = 100
MAX_TERMINAL_JOBS_GLOBAL = 500
TERMINAL_JOB_RETENTION_DAYS = 30
CANCEL_GRACE_SECONDS = 5


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_datetime(value):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def par_has_expired(job):
    expires_at = _parse_datetime((job or {}).get("par_expires_at"))
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def job_storage_target(job):
    payload = job or {}
    return {
        "profile_name": str(payload.get("storage_profile_name") or ""),
        "region": str(payload.get("storage_region") or ""),
        "namespace": str(payload.get("storage_namespace") or ""),
        "bucket_name": str(payload.get("storage_bucket_name") or ""),
        "bucket_prefix": str(payload.get("storage_bucket_prefix") or ""),
    }


def job_root():
    return ensure_private_directory(get_state_directory() / "mysqlsh" / "jobs")


def request_root():
    return ensure_private_directory(get_runtime_directory() / "mysqlsh" / "requests")


def build_owner_id(profile_name, username):
    identity = f"{str(profile_name or '').strip().lower()}\0{str(username or '').strip().lower()}"
    if identity == "\0":
        return ""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _valid_id(job_id):
    return bool(re.fullmatch(r"[a-f0-9]{32}", str(job_id or "")))


def _job_path(job_id):
    if not _valid_id(job_id):
        raise ValueError("MySQL Shell job identifier is invalid.")
    return job_root() / str(job_id)


def job_directory(job_id):
    return ensure_private_directory(_job_path(job_id))


def _metadata_path(job_id):
    return _job_path(job_id) / "job.json"


@contextmanager
def _file_lock(path):
    lock_path = Path(path)
    ensure_private_directory(lock_path.parent)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_metadata(path):
    if not path.exists():
        return None
    ensure_private_regular_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_job(job_id):
    return _read_metadata(_metadata_path(job_id))


def _write_job(job_id, payload):
    atomic_write_private_text(_metadata_path(job_id), json.dumps(payload, indent=2, sort_keys=True))
    return payload


def save_job(job_id, payload):
    directory = job_directory(job_id)
    with _file_lock(directory / ".job.lock"):
        return _write_job(job_id, payload)


def update_job(job_id, **changes):
    directory = _job_path(job_id)
    if not directory.is_dir():
        return None
    with _file_lock(directory / ".job.lock"):
        payload = _read_metadata(directory / "job.json")
        if payload is None:
            return None
        payload.update(changes)
        return _write_job(job_id, payload)


def _owns(payload, owner_session_id, owner_profile_name):
    return (
        bool(payload)
        and (payload.get("owner_id") or payload.get("owner_session_id")) == str(owner_session_id or "")
        and payload.get("owner_profile_name") == str(owner_profile_name or "")
    )


def _delete_secret_request(payload):
    raw_path = str((payload or {}).get("request_path") or "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    allowed_parents = {request_root().resolve(), _job_path(payload.get("job_id")).resolve()}
    resolved_parent = path.parent.resolve(strict=False)
    if resolved_parent not in allowed_parents:
        raise ValueError("Refusing to delete a MySQL Shell request outside its private job paths.")
    if path.is_symlink():
        raise ValueError("Refusing to delete a symlinked MySQL Shell request.")
    path.unlink(missing_ok=True)


def _submitted_without_worker_is_stale(payload):
    if payload.get("status") != "submitted" or payload.get("worker_pid"):
        return False
    submitted_at = _parse_datetime(payload.get("submitted_at"))
    if submitted_at is None:
        return True
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - submitted_at > timedelta(seconds=30)


def _all_jobs():
    rows = []
    for directory in job_root().iterdir():
        if not directory.is_dir() or not _valid_id(directory.name):
            continue
        payload = _read_metadata(directory / "job.json")
        if payload:
            if payload.get("status") in ACTIVE_STATUSES:
                process_ids = [payload.get("worker_pid"), payload.get("mysqlsh_pid")]
                known_ids = [pid for pid in process_ids if pid]
                if (known_ids and not any(_pid_alive(pid) for pid in known_ids)) or _submitted_without_worker_is_stale(payload):
                    try:
                        _delete_secret_request(payload)
                    except Exception:
                        pass
                    payload = update_job(
                        payload["job_id"],
                        status="failed",
                        finished_at=_now(),
                        error="MySQL Shell worker is no longer running.",
                    ) or payload
                    try:
                        finalize_job_par(payload["job_id"])
                    except Exception:
                        payload = load_job(payload["job_id"]) or payload
            rows.append(payload)
    return _purge_terminal_jobs(rows)


def _purge_terminal_jobs(rows):
    now = datetime.now(timezone.utc)
    removable = []
    for row in rows:
        if row.get("status") not in FINAL_STATUSES:
            continue
        if row.get("par_id") and not row.get("par_revoked_at") and not row.get("par_expired_at"):
            if par_has_expired(row):
                row = record_par_expired(row["job_id"]) or row
            else:
                continue
        finished_at = _parse_datetime(row.get("finished_at")) or _parse_datetime(row.get("submitted_at"))
        if finished_at and finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        removable.append((finished_at or now, row))
    removable.sort(key=lambda item: item[0], reverse=True)
    retained_ids = {row["job_id"] for _, row in removable[:MAX_TERMINAL_JOBS_GLOBAL]}
    cutoff = now - timedelta(days=TERMINAL_JOB_RETENTION_DAYS)
    removed_ids = set()
    for finished_at, row in removable:
        if row["job_id"] in retained_ids and finished_at >= cutoff:
            continue
        directory = _job_path(row["job_id"])
        if directory.is_symlink():
            continue
        try:
            _delete_secret_request(row)
        except (OSError, ValueError):
            continue
        shutil.rmtree(directory)
        removed_ids.add(row["job_id"])
    return [row for row in rows if row.get("job_id") not in removed_ids]


def reconcile_jobs():
    """Reconcile dead workers and remove stale credential requests at process startup."""
    return _all_jobs()


def active_job_count():
    return sum(row.get("status") in ACTIVE_STATUSES for row in _all_jobs())


def _enforce_submission_limits(owner_session_id, owner_profile_name):
    rows = _all_jobs()
    owner_rows = [row for row in rows if _owns(row, owner_session_id, owner_profile_name)]
    if sum(row.get("status") in ACTIVE_STATUSES for row in rows) >= MAX_ACTIVE_JOBS_GLOBAL:
        raise RuntimeError("The global MySQL Shell active-job limit has been reached.")
    if sum(row.get("status") in ACTIVE_STATUSES for row in owner_rows) >= MAX_ACTIVE_JOBS_PER_OWNER:
        raise RuntimeError("Your MySQL Shell active-job limit has been reached.")
    if sum(not row.get("cleaned_at") for row in owner_rows) >= MAX_RETAINED_JOBS_PER_OWNER:
        raise RuntimeError("Clean up older MySQL Shell jobs before submitting another job.")


def submit_job(
    profile,
    credentials,
    operation_request,
    *,
    owner_session_id,
    storage_target,
    par,
    operation_label,
    owner_id=None,
    job_id=None,
):
    if not owner_session_id:
        raise ValueError("No active DB Console session is available.")
    status = get_mysqlsh_status()
    if not status["available"]:
        raise RuntimeError(status["error"])
    job_id = str(job_id or uuid.uuid4().hex)
    if not _valid_id(job_id):
        raise ValueError("MySQL Shell job identifier is invalid.")

    with _file_lock(job_root() / ".submit.lock"):
        durable_owner_id = str(owner_id or owner_session_id or "")
        _enforce_submission_limits(durable_owner_id, str(profile.get("name") or ""))
        directory = job_directory(job_id)
        if (directory / "job.json").exists():
            raise ValueError("MySQL Shell job identifier already exists.")
        operation_request = deepcopy(operation_request)
        if operation_request.get("function_name") == "load_dump":
            args = list(operation_request.get("args") or [])
            if len(args) >= 2 and isinstance(args[-1], dict):
                args[-1].setdefault("progressFile", str(directory / "load-progress.json"))
                operation_request["args"] = args
        execution_request = build_execution_request(profile, credentials, operation_request)
        request_path = request_root() / f"{job_id}.json"
        atomic_write_private_text(request_path, json.dumps(execution_request))
        metadata = {
            "job_id": job_id,
            "status": "submitted",
            "operation": operation_request["function_name"],
            "operation_label": operation_label,
            "owner_id": durable_owner_id,
            "submission_session_id": str(owner_session_id),
            "owner_profile_name": str(profile.get("name") or ""),
            "storage_profile_name": str(storage_target.get("profile_name") or ""),
            "storage_region": str(storage_target.get("region") or ""),
            "storage_namespace": str(storage_target.get("namespace") or ""),
            "storage_bucket_name": str(storage_target.get("bucket_name") or ""),
            "storage_bucket_prefix": str(storage_target.get("bucket_prefix") or ""),
            "storage_prefix": str(par.get("prefix") or ""),
            "par_id": str(par.get("id") or ""),
            "par_expires_at": str(par.get("expires_at") or ""),
            "par_revoked_at": "",
            "par_expired_at": "",
            "par_delete_after_use": bool(par.get("delete_after_use")),
            "par_registry_entry_id": str(par.get("registry_entry_id") or ""),
            "par_registry_path": str(par.get("registry_path") or ""),
            "cleanup_status": "pending" if par.get("delete_after_use") else "retained_until_expiry",
            "cleanup_error": "",
            "submitted_at": _now(),
            "started_at": "",
            "finished_at": "",
            "worker_pid": None,
            "mysqlsh_pid": None,
            "returncode": None,
            "error": "",
            "last_progress": "Waiting for MySQL Shell worker to start.",
            "progress_updated_at": _now(),
            "progress_percent": None,
            "request_path": str(request_path),
            "stdout_path": str(directory / "stdout.log"),
            "stderr_path": str(directory / "stderr.log"),
            "preview": operation_preview(operation_request),
        }
        _write_job(job_id, metadata)
        try:
            worker = subprocess.Popen(
                [sys.executable, "-m", "modules.mysqlsh_job_worker", job_id],
                cwd=str(Path(__file__).resolve().parent.parent),
                start_new_session=True,
            )
        except Exception as error:
            request_path.unlink(missing_ok=True)
            _write_job(job_id, {**metadata, "status": "failed", "finished_at": _now(), "error": str(error)})
            raise
        update_job(job_id, worker_pid=worker.pid)
    return load_job(job_id)


def job_snapshot(job_id, *, owner_session_id, owner_profile_name, include_all=False):
    try:
        payload = load_job(job_id)
    except ValueError:
        return None
    if not include_all and not _owns(payload, owner_session_id, owner_profile_name):
        return None
    return _with_display_progress(payload)


def list_jobs(*, owner_session_id, owner_profile_name, limit=50, include_all=False):
    rows = [row for row in _all_jobs() if include_all or _owns(row, owner_session_id, owner_profile_name)]
    return [_with_display_progress(row) for row in sorted(rows, key=lambda row: row.get("submitted_at", ""), reverse=True)[:limit]]


def _with_display_progress(payload):
    row = dict(payload or {})
    value = row.get("progress_percent")
    if isinstance(value, bool):
        value = None
    try:
        value = int(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    row["progress_percent"] = min(100, max(0, value)) if value is not None else (100 if row.get("status") == "succeeded" else None)
    return row


def par_is_in_use(registry_entry_id):
    lookup = str(registry_entry_id or "").strip()
    return bool(
        lookup
        and any(
            row.get("par_registry_entry_id") == lookup and row.get("status") in ACTIVE_STATUSES
            for row in _all_jobs()
        )
    )


def read_job_log(job, name, max_chars=48000):
    path = Path(str(job.get(f"{name}_path") or ""))
    try:
        directory = _job_path(job["job_id"])
    except ValueError:
        return ""
    if not path.exists() or path.parent != directory:
        return ""
    ensure_private_regular_file(path)
    return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]


def _pid_alive(pid):
    try:
        process_id = int(pid)
        os.kill(process_id, 0)
    except (OSError, TypeError, ValueError):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(process_id), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # The process exists; inability to inspect its state is not enough to
        # declare it stopped or to risk reusing its PID.
        return True
    state = result.stdout.strip().upper()
    return result.returncode == 0 and bool(state) and not state.startswith("Z")


def _pid_matches(pid, required_fragment):
    if not _pid_alive(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
        return False
    return result.returncode == 0 and required_fragment in result.stdout


def cancel_job(job_id, *, owner_session_id, owner_profile_name, include_all=False):
    job = job_snapshot(
        job_id,
        owner_session_id=owner_session_id,
        owner_profile_name=owner_profile_name,
        include_all=include_all,
    )
    if job is None:
        raise ValueError("MySQL Shell job was not found.")
    if job.get("status") in FINAL_STATUSES:
        raise ValueError("MySQL Shell job is already complete.")
    update_job(job_id, status="cancel_requested")
    pid = job.get("mysqlsh_pid") or job.get("worker_pid")
    fragment = "mysqlsh" if job.get("mysqlsh_pid") else "modules.mysqlsh_job_worker"
    if pid and _pid_alive(pid):
        if not _pid_matches(pid, fragment):
            update_job(job_id, status=job.get("status", "running"))
            raise RuntimeError("Refusing to signal a process that does not match this MySQL Shell job.")
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except OSError as error:
            raise RuntimeError(f"Unable to stop the MySQL Shell process: {error}") from error
        deadline = time.monotonic() + CANCEL_GRACE_SECONDS
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _pid_alive(pid):
            os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
            time.sleep(0.05)
        if _pid_alive(pid):
            raise RuntimeError("MySQL Shell process did not stop after cancellation.")
    _delete_secret_request(job)
    canceled = update_job(job_id, status="canceled", finished_at=_now(), error="Canceled by user.")
    try:
        return finalize_job_par(job_id) or canceled
    except Exception:
        return load_job(job_id) or canceled


def record_cleanup_failure(job_id, error):
    return update_job(job_id, cleanup_status="failed", cleanup_error=str(error or "Cleanup failed."))


def record_par_revoked(job_id):
    return update_job(job_id, par_revoked_at=_now(), cleanup_status="revoked", cleanup_error="")


def record_par_expired(job_id):
    return update_job(job_id, par_expired_at=_now(), cleanup_status="expired", cleanup_error="")


def finalize_job_par(job_id):
    """Apply the job's immutable terminal PAR retention policy."""
    job = load_job(job_id)
    if not job or not job.get("par_id") or job.get("par_revoked_at") or job.get("par_expired_at"):
        return job
    if job.get("status") not in FINAL_STATUSES | {"finalizing"}:
        return job
    # Jobs created before the retention option existed keep the safer legacy
    # behavior: revoke before local cleanup. Only an explicit false retains it.
    if job.get("par_delete_after_use") is False:
        if par_has_expired(job):
            return record_par_expired(job_id)
        return update_job(job_id, cleanup_status="retained_until_expiry", cleanup_error="")
    target = job_storage_target(job)
    missing = [key for key in ("region", "namespace", "bucket_name") if not target.get(key)]
    if missing:
        error = "Stored Object Storage target is missing: " + ", ".join(missing)
        record_cleanup_failure(job_id, error)
        raise RuntimeError(error)
    try:
        oci_util.revoke_preauthenticated_request(
            target,
            namespace=target["namespace"],
            bucket_name=target["bucket_name"],
            par_id=job["par_id"],
        )
    except Exception as error:
        record_cleanup_failure(job_id, error)
        raise
    registry_path = str(job.get("par_registry_path") or "").strip()
    if registry_path:
        try:
            remove_par_by_oci_id(registry_path, job["par_id"])
        except Exception as error:
            record_cleanup_failure(job_id, f"PAR was revoked but its registry entry could not be removed: {error}")
            raise
    return record_par_revoked(job_id)


def cleanup_job(job_id, *, owner_session_id, owner_profile_name, include_all=False):
    job = job_snapshot(
        job_id,
        owner_session_id=owner_session_id,
        owner_profile_name=owner_profile_name,
        include_all=include_all,
    )
    if job is None:
        raise ValueError("MySQL Shell job was not found.")
    if job.get("status") not in FINAL_STATUSES:
        raise ValueError("Only completed MySQL Shell jobs may be cleaned up.")
    if job.get("par_id") and not job.get("par_revoked_at") and not job.get("par_expired_at"):
        if job.get("par_delete_after_use") is False and par_has_expired(job):
            job = record_par_expired(job_id) or job
        else:
            raise RuntimeError("The job's scoped PAR must be revoked or expired before removing local artifacts.")
    directory = _job_path(job_id)
    _delete_secret_request(job)
    for name in ("stdout.log", "stderr.log", "stdout.raw", "stderr.raw", "load-progress.json"):
        path = directory / name
        if path.is_symlink():
            raise ValueError(f"Refusing to remove symlinked job artifact: {name}")
        path.unlink(missing_ok=True)
    return update_job(job_id, cleaned_at=_now(), cleanup_status="completed", cleanup_error="")
