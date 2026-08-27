import codecs
import csv
import io
import json
from pathlib import Path
from threading import Lock

from werkzeug.utils import secure_filename


SUPPORTED_LAKEHOUSE_UPLOAD_EXTENSIONS = {"csv", "json", "parquet", "avro"}
MEBIBYTE = 1024 * 1024
DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_CONFIGURABLE_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
VALIDATION_CHUNK_BYTES = 1024 * 1024
STDLIB_JSON_FALLBACK_MAX_BYTES = 16 * MEBIBYTE

_INSTANCE_PRINCIPAL_SIGNER = None
_INSTANCE_PRINCIPAL_SIGNER_LOCK = Lock()


def _load_oci_sdk():
    try:
        import oci
    except Exception as error:
        raise RuntimeError("The OCI SDK is not installed. Run setup to install current requirements.") from error
    return oci


def get_instance_principal_signer():
    global _INSTANCE_PRINCIPAL_SIGNER
    if _INSTANCE_PRINCIPAL_SIGNER is not None:
        return _INSTANCE_PRINCIPAL_SIGNER
    with _INSTANCE_PRINCIPAL_SIGNER_LOCK:
        if _INSTANCE_PRINCIPAL_SIGNER is None:
            oci = _load_oci_sdk()
            try:
                _INSTANCE_PRINCIPAL_SIGNER = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            except Exception as error:
                raise RuntimeError(
                    "Unable to initialize OCI Instance Principal authentication. "
                    "Confirm this process runs on an OCI Compute instance with metadata access."
                ) from error
    return _INSTANCE_PRINCIPAL_SIGNER


def reset_instance_principal_signer():
    """Clear the cached signer for tests or an explicit recovery operation."""
    global _INSTANCE_PRINCIPAL_SIGNER
    with _INSTANCE_PRINCIPAL_SIGNER_LOCK:
        _INSTANCE_PRINCIPAL_SIGNER = None


def build_object_storage_client(config):
    region = str((config or {}).get("region") or "").strip().lower()
    if not region:
        raise ValueError("Object Storage region is required.")
    oci = _load_oci_sdk()
    return oci.object_storage.ObjectStorageClient(
        {"region": region},
        signer=get_instance_principal_signer(),
    )


def resolved_object_storage_endpoint(client):
    """Return the concrete HTTPS endpoint, never an SDK URI template."""
    base_client = getattr(client, "base_client", None)
    resolver = getattr(base_client, "get_endpoint", None)
    endpoint = resolver() if callable(resolver) else ""
    if not isinstance(endpoint, str):
        endpoint = getattr(base_client, "endpoint", "")
    endpoint = str(endpoint or "").strip().rstrip("/")
    if not endpoint.startswith("https://") or "{" in endpoint or "}" in endpoint:
        raise RuntimeError("OCI did not return a concrete HTTPS Object Storage endpoint.")
    return endpoint


def create_scoped_preauthenticated_request(
    config,
    *,
    namespace,
    bucket_name,
    prefix,
    name,
    access_type,
    expires_at,
):
    oci = _load_oci_sdk()
    client = build_object_storage_client(config)
    details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
        name=name,
        access_type=access_type,
        time_expires=expires_at,
        bucket_listing_action="ListObjects",
        object_name=prefix,
    )
    response = client.create_preauthenticated_request(
        namespace_name=namespace,
        bucket_name=bucket_name,
        create_preauthenticated_request_details=details,
    )
    return client, response.data


def revoke_preauthenticated_request(config, *, namespace, bucket_name, par_id):
    client = build_object_storage_client(config)
    try:
        client.delete_preauthenticated_request(
            namespace_name=namespace,
            bucket_name=bucket_name,
            par_id=par_id,
        )
    except Exception as error:
        if getattr(error, "status", None) != 404:
            raise


def normalize_folder_prefix(value):
    normalized = str(value or "").strip().strip("/")
    return f"{normalized}/" if normalized else ""


def join_object_prefix(*parts):
    cleaned_parts = [str(part or "").strip().strip("/") for part in parts if str(part or "").strip().strip("/")]
    return "/".join(cleaned_parts)


def build_object_storage_uri(namespace, bucket_name, object_name):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    object_value = str(object_name or "").strip().lstrip("/")
    if not namespace_value or not bucket_value or not object_value:
        return ""
    return f"oci://{bucket_value}@{namespace_value}/{object_value}"


