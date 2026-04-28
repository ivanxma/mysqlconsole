def _empty_report():
    return {"columns": [], "rows": [], "error": ""}


NORMAL_LOAD_STATUSES = {
    "AVAIL_RPDGTABSTATE",
    "AVAIL_RPDSTABSTATE",
}


def _first_defined_value(row, candidate_keys):
    lowered_row = None
    for key in candidate_keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
        if lowered_row is None:
            lowered_row = {str(existing_key).lower(): existing_value for existing_key, existing_value in row.items()}
        value = lowered_row.get(str(key).lower())
        if value not in (None, ""):
            return value
    return ""


def _normalize_identifier(value):
    return str(value or "").strip().strip("`")


def _split_qualified_name(value):
    normalized = _normalize_identifier(value)
    if not normalized or "." not in normalized:
        return "", normalized
    database_name, table_name = normalized.split(".", 1)
    return _normalize_identifier(database_name), _normalize_identifier(table_name)


def _normalize_progress_value(value):
    if value in (None, ""):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= numeric_value <= 1.0:
        return numeric_value * 100.0
    return numeric_value


def _extract_progress_value(row):
    return _normalize_progress_value(
        _first_defined_value(
            row,
            [
                "rpd_tables__load_progress",
                "rpd_tables__load_percentage",
                "rpd_tables__load_percent",
                "rpd_tables__percent_loaded",
                "rpd_tables__load_pct",
                "rpd_tables__availability_percentage",
                "rpd_tables__availability_percent",
            ],
        )
    )


def _extract_load_status_value(row):
    return str(
        _first_defined_value(
            row,
            [
                "rpd_tables__load_status",
                "rpd_tables__status",
                "rpd_tables__recovery_status",
                "rpd_tables__availability_status",
            ],
        )
        or ""
    ).strip()


def _format_progress_value(progress_value):
    if progress_value is None:
        return "-"
    rounded_value = round(progress_value, 3)
    if abs(rounded_value - round(rounded_value)) < 0.0001:
        return f"{int(round(rounded_value))}%"
    return f"{rounded_value:.3f}%"


def _derive_load_state(row):
    progress_value = _extract_progress_value(row)
    if progress_value is not None:
        if progress_value >= 99.999:
            return "loaded"
        if progress_value > 0:
            return "partial"
        return "not_loaded"

    raw_status = _extract_load_status_value(row).lower()
    numeric_status = _normalize_progress_value(raw_status)
    if numeric_status is not None:
        if numeric_status >= 99.999:
            return "loaded"
        if numeric_status > 0:
            return "partial"
        return "partial"
    if raw_status and any(token in raw_status for token in ("loaded", "complete", "available", "active", "healthy")):
            return "loaded"

    load_start = _first_defined_value(row, ["rpd_tables__load_start_timestamp"])
    load_end = _first_defined_value(row, ["rpd_tables__load_end_timestamp"])
    if load_end not in (None, ""):
        return "loaded"
    if load_start not in (None, ""):
        return "partial"
    return "partial"


def _derive_health_class(row, load_state, load_status_value, progress_value):
    normalized_status = load_status_value.upper()
    if normalized_status and normalized_status not in NORMAL_LOAD_STATUSES:
        return "error"
    if progress_value is not None:
        if progress_value >= 99.999:
            return "loaded"
        if progress_value > 0:
            return "progress"
        return "neutral"
    if load_state == "loaded":
        return "loaded"
    if load_state == "partial":
        return "progress"
    return "neutral"


