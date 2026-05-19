import os
import re
import subprocess


ERROR_LOG_PRIORITY_OPTIONS = ("Note", "System", "Warning", "Error")
ERROR_LOG_PERIOD_OPTIONS = (
    {"value": "1h", "label": "1 hour", "hours": 1},
    {"value": "2h", "label": "2 hours", "hours": 2},
    {"value": "1d", "label": "1 day", "hours": 24},
    {"value": "all", "label": "ALL", "hours": None},
)

_execute_query = None
_fetch_scalar = None
_fetch_table_column_lookup = None
_fetch_table_column_names = None
_fetch_full_table_report = None
_run_report_query = None
_fetch_replication_overview_info = None
_empty_replication_overview_info = None
_quote_identifier = None
_is_system_schema_name = None
_module_build_dashboard_heatwave_summary = None
_get_session_profile = None


def _format_bytes(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    unit_index = 0
    while number >= 1024 and unit_index < len(units) - 1:
        number /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(number)} {units[unit_index]}"
    return f"{number:.1f} {units[unit_index]}"


def _first_available_column(column_lookup, candidates):
    for candidate in candidates:
        actual_name = column_lookup.get(candidate.lower())
        if actual_name:
            return actual_name
    return None


def configure_dashboard_queries(
    *,
    execute_query,
    fetch_scalar,
    fetch_table_column_lookup,
    fetch_table_column_names,
    fetch_full_table_report,
    run_report_query,
    fetch_replication_overview_info,
    empty_replication_overview_info,
    quote_identifier,
    is_system_schema_name,
    build_dashboard_heatwave_summary,
    get_session_profile,
):
    global _execute_query, _fetch_scalar, _fetch_table_column_lookup, _fetch_table_column_names
    global _fetch_full_table_report, _run_report_query
    global _fetch_replication_overview_info, _empty_replication_overview_info, _quote_identifier
    global _is_system_schema_name, _module_build_dashboard_heatwave_summary, _get_session_profile
    _execute_query = execute_query
    _fetch_scalar = fetch_scalar
    _fetch_table_column_lookup = fetch_table_column_lookup
    _fetch_table_column_names = fetch_table_column_names
    _fetch_full_table_report = fetch_full_table_report
    _run_report_query = run_report_query
    _fetch_replication_overview_info = fetch_replication_overview_info
    _empty_replication_overview_info = empty_replication_overview_info
    _quote_identifier = quote_identifier
    _is_system_schema_name = is_system_schema_name
    _module_build_dashboard_heatwave_summary = build_dashboard_heatwave_summary
    _get_session_profile = get_session_profile


def execute_query(*args, **kwargs):
    if _execute_query is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _execute_query(*args, **kwargs)


def get_session_profile():
    if _get_session_profile is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _get_session_profile()


def fetch_scalar(*args, **kwargs):
    if _fetch_scalar is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _fetch_scalar(*args, **kwargs)


def fetch_table_column_lookup(*args, **kwargs):
    if _fetch_table_column_lookup is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _fetch_table_column_lookup(*args, **kwargs)


def fetch_table_column_names(*args, **kwargs):
    if _fetch_table_column_names is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _fetch_table_column_names(*args, **kwargs)


def fetch_full_table_report(*args, **kwargs):
    if _fetch_full_table_report is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _fetch_full_table_report(*args, **kwargs)


def run_report_query(*args, **kwargs):
    if _run_report_query is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _run_report_query(*args, **kwargs)


def fetch_replication_overview_info(*args, **kwargs):
    if _fetch_replication_overview_info is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _fetch_replication_overview_info(*args, **kwargs)


def empty_replication_overview_info(*args, **kwargs):
    if _empty_replication_overview_info is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _empty_replication_overview_info(*args, **kwargs)


def quote_identifier(*args, **kwargs):
    if _quote_identifier is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _quote_identifier(*args, **kwargs)


def is_system_schema_name(*args, **kwargs):
    if _is_system_schema_name is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _is_system_schema_name(*args, **kwargs)


def module_build_dashboard_heatwave_summary(*args, **kwargs):
    if _module_build_dashboard_heatwave_summary is None:
        raise RuntimeError("dashboard query dependencies are not configured")
    return _module_build_dashboard_heatwave_summary(*args, **kwargs)


