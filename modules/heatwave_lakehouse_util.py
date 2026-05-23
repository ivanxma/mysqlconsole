import json
import re

from modules.object_storage_util import build_object_storage_prefix_uri, build_object_storage_uri


HEATWAVE_EXTERNAL_FORMATS = ("csv", "json", "parquet", "avro", "delta")
HEATWAVE_EXTERNAL_LOAD_MODES = ("normal", "dryrun", "validation")
HEATWAVE_EXTERNAL_OUTPUTS = ("", "normal", "compact", "silent")
HEATWAVE_EXTERNAL_MATCH_COLUMNS_BY = ("", "order", "name_case_sensitive", "name_case_insensitive")
HEATWAVE_EXTERNAL_BOOL_OPTIONS = ("", "true", "false")


def sql_string_literal(value):
    return "'" + str(value or "").replace("\\", "\\\\").replace("'", "''") + "'"


def normalize_bool_option(value):
    normalized = str(value or "").strip().lower()
    return normalized if normalized in HEATWAVE_EXTERNAL_BOOL_OPTIONS else ""


def bool_option_to_value(value):
    normalized = normalize_bool_option(value)
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def infer_external_file_format(object_name):
    suffix = str(object_name or "").rsplit(".", 1)
    if len(suffix) == 2 and suffix[1].lower() in HEATWAVE_EXTERNAL_FORMATS:
        return suffix[1].lower()
    return ""


def normalize_external_form(source, object_storage_config=None):
    source = source or {}
    object_storage_config = object_storage_config or {}
    bucket_name = str(object_storage_config.get("bucket_name") or "").strip()
    namespace = str(object_storage_config.get("namespace") or "").strip()
    bucket_prefix = str(object_storage_config.get("bucket_prefix") or "").strip().strip("/")
    default_uri = ""
    if bucket_name and namespace:
        default_uri = build_object_storage_prefix_uri(namespace, bucket_name, bucket_prefix)

    load_folder = str(source.get("load_folder", source.get("upload_folder", bucket_prefix))).strip().strip("/")
    load_file = str(source.get("load_file", "")).strip().strip("/")
    explicit_oci_uri = str(source.get("oci_uri", "")).strip()
    selected_file_uri = ""
    if bucket_name and namespace and load_file:
        selected_file_uri = build_object_storage_uri(namespace, bucket_name, load_file)

    file_format = str(source.get("file_format", "")).strip().lower()
    if not file_format:
        file_format = infer_external_file_format(load_file) or "csv"
    if file_format not in HEATWAVE_EXTERNAL_FORMATS:
        file_format = "csv"
    load_mode = str(source.get("load_mode", "normal")).strip().lower()
    if load_mode not in HEATWAVE_EXTERNAL_LOAD_MODES:
        load_mode = "normal"
    output = str(source.get("output", "")).strip().lower()
    if output not in HEATWAVE_EXTERNAL_OUTPUTS:
        output = ""
    match_columns_by = str(source.get("match_columns_by", "")).strip().lower()
    if match_columns_by not in HEATWAVE_EXTERNAL_MATCH_COLUMNS_BY:
        match_columns_by = ""

    return {
        "upload_folder": str(source.get("upload_folder", bucket_prefix)).strip().strip("/"),
        "load_folder": load_folder,
        "load_file": load_file,
        "create_folder": str(source.get("create_folder", "")).strip().lower() in {"1", "true", "yes", "on"},
        "new_folder_name": str(source.get("new_folder_name", "")).strip(),
        "uploaded_oci_uri": str(source.get("uploaded_oci_uri", "")).strip(),
        "database_name": str(source.get("database_name", "")).strip(),
        "table_name": str(source.get("table_name", "")).strip(),
        "oci_uri": explicit_oci_uri or selected_file_uri or default_uri,
        "file_format": file_format,
        "has_header": str(source.get("has_header", "1")).strip().lower() in {"1", "true", "yes", "on"},
        "load_mode": load_mode,
        "output": output,
        "refresh_external_tables": str(source.get("refresh_external_tables", "")).strip().lower()
        in {"1", "true", "yes", "on"},
        "sampling": normalize_bool_option(source.get("sampling", "")),
        "match_columns_by": match_columns_by,
        "allow_missing_columns": normalize_bool_option(source.get("allow_missing_columns", "")),
        "allow_missing_files": normalize_bool_option(source.get("allow_missing_files", "")),
        "strict_mode": normalize_bool_option(source.get("strict_mode", "")),
        "skip_rows": str(source.get("skip_rows", "")).strip(),
        "load_sql": str(source.get("load_sql", "")).strip(),
        "refresh_database": str(source.get("refresh_database", "")).strip(),
        "refresh_table": str(source.get("refresh_table", "")).strip(),
        "refresh_source": str(source.get("refresh_source", "")).strip(),
        "refresh_sql": str(source.get("refresh_sql", "")).strip(),
    }


def validate_external_identifier(value, label):
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_$]+", normalized):
        raise ValueError(f"{label} must use only letters, numbers, `_`, or `$`.")
    return normalized


