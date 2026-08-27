import json
import re


COMPRESSION_OPTIONS = ("zstd;level=1", "zstd;level=8", "gzip;level=1", "gzip;level=8", "none")
DIALECT_OPTIONS = ("default", "csv", "tsv", "csv-unix", "csv-rfc-unix")
COMPATIBILITY_OPTIONS = (
    "create_invisible_pks", "force_innodb", "force_non_standard_fks", "ignore_missing_pks",
    "ignore_wildcard_grants", "lock_invalid_accounts", "skip_invalid_accounts", "strip_definers",
    "strip_invalid_grants", "strip_restricted_grants", "strip_tablespaces", "unescape_wildcard_grants",
)
ANALYZE_OPTIONS = ("off", "on", "histogram")
DEFER_INDEX_OPTIONS = ("off", "fulltext", "all")
GRANT_ERROR_OPTIONS = ("abort", "drop_account", "ignore")
GTID_OPTIONS = ("off", "replace", "append")
FILTER_TYPES = ("schemas", "tables", "users", "events", "routines", "triggers", "libraries")
FILTER_KEYS = {
    item: (f"include{item.title()}", f"exclude{item.title()}") for item in FILTER_TYPES
}
MANAGED_KEYS = {"osbucketname", "osnamespace", "ociconfigfile", "ociprofile", "progressfile"}
SENSITIVE_FRAGMENTS = ("password", "parurl", "storageurl", "privatekey", "fingerprint")
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
SIZE_RE = re.compile(r"^(?:0|[1-9]\d{0,9}(?:[kKmMgGtT])?)$")
MAX_MYSQLSH_THREADS = 64

DUMP_DEFAULTS = {
    "threads": 4, "maxRate": "0", "compression": "zstd;level=1", "dialect": "default",
    "bytesPerChunk": "64M", "defaultCharacterSet": "utf8mb4", "showProgress": True,
    "dryRun": False, "consistent": True, "skipConsistencyChecks": False,
    "skipUpgradeChecks": False, "checksum": False, "chunking": True, "tzUtc": True,
    "ddlOnly": False, "dataOnly": False, "users": True, "events": True, "routines": True,
    "triggers": True, "libraries": True, "ocimds": False,
    "excludeLakehouseTables": False,
}
LOAD_DEFAULTS = {
    "threads": 4, "waitDumpTimeout": 0, "showProgress": True, "dryRun": False,
    "resetProgress": False, "skipBinlog": False, "ignoreVersion": False, "checksum": False,
    "showMetadata": False, "createInvisiblePKs": False, "dropExistingObjects": False,
    "ignoreExistingObjects": False, "loadDdl": True, "loadData": True, "loadUsers": False,
    "loadIndexes": True, "analyzeTables": "off", "deferTableIndexes": "fulltext",
    "handleGrantErrors": "abort", "updateGtidSet": "off",
}

DUMP_FIELDS = {
    "threads", "maxRate", "compression", "dialect", "bytesPerChunk", "targetVersion",
    "defaultCharacterSet", "showProgress", "dryRun", "consistent", "skipConsistencyChecks",
    "skipUpgradeChecks", "checksum", "chunking", "tzUtc", "ddlOnly", "dataOnly", "users",
    "events", "routines", "triggers", "libraries", "ocimds", "compatibility",
    "excludeLakehouseTables",
}
LOAD_FIELDS = {
    "threads", "backgroundThreads", "waitDumpTimeout", "schema", "characterSet",
    "maxBytesPerTransaction", "showProgress", "dryRun", "resetProgress", "skipBinlog",
    "ignoreVersion", "checksum", "showMetadata", "createInvisiblePKs", "dropExistingObjects",
    "ignoreExistingObjects", "loadDdl", "loadData", "loadUsers", "loadIndexes", "analyzeTables",
    "deferTableIndexes", "handleGrantErrors", "updateGtidSet", "sessionInitSql",
}
KNOWN_FIELDS = {
    "dump": DUMP_FIELDS | {key for pair in FILTER_KEYS.values() for key in pair},
    "load": LOAD_FIELDS | {key for pair in FILTER_KEYS.values() for key in pair},
}


