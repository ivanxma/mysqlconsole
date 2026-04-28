import re


def _empty_report():
    return {"columns": [], "rows": [], "error": ""}


def _empty_dashboard_summary():
    return {
        "database_rows": [],
        "totals": {
            "configured_table_count": 0,
            "tracked_table_count": 0,
            "heatwave_table_count": 0,
            "lakehouse_table_count": 0,
            "loaded_count": 0,
            "partial_count": 0,
            "not_loaded_count": 0,
        },
        "error": "",
    }


NORMAL_LOAD_STATUSES = {
    "AVAIL_RPDGTABSTATE",
    "AVAIL_RPDSTABSTATE",
}


def _is_normal_load_status(load_status_value):
    normalized_status = str(load_status_value or "").strip().upper()
    if not normalized_status:
        return False
    return normalized_status.startswith("AVAIL_") or normalized_status in NORMAL_LOAD_STATUSES


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
    if normalized_status and not _is_normal_load_status(normalized_status):
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


def build_dashboard_heatwave_summary(
    *,
    fetch_heatwave_inventory_report,
    fetch_heatwave_defined_secondary_engine_tables,
    fetch_lakehouse_engine_tables,
    is_system_schema_name,
):
    summary = _empty_dashboard_summary()
    inventory_report = _safe_report(
        fetch_heatwave_inventory_report,
        {"columns": [], "rows": [], "table_id_columns": [], "tables_columns": []},
    )
    configured_tables, configured_error = _safe_items(fetch_heatwave_defined_secondary_engine_tables)
    configured_tables, _ = _build_secondary_engine_lookup(configured_tables)
    lakehouse_tables, lakehouse_error = _safe_items(fetch_lakehouse_engine_tables)
    lakehouse_tables, _ = _build_lakehouse_lookup(lakehouse_tables)
    inventory_rows = _build_heatwave_inventory_rows(inventory_report)

    configured_keys_by_db = {}
    lakehouse_keys_by_db = {}
    tracked_keys_by_db = {}
    tracked_states_by_key = {}

    for row in configured_tables:
        database_name = row["database_name"]
        table_key = row["table_key"]
        if not database_name or not table_key or is_system_schema_name(database_name):
            continue
        configured_keys_by_db.setdefault(database_name, set()).add(table_key)

    for row in lakehouse_tables:
        database_name = row["database_name"]
        table_key = row["table_key"]
        if not database_name or not table_key or is_system_schema_name(database_name):
            continue
        lakehouse_keys_by_db.setdefault(database_name, set()).add(table_key)

    for row in inventory_rows:
        database_name = row["database_name"]
        table_key = row["table_key"]
        if (
            not database_name
            or database_name == "Unknown"
            or not table_key
            or is_system_schema_name(database_name)
        ):
            continue
        tracked_keys_by_db.setdefault(database_name, set()).add(table_key)
        tracked_states_by_key[table_key] = row["load_state"]

    totals = summary["totals"]
    all_databases = sorted(
        set(configured_keys_by_db) | set(lakehouse_keys_by_db) | set(tracked_keys_by_db),
        key=str.lower,
    )
    for database_name in all_databases:
        configured_keys = configured_keys_by_db.get(database_name, set())
        lakehouse_keys = lakehouse_keys_by_db.get(database_name, set())
        tracked_keys = tracked_keys_by_db.get(database_name, set())
        combined_keys = configured_keys | tracked_keys
        summary_row = {
            "database_name": database_name,
            "configured_table_count": len(configured_keys),
            "tracked_table_count": len(tracked_keys),
            "heatwave_table_count": len(combined_keys),
            "lakehouse_table_count": len(lakehouse_keys),
            "loaded_count": 0,
            "partial_count": 0,
            "not_loaded_count": 0,
        }
        for table_key in combined_keys:
            load_state = tracked_states_by_key.get(table_key, "not_loaded")
            if load_state == "loaded":
                summary_row["loaded_count"] += 1
            elif load_state == "partial":
                summary_row["partial_count"] += 1
            else:
                summary_row["not_loaded_count"] += 1

        for metric_name in totals:
            totals[metric_name] += summary_row[metric_name]
        summary["database_rows"].append(summary_row)

    summary["error"] = " | ".join(
        error_message
        for error_message in (configured_error, lakehouse_error, inventory_report.get("error", ""))
        if error_message
    )
    return summary


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