def fetch_database_inventory():
    rows = execute_query(
        """
        SELECT
          s.schema_name AS database_name_value,
          COALESCE(table_stats.object_count, 0) AS object_count_value,
          COALESCE(table_stats.base_table_count, 0) AS base_table_count_value,
          COALESCE(table_stats.innodb_table_count, 0) AS innodb_table_count_value,
          COALESCE(table_stats.view_count, 0) AS view_count_value,
          COALESCE(table_stats.data_bytes, 0) AS data_bytes_value,
          COALESCE(table_stats.index_bytes, 0) AS index_bytes_value,
          COALESCE(table_stats.total_bytes, 0) AS total_bytes_value,
          COALESCE(routine_stats.routine_count, 0) AS routine_count_value
        FROM information_schema.schemata AS s
        LEFT JOIN (
          SELECT
            table_schema,
            COUNT(*) AS object_count,
            SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END) AS base_table_count,
            SUM(CASE WHEN UPPER(COALESCE(engine, '')) = 'INNODB' THEN 1 ELSE 0 END) AS innodb_table_count,
            SUM(CASE WHEN table_type = 'VIEW' THEN 1 ELSE 0 END) AS view_count,
            COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length ELSE 0 END), 0) AS data_bytes,
            COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN index_length ELSE 0 END), 0) AS index_bytes,
            COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length + index_length ELSE 0 END), 0) AS total_bytes
          FROM information_schema.tables
          GROUP BY table_schema
        ) AS table_stats
          ON table_stats.table_schema = s.schema_name
        LEFT JOIN (
          SELECT
            routine_schema,
            SUM(CASE WHEN routine_type IN ('PROCEDURE', 'FUNCTION') THEN 1 ELSE 0 END) AS routine_count
          FROM information_schema.routines
          GROUP BY routine_schema
        ) AS routine_stats
          ON routine_stats.routine_schema = s.schema_name
        ORDER BY s.schema_name
        """
    )
    inventory = []
    for row in rows:
        database_name = row["database_name_value"]
        total_bytes = row["total_bytes_value"] or 0
        inventory.append(
            {
                "database_name": database_name,
                "table_count": row["object_count_value"] or 0,
                "object_count": row["object_count_value"] or 0,
                "base_table_count": row["base_table_count_value"] or 0,
                "innodb_table_count": row["innodb_table_count_value"] or 0,
                "view_count": row["view_count_value"] or 0,
                "routine_count": row["routine_count_value"] or 0,
                "procedure_count": row["routine_count_value"] or 0,
                "data_bytes": row["data_bytes_value"] or 0,
                "index_bytes": row["index_bytes_value"] or 0,
                "total_bytes": total_bytes,
                "db_size_label": _format_bytes(total_bytes),
                "is_system": is_system_schema_name(database_name),
            }
        )
    return inventory


def fetch_dashboard_innodb_table_rows():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS table_name_value,
          engine AS engine_value,
          table_rows AS table_rows_value
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND UPPER(COALESCE(engine, '')) = 'INNODB'
          AND table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND table_schema NOT LIKE 'mysql@_%' ESCAPE '@'
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "table_name": row["table_name_value"],
            "engine": row["engine_value"] or "InnoDB",
            "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
        }
        for row in rows
    ]


def fetch_dashboard_view_rows():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS view_name_value
        FROM information_schema.views
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND table_schema NOT LIKE 'mysql@_%' ESCAPE '@'
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "view_name": row["view_name_value"],
        }
        for row in rows
    ]