def _derive_inventory_labels(row):
    raw_name = _first_defined_value(
        row,
        [
            "rpd_table_id__name",
            "rpd_table_id__table_name",
            "rpd_tables__name",
            "rpd_tables__table_name",
        ],
    )
    parsed_database_name, parsed_table_name = _split_qualified_name(raw_name)

    database_name = _normalize_identifier(
        _first_defined_value(
            row,
            [
                "rpd_table_id__schema_name",
                "rpd_table_id__database_name",
                "rpd_table_id__table_schema",
                "rpd_table_id__schema",
                "rpd_table_id__db_name",
                "rpd_tables__schema_name",
                "rpd_tables__database_name",
                "rpd_tables__table_schema",
                "rpd_tables__schema",
                "rpd_tables__db_name",
            ],
        )
    )
    if not database_name:
        database_name = parsed_database_name or "Unknown"

    table_name_raw = _normalize_identifier(
        _first_defined_value(
            row,
            [
                "rpd_table_id__table_name",
                "rpd_tables__table_name",
            ],
        )
    )
    parsed_table_database_name, parsed_table_name = _split_qualified_name(table_name_raw)
    table_name = parsed_table_name if parsed_table_database_name else table_name_raw
    if not database_name and parsed_table_database_name:
        database_name = parsed_table_database_name
    if not table_name:
        table_name = parsed_table_name or _normalize_identifier(raw_name)
    if not table_name:
        table_name = str(
            _first_defined_value(row, ["rpd_table_id__id", "rpd_tables__id"]) or "Unknown Table"
        ).strip()

    full_table_name = f"{database_name}.{table_name}" if database_name != "Unknown" else table_name
    return {
        "database_name": database_name,
        "table_label": table_name,
        "full_table_name": full_table_name,
    }


def _safe_report(fetcher, empty_report=None):
    try:
        report = fetcher()
        report["error"] = ""
        return report
    except Exception as error:  # pragma: no cover - depends on server features
        report = dict(empty_report or _empty_report())
        report["error"] = str(error)
        return report


def _safe_items(fetcher):
    try:
        return fetcher(), ""
    except Exception as error:  # pragma: no cover - depends on server features
        return [], str(error)


def _build_heatwave_inventory_rows(inventory_report):
    load_state_labels = {
        "loaded": "Fully Loaded",
        "partial": "Partially Loaded",
        "not_loaded": "Not Loaded",
    }
    enriched_rows = []
    for raw_row in inventory_report["rows"]:
        row = dict(raw_row)
        row.update(_derive_inventory_labels(raw_row))
        progress_value = _extract_progress_value(raw_row)
        load_status_value = _extract_load_status_value(raw_row)
        row["load_state"] = _derive_load_state(raw_row)
        row["load_state_label"] = load_state_labels[row["load_state"]]
        row["load_progress_value"] = progress_value
        row["load_progress_label"] = _format_progress_value(progress_value)
        row["load_status_value"] = load_status_value or "-"
        recovery_source = str(
            _first_defined_value(raw_row, ["rpd_tables__recovery_source", "rpd_table_id__recovery_source"]) or ""
        ).strip()
        row["recovery_source_label"] = recovery_source or "-"
        row["is_fully_loaded"] = row["load_state"] == "loaded"
        row["is_heatwave"] = False
        row["is_lakehouse"] = False
        row["lakehouse_definition_label"] = "-"
        row["health_class"] = _derive_health_class(raw_row, row["load_state"], load_status_value, progress_value)
        row["table_key"] = _normalize_table_key(row["database_name"], row["table_label"]) or row["full_table_name"].lower()
        enriched_rows.append(row)

    enriched_rows.sort(
        key=lambda item: (
            item["database_name"].lower(),
            item["table_label"].lower(),
            str(_first_defined_value(item, ["rpd_table_id__id", "rpd_tables__id"])),
        )
    )
    return enriched_rows


def _build_heatwave_inventory_groups(rows):
    groups = []
    current_group = None

    for row in rows:
        if current_group is None or current_group["database_name"] != row["database_name"]:
            if current_group is not None:
                groups.append(current_group)
            current_group = {
                "database_name": row["database_name"],
                "rows": [],
                "row_count": 0,
                "loaded_count": 0,
                "partial_count": 0,
                "not_loaded_count": 0,
                "heatwave_count": 0,
                "lakehouse_count": 0,
                "open_by_default": False,
            }

        current_group["rows"].append(row)
        current_group["row_count"] += 1
        if row["load_state"] == "loaded":
            current_group["loaded_count"] += 1
        elif row["load_state"] == "partial":
            current_group["partial_count"] += 1
        else:
            current_group["not_loaded_count"] += 1
        if row["is_heatwave"]:
            current_group["heatwave_count"] += 1
        if row["is_lakehouse"]:
            current_group["lakehouse_count"] += 1

    if current_group is not None:
        groups.append(current_group)

    if groups:
        groups[0]["open_by_default"] = True
    return groups


def _normalize_table_key(database_name, table_name):
    normalized_database = _normalize_identifier(database_name)
    normalized_table = _normalize_identifier(table_name)
    if not normalized_database or not normalized_table:
        return ""
    return f"{normalized_database}.{normalized_table}".lower()


