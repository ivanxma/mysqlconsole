import csv
import io
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


IMPORT_CACHE_DIR = Path(tempfile.gettempdir()) / "dbconsole-import-cache"
IMPORT_SQL_TYPE_RE = re.compile(r"^[A-Za-z]+(?: [A-Za-z]+)*(?:\([0-9, ]+\))?$")
IMPORT_TYPE_OPTIONS = [
    "BIGINT",
    "DOUBLE",
    "DECIMAL(18,6)",
    "TINYINT(1)",
    "VARCHAR(255)",
    "TEXT",
    "LONGTEXT",
    "DATE",
    "DATETIME",
    "JSON",
]


def _normalize_checkbox(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ensure_import_cache_dir():
    IMPORT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _import_cache_path(plan_id):
    candidate = str(plan_id or "").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", candidate):
        return None
    return IMPORT_CACHE_DIR / f"{candidate}.json"


def save_mysql_import_plan(plan):
    _ensure_import_cache_dir()
    plan_payload = dict(plan)
    plan_payload["plan_id"] = uuid4().hex
    cache_path = _import_cache_path(plan_payload["plan_id"])
    cache_path.write_text(json.dumps(plan_payload, ensure_ascii=False), encoding="utf-8")
    return plan_payload


def load_mysql_import_plan(plan_id):
    cache_path = _import_cache_path(plan_id)
    if cache_path is None or not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def delete_mysql_import_plan(plan_id):
    cache_path = _import_cache_path(plan_id)
    if cache_path is None or not cache_path.exists():
        return
    cache_path.unlink(missing_ok=True)


def sanitize_import_identifier(value, prefix="column"):
    cleaned = re.sub(r"[^A-Za-z0-9_$]+", "_", str(value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:64]


def lowercase_import_identifier(value, prefix="column"):
    return sanitize_import_identifier(value, prefix).lower()


def _make_unique_labels(values, prefix):
    labels = []
    seen = set()
    for index, value in enumerate(values, start=1):
        base_label = str(value or "").strip() or f"{prefix}_{index}"
        candidate = base_label
        suffix = 2
        while candidate.lower() in seen:
            candidate = f"{base_label}_{suffix}"
            suffix += 1
        labels.append(candidate)
        seen.add(candidate.lower())
    return labels


def derive_import_table_name(filename):
    return lowercase_import_identifier(Path(str(filename or "import_table")).stem, "import_table")


def _normalize_upload_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _preview_import_value(value, max_length=120):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def _normalize_json_row(item):
    if isinstance(item, dict):
        return {
            str(key or f"column_{index + 1}"): _normalize_upload_value(value)
            for index, (key, value) in enumerate(item.items())
        }
    if isinstance(item, list):
        return {
            f"value_{index + 1}": _normalize_upload_value(value)
            for index, value in enumerate(item)
        }
    return {"value": _normalize_upload_value(item)}


def parse_json_upload(text):
    payload = json.loads(text)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if len(payload) == 1:
            only_value = next(iter(payload.values()))
            items = only_value if isinstance(only_value, list) else [payload]
        else:
            items = [payload]
    else:
        items = [payload]

    rows = []
    column_order = []
    for item in items:
        row = _normalize_json_row(item)
        for column_name in row:
            if column_name not in column_order:
                column_order.append(column_name)
        rows.append(row)

    if not column_order:
        raise ValueError("The JSON file did not contain tabular rows.")

    normalized_rows = [{column_name: row.get(column_name) for column_name in column_order} for row in rows]
    return {"file_format": "json", "column_order": column_order, "rows": normalized_rows}


def parse_csv_upload(text):
    sample = text[:4096] or "column_1\n"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = True

    stream = io.StringIO(text, newline="")
    reader = list(csv.reader(stream, dialect))
    if not reader:
        raise ValueError("The CSV file is empty.")

    if has_header:
        raw_headers = _make_unique_labels(reader[0], "column")
        data_rows = reader[1:]
    else:
        raw_headers = []
        data_rows = reader

    max_columns = max((len(row) for row in ([reader[0]] + data_rows)), default=0)
    if not raw_headers:
        raw_headers = [f"column_{index + 1}" for index in range(max_columns)]
    elif len(raw_headers) < max_columns:
        raw_headers.extend([f"column_{index + 1}" for index in range(len(raw_headers), max_columns)])

    rows = []
    for row_values in data_rows:
        if not row_values or all(str(value or "").strip() == "" for value in row_values):
            continue
        padded_values = list(row_values) + [""] * (len(raw_headers) - len(row_values))
        rows.append(
            {
                header: _normalize_upload_value(padded_values[index] if index < len(padded_values) else None)
                for index, header in enumerate(raw_headers)
            }
        )

    return {"file_format": "csv", "column_order": raw_headers, "rows": rows}


def parse_import_upload(upload_storage):
    filename = Path(str(getattr(upload_storage, "filename", "") or "")).name
    if not filename:
        raise ValueError("Choose a CSV or JSON file to upload.")

    payload = upload_storage.read()
    if not payload:
        raise ValueError("The uploaded file is empty.")

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Upload files must be UTF-8 encoded.") from error

    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        parsed = parse_csv_upload(text)
    elif suffix == ".json":
        parsed = parse_json_upload(text)
    else:
        raise ValueError("Only CSV and JSON files are supported.")

    parsed["source_filename"] = filename
    return parsed


def _is_bool_like(value):
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return str(value).strip().lower() in {"true", "false", "yes", "no", "on", "off"}
    return False


def _is_int_like(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return bool(re.fullmatch(r"[+-]?\d+", value.strip()))
    return False


def _is_float_like(value):
    if _is_int_like(value):
        return True
    if isinstance(value, float):
        return True
    if isinstance(value, str):
        return bool(re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", value.strip()))
    return False


def _is_date_like(value):
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return False
    try:
        datetime.strptime(stripped, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _is_datetime_like(value):
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if "T" not in stripped and " " not in stripped:
        return False
    candidate = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return False


def infer_import_column_type(values):
    non_null_values = [value for value in values if value is not None]
    if not non_null_values:
        return "VARCHAR(255)"
    if all(isinstance(value, (dict, list)) for value in non_null_values):
        return "JSON"
    if all(_is_bool_like(value) for value in non_null_values):
        return "TINYINT(1)"
    if all(_is_int_like(value) for value in non_null_values):
        return "BIGINT"
    if all(_is_float_like(value) for value in non_null_values):
        return "DOUBLE"
    if all(_is_datetime_like(value) for value in non_null_values):
        return "DATETIME"
    if all(_is_date_like(value) for value in non_null_values):
        return "DATE"

    max_length = max(len(_preview_import_value(value, max_length=1000000)) for value in non_null_values)
    if max_length > 65535:
        return "LONGTEXT"
    if max_length > 255:
        return "TEXT"
    return "VARCHAR(255)"


def build_import_column_definitions(rows, column_order):
    definitions = []
    seen_names = set()
    for index, source_name in enumerate(column_order, start=1):
        suggested_name = lowercase_import_identifier(source_name, f"column_{index}")
        candidate_name = suggested_name
        suffix = 2
        while candidate_name.lower() in seen_names:
            candidate_name = lowercase_import_identifier(f"{suggested_name}_{suffix}", f"column_{index}")
            suffix += 1
        seen_names.add(candidate_name.lower())
        column_values = [row.get(source_name) for row in rows]
        sample_values = []
        for value in column_values:
            if value is None:
                continue
            sample_values.append(_preview_import_value(value))
            if len(sample_values) >= 3:
                break
        definitions.append(
            {
                "source_name": source_name,
                "column_name": candidate_name,
                "data_type": infer_import_column_type(column_values),
                "allow_null": any(value is None for value in column_values) or not rows,
                "sample_values": sample_values,
            }
        )
    return definitions


def build_import_sample_rows(rows, column_order, limit=10):
    sample_rows = []
    for row in rows[:limit]:
        sample_rows.append({column_name: _preview_import_value(row.get(column_name)) for column_name in column_order})
    return sample_rows


def _extract_mysql_import_state(payload):
    return {
        "create_database": _normalize_checkbox(payload.get("create_database", "")),
        "selected_database": str(payload.get("selected_database", "")).strip(),
        "new_database_name": str(payload.get("new_database_name", "")).strip(),
        "table_name": str(payload.get("table_name", "")).strip().lower(),
        "replace_existing_table": _normalize_checkbox(payload.get("replace_existing_table", "")),
    }


def _effective_import_database_name(import_state):
    return import_state["new_database_name"] if import_state["create_database"] else import_state["selected_database"]


def build_mysql_import_plan(upload_storage, payload, database_inventory, *, quote_identifier):
    parsed_upload = parse_import_upload(upload_storage)
    import_state = _extract_mysql_import_state(payload)
    target_database = _effective_import_database_name(import_state)
    available_database_names = {row["database_name"] for row in database_inventory}

    if not target_database:
        raise ValueError("Choose a database, or enable Create DB and enter a database name.")
    quote_identifier(target_database)
    if not import_state["create_database"] and target_database not in available_database_names:
        raise ValueError(f"Database `{target_database}` was not found.")
    if not parsed_upload["column_order"]:
        raise ValueError("The uploaded file did not contain any columns to import.")

    return {
        "source_filename": parsed_upload["source_filename"],
        "file_format": parsed_upload["file_format"],
        "rows": parsed_upload["rows"],
        "row_count": len(parsed_upload["rows"]),
        "column_order": parsed_upload["column_order"],
        "sample_columns": parsed_upload["column_order"],
        "sample_rows": build_import_sample_rows(parsed_upload["rows"], parsed_upload["column_order"]),
        "column_definitions": build_import_column_definitions(parsed_upload["rows"], parsed_upload["column_order"]),
        "selected_database": import_state["selected_database"],
        "create_database": import_state["create_database"],
        "new_database_name": import_state["new_database_name"],
        "table_name": derive_import_table_name(parsed_upload["source_filename"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _hydrate_import_column_definitions(plan, payload=None):
    column_definitions = []
    for index, definition in enumerate(plan.get("column_definitions", [])):
        if payload is None:
            column_name = definition.get("column_name", "")
            data_type = definition.get("data_type", "")
            allow_null = bool(definition.get("allow_null"))
        else:
            column_name = str(payload.get(f"column_name_{index}", definition.get("column_name", ""))).strip().lower()
            data_type = str(payload.get(f"column_type_{index}", definition.get("data_type", ""))).strip()
            allow_null = _normalize_checkbox(payload.get(f"column_allow_null_{index}", ""))
        column_definitions.append(
            {
                "source_name": definition.get("source_name", ""),
                "column_name": column_name,
                "data_type": data_type,
                "allow_null": allow_null,
                "sample_values": definition.get("sample_values", []),
            }
        )
    return column_definitions


def build_mysql_import_page_state(plan, database_inventory, *, fetch_table_exists, payload=None):
    available_database_names = {row["database_name"] for row in database_inventory}
    if payload is None:
        import_state = {
            "create_database": bool(plan.get("create_database")) if plan else False,
            "selected_database": plan.get("selected_database", "") if plan else "",
            "new_database_name": plan.get("new_database_name", "") if plan else "",
            "table_name": plan.get("table_name", "") if plan else "",
            "replace_existing_table": False,
        }
    else:
        import_state = _extract_mysql_import_state(payload)

    state = {
        "database_inventory": database_inventory,
        "import_type_options": IMPORT_TYPE_OPTIONS,
        "plan_loaded": bool(plan),
        "plan_id": plan.get("plan_id", "") if plan else "",
        "source_filename": plan.get("source_filename", "") if plan else "",
        "file_format": str(plan.get("file_format", "")).upper() if plan else "",
        "row_count": plan.get("row_count", 0) if plan else 0,
        "sample_columns": plan.get("sample_columns", []) if plan else [],
        "sample_rows": plan.get("sample_rows", []) if plan else [],
        "column_definitions": _hydrate_import_column_definitions(plan, payload) if plan else [],
        "create_database": import_state["create_database"],
        "selected_database": import_state["selected_database"],
        "new_database_name": import_state["new_database_name"],
        "table_name": import_state["table_name"] or (plan.get("table_name", "") if plan else ""),
        "replace_existing_table": import_state["replace_existing_table"],
        "database_exists": False,
        "table_exists": False,
        "effective_database_name": "",
    }
    state["effective_database_name"] = _effective_import_database_name(state)
    if state["effective_database_name"] in available_database_names:
        state["database_exists"] = True
        if state["table_name"]:
            state["table_exists"] = fetch_table_exists(state["effective_database_name"], state["table_name"])
    return state


def _normalize_import_type(data_type):
    normalized = re.sub(r"\s+", " ", str(data_type or "").strip().upper())
    if not normalized:
        raise ValueError("Each import column must have a data type.")
    if not IMPORT_SQL_TYPE_RE.fullmatch(normalized):
        raise ValueError(f"Invalid data type `{data_type}`.")
    return normalized


def validate_mysql_import_request(
    payload,
    plan,
    database_inventory,
    *,
    quote_identifier,
    fetch_table_exists,
    fetch_database_exists,
):
    import_state = _extract_mysql_import_state(payload)
    target_database = _effective_import_database_name(import_state)
    available_database_names = {row["database_name"] for row in database_inventory}

    if not target_database:
        raise ValueError("Choose a database, or enable Create DB and enter a database name.")
    quote_identifier(target_database)
    if not import_state["create_database"] and target_database not in available_database_names:
        raise ValueError(f"Database `{target_database}` was not found.")

    table_name = import_state["table_name"] or derive_import_table_name(plan.get("source_filename", "import_table"))
    quote_identifier(table_name)

    column_definitions = []
    seen_column_names = set()
    for index, definition in enumerate(_hydrate_import_column_definitions(plan, payload), start=1):
        column_name = str(definition.get("column_name", "")).strip()
        if not column_name:
            raise ValueError(f"Column name {index} cannot be empty.")
        quote_identifier(column_name)
        column_key = column_name.lower()
        if column_key in seen_column_names:
            raise ValueError(f"Duplicate import column name `{column_name}` is not allowed.")
        seen_column_names.add(column_key)
        column_definitions.append(
            {
                "source_name": definition.get("source_name", ""),
                "column_name": column_name,
                "data_type": _normalize_import_type(definition.get("data_type", "")),
                "allow_null": bool(definition.get("allow_null")),
            }
        )

    table_exists = fetch_table_exists(target_database, table_name) if fetch_database_exists(target_database) else False
    if table_exists and not import_state["replace_existing_table"]:
        raise ValueError(f"Table `{target_database}.{table_name}` already exists. Choose Replace Table or change the table name.")

    return {
        "create_database": import_state["create_database"],
        "replace_existing_table": import_state["replace_existing_table"],
        "effective_database_name": target_database,
        "table_name": table_name,
        "column_definitions": column_definitions,
    }


def _coerce_import_cell_value(value, column_definition):
    data_type = str(column_definition["data_type"]).upper()
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            if data_type.startswith(("VARCHAR", "TEXT", "LONGTEXT")):
                return ""
            return None
    if data_type.startswith("JSON"):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return json.dumps(value)
        if isinstance(value, (int, float)):
            return json.dumps(value)
        return str(value)
    if data_type.startswith("TINYINT(1)"):
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return int(value)
        lowered = str(value).strip().lower()
        bool_map = {"true": 1, "false": 0, "yes": 1, "no": 0, "on": 1, "off": 0}
        if lowered in bool_map:
            return bool_map[lowered]
        return int(lowered)
    if data_type.startswith(("BIGINT", "INT", "INTEGER", "SMALLINT", "MEDIUMINT", "TINYINT")):
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return int(value)
        return int(str(value).strip())
    if data_type.startswith(("DOUBLE", "FLOAT", "REAL")):
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip())
    if data_type.startswith(("DECIMAL", "NUMERIC")):
        return str(value).strip() if isinstance(value, str) else str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def run_mysql_import(plan, import_request, *, quote_identifier, execute_statement, mysql_connection):
    target_database = import_request["effective_database_name"]
    table_name = import_request["table_name"]
    column_definitions = import_request["column_definitions"]
    safe_database = quote_identifier(target_database)
    safe_table = quote_identifier(table_name)

    if import_request["create_database"]:
        execute_statement(f"CREATE DATABASE IF NOT EXISTS {safe_database}")

    create_columns_sql = ", ".join(
        f"{quote_identifier(column['column_name'])} {column['data_type']} {'NULL' if column['allow_null'] else 'NOT NULL'}"
        for column in column_definitions
    )
    insert_columns_sql = ", ".join(quote_identifier(column["column_name"]) for column in column_definitions)
    insert_placeholders = ", ".join(["%s"] * len(column_definitions))
    insert_sql = f"INSERT INTO {safe_database}.{safe_table} ({insert_columns_sql}) VALUES ({insert_placeholders})"

    with mysql_connection(database_override=target_database, autocommit=False) as connection:
        try:
            with connection.cursor() as cursor:
                if import_request["replace_existing_table"]:
                    cursor.execute(f"DROP TABLE IF EXISTS {safe_database}.{safe_table}")
                cursor.execute(
                    f"CREATE TABLE {safe_database}.{safe_table} ({create_columns_sql}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                )
                pending_rows = []
                for raw_row in plan.get("rows", []):
                    pending_rows.append(
                        [
                            _coerce_import_cell_value(raw_row.get(column["source_name"]), column)
                            for column in column_definitions
                        ]
                    )
                    if len(pending_rows) >= 500:
                        cursor.executemany(insert_sql, pending_rows)
                        pending_rows = []
                if pending_rows:
                    cursor.executemany(insert_sql, pending_rows)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