def build_heatwave_external_load_sql(form):
    database_name = validate_external_identifier(form.get("database_name"), "Database")
    table_name = validate_external_identifier(form.get("table_name"), "Table")
    oci_uri = str(form.get("oci_uri") or "").strip()
    if not oci_uri.lower().startswith("oci://"):
        raise ValueError("OCI URI must start with `oci://`.")
    file_format = str(form.get("file_format") or "csv").strip().lower()
    if file_format not in HEATWAVE_EXTERNAL_FORMATS:
        raise ValueError("Choose a supported file format.")

    dialect = {"format": file_format}
    if file_format == "csv":
        dialect["has_header"] = bool(form.get("has_header"))
    strict_mode = bool_option_to_value(form.get("strict_mode"))
    if strict_mode is not None:
        dialect["is_strict_mode"] = strict_mode
    allow_missing_files = bool_option_to_value(form.get("allow_missing_files"))
    if allow_missing_files is not None:
        dialect["allow_missing_files"] = allow_missing_files
    skip_rows = str(form.get("skip_rows") or "").strip()
    if skip_rows:
        try:
            dialect["skip_rows"] = int(skip_rows)
        except ValueError as error:
            raise ValueError("Skip rows must be a number.") from error

    engine_attribute = {"dialect": dialect, "file": [{"uri": oci_uri}]}
    sampling = bool_option_to_value(form.get("sampling"))
    if sampling is not None:
        engine_attribute["sampling"] = sampling
    match_columns_by = str(form.get("match_columns_by") or "").strip()
    if match_columns_by:
        engine_attribute["match_columns_by"] = match_columns_by
    allow_missing_columns = bool_option_to_value(form.get("allow_missing_columns"))
    if allow_missing_columns is not None:
        engine_attribute["allow_missing_columns"] = allow_missing_columns

    input_list = [{"db_name": database_name, "tables": [{"table_name": table_name, "engine_attribute": engine_attribute}]}]
    options = {
        "mode": str(form.get("load_mode") or "normal").strip().lower(),
        "refresh_external_tables": bool(form.get("refresh_external_tables")),
    }
    output = str(form.get("output") or "").strip().lower()
    if output:
        options["output"] = output
    return (
        "CALL sys.HEATWAVE_LOAD("
        f"CAST({sql_string_literal(json.dumps(input_list, indent=2))} AS JSON), "
        f"CAST({sql_string_literal(json.dumps(options, indent=2))} AS JSON)"
        ");"
    )


def fetch_lakehouse_database_names(execute_query):
    rows = execute_query(
        """
        SELECT DISTINCT table_schema AS database_name_value
        FROM information_schema.tables
        WHERE UPPER(COALESCE(engine, '')) = 'LAKEHOUSE'
        ORDER BY table_schema
        """
    )
    return [row["database_name_value"] for row in rows]


def fetch_lakehouse_table_rows(execute_query, database_name):
    if not database_name:
        return []
    rows = execute_query(
        """
        SELECT
          table_name AS table_name_value,
          engine AS engine_value,
          table_rows AS table_rows_value,
          create_time AS create_time_value,
          update_time AS update_time_value,
          table_comment AS table_comment_value
        FROM information_schema.tables
        WHERE table_schema = %s
          AND UPPER(COALESCE(engine, '')) = 'LAKEHOUSE'
        ORDER BY table_name
        """,
        [database_name],
    )
    return [
        {
            "table_name": row["table_name_value"],
            "engine": row["engine_value"] or "-",
            "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
            "create_time": row["create_time_value"] or "-",
            "update_time": row["update_time_value"] or "-",
            "table_comment": row["table_comment_value"] or "",
        }
        for row in rows
    ]


def extract_auto_refresh_source(create_statement):
    match = re.search(r"AUTO_REFRESH_SOURCE\s*=\s*(?:'([^']*)'|(NONE))", str(create_statement or ""), re.I)
    if not match:
        return ""
    return match.group(1) or ""


def fetch_lakehouse_table_definition(execute_query, quote_identifier, database_name, table_name):
    if not database_name or not table_name:
        return {"create_statement": "", "auto_refresh_source": ""}
    safe_database = quote_identifier(database_name)
    safe_table = quote_identifier(table_name)
    rows = execute_query(f"SHOW CREATE TABLE {safe_database}.{safe_table}")
    row = rows[0] if rows else {}
    create_statement = row.get("Create Table", "")
    return {
        "create_statement": create_statement,
        "auto_refresh_source": extract_auto_refresh_source(create_statement),
    }


def build_incremental_refresh_sql(database_name, table_name):
    database_name = validate_external_identifier(database_name, "Database")
    table_name = validate_external_identifier(table_name, "Table")
    input_list = [{"db_name": database_name, "tables": [{"table_name": table_name}]}]
    options = {"mode": "normal", "refresh_external_tables": True}
    return (
        "CALL sys.HEATWAVE_LOAD("
        f"CAST({sql_string_literal(json.dumps(input_list, indent=2))} AS JSON), "
        f"CAST({sql_string_literal(json.dumps(options, indent=2))} AS JSON)"
        ");"
    )


def build_auto_refresh_source_sql(quote_identifier, database_name, table_name, source_value):
    safe_database = quote_identifier(database_name)
    safe_table = quote_identifier(table_name)
    normalized_source = str(source_value or "").strip()
    if normalized_source:
        return f"ALTER TABLE {safe_database}.{safe_table} AUTO_REFRESH_SOURCE = {sql_string_literal(normalized_source)};"
    return f"ALTER TABLE {safe_database}.{safe_table} AUTO_REFRESH_SOURCE = NONE;"