def _empty_management_database_status(selected_database=""):
    return {
        "selected_database": selected_database,
        "has_selection": bool(selected_database),
        "error": "",
        "summary": {
            "configured_table_count": 0,
            "tracked_table_count": 0,
            "heatwave_table_count": 0,
            "loaded_count": 0,
            "partial_count": 0,
            "not_loaded_count": 0,
        },
        "rows": [],
    }


def _build_management_database_status(selected_database, configured_tables, inventory_rows):
    database_status = _empty_management_database_status(selected_database)
    normalized_database = str(selected_database or "").strip().lower()
    if not normalized_database:
        return database_status

    configured_rows = [
        row for row in configured_tables if str(row.get("database_name") or "").strip().lower() == normalized_database
    ]
    inventory_rows = [
        row for row in inventory_rows if str(row.get("database_name") or "").strip().lower() == normalized_database
    ]
    configured_rows, configured_lookup = _build_secondary_engine_lookup(configured_rows)
    inventory_by_key = {row["table_key"]: row for row in inventory_rows}
    combined_keys = sorted(set(configured_lookup) | set(inventory_by_key), key=str.lower)

    for table_key in combined_keys:
        configured_row = configured_lookup.get(table_key)
        tracked_row = inventory_by_key.get(table_key)
        database_status["rows"].append(
            {
                "table_name": (
                    configured_row["table_name"]
                    if configured_row
                    else (tracked_row["table_label"] if tracked_row else "")
                ),
                "secondary_engine_configured": configured_row is not None,
                "tracked_in_rpd": tracked_row is not None,
                "load_progress_label": tracked_row["load_progress_label"] if tracked_row else "0%",
                "load_status_value": tracked_row["load_status_value"] if tracked_row else "-",
                "load_state": tracked_row["load_state"] if tracked_row else "not_loaded",
                "load_state_label": tracked_row["load_state_label"] if tracked_row else "Not Loaded",
                "recovery_source_label": tracked_row["recovery_source_label"] if tracked_row else "-",
                "health_class": tracked_row["health_class"] if tracked_row else "neutral",
            }
        )

    database_status["summary"]["configured_table_count"] = len(configured_rows)
    database_status["summary"]["tracked_table_count"] = len(inventory_rows)
    database_status["summary"]["heatwave_table_count"] = len(combined_keys)
    database_status["summary"]["loaded_count"] = sum(1 for row in database_status["rows"] if row["load_state"] == "loaded")
    database_status["summary"]["partial_count"] = sum(1 for row in database_status["rows"] if row["load_state"] == "partial")
    database_status["summary"]["not_loaded_count"] = sum(
        1 for row in database_status["rows"] if row["load_state"] == "not_loaded"
    )
    return database_status


def _extract_column_definitions_from_create_statement(create_table_statement):
    definitions = {}
    for line in str(create_table_statement or "").splitlines():
        match = re.match(r"^\s*`([^`]+)`\s+(.*?)(?:,)?\s*$", line.rstrip())
        if match:
            definitions[match.group(1)] = match.group(2).strip()
    return definitions


def _definition_has_not_secondary(column_definition):
    return bool(re.search(r"\bNOT\s+SECONDARY\b", str(column_definition or ""), flags=re.IGNORECASE))


def _definition_without_not_secondary(column_definition):
    return re.sub(
        r"\s+NOT\s+SECONDARY\b",
        "",
        str(column_definition or ""),
        flags=re.IGNORECASE,
    ).strip()


def _build_management_table_columns(table_columns, create_table_statement):
    column_definitions = _extract_column_definitions_from_create_statement(create_table_statement)
    rows = []
    for row in table_columns:
        column_name = str(row.get("column_name") or "").strip()
        column_definition = column_definitions.get(column_name, "")
        rows.append(
            {
                **row,
                "supports_exclusion": bool(column_definition),
                "is_not_secondary": _definition_has_not_secondary(column_definition),
            }
        )
    return rows


