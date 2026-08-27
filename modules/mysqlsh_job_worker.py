"""Worker process for a single DB Console MySQL Shell job."""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from modules.mysqlsh_jobs import FINAL_STATUSES, finalize_job_par, load_job, update_job
from modules.mysqlsh_runner import build_command, evaluate_execution, get_mysqlsh_status, mysqlsh_environment
from modules.runtime_util import append_private_text, atomic_write_private_text, ensure_private_regular_file


MAX_LOG_BYTES = 1024 * 1024
MAX_LIVE_PROGRESS_CHARS = 600
PROGRESS_PERCENT_PATTERN = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*%")
JOB_TIMEOUT_SECONDS = 24 * 60 * 60
SUBMISSION_HANDSHAKE_SECONDS = 5


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _request_secrets(request_path):
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    connection = payload.get("connection_options", {})
    values = [str(connection.get("password") or "")]
    for item in payload.get("args", []):
        if isinstance(item, str) and item.startswith(("https://", "http://")):
            values.append(item)
    return [value for value in values if value]


def _redact(text, secrets):
    rendered = str(text or "")
    for value in secrets:
        rendered = rendered.replace(value, "[redacted]")
    return re.sub(r"https?://[^\s\"'<>]+", "[redacted-url]", rendered)


def _publish_live_output(job_id, log_path, payload, secrets):
    rendered = _redact(payload.decode("utf-8", errors="replace"), secrets)
    if not rendered:
        return
    append_private_text(log_path, rendered)
    if Path(log_path).stat().st_size > MAX_LOG_BYTES:
        bounded = Path(log_path).read_text(encoding="utf-8", errors="replace")[-MAX_LOG_BYTES:]
        atomic_write_private_text(log_path, bounded)
    summary = " ".join(rendered.split())[-MAX_LIVE_PROGRESS_CHARS:]
    if summary:
        changes = {"last_progress": summary, "progress_updated_at": _now()}
        percentages = PROGRESS_PERCENT_PATTERN.findall(rendered)
        if percentages:
            changes["progress_percent"] = int(percentages[-1])
        update_job(job_id, **changes)


def _capture_stream(stream, output, *, job_id, log_path, secrets):
    while True:
        # readline preserves complete mysqlsh status messages for redaction
        # before anything reaches durable storage. The size limit prevents a
        # malformed worker from retaining an unbounded line in memory.
        chunk = stream.readline(65536)
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > MAX_LOG_BYTES:
            del output[: len(output) - MAX_LOG_BYTES]
        _publish_live_output(job_id, log_path, chunk, secrets)


def _wait_for_parent_submission(job_id):
    deadline = time.monotonic() + SUBMISSION_HANDSHAKE_SECONDS
    while time.monotonic() < deadline:
        job = load_job(job_id)
        if not job or job.get("status") in FINAL_STATUSES or job.get("status") == "cancel_requested":
            return None
        if job.get("worker_pid") == os.getpid():
            return job
        time.sleep(0.02)
    raise RuntimeError("MySQL Shell worker submission handshake timed out.")


def _finalize_par_without_changing_operation(job_id):
    try:
        finalize_job_par(job_id)
    except Exception:
        # finalize_job_par records a separate cleanup failure; the database
        # operation result must remain accurate.
        pass


def _complete_job(job_id, terminal_status, **fields):
    """Publish a terminal status only after applying the PAR retention policy."""
    if terminal_status == "succeeded":
        fields.setdefault("progress_percent", 100)
        fields.setdefault("progress_updated_at", _now())
    update_job(job_id, status="finalizing", **fields)
    _finalize_par_without_changing_operation(job_id)
    return update_job(job_id, status=terminal_status)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("MySQL Shell job identifier is required.")
    job_id = sys.argv[1]
    job = _wait_for_parent_submission(job_id)
    if job is None:
        return
    request_path = Path(job["request_path"])
    ensure_private_regular_file(request_path)
    secrets = _request_secrets(request_path)
    status = get_mysqlsh_status()
    if not status["available"]:
        request_path.unlink(missing_ok=True)
        _complete_job(job_id, "failed", finished_at=_now(), error=status["error"])
        return

    stdout_buffer, stderr_buffer = bytearray(), bytearray()
    stdout_text, stderr_text = "", ""
    try:
        process = subprocess.Popen(
            build_command(status["binary"], request_path),
            cwd=str(Path(__file__).resolve().parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=mysqlsh_environment(),
            start_new_session=True,
        )
        update_job(
            job_id,
            status="running",
            started_at=_now(),
            mysqlsh_pid=process.pid,
            last_progress="MySQL Shell started; waiting for progress output.",
            progress_updated_at=_now(),
        )
        stdout_thread = threading.Thread(
            target=_capture_stream,
            args=(process.stdout, stdout_buffer),
            kwargs={"job_id": job_id, "log_path": job["stdout_path"], "secrets": secrets},
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_capture_stream,
            args=(process.stderr, stderr_buffer),
            kwargs={"job_id": job_id, "log_path": job["stderr_path"], "secrets": secrets},
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            returncode = process.wait(timeout=JOB_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            process_group_id = os.getpgid(process.pid)
            os.killpg(process_group_id, signal.SIGTERM)
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process_group_id, signal.SIGKILL)
                returncode = process.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        stdout_text = stdout_buffer.decode("utf-8", errors="replace")
        stderr_text = stderr_buffer.decode("utf-8", errors="replace")
        # The streams were redacted and persisted while running. Rewrite each
        # bounded final buffer so terminal logs have the same retention limit.
        atomic_write_private_text(Path(job["stdout_path"]), _redact(stdout_text, secrets))
        atomic_write_private_text(Path(job["stderr_path"]), _redact(stderr_text, secrets))
        current = load_job(job_id) or {}
        if current.get("status") in {"canceled", "cancel_requested"}:
            return
        evaluation = evaluate_execution(returncode, stdout_text, stderr_text)
        if timed_out:
            evaluation = {"succeeded": False, "error": "MySQL Shell job exceeded the 24-hour execution limit."}
        _complete_job(
            job_id,
            "succeeded" if evaluation["succeeded"] else "failed",
            finished_at=_now(),
            returncode=returncode,
            error=_redact(evaluation["error"], secrets),
        )
    except Exception as error:
        current = load_job(job_id) or {}
        if current.get("status") not in {"canceled", "cancel_requested"}:
            _complete_job(job_id, "failed", finished_at=_now(), error=_redact(str(error), secrets))
    finally:
        request_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