def _build_secondary_engine_lookup(configured_tables):
    lookup = {}
    normalized_rows = []
    for row in configured_tables:
        database_name = _normalize_identifier(row.get("database_name"))
        table_name = _normalize_identifier(row.get("table_name"))
        table_key = _normalize_table_key(database_name, table_name)
        if not table_key:
            continue
        normalized_row = {
            "database_name": database_name,
            "table_name": table_name,
            "table_key": table_key,
            "row_count": row.get("row_count", "-"),
            "create_options": row.get("create_options", "") or "",
        }
        lookup[table_key] = normalized_row
        normalized_rows.append(normalized_row)
    normalized_rows.sort(key=lambda item: (item["database_name"].lower(), item["table_name"].lower()))
    return normalized_rows, lookup


def _build_lakehouse_lookup(lakehouse_tables):
    lookup = {}
    normalized_rows = []
    for row in lakehouse_tables:
        database_name = _normalize_identifier(row.get("database_name"))
        table_name = _normalize_identifier(row.get("table_name"))
        table_key = _normalize_table_key(database_name, table_name)
        if not table_key:
            continue
        definition_label = str(row.get("engine") or "").strip() or str(row.get("create_options") or "").strip() or "-"
        normalized_row = {
            "database_name": database_name,
            "table_name": table_name,
            "table_key": table_key,
            "engine": row.get("engine", "-") or "-",
            "create_options": row.get("create_options", "") or "",
            "definition_label": definition_label,
        }
        lookup[table_key] = normalized_row
        normalized_rows.append(normalized_row)
    normalized_rows.sort(key=lambda item: (item["database_name"].lower(), item["table_name"].lower()))
    return normalized_rows, lookup


def _apply_inventory_membership(inventory_rows, configured_lookup, lakehouse_lookup):
    for row in inventory_rows:
        row["is_heatwave"] = True
        row["is_secondary_engine_configured"] = row["table_key"] in configured_lookup
        lakehouse_row = lakehouse_lookup.get(row["table_key"])
        row["is_lakehouse"] = lakehouse_row is not None
        row["lakehouse_definition_label"] = lakehouse_row["definition_label"] if lakehouse_row else "-"
    return inventory_rows


def _build_heatwave_table_rows(configured_tables, inventory_rows):
    inventory_by_key = {row["table_key"]: row for row in inventory_rows}
    heatwave_rows = []
    for row in configured_tables:
        table_key = row["table_key"]
        tracked_row = inventory_by_key.get(table_key)
        load_state = tracked_row["load_state"] if tracked_row else "not_loaded"
        load_state_label = tracked_row["load_state_label"] if tracked_row else "Not Loaded"
        progress_value = tracked_row["load_progress_value"] if tracked_row else 0.0
        progress_label = tracked_row["load_progress_label"] if tracked_row and tracked_row["load_progress_label"] != "-" else "0%"
        load_status_value = tracked_row["load_status_value"] if tracked_row else "-"
        health_class = tracked_row["health_class"] if tracked_row else "neutral"
        heatwave_rows.append(
            {
                "database_name": row["database_name"],
                "table_name": row["table_name"],
                "table_key": table_key,
                "row_count": row["row_count"],
                "create_options": row["create_options"],
                "load_state": load_state,
                "load_state_label": load_state_label,
                "load_progress_value": progress_value,
                "load_progress_label": progress_label,
                "load_status_value": load_status_value,
                "recovery_source_label": tracked_row["recovery_source_label"] if tracked_row else "-",
                "health_class": health_class,
                "is_lakehouse": bool(tracked_row and tracked_row["is_lakehouse"]),
            }
        )
    heatwave_rows.sort(key=lambda item: (item["database_name"].lower(), item["table_name"].lower()))
    return heatwave_rows