def _build_not_secondary_modify_clauses(selected_columns, create_table_statement, *, quote_identifier):
    column_definitions = _extract_column_definitions_from_create_statement(create_table_statement)
    modify_clauses = []
    missing_columns = []
    selected_lookup = {
        str(column_name or "").strip()
        for column_name in selected_columns or []
        if str(column_name or "").strip()
    }

    for column_name, column_definition in column_definitions.items():
        should_be_not_secondary = column_name in selected_lookup
        currently_not_secondary = _definition_has_not_secondary(column_definition)
        if should_be_not_secondary == currently_not_secondary:
            continue

        normalized_definition = _definition_without_not_secondary(column_definition)
        if not normalized_definition:
            missing_columns.append(column_name)
            continue

        final_definition = (
            f"{normalized_definition} NOT SECONDARY"
            if should_be_not_secondary
            else normalized_definition
        )
        modify_clauses.append(
            f"MODIFY COLUMN {quote_identifier(column_name)} {final_definition}"
        )

    for column_name in selected_lookup:
        if column_name not in column_definitions:
            missing_columns.append(column_name)

    if missing_columns:
        missing_list = ", ".join(f"`{column_name}`" for column_name in sorted(set(missing_columns), key=str.lower))
        raise ValueError(f"Unable to determine current column definitions for {missing_list}.")
    return modify_clauses


def _summarize_not_secondary_changes(selected_columns, create_table_statement):
    column_definitions = _extract_column_definitions_from_create_statement(create_table_statement)
    selected_lookup = {
        str(column_name or "").strip()
        for column_name in selected_columns or []
        if str(column_name or "").strip()
    }
    added_count = 0
    removed_count = 0

    for column_name, column_definition in column_definitions.items():
        should_be_not_secondary = column_name in selected_lookup
        currently_not_secondary = _definition_has_not_secondary(column_definition)
        if should_be_not_secondary and not currently_not_secondary:
            added_count += 1
        elif currently_not_secondary and not should_be_not_secondary:
            removed_count += 1

    return {
        "added_count": added_count,
        "removed_count": removed_count,
        "changed_count": added_count + removed_count,
        "selected_count": len(selected_lookup),
    }