def fetch_dashboard_routine_rows():
    rows = execute_query(
        """
        SELECT
          routine_schema AS database_name_value,
          routine_type AS routine_type_value,
          routine_name AS routine_name_value
        FROM information_schema.routines
        WHERE routine_type IN ('PROCEDURE', 'FUNCTION')
          AND routine_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND routine_schema NOT LIKE 'mysql@_%' ESCAPE '@'
        ORDER BY routine_schema, routine_type, routine_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "routine_type": row["routine_type_value"] or "-",
            "routine_name": row["routine_name_value"],
        }
        for row in rows
    ]


def fetch_tables_for_database(database_name):
    if not database_name:
        return []
    rows = execute_query(
        """
        SELECT
          table_name AS table_name_value,
          engine AS engine_value,
          table_rows AS table_rows_value,
          table_comment AS table_comment_value,
          create_options AS create_options_value
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
        """,
        [database_name],
    )
    tables = []
    for row in rows:
        create_options = row["create_options_value"] or ""
        heatwave_configured = "SECONDARY_ENGINE=RAPID" in create_options.upper()
        tables.append(
            {
                "table_name": row["table_name_value"],
                "engine": row["engine_value"] or "-",
                "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "table_comment": row["table_comment_value"] or "",
                "create_options": create_options,
                "heatwave_configured": heatwave_configured,
            }
        )
    return tables


def fetch_heatwave_status_variable_report():
    return run_report_query("SHOW GLOBAL STATUS LIKE 'rapid%status'")


def fetch_heatwave_nodes_report():
    return fetch_full_table_report(
        "performance_schema",
        "rpd_nodes",
        order_by_candidates=[
            "node_name",
            "node_id",
            "host_name",
            "hostname",
            "host",
            "address",
            "ip_address",
        ],
        limit=200,
    )


def fetch_heatwave_inventory_report():
    table_id_columns = fetch_table_column_names("performance_schema", "rpd_table_id")
    tables_columns = fetch_table_column_names("performance_schema", "rpd_tables")
    if not table_id_columns:
        raise ValueError("No columns found for performance_schema.rpd_table_id.")
    if not tables_columns:
        raise ValueError("No columns found for performance_schema.rpd_tables.")

    table_id_lookup = {column_name.lower(): column_name for column_name in table_id_columns}
    tables_lookup = {column_name.lower(): column_name for column_name in tables_columns}
    join_pairs = [
        ("id", "id"),
        ("table_id", "table_id"),
        ("name", "name"),
    ]
    join_pair = next(
        (
            (table_id_lookup[left_name], tables_lookup[right_name])
            for left_name, right_name in join_pairs
            if left_name in table_id_lookup and right_name in tables_lookup
        ),
        None,
    )
    if join_pair is None:
        raise ValueError("Unable to determine a join key between performance_schema.rpd_table_id and performance_schema.rpd_tables.")

    selected_columns = []
    table_id_aliases = []
    tables_aliases = []

    for column_name in table_id_columns:
        alias = f"rpd_table_id__{column_name}"
        selected_columns.append(f"tid.{quote_identifier(column_name)} AS {quote_identifier(alias)}")
        table_id_aliases.append(alias)

    for column_name in tables_columns:
        alias = f"rpd_tables__{column_name}"
        selected_columns.append(f"rt.{quote_identifier(column_name)} AS {quote_identifier(alias)}")
        tables_aliases.append(alias)

    sql = """
        SELECT {columns}
        FROM performance_schema.rpd_table_id AS tid
        LEFT JOIN performance_schema.rpd_tables AS rt
          ON tid.{table_id_key} = rt.{tables_key}
    """.format(
        columns=", ".join(selected_columns),
        table_id_key=quote_identifier(join_pair[0]),
        tables_key=quote_identifier(join_pair[1]),
    )

    order_clauses = []
    for candidate in ("schema_name", "database_name", "table_schema", "name", "table_name", "id"):
        actual_name = table_id_lookup.get(candidate)
        if actual_name:
            order_clauses.append(f"tid.{quote_identifier(actual_name)}")
    if order_clauses:
        sql += " ORDER BY " + ", ".join(order_clauses)

    report = run_report_query(sql)
    report["table_id_columns"] = table_id_aliases
    report["tables_columns"] = tables_aliases
    return report


def fetch_heatwave_defined_secondary_engine_tables():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS table_name_value,
          table_rows AS row_count_value,
          create_options AS create_options_value
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND UPPER(COALESCE(create_options, '')) LIKE '%%SECONDARY_ENGINE=RAPID%%'
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "table_name": row["table_name_value"],
            "row_count": row["row_count_value"] if row["row_count_value"] is not None else "-",
            "create_options": row["create_options_value"] or "",
        }
        for row in rows
    ]


def fetch_lakehouse_engine_tables():
    rows = execute_query(
        """
        SELECT
          table_schema AS database_name_value,
          table_name AS table_name_value,
          engine AS engine_value,
          create_options AS create_options_value
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND (
            UPPER(COALESCE(engine, '')) LIKE '%%LAKEHOUSE%%'
            OR UPPER(COALESCE(create_options, '')) LIKE '%%LAKEHOUSE%%'
          )
        ORDER BY table_schema, table_name
        """
    )
    return [
        {
            "database_name": row["database_name_value"],
            "table_name": row["table_name_value"],
            "engine": row["engine_value"] or "-",
            "create_options": row["create_options_value"] or "",
        }
        for row in rows
    ]


def fetch_dashboard_heatwave_summary():
    return module_build_dashboard_heatwave_summary(
        fetch_heatwave_inventory_report=fetch_heatwave_inventory_report,
        fetch_heatwave_defined_secondary_engine_tables=fetch_heatwave_defined_secondary_engine_tables,
        fetch_lakehouse_engine_tables=fetch_lakehouse_engine_tables,
        is_system_schema_name=is_system_schema_name,
    )

SECURITY_COMPONENT_KEYWORDS = (
    "audit",
    "authentication",
    "connection_control",
    "firewall",
    "keyring",
    "password",
    "security",
)


def _is_security_related_name(name):
    lowered_name = str(name or "").strip().lower()
    return any(token in lowered_name for token in SECURITY_COMPONENT_KEYWORDS)


def _component_name_from_urn(component_urn):
    normalized_urn = str(component_urn or "").strip()
    if not normalized_urn:
        return "-"
    return normalized_urn.rsplit("/", 1)[-1]


def fetch_installed_component_rows():
    rows = execute_query(
        """
        SELECT
          component_urn AS component_urn_value
        FROM mysql.component
        ORDER BY component_urn
        """
    )
    component_rows = []
    for row in rows:
        component_urn = row["component_urn_value"] or "-"
        component_name = _component_name_from_urn(component_urn)
        component_rows.append(
            {
                "component_name": component_name,
                "component_urn": component_urn,
                "is_security_related": _is_security_related_name(component_name),
            }
        )
    component_rows.sort(key=lambda item: (not item["is_security_related"], item["component_name"].lower()))
    return component_rows


def fetch_security_feature_rows(installed_components):
    security_rows = []
    seen_keys = set()

    for row in installed_components:
        if not row["is_security_related"]:
            continue
        row_key = ("component", row["component_name"].lower())
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        security_rows.append(
            {
                "feature_type": "Component",
                "feature_name": row["component_name"],
                "status_label": "Installed",
                "is_enabled": True,
                "details": row["component_urn"],
            }
        )

    plugin_rows = execute_query(
        """
        SELECT
          plugin_name AS plugin_name_value,
          plugin_status AS plugin_status_value,
          load_option AS load_option_value
        FROM information_schema.plugins
        ORDER BY plugin_name
        """
    )
    for row in plugin_rows:
        plugin_name = row["plugin_name_value"] or "-"
        if not _is_security_related_name(plugin_name):
            continue
        row_key = ("plugin", str(plugin_name).lower())
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        plugin_status = row["plugin_status_value"] or "-"
        security_rows.append(
            {
                "feature_type": "Plugin",
                "feature_name": plugin_name,
                "status_label": plugin_status,
                "is_enabled": str(plugin_status).strip().upper() in {"ACTIVE", "ENABLED"},
                "details": row["load_option_value"] or "-",
            }
        )

    security_rows.sort(key=lambda item: (not item["is_enabled"], item["feature_name"].lower()))
    return security_rows


def _normalize_show_variable_row(row):
    return {
        "name": (
            row.get("Variable_name")
            or row.get("variable_name")
            or row.get("VARIABLE_NAME")
            or row.get("Name")
            or row.get("name")
            or "-"
        ),
        "value": (
            row.get("Value")
            if "Value" in row
            else row.get("value")
            if "value" in row
            else row.get("VARIABLE_VALUE")
            if "VARIABLE_VALUE" in row
            else row.get("variable_value", "-")
        ),
    }


def fetch_show_variable_rows(kind, patterns):
    rows = []
    seen_names = set()
    for pattern in patterns:
        for row in execute_query(f"SHOW GLOBAL {kind} LIKE %s", [pattern]):
            normalized_row = _normalize_show_variable_row(row)
            row_key = str(normalized_row["name"]).lower()
            if row_key in seen_names:
                continue
            seen_names.add(row_key)
            rows.append(normalized_row)
    rows.sort(key=lambda item: str(item["name"]).lower())
    return rows


def fetch_all_show_variable_rows(kind):
    normalized_kind = str(kind or "").strip().upper()
    if normalized_kind not in {"VARIABLES", "STATUS"}:
        raise ValueError("Unsupported SHOW GLOBAL kind.")
    rows = [_normalize_show_variable_row(row) for row in execute_query(f"SHOW GLOBAL {normalized_kind}")]
    rows.sort(key=lambda item: str(item["name"]).lower())
    return rows


def _is_on_value(value):
    return str(value or "").strip().upper() in {"1", "ON", "YES", "TRUE", "ENABLED", "ACTIVE", "FORCE"}


def _dynamic_table_rows(schema_name, table_name, column_candidates, *, order_by_candidates=None, limit=50):
    column_lookup = fetch_table_column_lookup(schema_name, table_name)
    if not column_lookup:
        return []

    selected_columns = []
    output_columns = []
    for output_name, candidates in column_candidates:
        actual_column = _first_available_column(column_lookup, candidates)
        if not actual_column:
            continue
        selected_columns.append(f"{quote_identifier(actual_column)} AS {quote_identifier(output_name)}")
        output_columns.append(output_name)

    if not selected_columns:
        return []

    safe_schema = quote_identifier(schema_name)
    safe_table = quote_identifier(table_name)
    sql = f"SELECT {', '.join(selected_columns)} FROM {safe_schema}.{safe_table}"
    order_columns = []
    for candidate in order_by_candidates or []:
        actual_column = column_lookup.get(str(candidate).lower())
        if actual_column:
            order_columns.append(quote_identifier(actual_column))
    if order_columns:
        sql += " ORDER BY " + ", ".join(order_columns)
    sql += f" LIMIT {int(limit)}"

    result_rows = []
    for row in execute_query(sql):
        result_rows.append({column: row.get(column) if row.get(column) is not None else "-" for column in output_columns})
    return result_rows


def fetch_audit_security_info(security_features):
    info = {
        "enabled_label": "Off",
        "variables": [],
        "status_rows": [],
        "filter_rows": [],
        "user_rows": [],
        "errors": [],
    }
    try:
        info["variables"] = fetch_show_variable_rows("VARIABLES", ["audit_log%"])
        info["status_rows"] = fetch_show_variable_rows("STATUS", ["audit_log%"])
    except Exception as error:  # pragma: no cover - depends on privileges / server features
        info["errors"].append(str(error))

    try:
        info["filter_rows"] = _dynamic_table_rows(
            "mysql",
            "audit_log_filter",
            [
                ("filter_name", ["name", "filter_name"]),
                ("filter_rule", ["filter", "rule", "definition"]),
            ],
            order_by_candidates=["name", "filter_name"],
            limit=25,
        )
    except Exception as error:  # pragma: no cover - Enterprise Audit only
        info["errors"].append(str(error))

    try:
        info["user_rows"] = _dynamic_table_rows(
            "mysql",
            "audit_log_user",
            [
                ("user", ["user", "username"]),
                ("host", ["host"]),
                ("filter_name", ["filtername", "filter_name", "name"]),
            ],
            order_by_candidates=["user", "host"],
            limit=50,
        )
    except Exception as error:  # pragma: no cover - Enterprise Audit only
        info["errors"].append(str(error))

    audit_feature_enabled = any("audit" in row["feature_name"].lower() and row["is_enabled"] for row in security_features)
    audit_variable_enabled = any(_is_on_value(row["value"]) for row in info["variables"] if str(row["name"]).lower() in {"audit_log", "audit_log_filter_id"})
    if audit_feature_enabled or audit_variable_enabled or info["filter_rows"] or info["user_rows"]:
        info["enabled_label"] = "On"
    return info


def empty_audit_security_info():
    return {
        "enabled_label": "-",
        "variables": [],
        "status_rows": [],
        "filter_rows": [],
        "user_rows": [],
        "errors": [],
    }


def fetch_firewall_security_info(security_features):
    info = {
        "enabled_label": "Off",
        "variables": [],
        "status_rows": [],
        "user_rows": [],
        "rule_rows": [],
        "errors": [],
    }
    try:
        info["variables"] = fetch_show_variable_rows("VARIABLES", ["mysql_firewall%"])
        info["status_rows"] = fetch_show_variable_rows("STATUS", ["mysql_firewall%"])
    except Exception as error:  # pragma: no cover - depends on privileges / server features
        info["errors"].append(str(error))

    try:
        info["user_rows"] = _dynamic_table_rows(
            "mysql",
            "firewall_users",
            [
                ("user_host", ["userhost", "user_host"]),
                ("mode", ["mode"]),
            ],
            order_by_candidates=["userhost", "user_host"],
            limit=50,
        )
    except Exception as error:  # pragma: no cover - Enterprise Firewall only
        info["errors"].append(str(error))
    if not info["user_rows"]:
        try:
            info["user_rows"] = _dynamic_table_rows(
                "information_schema",
                "MYSQL_FIREWALL_USERS",
                [
                    ("user_host", ["userhost", "user_host"]),
                    ("mode", ["mode"]),
                ],
                order_by_candidates=["userhost", "user_host"],
                limit=50,
            )
        except Exception as error:  # pragma: no cover - Enterprise Firewall only
            info["errors"].append(str(error))

    try:
        info["rule_rows"] = _dynamic_table_rows(
            "mysql",
            "firewall_whitelist",
            [
                ("user_host", ["userhost", "user_host"]),
                ("rule", ["rule"]),
            ],
            order_by_candidates=["userhost", "user_host"],
            limit=50,
        )
    except Exception as error:  # pragma: no cover - Enterprise Firewall only
        info["errors"].append(str(error))
    if not info["rule_rows"]:
        try:
            info["rule_rows"] = _dynamic_table_rows(
                "information_schema",
                "MYSQL_FIREWALL_WHITELIST",
                [
                    ("user_host", ["userhost", "user_host"]),
                    ("rule", ["rule"]),
                ],
                order_by_candidates=["userhost", "user_host"],
                limit=50,
            )
        except Exception as error:  # pragma: no cover - Enterprise Firewall only
            info["errors"].append(str(error))

    firewall_feature_enabled = any("firewall" in row["feature_name"].lower() and row["is_enabled"] for row in security_features)
    firewall_variable_enabled = any(_is_on_value(row["value"]) for row in info["variables"] if str(row["name"]).lower() in {"mysql_firewall_mode", "mysql_firewall_trace"})
    if firewall_feature_enabled or firewall_variable_enabled or info["user_rows"] or info["rule_rows"]:
        info["enabled_label"] = "On"
    return info


def empty_firewall_security_info():
    return {
        "enabled_label": "-",
        "variables": [],
        "status_rows": [],
        "user_rows": [],
        "rule_rows": [],
        "errors": [],
    }


def fetch_password_security_info(security_features):
    info = {
        "enabled_label": "Off",
        "variables": [],
        "status_rows": [],
        "errors": [],
    }
    password_variable_patterns = [
        "validate_password%",
        "default_password_lifetime",
        "disconnect_on_expired_password",
        "password_history",
        "password_reuse_interval",
        "password_require_current",
    ]
    try:
        info["variables"] = fetch_show_variable_rows("VARIABLES", password_variable_patterns)
        info["status_rows"] = fetch_show_variable_rows("STATUS", ["validate_password%"])
    except Exception as error:  # pragma: no cover - depends on privileges / server features
        info["errors"].append(str(error))

    password_feature_enabled = any(
        ("password" in row["feature_name"].lower() or "validate_password" in row["feature_name"].lower())
        and row["is_enabled"]
        for row in security_features
    )
    policy_variable_enabled = any(
        str(row["name"]).lower().startswith("validate_password")
        for row in info["variables"]
    )
    if password_feature_enabled or policy_variable_enabled:
        info["enabled_label"] = "On"
    return info


def empty_password_security_info():
    return {
        "enabled_label": "-",
        "variables": [],
        "status_rows": [],
        "errors": [],
    }


def normalize_error_log_period(value):
    candidate = str(value or "").strip().lower()
    allowed = {option["value"]: option for option in ERROR_LOG_PERIOD_OPTIONS}
    return allowed.get(candidate, allowed["1d"])


def fetch_recent_error_log_rows(hours=24, limit=50, priorities=None, error_code="", message_like=""):
    normalized_priorities = _normalize_error_log_priorities(priorities)
    normalized_error_code = normalize_error_log_code(error_code)
    error_code_filter = parse_error_log_code_filter(normalized_error_code)
    error_code_values = error_code_filter["codes"]
    normalized_message_like = normalize_error_log_message_like(message_like)
    column_lookup = fetch_table_column_lookup("performance_schema", "error_log")
    if not column_lookup:
        raise ValueError("performance_schema.error_log is not available on this server.")

    logged_column = column_lookup.get("logged")
    prio_column = column_lookup.get("prio") or column_lookup.get("priority")
    error_code_column = column_lookup.get("error_code")
    subsystem_column = column_lookup.get("subsystem")
    data_column = column_lookup.get("data") or column_lookup.get("message")

    if not logged_column or not data_column:
        raise ValueError("Unable to determine required columns for performance_schema.error_log.")

    selected_columns = [f"{quote_identifier(logged_column)} AS logged_value"]
    if prio_column:
        selected_columns.append(f"{quote_identifier(prio_column)} AS priority_value")
    if error_code_column:
        selected_columns.append(f"{quote_identifier(error_code_column)} AS error_code_value")
    if subsystem_column:
        selected_columns.append(f"{quote_identifier(subsystem_column)} AS subsystem_value")
    selected_columns.append(f"{quote_identifier(data_column)} AS message_value")

    sql = (
        "SELECT {columns} "
        "FROM performance_schema.error_log "
        "WHERE 1 = 1"
    ).format(
        columns=", ".join(selected_columns),
    )
    params = []
    if hours is not None:
        sql += " AND {logged_column} >= NOW() - INTERVAL %s HOUR".format(
            logged_column=quote_identifier(logged_column),
        )
        params.append(int(hours))
    if prio_column and normalized_priorities:
        sql += " AND UPPER(COALESCE({priority_column}, '')) IN ({placeholders})".format(
            priority_column=quote_identifier(prio_column),
            placeholders=", ".join(["%s"] * len(normalized_priorities)),
        )
        params.extend(priority.upper() for priority in normalized_priorities)
    if error_code_column and error_code_values:
        sql += " AND CAST({error_code_column} AS CHAR) {operator} ({placeholders})".format(
            error_code_column=quote_identifier(error_code_column),
            operator=error_code_filter["operator"],
            placeholders=", ".join(["%s"] * len(error_code_values)),
        )
        params.extend(error_code_values)
    if normalized_message_like:
        sql += " AND {data_column} LIKE %s".format(
            data_column=quote_identifier(data_column),
        )
        params.append(f"%{normalized_message_like}%")
    sql += " ORDER BY {logged_column} DESC LIMIT {limit}".format(
        logged_column=quote_identifier(logged_column),
        limit=int(limit),
    )

    rows = execute_query(sql, params)
    return [
        {
            "logged": row["logged_value"],
            "priority": str(row.get("priority_value") or "-"),
            "error_code": row.get("error_code_value") or "-",
            "subsystem": row.get("subsystem_value") or "-",
            "message": row.get("message_value") or "-",
        }
        for row in rows
    ]


def fetch_mysql_shell_version():
    package_checks = [
        (["rpm", "-q", "--qf", "%{VERSION}", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
        (["dpkg-query", "-W", "-f=${Version}", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
        (["brew", "list", "--cask", "--versions", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
        (["brew", "list", "--formula", "--versions", "mysql-shell"], r"[0-9]+(?:\.[0-9]+){2}"),
    ]
    for command, version_pattern in package_checks:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        package_output = (result.stdout or result.stderr or "").strip()
        version_match = re.search(version_pattern, package_output)
        if version_match:
            return version_match.group(0)

    mysqlsh_command = (
        os.environ.get("DBCONSOLE_MYSQLSH")
        or os.environ.get("MYSQLSH")
        or "mysqlsh"
    )
    try:
        mysqlsh_timeout = max(1, int(os.environ.get("DBCONSOLE_MYSQLSH_TIMEOUT", "5")))
    except (TypeError, ValueError):
        mysqlsh_timeout = 5
    try:
        result = subprocess.run(
            [mysqlsh_command, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=mysqlsh_timeout,
            check=False,
        )
    except FileNotFoundError:
        return "Not found"
    except subprocess.TimeoutExpired:
        return f"Timed out after {mysqlsh_timeout}s"
    except Exception as error:  # pragma: no cover - depends on host runtime
        return f"Unavailable: {error}"

    version_output = (result.stdout or result.stderr or "").strip()
    if not version_output:
        return "Unavailable"
    version_match = re.search(r"\b[0-9]+(?:\.[0-9]+){2}\b", version_output)
    if version_match:
        return version_match.group(0)
    return version_output.splitlines()[0]


def fetch_server_overview(
    recent_error_log_priorities=None,
    recent_error_log_period=None,
    recent_error_log_code="",
    recent_error_log_message_like="",
    sections=None,
):
    selected_sections = set(sections or {"server-database", "logs", "security", "heatwave", "replication"})
    selected_error_log_priorities = _normalize_error_log_priorities(recent_error_log_priorities)
    selected_error_log_period = normalize_error_log_period(recent_error_log_period)
    selected_error_log_code = normalize_error_log_code(recent_error_log_code)
    selected_error_log_message_like = normalize_error_log_message_like(recent_error_log_message_like)
    version = fetch_scalar("SELECT VERSION()", default="-")
    hostname = fetch_scalar("SELECT @@hostname", default="-")
    include_server_database = "server-database" in selected_sections
    include_logs = "logs" in selected_sections
    include_security = "security" in selected_sections
    include_heatwave = "heatwave" in selected_sections
    include_replication = "replication" in selected_sections

    mysql_shell_version = fetch_mysql_shell_version() if include_server_database else "-"
    current_user = default_database = global_time_zone = session_time_zone = system_time_zone = "-"
    global_sql_mode = session_sql_mode = server_charset = server_collation = "-"
    max_connections = current_connection_count = database_count = 0
    table_totals = {}
    total_size_bytes = 0
    innodb_table_rows = []
    view_rows = []
    routine_rows = []
    time_zone_name_count = 0
    time_zone_tables_populated = False
    time_zone_tables_label = "-"
    time_zone_tables_error = ""

    if include_server_database:
        current_user = fetch_scalar("SELECT CURRENT_USER()", default="-")
        default_database = fetch_scalar("SELECT DATABASE()", default="-")
        global_time_zone = fetch_scalar("SELECT @@GLOBAL.time_zone", default="-")
        session_time_zone = fetch_scalar("SELECT @@SESSION.time_zone", default="-")
        system_time_zone = fetch_scalar("SELECT @@system_time_zone", default="-")
        global_sql_mode = fetch_scalar("SELECT @@GLOBAL.sql_mode", default="-")
        session_sql_mode = fetch_scalar("SELECT @@SESSION.sql_mode", default="-")
        server_charset = fetch_scalar("SELECT @@character_set_server", default="-")
        server_collation = fetch_scalar("SELECT @@collation_server", default="-")
        max_connections = fetch_scalar("SELECT @@max_connections", default=0)
        threads_connected_rows = execute_query("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
        if threads_connected_rows:
            thread_row = threads_connected_rows[0]
            current_connection_count = int(
                thread_row.get("Value")
                or thread_row.get("value")
                or thread_row.get("VARIABLE_VALUE")
                or thread_row.get("variable_value")
                or 0
            )
        database_count = fetch_scalar(
            """
            SELECT COUNT(*) AS database_count_value
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
              AND schema_name NOT LIKE 'mysql@_%' ESCAPE '@'
            """,
            default=0,
        )
        table_totals = execute_query(
            """
            SELECT
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END), 0) AS base_table_count_value,
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length ELSE 0 END), 0) AS data_bytes_value,
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN index_length ELSE 0 END), 0) AS index_bytes_value,
              COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN data_length + index_length ELSE 0 END), 0) AS total_bytes_value
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
              AND table_schema NOT LIKE 'mysql@_%' ESCAPE '@'
            """,
        )
        table_totals = table_totals[0] if table_totals else {}
        total_size_bytes = table_totals.get("total_bytes_value") or 0
        innodb_table_rows = fetch_dashboard_innodb_table_rows()
        view_rows = fetch_dashboard_view_rows()
        routine_rows = fetch_dashboard_routine_rows()

        try:
            time_zone_name_count = fetch_scalar("SELECT COUNT(*) FROM mysql.time_zone_name", default=0)
            time_zone_tables_populated = bool(time_zone_name_count)
            time_zone_tables_label = f"Yes ({time_zone_name_count} rows)" if time_zone_name_count else "No"
            time_zone_tables_error = ""
        except Exception as error:  # pragma: no cover - depends on privileges / server setup
            time_zone_name_count = 0
            time_zone_tables_populated = False
            time_zone_tables_label = "Unavailable"
            time_zone_tables_error = str(error)

    replication_info = fetch_replication_overview_info() if include_replication else empty_replication_overview_info()
    try:
        rapid_status_rows = execute_query("SHOW GLOBAL STATUS LIKE 'rapid%status'") if include_heatwave else []
    except Exception as error:  # pragma: no cover - depends on server features
        rapid_status_rows = [{"Variable_name": "rapid_status_error", "Value": str(error)}]

    installed_components = []
    installed_components_error = ""
    security_features = []
    security_features_error = ""
    audit_info = empty_audit_security_info()
    firewall_info = empty_firewall_security_info()
    password_info = empty_password_security_info()
    if include_security:
        try:
            installed_components = fetch_installed_component_rows()
            installed_components_error = ""
        except Exception as error:  # pragma: no cover - depends on server features
            installed_components = []
            installed_components_error = str(error)

        try:
            security_features = fetch_security_feature_rows(installed_components)
            security_features_error = ""
        except Exception as error:  # pragma: no cover - depends on server features
            security_features = []
            security_features_error = str(error)

        audit_info = fetch_audit_security_info(security_features)
        firewall_info = fetch_firewall_security_info(security_features)
        password_info = fetch_password_security_info(security_features)

    recent_error_log_rows = []
    recent_error_log_error = ""
    if include_logs:
        try:
            recent_error_log_rows = fetch_recent_error_log_rows(
                hours=selected_error_log_period["hours"],
                limit=50,
                priorities=selected_error_log_priorities,
                error_code=selected_error_log_code,
                message_like=selected_error_log_message_like,
            )
            recent_error_log_error = ""
        except Exception as error:  # pragma: no cover - depends on server features
            recent_error_log_rows = []
            recent_error_log_error = str(error)

    return {
        "server_version": version,
        "server_hostname": hostname,
        "mysql_shell_version": mysql_shell_version,
        "current_user": current_user,
        "default_database": default_database,
        "global_time_zone": global_time_zone,
        "session_time_zone": session_time_zone,
        "system_time_zone": system_time_zone,
        "time_zone_tables_populated": time_zone_tables_populated,
        "time_zone_tables_label": time_zone_tables_label,
        "time_zone_table_row_count": time_zone_name_count,
        "time_zone_tables_error": time_zone_tables_error,
        "global_sql_mode": global_sql_mode,
        "session_sql_mode": session_sql_mode,
        "server_charset": server_charset,
        "server_collation": server_collation,
        "max_connections": max_connections or 0,
        "current_connection_count": current_connection_count,
        "database_count": database_count,
        "table_count": table_totals.get("base_table_count_value") or 0,
        "innodb_table_count": len(innodb_table_rows),
        "view_count": len(view_rows),
        "routine_count": len(routine_rows),
        "procedure_count": len(routine_rows),
        "data_bytes": table_totals.get("data_bytes_value") or 0,
        "index_bytes": table_totals.get("index_bytes_value") or 0,
        "total_size_bytes": total_size_bytes,
        "total_size_label": _format_bytes(total_size_bytes),
        "innodb_table_rows": innodb_table_rows,
        "view_rows": view_rows,
        "routine_rows": routine_rows,
        "replication_info": replication_info,
        "rapid_status_rows": rapid_status_rows[:10],
        "installed_components": installed_components,
        "installed_components_error": installed_components_error,
        "installed_component_count": len(installed_components),
        "security_features": security_features,
        "security_features_error": security_features_error,
        "security_feature_count": len(security_features),
        "enabled_security_feature_count": sum(1 for row in security_features if row["is_enabled"]),
        "audit_info": audit_info,
        "firewall_info": firewall_info,
        "password_info": password_info,
        "error_log_priority_options": list(ERROR_LOG_PRIORITY_OPTIONS),
        "error_log_period_options": list(ERROR_LOG_PERIOD_OPTIONS),
        "selected_error_log_priorities": selected_error_log_priorities,
        "selected_error_log_priority_label": ", ".join(selected_error_log_priorities) if selected_error_log_priorities else "All",
        "selected_error_log_period": selected_error_log_period,
        "selected_error_log_code": selected_error_log_code,
        "selected_error_log_message_like": selected_error_log_message_like,
        "recent_error_log_rows": recent_error_log_rows,
        "recent_error_log_error": recent_error_log_error,
        "recent_error_log_count": len(recent_error_log_rows),
        "connection_endpoint": f"{get_session_profile()['host']}:{get_session_profile()['port']}",
    }

def _normalize_checkbox(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_error_log_priorities(values):
    raw_values = [str(value or "").strip() for value in values or []]
    if any(value.lower() == "all" for value in raw_values):
        return []
    allowed_lookup = {
        str(option).strip().lower(): str(option)
        for option in ERROR_LOG_PRIORITY_OPTIONS
    }
    normalized = []
    seen = set()
    for value in raw_values:
        normalized_value = allowed_lookup.get(str(value or "").strip().lower())
        if not normalized_value or normalized_value in seen:
            continue
        normalized.append(normalized_value)
        seen.add(normalized_value)
    return normalized


def normalize_error_log_code(value):
    return str(value or "").strip()


def parse_error_log_code_filter(value):
    text = normalize_error_log_code(value)
    if not text:
        return {"operator": "IN", "codes": []}
    operator = "IN"
    match = re.match(r"(?is)^\s*(not\s+in|in)\s*\((.*)\)\s*$", text)
    if match:
        operator = "NOT IN" if re.sub(r"\s+", " ", match.group(1).strip().upper()) == "NOT IN" else "IN"
        text = match.group(2)
    raw_items = _split_error_log_code_items(text)
    codes = []
    seen = set()
    for item in raw_items:
        code = _unquote_error_log_code_item(item)
        if not code or code in seen:
            continue
        codes.append(code)
        seen.add(code)
    return {"operator": operator, "codes": codes}


def _split_error_log_code_items(value):
    items = []
    current = []
    quote_char = ""
    escaped = False
    for char in str(value or ""):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote_char:
            current.append(char)
            escaped = True
            continue
        if quote_char:
            current.append(char)
            if char == quote_char:
                quote_char = ""
            continue
        if char in {"'", '"'}:
            quote_char = char
            current.append(char)
            continue
        if char == ",":
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    items.append("".join(current).strip())
    return [item for item in items if item]


def _unquote_error_log_code_item(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.replace("\\'", "'").replace('\\"', '"').strip()


def normalize_error_log_message_like(value):
    return str(value or "").strip()