def _string(value):
    return str(value or "").strip()


def _positive_int(value, label, *, optional=False):
    if optional and not _string(value):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a positive integer.") from error
    if parsed < 1 or parsed > MAX_MYSQLSH_THREADS:
        raise ValueError(f"{label} must be between 1 and {MAX_MYSQLSH_THREADS}.")
    return parsed


def _size_option(value, label, *, default=""):
    candidate = _string(value) or default
    if candidate and not SIZE_RE.fullmatch(candidate):
        raise ValueError(f"{label} must be 0 or a positive size with an optional K, M, G, or T suffix.")
    return candidate


def _nonnegative_float(value, label):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be zero or greater.") from error
    if parsed < 0:
        raise ValueError(f"{label} must be zero or greater.")
    return int(parsed) if parsed.is_integer() else parsed


def _checkbox(form, key):
    return str(form.get(key) or "").lower() in {"1", "true", "on", "yes"}


def _choice(value, allowed, label):
    candidate = _string(value)
    if candidate not in allowed:
        raise ValueError(f"Choose a valid {label} value.")
    return candidate


def _json_list(value, label):
    raw = _string(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} selection is invalid.") from error
    if not isinstance(parsed, list):
        raise ValueError(f"{label} selection must be a list.")
    result = []
    seen = set()
    for item in parsed:
        candidate = _string(item)
        if candidate and candidate not in seen:
            result.append(candidate)
            seen.add(candidate)
    return result


def _advanced_options(value, known):
    raw = _string(value)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"Advanced options JSON is invalid: {error.msg}.") from error
    if not isinstance(parsed, dict):
        raise ValueError("Advanced options JSON must be an object.")
    for key in parsed:
        normalized = _string(key)
        lowered = normalized.lower()
        if not KEY_RE.fullmatch(normalized):
            raise ValueError(f"Invalid advanced option name `{normalized}`.")
        if lowered in MANAGED_KEYS or any(part in lowered for part in SENSITIVE_FRAGMENTS):
            raise ValueError(f"Option `{normalized}` is managed by DB Console or is sensitive.")
        if normalized in known:
            raise ValueError(f"Option `{normalized}` already has a structured field.")
    return parsed


def defaults(kind):
    return dict(LOAD_DEFAULTS if kind == "load" else DUMP_DEFAULTS)


def form_state(kind, options=None):
    options = dict(options or {})
    state = defaults(kind)
    for key in KNOWN_FIELDS[kind]:
        if key in options:
            state[key] = options[key]
    for item, (include_key, exclude_key) in FILTER_KEYS.items():
        state[f"include_{item}_json"] = json.dumps(options.get(include_key) or [])
        state[f"exclude_{item}_json"] = json.dumps(options.get(exclude_key) or [])
    advanced = {key: value for key, value in options.items() if key not in KNOWN_FIELDS[kind]}
    state["advanced_json"] = json.dumps(advanced, indent=2, sort_keys=True) if advanced else ""
    state["compatibility"] = list(options.get("compatibility") or [])
    state["sessionInitSql"] = "\n".join(options.get("sessionInitSql") or [])
    return state