def _build_management_procedure_popup(title, sql_text, selected_database, result_sets):
    tabs = [
        {
            "key": "info",
            "label": "Info",
            "kind": "info",
            "details": [
                {"label": "Action", "value": title},
                {"label": "Database", "value": selected_database or "-"},
                {"label": "SQL", "value": sql_text},
                {"label": "Result Sets", "value": str(len(result_sets))},
            ],
        }
    ]
    if not result_sets:
        tabs.append(
            {
                "key": "result_0",
                "label": "Output",
                "kind": "message",
                "message": "Procedure completed without tabular result sets.",
            }
        )
    for index, result_set in enumerate(result_sets, start=1):
        tabs.append(
            {
                "key": f"result_{index}",
                "label": result_set.get("label") or f"Result {index}",
                "kind": "table",
                "columns": result_set.get("columns", []),
                "rows": result_set.get("rows", []),
                "empty_text": "This result set did not return any rows.",
            }
        )
    return {
        "title": title,
        "tabs": tabs,
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


def handle_heatwave_management_action(
    action,
    selected_database,
    selected_table,
    excluded_columns=None,
    *,
    quote_identifier,
    execute_statement,
    execute_multi_result_query,
    fetch_table_columns,
    fetch_create_table_statement,
):
    normalized_action = str(action or "").strip()
    normalized_database = str(selected_database or "").strip()
    normalized_table = str(selected_table or "").strip()

    if normalized_action == "db_load":
        if not normalized_database:
            raise ValueError("Choose a database before running a HeatWave database action.")
        call_sql = f'CALL sys.heatwave_load(JSON_ARRAY("{normalized_database}"), null)'
        popup_result = _build_management_procedure_popup(
            "HeatWave Load Result",
            call_sql,
            normalized_database,
            execute_multi_result_query("CALL sys.heatwave_load(JSON_ARRAY(%s), null)", [normalized_database]),
        )
        return {
            "flash_category": "success",
            "flash_message": f"HeatWave load requested for database `{normalized_database}`.",
            "redirect_values": {"tab": "db", "database": normalized_database},
            "render_popup": True,
            "open_dialog": "procedure-result-dialog",
            "popup_result": popup_result,
        }

    if normalized_action == "db_unload":
        if not normalized_database:
            raise ValueError("Choose a database before running a HeatWave database action.")
        call_sql = f'CALL sys.heatwave_unload(JSON_ARRAY("{normalized_database}"), null)'
        popup_result = _build_management_procedure_popup(
            "HeatWave Unload Result",
            call_sql,
            normalized_database,
            execute_multi_result_query("CALL sys.heatwave_unload(JSON_ARRAY(%s), null)", [normalized_database]),
        )
        return {
            "flash_category": "success",
            "flash_message": f"HeatWave unload requested for database `{normalized_database}`.",
            "redirect_values": {"tab": "db", "database": normalized_database},
            "render_popup": True,
            "open_dialog": "procedure-result-dialog",
            "popup_result": popup_result,
        }

    if not normalized_database or not normalized_table:
        raise ValueError("Choose both database and table before running a HeatWave table action.")

    safe_database = quote_identifier(normalized_database)
    safe_table = quote_identifier(normalized_table)

    if normalized_action == "table_load":
        create_table_statement = fetch_create_table_statement(normalized_database, normalized_table)
        secondary_engine_configured = "SECONDARY_ENGINE=RAPID" in str(create_table_statement or "").upper()
        if not secondary_engine_configured:
            execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_ENGINE RAPID")
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_LOAD")
        flash_message = (
            f"HeatWave secondary engine configured and load requested for `{normalized_database}.{normalized_table}`."
            if not secondary_engine_configured
            else f"HeatWave load requested for `{normalized_database}.{normalized_table}`."
        )
        return {
            "flash_category": "success",
            "flash_message": flash_message,
            "redirect_values": {"tab": "table", "database": normalized_database, "table": normalized_table},
        }

    if normalized_action == "table_unload":
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} SECONDARY_UNLOAD")
        return {
            "flash_category": "success",
            "flash_message": f"HeatWave unload requested for `{normalized_database}.{normalized_table}`.",
            "redirect_values": {"tab": "table", "database": normalized_database, "table": normalized_table},
        }

    if normalized_action == "exclude_columns_update":
        available_columns = {
            str(row.get("column_name") or "").strip()
            for row in fetch_table_columns(normalized_database, normalized_table)
        }
        selected_columns = []
        seen_columns = set()
        for value in excluded_columns or []:
            column_name = str(value or "").strip()
            if not column_name or column_name in seen_columns:
                continue
            if column_name not in available_columns:
                raise ValueError(f"Column `{column_name}` was not found on `{normalized_database}.{normalized_table}`.")
            selected_columns.append(column_name)
            seen_columns.add(column_name)

        create_table_statement = fetch_create_table_statement(normalized_database, normalized_table)
        change_summary = _summarize_not_secondary_changes(selected_columns, create_table_statement)
        modify_clauses = _build_not_secondary_modify_clauses(
            selected_columns,
            create_table_statement,
            quote_identifier=quote_identifier,
        )
        if modify_clauses:
            execute_statement(
                f"ALTER TABLE {safe_database}.{safe_table} " + ", ".join(modify_clauses)
            )
            if change_summary["added_count"] and change_summary["removed_count"]:
                flash_message = (
                    f"Updated exclusions on `{normalized_database}.{normalized_table}`: "
                    f"{change_summary['added_count']} column(s) marked NOT SECONDARY and "
                    f"{change_summary['removed_count']} column(s) restored."
                )
            elif change_summary["added_count"]:
                flash_message = (
                    f"Marked {change_summary['added_count']} column(s) on "
                    f"`{normalized_database}.{normalized_table}` as NOT SECONDARY."
                )
            else:
                flash_message = (
                    f"Restored {change_summary['removed_count']} column(s) on "
                    f"`{normalized_database}.{normalized_table}` back to secondary eligibility."
                )
        else:
            flash_message = f"No column exclusion changes were needed for `{normalized_database}.{normalized_table}`."
        return {
            "flash_category": "success",
            "flash_message": flash_message,
            "redirect_values": {"tab": "table", "database": normalized_database, "table": normalized_table},
        }

    raise ValueError("Unknown HeatWave action.")