def list_object_storage_folders(
    config,
    *,
    namespace,
    bucket_name,
    base_prefix="",
    limit=1000,
    max_folders=1000,
    max_pages=100,
):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    if not namespace_value or not bucket_value:
        return []
    prefix = normalize_folder_prefix(base_prefix)
    client = build_object_storage_client(config)
    folders = [prefix]
    seen = {prefix}
    pending = [prefix]
    seen_page_tokens = set()
    page_count = 0
    while pending and len(folders) < max_folders:
        current_prefix = pending.pop(0)
        start = None
        while len(folders) < max_folders:
            kwargs = {"prefix": current_prefix, "delimiter": "/", "limit": limit}
            if start:
                kwargs["start"] = start
            response = client.list_objects(namespace_value, bucket_value, **kwargs)
            page_count += 1
            if page_count > max_pages:
                raise RuntimeError("Object Storage folder listing exceeded the page limit.")
            data = response.data
            for item in getattr(data, "prefixes", []) or []:
                folder = normalize_folder_prefix(item)
                if prefix and not folder.startswith(prefix):
                    continue
                if folder and folder not in seen:
                    seen.add(folder)
                    folders.append(folder)
                    pending.append(folder)
                    if len(folders) >= max_folders:
                        break
            next_start = getattr(data, "next_start_with", None)
            if not next_start:
                break
            token_key = (current_prefix, str(next_start))
            if token_key in seen_page_tokens or next_start == start:
                raise RuntimeError("Object Storage folder listing returned a repeated page token.")
            seen_page_tokens.add(token_key)
            start = next_start
    return sorted(folders, key=lambda value: (value.count("/"), value.lower()))


def list_object_storage_files(
    config,
    *,
    namespace,
    bucket_name,
    folder_prefix="",
    limit=1000,
    max_objects=10000,
    max_pages=100,
):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    if not namespace_value or not bucket_value:
        return []
    prefix = normalize_folder_prefix(folder_prefix)
    client = build_object_storage_client(config)
    files = []
    start = None
    seen_page_tokens = set()
    page_count = 0
    while True:
        kwargs = {"prefix": prefix, "delimiter": "/", "limit": limit}
        if start:
            kwargs["start"] = start
        response = client.list_objects(namespace_value, bucket_value, **kwargs)
        page_count += 1
        if page_count > max_pages:
            raise RuntimeError("Object Storage file listing exceeded the page limit.")
        data = response.data
        for item in getattr(data, "objects", []) or []:
            object_name = str(getattr(item, "name", "") or "").strip()
            if not object_name or object_name.endswith("/") or object_name == prefix:
                continue
            file_name = object_name[len(prefix) :] if prefix and object_name.startswith(prefix) else Path(object_name).name
            files.append(
                {
                    "object_name": object_name,
                    "file_name": file_name or object_name,
                    "size": getattr(item, "size", ""),
                    "time_modified": getattr(item, "time_modified", ""),
                    "oci_uri": build_object_storage_uri(namespace_value, bucket_value, object_name),
                }
            )
            if len(files) > max_objects:
                raise RuntimeError("Object Storage file listing exceeded the object limit.")
        next_start = getattr(data, "next_start_with", None)
        if not next_start:
            break
        if len(files) >= max_objects:
            raise RuntimeError("Object Storage file listing was truncated at the object limit.")
        if next_start in seen_page_tokens or next_start == start:
            raise RuntimeError("Object Storage file listing returned a repeated page token.")
        seen_page_tokens.add(next_start)
        start = next_start
    return sorted(files, key=lambda item: str(item["file_name"]).lower())


def create_object_storage_folder(config, *, namespace, bucket_name, parent_prefix="", folder_name=""):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    folder_value = secure_filename(str(folder_name or "").strip())
    if not namespace_value or not bucket_value:
        raise ValueError("Object Storage namespace and bucket name are required.")
    if not folder_value:
        raise ValueError("Folder name is required.")
    object_name = normalize_folder_prefix(join_object_prefix(parent_prefix, folder_value))
    client = build_object_storage_client(config)
    client.put_object(namespace_value, bucket_value, object_name, b"")
    return object_name


def _measure_upload_stream(upload_storage):
    stream = upload_storage.stream
    try:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
    except (AttributeError, OSError) as error:
        raise ValueError("Unable to inspect the uploaded file stream.") from error
    return size