def _build_lakehouse_rows(lakehouse_tables, inventory_rows):
    inventory_by_key = {row["table_key"]: row for row in inventory_rows}
    lakehouse_rows = []
    for row in lakehouse_tables:
        tracked_row = inventory_by_key.get(row["table_key"])
        load_state = tracked_row["load_state"] if tracked_row else "not_loaded"
        load_state_label = tracked_row["load_state_label"] if tracked_row else "Not Loaded"
        progress_value = tracked_row["load_progress_value"] if tracked_row else 0.0
        progress_label = tracked_row["load_progress_label"] if tracked_row and tracked_row["load_progress_label"] != "-" else "0%"
        load_status_value = tracked_row["load_status_value"] if tracked_row else "-"
        health_class = tracked_row["health_class"] if tracked_row else "neutral"
        is_healthy = bool(tracked_row and tracked_row["load_state"] == "loaded" and tracked_row["health_class"] == "loaded")
        lakehouse_rows.append(
            {
                "database_name": row["database_name"],
                "table_name": row["table_name"],
                "table_key": row["table_key"],
                "definition_label": row["definition_label"],
                "load_state": load_state,
                "load_state_label": load_state_label,
                "load_progress_value": progress_value,
                "load_progress_label": progress_label,
                "load_status_value": load_status_value,
                "recovery_source_label": tracked_row["recovery_source_label"] if tracked_row else "-",
                "health_class": health_class,
                "is_healthy": is_healthy,
            }
        )
    lakehouse_rows.sort(key=lambda item: (item["database_name"].lower(), item["table_name"].lower()))
    return lakehouse_rows


def build_heatwave_tables_context(
    *,
    fetch_heatwave_inventory_report,
    fetch_heatwave_status_variable_report,
    fetch_heatwave_nodes_report,
    fetch_heatwave_defined_secondary_engine_tables,
    fetch_lakehouse_engine_tables,
):
    inventory_report = _safe_report(
        fetch_heatwave_inventory_report,
        {"columns": [], "rows": [], "table_id_columns": [], "tables_columns": []},
    )
    status_report = _safe_report(fetch_heatwave_status_variable_report)
    nodes_report = _safe_report(fetch_heatwave_nodes_report)
    configured_tables, configured_secondary_engine_error = _safe_items(fetch_heatwave_defined_secondary_engine_tables)
    lakehouse_tables, lakehouse_error = _safe_items(fetch_lakehouse_engine_tables)

    inventory_rows = _build_heatwave_inventory_rows(inventory_report)
    configured_tables, configured_lookup = _build_secondary_engine_lookup(configured_tables)
    lakehouse_tables, lakehouse_lookup = _build_lakehouse_lookup(lakehouse_tables)
    inventory_rows = _apply_inventory_membership(inventory_rows, configured_lookup, lakehouse_lookup)
    inventory_groups = _build_heatwave_inventory_groups(inventory_rows)
    heatwave_rows = inventory_rows
    loaded_rows = [row for row in heatwave_rows if row["load_state"] == "loaded"]
    partial_rows = [row for row in heatwave_rows if row["load_state"] == "partial"]
    not_loaded_rows = [row for row in heatwave_rows if row["load_state"] == "not_loaded"]
    lakehouse_rows = _build_lakehouse_rows(lakehouse_tables, inventory_rows)
    lakehouse_needs_attention_rows = [row for row in lakehouse_rows if not row["is_healthy"]]
    secondary_engine_not_loaded_rows = _build_heatwave_table_rows(configured_tables, inventory_rows)
    secondary_engine_not_loaded_rows = [row for row in secondary_engine_not_loaded_rows if row["load_state"] != "loaded"]

    export_columns = [
        "database_name",
        "table_label",
        "full_table_name",
        "is_heatwave",
        "is_lakehouse",
        "load_progress_label",
        "load_status_value",
        "load_state_label",
        "recovery_source_label",
    ]
    export_columns.extend(inventory_report.get("table_id_columns", []))
    export_columns.extend(inventory_report.get("tables_columns", []))

    export_rows = []
    for row in inventory_rows:
        export_row = {
            "database_name": row["database_name"],
            "table_label": row["table_label"],
            "full_table_name": row["full_table_name"],
            "is_heatwave": "yes" if row["is_heatwave"] else "no",
            "is_lakehouse": "yes" if row["is_lakehouse"] else "no",
            "load_progress_label": row["load_progress_label"],
            "load_status_value": row["load_status_value"],
            "load_state_label": row["load_state_label"],
            "recovery_source_label": row["recovery_source_label"],
        }
        for column_name in inventory_report.get("table_id_columns", []):
            export_row[column_name] = row.get(column_name)
        for column_name in inventory_report.get("tables_columns", []):
            export_row[column_name] = row.get(column_name)
        export_rows.append(export_row)

    return {
        "inventory_groups": inventory_groups,
        "table_id_columns": inventory_report.get("table_id_columns", []),
        "tables_columns": inventory_report.get("tables_columns", []),
        "inventory_error": inventory_report.get("error", ""),
        "status_report": status_report,
        "nodes_report": nodes_report,
        "total_heatwave_tables": len(heatwave_rows),
        "loaded_rows": loaded_rows,
        "partial_rows": partial_rows,
        "not_loaded_rows": not_loaded_rows,
        "fully_loaded_count": len(loaded_rows),
        "partially_loaded_count": len(partial_rows),
        "not_loaded_count": len(not_loaded_rows),
        "lakehouse_table_count": len(lakehouse_tables),
        "lakehouse_rows": lakehouse_rows,
        "healthy_lakehouse_count": sum(1 for row in lakehouse_rows if row["is_healthy"]),
        "lakehouse_needs_attention_count": len(lakehouse_needs_attention_rows),
        "lakehouse_needs_attention_rows": lakehouse_needs_attention_rows,
        "secondary_engine_not_loaded_count": len(secondary_engine_not_loaded_rows),
        "secondary_engine_not_loaded_rows": secondary_engine_not_loaded_rows,
        "configured_secondary_engine_error": configured_secondary_engine_error,
        "lakehouse_error": lakehouse_error,
        "export_columns": export_columns,
        "export_rows": export_rows,
    }


