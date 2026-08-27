import os
import stat
import tempfile
from pathlib import Path


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
APPLICATION_ROOT = Path(__file__).resolve().parent.parent


def _current_uid():
    return os.getuid() if hasattr(os, "getuid") else None


def _configured_private_roots():
    roots = []
    for name in ("DBCONSOLE_RUNTIME_DIR", "DBCONSOLE_STATE_DIR"):
        value = os.environ.get(name, "").strip()
        if value:
            roots.append(Path(value).expanduser().resolve(strict=False))
    return roots


def _validate_private_directory(directory):
    if directory.is_symlink():
        raise ValueError(f"Private directory must not be a symlink: {directory}")
    status = directory.stat()
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError(f"Private path is not a directory: {directory}")
    uid = _current_uid()
    if uid is not None and status.st_uid != uid:
        raise PermissionError(f"Private directory is not owned by the current user: {directory}")
    if stat.S_IMODE(status.st_mode) != PRIVATE_DIRECTORY_MODE:
        directory.chmod(PRIVATE_DIRECTORY_MODE)


def ensure_private_directory(path):
    directory = Path(path)
    if directory.is_symlink():
        raise ValueError(f"Private directory must not be a symlink: {directory}")
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)

    resolved = directory.resolve(strict=False)
    chain_root = next(
        (root for root in _configured_private_roots() if resolved == root or root in resolved.parents),
        resolved,
    )
    current = chain_root
    if current.exists():
        _validate_private_directory(current)
    if resolved != chain_root:
        for component in resolved.relative_to(chain_root).parts:
            current = current / component
            _validate_private_directory(current)
    else:
        _validate_private_directory(resolved)
    return directory


def get_runtime_directory():
    configured_path = os.environ.get("DBCONSOLE_RUNTIME_DIR", "").strip()
    selected = Path(configured_path).expanduser() if configured_path else APPLICATION_ROOT / ".runtime"
    os.environ["DBCONSOLE_RUNTIME_DIR"] = str(selected)
    return ensure_private_directory(selected)


def get_state_directory():
    configured_path = os.environ.get("DBCONSOLE_STATE_DIR", "").strip()
    selected = Path(configured_path).expanduser() if configured_path else APPLICATION_ROOT / ".state"
    os.environ["DBCONSOLE_STATE_DIR"] = str(selected)
    return ensure_private_directory(selected)


def ensure_private_regular_file(path):
    file_path = Path(path)
    if not file_path.exists():
        return file_path
    status = file_path.lstat()
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"Runtime state path must be a regular file: {file_path}")
    uid = _current_uid()
    if uid is not None and status.st_uid != uid:
        raise PermissionError(f"Runtime state file is not owned by the current user: {file_path}")
    if stat.S_IMODE(status.st_mode) != PRIVATE_FILE_MODE:
        file_path.chmod(PRIVATE_FILE_MODE)
    return file_path


def atomic_write_private_text(path, text):
    file_path = Path(path)
    parent = ensure_private_directory(file_path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{file_path.name}.", dir=parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        temporary_path.replace(file_path)
        return ensure_private_regular_file(file_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def atomic_write_private_bytes(path, payload):
    file_path = Path(path)
    parent = ensure_private_directory(file_path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{file_path.name}.", dir=parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(bytes(payload))
        temporary_path.replace(file_path)
        return ensure_private_regular_file(file_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def write_new_private_text(path, text):
    file_path = Path(path)
    ensure_private_directory(file_path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(file_path, flags, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise
    return ensure_private_regular_file(file_path)


def append_private_text(path, text):
    file_path = Path(path)
    ensure_private_directory(file_path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(file_path, flags, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(text)
    return ensure_private_regular_file(file_path)