def build_heatwave_management_context(
    selected_database,
    selected_table,
    active_tab,
    *,
    fetch_database_inventory,
    fetch_tables_for_database,
    fetch_table_columns,
    fetch_create_table_statement,
    fetch_heatwave_inventory_report,
    fetch_heatwave_defined_secondary_engine_tables,
    execute_query,
):
    normalized_database = str(selected_database or "").strip()
    normalized_table = str(selected_table or "").strip()
    database_inventory = [row for row in fetch_database_inventory() if not row["is_system"]]
    available_database_names = {row["database_name"] for row in database_inventory}
    if normalized_database and normalized_database not in available_database_names:
        normalized_database = ""

    tables = fetch_tables_for_database(normalized_database) if normalized_database else []
    table_lookup = {row["table_name"]: row for row in tables}
    if normalized_table and normalized_table not in table_lookup:
        normalized_table = ""

    database_status = _empty_management_database_status(normalized_database)
    if normalized_database:
        inventory_report = _safe_report(
            fetch_heatwave_inventory_report,
            {"columns": [], "rows": [], "table_id_columns": [], "tables_columns": []},
        )
        configured_tables, configured_error = _safe_items(fetch_heatwave_defined_secondary_engine_tables)
        configured_tables, _ = _build_secondary_engine_lookup(configured_tables)
        inventory_rows = _build_heatwave_inventory_rows(inventory_report)
        database_status = _build_management_database_status(normalized_database, configured_tables, inventory_rows)
        database_status["error"] = " | ".join(
            error_message for error_message in (configured_error, inventory_report.get("error", "")) if error_message
        )

    table_status_lookup = {
        str(row["table_name"]).lower(): row
        for row in database_status["rows"]
    }
    table_action_summary = {
        "has_selection": bool(normalized_database and normalized_table),
        "secondary_engine_configured": bool(table_lookup.get(normalized_table, {}).get("heatwave_configured"))
        if normalized_table
        else False,
        "tracked_in_rpd": False,
        "load_progress_label": "-",
        "load_state_label": "-",
        "load_status_value": "-",
        "recovery_source_label": "-",
        "excluded_column_count": 0,
        "column_error": "",
        "columns": [],
    }
    if normalized_table:
        tracked_row = table_status_lookup.get(normalized_table.lower())
        if tracked_row:
            table_action_summary["tracked_in_rpd"] = True
            table_action_summary["load_progress_label"] = tracked_row["load_progress_label"]
            table_action_summary["load_state_label"] = tracked_row["load_state_label"]
            table_action_summary["load_status_value"] = tracked_row["load_status_value"]
            table_action_summary["recovery_source_label"] = tracked_row["recovery_source_label"]
        else:
            table_action_summary["load_progress_label"] = "0%"
            table_action_summary["load_state_label"] = "Not Loaded"
        try:
            table_columns = fetch_table_columns(normalized_database, normalized_table)
            create_table_statement = fetch_create_table_statement(normalized_database, normalized_table)
            table_action_summary["columns"] = _build_management_table_columns(table_columns, create_table_statement)
            table_action_summary["excluded_column_count"] = sum(
                1 for row in table_action_summary["columns"] if row["is_not_secondary"]
            )
        except Exception as error:  # pragma: no cover - depends on server features
            table_action_summary["column_error"] = str(error)

    return {
        "active_tab": "table" if str(active_tab or "").strip().lower() == "table" else "db",
        "database_inventory": database_inventory,
        "selected_database": normalized_database,
        "selected_table": normalized_table,
        "tables": tables,
        "database_status": database_status,
        "table_action_summary": table_action_summary,
        "management_summary": fetch_heatwave_management_summary(execute_query=execute_query),
    }