def _format_text_location(byte_offset, line_number, column_number):
    return f"byte {byte_offset}, line {line_number}, column {column_number}"


def _stream_line_and_column(stream, byte_offset):
    stream.seek(0)
    prefix = stream.read(max(0, byte_offset))
    text = prefix.decode("utf-8", errors="replace")
    line_number = text.count("\n") + 1
    column_number = len(text.rsplit("\n", 1)[-1]) + 1
    stream.seek(0)
    return line_number, column_number


def _validate_utf8_text_stream(stream, suffix):
    """Read every byte once without materialising the upload in memory."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    byte_offset = 0
    line_number = 1
    column_number = 1
    while True:
        chunk = stream.read(VALIDATION_CHUNK_BYTES)
        if not chunk:
            break
        nul_offset = chunk.find(b"\x00")
        if nul_offset >= 0:
            raise ValueError(
                f"{suffix.upper()} upload contains a binary NUL at "
                f"{_format_text_location(byte_offset + nul_offset, line_number, column_number)}."
            )
        try:
            text = decoder.decode(chunk, final=False)
        except UnicodeDecodeError as error:
            raise ValueError(
                f"{suffix.upper()} upload is not valid UTF-8 near "
                f"{_format_text_location(byte_offset + error.start, line_number, column_number)}."
            ) from error
        byte_offset += len(chunk)
        for character in text:
            if character == "\n":
                line_number += 1
                column_number = 1
            else:
                column_number += 1
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise ValueError(
            f"{suffix.upper()} upload is not valid UTF-8 near "
            f"{_format_text_location(byte_offset + error.start, line_number, column_number)}."
        ) from error
    stream.seek(0)


def _validate_csv_stream(stream):
    text_stream = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    try:
        for _row in csv.reader(text_stream, strict=True):
            pass
    except csv.Error as error:
        raise ValueError(f"CSV syntax validation failed at line {getattr(error, 'line_num', '?')}: {error}") from error
    finally:
        text_stream.detach()
    stream.seek(0)


def _validate_json_stream(stream, size):
    """Fully parse JSON with ijson when installed, retaining a stdlib fallback."""
    try:
        import ijson
    except ImportError:
        if size > STDLIB_JSON_FALLBACK_MAX_BYTES:
            raise ValueError(
                "Streaming JSON validation requires the ijson dependency for uploads above "
                f"{STDLIB_JSON_FALLBACK_MAX_BYTES // MEBIBYTE} MiB. Run setup to install requirements."
            )
        try:
            text_stream = io.TextIOWrapper(stream, encoding="utf-8-sig")
            parsed = json.load(text_stream)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"JSON syntax validation failed at line {error.lineno}, column {error.colno} (byte {error.pos}): {error.msg}."
            ) from error
        finally:
            text_stream.detach()
        if not isinstance(parsed, (dict, list)):
            raise ValueError("JSON upload must contain an object or array at the document root.")
    else:
        try:
            events = ijson.parse(stream)
            _prefix, first_event, _value = next(events)
            if first_event not in {"start_map", "start_array"}:
                raise ValueError("JSON upload must contain an object or array at the document root.")
            for _event in events:
                pass
        except ValueError:
            raise
        except Exception as error:
            byte_offset = stream.tell()
            line_number, column_number = _stream_line_and_column(stream, byte_offset)
            raise ValueError(
                "JSON syntax validation failed near "
                f"{_format_text_location(byte_offset, line_number, column_number)}: {error}"
            ) from error
    stream.seek(0)


def _validate_optional_binary_format(stream, suffix):
    """Use installed format libraries without making heavyweight packages mandatory."""
    try:
        stream.seek(0)
        if suffix == "parquet":
            import pyarrow.parquet as parquet

            parquet.ParquetFile(stream)
        elif suffix == "avro":
            from fastavro import reader as avro_reader

            for _record in avro_reader(stream):
                pass
    except ImportError:
        return
    except Exception as error:
        raise ValueError(f"{suffix.title()} format-library validation failed: {error}") from error
    finally:
        stream.seek(0)


def validate_object_storage_upload(upload_storage, *, max_upload_bytes=DEFAULT_MAX_UPLOAD_BYTES):
    if upload_storage is None or not getattr(upload_storage, "filename", ""):
        raise ValueError("Choose a file to upload.")
    filename = secure_filename(Path(upload_storage.filename).name)
    if not filename:
        raise ValueError("Upload file name is invalid.")
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_LAKEHOUSE_UPLOAD_EXTENSIONS:
        raise ValueError("Upload a supported Lakehouse file: csv, json, parquet, or avro.")

    size = _measure_upload_stream(upload_storage)
    if size <= 0:
        raise ValueError("Uploaded file is empty.")
    if size > max_upload_bytes:
        raise ValueError(f"Uploaded file exceeds the {max_upload_bytes}-byte limit.")

    stream = upload_storage.stream
    prefix = stream.read(min(size, 65536))
    if suffix == "parquet":
        if not prefix.startswith(b"PAR1"):
            raise ValueError("Parquet upload does not contain the expected PAR1 signature.")
        stream.seek(max(0, size - 4))
        if stream.read(4) != b"PAR1":
            raise ValueError("Parquet upload does not contain the expected closing PAR1 signature.")
        _validate_optional_binary_format(stream, suffix)
    elif suffix == "avro" and not prefix.startswith(b"Obj\x01"):
        raise ValueError("Avro upload does not contain the expected object-container signature.")
    elif suffix == "avro":
        _validate_optional_binary_format(stream, suffix)
    else:
        stream.seek(0)
        _validate_utf8_text_stream(stream, suffix)
        if suffix == "csv":
            _validate_csv_stream(stream)
        else:
            _validate_json_stream(stream, size)
    stream.seek(0)
    return {"filename": filename, "suffix": suffix, "size": size}


def upload_object_storage_file(
    config,
    *,
    namespace,
    bucket_name,
    folder_prefix="",
    upload_storage=None,
    validated_upload=None,
):
    namespace_value = str(namespace or "").strip()
    bucket_value = str(bucket_name or "").strip()
    if not namespace_value or not bucket_value:
        raise ValueError("Object Storage namespace and bucket name are required.")
    upload = validated_upload or validate_object_storage_upload(upload_storage)
    object_name = join_object_prefix(folder_prefix, upload["filename"])
    upload_storage.stream.seek(0)
    client = build_object_storage_client(config)
    response = client.put_object(
        namespace_value,
        bucket_value,
        object_name,
        upload_storage.stream,
        content_length=upload["size"],
    )
    head_response = client.head_object(namespace_value, bucket_value, object_name)
    remote_size = int((getattr(head_response, "headers", {}) or {}).get("content-length", -1))
    if remote_size != upload["size"]:
        request_id = (getattr(response, "headers", {}) or {}).get("opc-request-id", "")
        detail = f" OCI request ID: {request_id}." if request_id else ""
        raise RuntimeError(f"Upload verification failed for `{object_name}`; verify the object before retrying.{detail}")
    headers = getattr(response, "headers", {}) or {}
    return {
        "object_name": object_name,
        "oci_uri": build_object_storage_uri(namespace_value, bucket_value, object_name),
        "size": upload["size"],
        "etag": headers.get("etag", ""),
        "request_id": headers.get("opc-request-id", ""),
    }


def test_instance_principal_access(config):
    config = config or {}
    region = str(config.get("region") or "").strip().lower()
    namespace = str(config.get("namespace") or "").strip()
    bucket_name = str(config.get("bucket_name") or "").strip()
    missing = [name for name, value in (("region", region), ("namespace", namespace), ("bucket_name", bucket_name)) if not value]
    if missing:
        return {"ok": False, "message": "Object Storage access test is missing: " + ", ".join(missing)}
    try:
        client = build_object_storage_client({"region": region})
        namespace_response = client.get_namespace()
        returned_namespace = str(namespace_response.data or "").strip()
        if returned_namespace and returned_namespace != namespace:
            return {
                "ok": False,
                "message": f"Instance Principal returned namespace `{returned_namespace}`, not configured namespace `{namespace}`.",
            }
        client.list_objects(namespace, bucket_name, limit=1)
        return {
            "ok": True,
            "message": (
                f"Instance Principal access succeeded for bucket `{bucket_name}` in namespace `{namespace}` "
                f"using region `{region}`."
            ),
        }
    except Exception as error:
        return {"ok": False, "message": f"Instance Principal Object Storage access failed: {error}"}