def build_heatwave_tables_export(report):
    return {
        "filename": "heatwave-table-inventory.csv",
        "columns": report["export_columns"],
        "rows": report["export_rows"],
    }


def fetch_heatwave_management_summary(*, execute_query):
    summary = {
        "variables": [],
        "plugins": [],
        "load_errors": [],
    }
    try:
        summary["variables"] = execute_query("SHOW GLOBAL VARIABLES LIKE 'rapid%%'")
    except Exception as error:  # pragma: no cover - depends on server features
        summary["load_errors"].append(str(error))
    try:
        summary["plugins"] = execute_query(
            """
            SELECT
              plugin_name AS plugin_name_value,
              plugin_status AS plugin_status_value
            FROM information_schema.plugins
            WHERE plugin_name LIKE 'rapid%%' OR plugin_name LIKE 'heatwave%%'
            ORDER BY plugin_name
            """
        )
    except Exception as error:  # pragma: no cover - depends on server features
        summary["load_errors"].append(str(error))
    return summary


def handle_heatwave_management_action(action, selected_database, selected_table, *, quote_identifier, execute_statement):
    normalized_action = str(action or "").strip()
    normalized_database = str(selected_database or "").strip()
    normalized_table = str(selected_table or "").strip()

    if not normalized_database or not normalized_table:
        raise ValueError("Choose both database and table before running a HeatWave action.")

    safe_database = quote_identifier(normalized_database)
    safe_table = quote_identifier(normalized_table)

    if normalized_action == "configure_load":
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_ENGINE RAPID")
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_LOAD")
        return {
            "flash_category": "success",
            "flash_message": f"HeatWave load requested for `{normalized_database}.{normalized_table}`.",
            "redirect_values": {"database": normalized_database},
        }

    if normalized_action == "unload":
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_UNLOAD")
        return {
            "flash_category": "success",
            "flash_message": f"HeatWave unload requested for `{normalized_database}.{normalized_table}`.",
            "redirect_values": {"database": normalized_database},
        }

    if normalized_action == "drop_secondary_engine":
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_ENGINE = NULL")
        return {
            "flash_category": "success",
            "flash_message": f"HeatWave secondary engine removed for `{normalized_database}.{normalized_table}`.",
            "redirect_values": {"database": normalized_database},
        }

    raise ValueError("Unknown HeatWave action.")


def build_heatwave_management_context(
    selected_database,
    *,
    fetch_database_inventory,
    fetch_tables_for_database,
    execute_query,
    load_object_storage_config,
):
    normalized_database = str(selected_database or "").strip()
    return {
        "database_inventory": [row for row in fetch_database_inventory() if not row["is_system"]],
        "selected_database": normalized_database,
        "tables": fetch_tables_for_database(normalized_database) if normalized_database else [],
        "management_summary": fetch_heatwave_management_summary(execute_query=execute_query),
        "object_storage_config": load_object_storage_config(),
    }
