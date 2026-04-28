import json
import re


STATUS_VARIABLE_SECTIONS = [
    {"key": "replication", "label": "Replication"},
    {"key": "performance_schema", "label": "Performance Schema"},
    {"key": "heatwave_rapid", "label": "HeatWave (rapid)"},
    {"key": "innodb", "label": "InnoDB"},
    {"key": "full_text", "label": "Full Text"},
    {"key": "mysqlx_specific", "label": "MySQLX Specific"},
    {"key": "security", "label": "Security"},
    {"key": "query_performance", "label": "Query Performance related"},
    {"key": "connection_threads", "label": "Connection & Threads"},
    {"key": "general", "label": "General"},
]


def _first_available_column(column_lookup, candidates):
    for candidate in candidates:
        actual_name = column_lookup.get(candidate.lower())
        if actual_name:
            return actual_name
    return None


def _fetch_table_column_lookup(schema_name, table_name, execute_query):
    rows = execute_query(
        """
        SELECT
          column_name AS column_name_value
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        [schema_name, table_name],
        database="information_schema",
    )
    return {row["column_name_value"].lower(): row["column_name_value"] for row in rows}


def _empty_status_variable_page(active_tab):
    normalized_tab = "variables" if str(active_tab or "").strip().lower() == "variables" else "status"
    return {
        "tab": normalized_tab,
        "tab_label": "Global Variables" if normalized_tab == "variables" else "Global Status",
        "show_source_details": normalized_tab == "variables",
        "total_count": 0,
        "non_empty_count": 0,
        "sections": [
            {
                "key": section["key"],
                "label": section["label"],
                "rows": [],
                "row_count": 0,
                "open_by_default": False,
            }
            for section in STATUS_VARIABLE_SECTIONS
        ],
    }


def build_empty_status_variable_page(active_tab):
    return _empty_status_variable_page(active_tab)


def _format_status_variable_source(raw_source):
    source = str(raw_source or "").strip()
    if not source:
        return ""
    return source.replace("_", " ").title()


def _normalize_status_variable_row(row):
    name = str(
        row.get("Variable_name")
        or row.get("variable_name")
        or row.get("metric_name")
        or row.get("variable_name_value")
        or ""
    ).strip()
    raw_value = (
        row.get("Value")
        if "Value" in row
        else row.get("value")
        if "value" in row
        else row.get("metric_value")
        if "metric_value" in row
        else row.get("variable_value")
    )
    raw_source = (
        row.get("variable_source")
        if "variable_source" in row
        else row.get("variable_source_value")
        if "variable_source_value" in row
        else row.get("source")
    )
    raw_path = (
        row.get("variable_path")
        if "variable_path" in row
        else row.get("variable_path_value")
        if "variable_path_value" in row
        else row.get("path")
    )
    return {
        "name": name,
        "value": "" if raw_value is None else str(raw_value),
        "source": _format_status_variable_source(raw_source),
        "path": str(raw_path or "").strip(),
    }


def _classify_status_variable(name):
    lowered = str(name or "").strip().lower()
    if not lowered:
        return "general"
    if lowered.startswith(("innodb_ft_", "ft_", "fts_")) or "_fts_" in lowered:
        return "full_text"
    if lowered.startswith("performance_schema") or "performance_schema" in lowered:
        return "performance_schema"
    if lowered.startswith(
        (
            "ssl_",
            "tls_",
            "admin_ssl_",
            "admin_tls_",
            "validate_password",
            "caching_sha2_password",
            "sha256_password",
            "authentication_",
            "keyring_",
            "component_keyring_",
            "mysql_firewall_",
            "enterprise_encryption",
            "password_",
            "secure_",
        )
    ) or lowered in {
        "auto_generate_certs",
        "default_authentication_plugin",
        "default_password_lifetime",
        "disconnect_on_expired_password",
        "generated_random_password_length",
        "have_openssl",
        "have_ssl",
        "require_secure_transport",
        "table_encryption_privilege_check",
    } or any(
        token in lowered
        for token in (
            "password",
            "ssl",
            "tls",
            "keyring",
            "wallet",
            "tde",
            "encrypt",
            "openssl",
            "kerberos",
            "ldap",
            "private_key",
            "public_key",
            "master_key",
            "key_path",
            "key_file",
            "_cert",
            "_crl",
            "rsa",
        )
    ):
        return "security"
    if lowered.startswith("mysqlx_"):
        return "mysqlx_specific"
    if lowered.startswith(("rapid_", "heatwave_", "secondary_engine", "use_secondary_engine")):
        return "heatwave_rapid"
    if lowered.startswith(
        (
            "replica_",
            "slave_",
            "source_",
            "replication_",
            "rpl_",
            "relay_log",
            "sync_relay_log",
            "master_",
            "binlog_",
            "gtid_",
            "group_replication_",
            "log_replica_updates",
            "log_slave_updates",
        )
    ) or lowered in {"read_only", "super_read_only"}:
        return "replication"
    if lowered.startswith(("innodb_", "innobase_", "have_innodb")):
        return "innodb"
    if lowered.startswith(
        (
            "join_buffer",
            "sort_buffer",
            "read_buffer",
            "read_rnd_buffer",
            "bulk_insert_buffer",
            "preload_buffer_size",
            "query_alloc_block",
            "query_prealloc_size",
            "query_cache",
            "optimizer_",
            "max_execution",
            "flush",
            "transaction_",
            "temptable_",
            "tmp_table_size",
            "max_heap_table_size",
            "table_open_cache",
            "table_definition_cache",
            "stored_program_cache",
            "host_cache_size",
            "range_alloc_block_size",
            "range_optimizer_",
            "parser_max_mem_size",
            "select_",
            "sort_",
            "handler_",
            "created_tmp_",
            "opened_",
            "queries",
            "slow_",
        )
    ) or lowered in {
        "eq_range_index_dive_limit",
        "flush_time",
        "lock_wait_timeout",
        "long_query_time",
        "max_seeks_for_key",
        "max_sort_length",
        "open_files_limit",
        "optimizer_prune_level",
        "optimizer_search_depth",
        "optimizer_trace_limit",
        "optimizer_trace_max_mem_size",
        "optimizer_trace_offset",
        "optimizer_trace_features",
        "sql_buffer_result",
        "sql_select_limit",
        "table_open_cache_instances",
        "table_open_cache_triggers",
        "transaction_alloc_block_size",
        "transaction_prealloc_size",
    } or any(
        token in lowered
        for token in (
            "join_buffer",
            "key_buffer",
            "key_cache",
            "max_execution",
            "optimizer",
            "transaction",
            "flush",
            "tmp_table",
            "table_open_cache",
            "table_definition_cache",
            "stored_program_cache",
            "query_cache",
            "query_alloc",
            "prealloc",
            "_instances",
        )
    ):
        return "query_performance"
    if lowered.startswith(
        (
            "threads_",
            "thread_",
            "connection_",
            "connections",
            "connection_errors_",
            "max_used_connections",
            "aborted_",
            "bytes_received",
            "bytes_sent",
            "socket_",
            "tcp_",
            "net_",
        )
    ) or lowered in {
        "connections",
        "aborted_clients",
        "aborted_connects",
        "locked_connects",
        "max_used_connections",
    }:
        return "connection_threads"
    return "general"


def _group_status_variables(rows, active_tab):
    grouped = _empty_status_variable_page(active_tab)
    section_lookup = {section["key"]: section for section in grouped["sections"]}
    total_count = 0

    for raw_row in rows:
        row = _normalize_status_variable_row(raw_row)
        if not row["name"]:
            continue
        section_key = _classify_status_variable(row["name"])
        section_lookup[section_key]["rows"].append(row)
        total_count += 1

    first_open_key = next(
        (section["key"] for section in grouped["sections"] if section["rows"]),
        grouped["sections"][0]["key"] if grouped["sections"] else "",
    )

    non_empty_count = 0
    for section in grouped["sections"]:
        section["rows"].sort(key=lambda item: item["name"].lower())
        section["row_count"] = len(section["rows"])
        if section["row_count"]:
            non_empty_count += 1
        section["open_by_default"] = section["key"] == first_open_key

    grouped["total_count"] = total_count
    grouped["non_empty_count"] = non_empty_count
    return grouped


def _fetch_grouped_variable_rows(execute_query):
    try:
        global_columns = _fetch_table_column_lookup("performance_schema", "global_variables", execute_query)
        info_columns = _fetch_table_column_lookup("performance_schema", "variables_info", execute_query)
        global_name_column = _first_available_column(global_columns, ["variable_name"])
        global_value_column = _first_available_column(global_columns, ["variable_value"])
        info_name_column = _first_available_column(info_columns, ["variable_name"])
        info_source_column = _first_available_column(info_columns, ["variable_source"])
        info_path_column = _first_available_column(info_columns, ["variable_path"])

        if global_name_column and global_value_column and info_name_column and (info_source_column or info_path_column):
            selected_columns = [
                f"gv.`{global_name_column}` AS variable_name_value",
                f"gv.`{global_value_column}` AS variable_value",
            ]
            if info_source_column:
                selected_columns.append(f"vi.`{info_source_column}` AS variable_source_value")
            if info_path_column:
                selected_columns.append(f"vi.`{info_path_column}` AS variable_path_value")
            return execute_query(
                """
                SELECT
                  {selected_columns}
                FROM performance_schema.global_variables AS gv
                LEFT JOIN performance_schema.variables_info AS vi
                  ON gv.`{global_name_column}` = vi.`{info_name_column}`
                ORDER BY gv.`{global_name_column}`
                """.format(
                    selected_columns=",\n                  ".join(selected_columns),
                    global_name_column=global_name_column,
                    info_name_column=info_name_column,
                ),
                database="performance_schema",
            )
    except Exception:
        pass
    return execute_query("SHOW GLOBAL VARIABLES")


def fetch_grouped_status_variables(active_tab, execute_query):
    normalized_tab = "variables" if str(active_tab or "").strip().lower() == "variables" else "status"
    if normalized_tab == "variables":
        rows = _fetch_grouped_variable_rows(execute_query)
    else:
        rows = execute_query("SHOW GLOBAL STATUS")
    return _group_status_variables(rows, normalized_tab)