def build_options(kind, form):
    if kind == "dump":
        result = {
            "threads": _positive_int(form.get("threads"), "Threads"),
            "maxRate": _size_option(form.get("maxRate"), "Maximum rate", default="0"),
            "compression": _choice(form.get("compression"), COMPRESSION_OPTIONS, "compression"),
            "dialect": _choice(form.get("dialect"), DIALECT_OPTIONS, "dialect"),
            "defaultCharacterSet": _string(form.get("defaultCharacterSet")) or "utf8mb4",
        }
        for key in ("showProgress", "dryRun", "consistent", "skipConsistencyChecks", "skipUpgradeChecks",
                    "checksum", "chunking", "tzUtc", "ddlOnly", "dataOnly", "users", "events", "routines",
                    "triggers", "libraries", "ocimds"):
            result[key] = _checkbox(form, key)
        result["excludeLakehouseTables"] = _checkbox(form, "excludeLakehouseTables")
        if result["ddlOnly"] and result["dataOnly"]:
            raise ValueError("`ddlOnly` and `dataOnly` cannot both be enabled.")
        if result["chunking"]:
            result["bytesPerChunk"] = _size_option(form.get("bytesPerChunk"), "Bytes per chunk", default="64M")
        if _string(form.get("targetVersion")):
            result["targetVersion"] = _string(form.get("targetVersion"))
        compatibility = [value for value in form.getlist("compatibility") if value in COMPATIBILITY_OPTIONS]
        if compatibility:
            result["compatibility"] = compatibility
    else:
        result = {
            "threads": _positive_int(form.get("threads"), "Threads"),
            "waitDumpTimeout": _nonnegative_float(form.get("waitDumpTimeout") or 0, "Wait dump timeout"),
            "analyzeTables": _choice(form.get("analyzeTables"), ANALYZE_OPTIONS, "analyze tables"),
            "deferTableIndexes": _choice(form.get("deferTableIndexes"), DEFER_INDEX_OPTIONS, "defer indexes"),
            "handleGrantErrors": _choice(form.get("handleGrantErrors"), GRANT_ERROR_OPTIONS, "grant error handling"),
            "updateGtidSet": _choice(form.get("updateGtidSet"), GTID_OPTIONS, "GTID update"),
        }
        for key in ("showProgress", "dryRun", "resetProgress", "skipBinlog", "ignoreVersion", "checksum",
                    "showMetadata", "createInvisiblePKs", "dropExistingObjects", "ignoreExistingObjects",
                    "loadDdl", "loadData", "loadUsers", "loadIndexes"):
            result[key] = _checkbox(form, key)
        if result["dropExistingObjects"] and result["ignoreExistingObjects"]:
            raise ValueError("`dropExistingObjects` and `ignoreExistingObjects` cannot both be enabled.")
        background = _positive_int(form.get("backgroundThreads"), "Background threads", optional=True)
        if background is not None:
            result["backgroundThreads"] = background
        for key in ("schema", "characterSet"):
            if _string(form.get(key)):
                result[key] = _string(form.get(key))
        if _string(form.get("maxBytesPerTransaction")):
            result["maxBytesPerTransaction"] = _size_option(
                form.get("maxBytesPerTransaction"), "Maximum bytes per transaction"
            )
        statements = [_string(line) for line in str(form.get("sessionInitSql") or "").replace("\r", "").splitlines() if _string(line)]
        if statements:
            result["sessionInitSql"] = statements

    for item, (include_key, exclude_key) in FILTER_KEYS.items():
        includes = _json_list(form.get(f"include_{item}_json"), f"Include {item}")
        excludes = _json_list(form.get(f"exclude_{item}_json"), f"Exclude {item}")
        overlap = sorted(set(includes) & set(excludes))
        if overlap:
            raise ValueError(f"The same {item} cannot be both included and excluded: {', '.join(overlap)}")
        if includes:
            result[include_key] = includes
        if excludes:
            result[exclude_key] = excludes

    result.update(_advanced_options(form.get("advanced_json"), KNOWN_FIELDS[kind]))
    return result


def merge_lakehouse_exclusions(options, lakehouse_tables):
    """Construct mysqlsh excludeTables without leaking the UI-only control."""
    result = dict(options or {})
    enabled = bool(result.pop("excludeLakehouseTables", False))
    if not enabled:
        return result, []
    existing = list(result.get("excludeTables") or [])
    merged = []
    for table_name in [*existing, *(lakehouse_tables or [])]:
        candidate = _string(table_name)
        if candidate and candidate not in merged:
            merged.append(candidate)
    if merged:
        result["excludeTables"] = merged
    return result, list(lakehouse_tables or [])


def fetch_lakehouse_table_exclusions(mysql_connection, schema_names=None):
    schemas = sorted({_string(name) for name in (schema_names or []) if _string(name)})
    sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE UPPER(engine) = 'LAKEHOUSE' "
        "AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys') "
        "AND table_schema NOT LIKE 'mysql\\_%'"
    )
    params = []
    if schemas:
        sql += " AND table_schema IN (" + ", ".join(["%s"] * len(schemas)) + ")"
        params = schemas
    sql += " ORDER BY table_schema, table_name"
    with mysql_connection(connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
    exclusions = []
    for row in rows:
        schema = _string(row.get("table_schema"))
        table = _string(row.get("table_name"))
        if schema and table:
            exclusions.append(f"`{schema.replace('`', '``')}`.`{table.replace('`', '``')}`")
    return exclusions


def fetch_filter_catalog(mysql_connection):
    catalog = {item: [] for item in FILTER_TYPES}
    catalog["errors"] = {}
    queries = {
        "schemas": ("SELECT schema_name AS value, schema_name AS label FROM information_schema.schemata WHERE schema_name NOT IN ('information_schema','mysql','performance_schema','sys') AND schema_name NOT LIKE 'mysql\\_%' ORDER BY schema_name", ()),
        "tables": ("SELECT CONCAT(table_schema, '.', table_name) AS value, CONCAT(table_schema, '.', table_name) AS label FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') AND table_schema NOT LIKE 'mysql\\_%' ORDER BY table_schema, table_name", ()),
        "users": ("SELECT CONCAT(QUOTE(User), '@', QUOTE(Host)) AS value, CONCAT(QUOTE(User), '@', QUOTE(Host)) AS label FROM mysql.user ORDER BY User, Host", ()),
        "events": ("SELECT CONCAT(event_schema, '.', event_name) AS value, CONCAT(event_schema, '.', event_name) AS label FROM information_schema.events WHERE event_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY event_schema, event_name", ()),
        "routines": ("SELECT CONCAT(routine_schema, '.', routine_name) AS value, CONCAT(routine_schema, '.', routine_name, ' (', routine_type, ')') AS label FROM information_schema.routines WHERE routine_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY routine_schema, routine_type, routine_name", ()),
        "triggers": ("SELECT CONCAT(trigger_schema, '.', event_object_table, '.', trigger_name) AS value, CONCAT(trigger_schema, '.', event_object_table, '.', trigger_name) AS label FROM information_schema.triggers WHERE trigger_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY trigger_schema, event_object_table, trigger_name", ()),
        "libraries": ("SELECT CONCAT(library_schema, '.', library_name) AS value, CONCAT(library_schema, '.', library_name) AS label FROM information_schema.libraries WHERE library_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY library_schema, library_name", ()),
    }
    try:
        connection_context = mysql_connection(connect_timeout=5)
        connection = connection_context.__enter__()
    except Exception as error:
        message = str(error)
        catalog["errors"] = {item: message for item in FILTER_TYPES}
        return catalog
    try:
        cursor_context = connection.cursor()
        cursor = cursor_context.__enter__()
    except Exception as error:
        connection_context.__exit__(None, None, None)
        message = str(error)
        catalog["errors"] = {item: message for item in FILTER_TYPES}
        return catalog
    try:
        for item, (sql, params) in queries.items():
            try:
                cursor.execute(sql, list(params))
                rows = cursor.fetchall() or []
                catalog[item] = [
                    {"value": _string(row.get("value")), "label": _string(row.get("label"))}
                    for row in rows if _string(row.get("value"))
                ]
            except Exception as error:
                catalog["errors"][item] = str(error)
    finally:
        cursor_context.__exit__(None, None, None)
        connection_context.__exit__(None, None, None)
    return catalog
