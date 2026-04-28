import json
import re
from datetime import datetime, timezone

import pymysql


def _empty_heatwave_summary():
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


def build_mysql_dashboard_context(*, fetch_server_overview, fetch_database_inventory, fetch_dashboard_heatwave_summary):
    overview = fetch_server_overview()
    inventory = [row for row in fetch_database_inventory() if not row["is_system"]]
    try:
        heatwave_summary = fetch_dashboard_heatwave_summary()
    except Exception as error:  # pragma: no cover - depends on server features
        heatwave_summary = _empty_heatwave_summary()
        heatwave_summary["error"] = str(error)

    heatwave_by_database = {
        row["database_name"].lower(): row
        for row in heatwave_summary.get("database_rows", [])
    }
    merged_inventory = []
    for row in inventory:
        heatwave_row = heatwave_by_database.get(row["database_name"].lower(), {})
        merged_row = dict(row)
        merged_row["configured_heatwave_table_count"] = heatwave_row.get("configured_table_count", 0)
        merged_row["tracked_heatwave_table_count"] = heatwave_row.get("tracked_table_count", 0)
        merged_row["heatwave_table_count"] = heatwave_row.get("heatwave_table_count", 0)
        merged_row["heatwave_loaded_count"] = heatwave_row.get("loaded_count", 0)
        merged_row["heatwave_partial_count"] = heatwave_row.get("partial_count", 0)
        merged_row["heatwave_not_loaded_count"] = heatwave_row.get("not_loaded_count", 0)
        if merged_row["heatwave_table_count"]:
            merged_row["heatwave_summary_label"] = (
                f"{merged_row['heatwave_table_count']} total | "
                f"{merged_row['heatwave_loaded_count']} loaded | "
                f"{merged_row['heatwave_partial_count']} partial | "
                f"{merged_row['heatwave_not_loaded_count']} none"
            )
        else:
            merged_row["heatwave_summary_label"] = "-"
        merged_inventory.append(merged_row)

    return {
        "server_overview": overview,
        "database_inventory": merged_inventory,
        "non_system_databases": merged_inventory,
        "heatwave_summary": heatwave_summary,
    }


def _extract_column_definitions_from_create_statement(create_table_statement):
    definitions = {}
    for line in str(create_table_statement or "").splitlines():
        match = re.match(r"^\s*`([^`]+)`\s+(.*?)(?:,)?\s*$", line.rstrip())
        if match:
            definitions[match.group(1)] = match.group(2).strip()
    return definitions


def _build_db_admin_column_edit_rows(columns, ddl_statement, *, payload=None):
    definition_lookup = _extract_column_definitions_from_create_statement(ddl_statement)
    submitted_name_by_source = {}
    submitted_definition_by_source = {}

    if payload is not None and hasattr(payload, "getlist"):
        source_values = payload.getlist("source_column_name")
        new_name_values = payload.getlist("new_column_name")
        definition_values = payload.getlist("column_definition")
        for index, source_value in enumerate(source_values):
            source_column_name = str(source_value or "").strip()
            if not source_column_name or source_column_name in submitted_name_by_source:
                continue
            submitted_name_by_source[source_column_name] = str(
                new_name_values[index] if index < len(new_name_values) else source_column_name
            ).strip()
            submitted_definition_by_source[source_column_name] = str(
                definition_values[index] if index < len(definition_values) else ""
            ).strip()

    rows = []
    unsupported_columns = []
    for row in columns:
        source_column_name = str(row.get("column_name") or "").strip()
        current_definition = definition_lookup.get(source_column_name, "")
        supports_modify = bool(current_definition)
        if not supports_modify:
            unsupported_columns.append(source_column_name)
        rows.append(
            {
                "source_column_name": source_column_name,
                "current_definition": current_definition,
                "edited_column_name": submitted_name_by_source.get(source_column_name, source_column_name),
                "edited_definition": submitted_definition_by_source.get(source_column_name, current_definition),
                "supports_modify": supports_modify,
                "column_type": row.get("column_type") or "",
                "is_nullable": row.get("is_nullable") or "",
                "column_key": row.get("column_key") or "",
                "extra": row.get("extra") or "",
            }
        )
    return rows, unsupported_columns


def _build_db_admin_change_requests(columns, ddl_statement, payload):
    if payload is None or not hasattr(payload, "getlist"):
        raise ValueError("Column update payload is missing.")

    definition_lookup = _extract_column_definitions_from_create_statement(ddl_statement)
    current_columns = [str(row.get("column_name") or "").strip() for row in columns]
    current_columns = [column_name for column_name in current_columns if column_name]
    current_column_set = set(current_columns)

    source_values = payload.getlist("source_column_name")
    new_name_values = payload.getlist("new_column_name")
    definition_values = payload.getlist("column_definition")
    if not source_values:
        raise ValueError("No column definitions were submitted.")

    final_name_by_source = {column_name: column_name for column_name in current_columns}
    change_requests = []
    seen_sources = set()

    for index, source_value in enumerate(source_values):
        source_column_name = str(source_value or "").strip()
        if not source_column_name or source_column_name in seen_sources:
            continue
        if source_column_name not in current_column_set:
            raise ValueError(f"Column `{source_column_name}` was not found on the selected table.")

        seen_sources.add(source_column_name)
        new_column_name = str(
            new_name_values[index] if index < len(new_name_values) else source_column_name
        ).strip()
        column_definition = str(
            definition_values[index] if index < len(definition_values) else ""
        ).strip()
        current_definition = definition_lookup.get(source_column_name, "")
        if not current_definition:
            raise ValueError(
                f"Unable to determine the current definition for column `{source_column_name}` from SHOW CREATE TABLE."
            )
        if not new_column_name:
            raise ValueError(f"Column name is required for `{source_column_name}`.")
        if not column_definition:
            raise ValueError(f"Column definition is required for `{source_column_name}`.")

        final_name_by_source[source_column_name] = new_column_name
        if new_column_name != source_column_name or column_definition != current_definition:
            change_requests.append(
                {
                    "source_column_name": source_column_name,
                    "new_column_name": new_column_name,
                    "column_definition": column_definition,
                }
            )

    normalized_final_names = [column_name.lower() for column_name in final_name_by_source.values()]
    if len(set(normalized_final_names)) != len(normalized_final_names):
        raise ValueError("Column names must remain unique after the update.")

    return change_requests


def _build_db_admin_change_column_clauses(change_requests, *, quote_identifier):
    clauses = []
    for row in change_requests:
        clauses.append(
            "CHANGE COLUMN {source_column} {target_column} {column_definition}".format(
                source_column=quote_identifier(row["source_column_name"]),
                target_column=quote_identifier(row["new_column_name"]),
                column_definition=row["column_definition"],
            )
        )
    return clauses


def _empty_missing_primary_key_report():
    return {
        "rows": [],
        "error": "",
        "table_count": 0,
        "fixable_table_count": 0,
        "auto_increment_fix_count": 0,
        "invisible_row_id_fix_count": 0,
        "manual_review_count": 0,
    }


def _build_missing_primary_key_report(raw_rows):
    report = _empty_missing_primary_key_report()
    for raw_row in raw_rows or []:
        database_name = str(raw_row.get("database_name") or "").strip()
        table_name = str(raw_row.get("table_name") or "").strip()
        auto_increment_column_name = str(raw_row.get("auto_increment_column_name") or "").strip()
        has_my_row_id = bool(raw_row.get("has_my_row_id"))

        fix_method = "add_invisible_my_row_id"
        fix_method_label = "Add invisible `my_row_id` AUTO_INCREMENT primary key"
        is_fixable = True
        if auto_increment_column_name:
            fix_method = "use_auto_increment"
            fix_method_label = f"Add PRIMARY KEY on existing AUTO_INCREMENT column `{auto_increment_column_name}`"
        elif has_my_row_id:
            fix_method = "manual_review"
            fix_method_label = "Manual review required because `my_row_id` already exists"
            is_fixable = False

        report_row = {
            "database_name": database_name,
            "table_name": table_name,
            "full_table_name": f"{database_name}.{table_name}" if database_name and table_name else table_name,
            "engine": raw_row.get("engine") or "-",
            "row_count": raw_row.get("row_count") if raw_row.get("row_count") not in (None, "") else "-",
            "auto_increment_column_name": auto_increment_column_name,
            "has_my_row_id": has_my_row_id,
            "fix_method": fix_method,
            "fix_method_label": fix_method_label,
            "is_fixable": is_fixable,
        }
        report["rows"].append(report_row)
        report["table_count"] += 1
        if fix_method == "use_auto_increment":
            report["fixable_table_count"] += 1
            report["auto_increment_fix_count"] += 1
        elif fix_method == "add_invisible_my_row_id":
            report["fixable_table_count"] += 1
            report["invisible_row_id_fix_count"] += 1
        else:
            report["manual_review_count"] += 1
    return report


def _summarize_name_list(items, *, max_items=3):
    normalized_items = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not normalized_items:
        return ""
    if len(normalized_items) <= max_items:
        return ", ".join(normalized_items)
    remaining_count = len(normalized_items) - max_items
    return ", ".join(normalized_items[:max_items]) + f", and {remaining_count} more"


def handle_db_admin_action(
    action,
    database_name,
    *,
    table_name="",
    payload=None,
    quote_identifier,
    execute_statement,
    system_schemas,
    fetch_create_table_statement=None,
    fetch_table_columns=None,
    fetch_missing_primary_key_rows=None,
    fix_missing_primary_key_table=None,
):
    normalized_action = str(action or "").strip()
    normalized_name = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()

    if normalized_action == "create_database":
        if not normalized_name:
            raise ValueError("Database name is required.")
        safe_database = quote_identifier(normalized_name)
        execute_statement(f"CREATE DATABASE IF NOT EXISTS {safe_database}")
        return {
            "flash_category": "success",
            "flash_message": f"Database `{normalized_name}` is ready.",
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"database": normalized_name},
        }

    if normalized_action == "drop_database":
        if not normalized_name:
            raise ValueError("Database name is required.")
        if normalized_name in system_schemas:
            raise ValueError("System schemas cannot be dropped here.")
        safe_database = quote_identifier(normalized_name)
        execute_statement(f"DROP DATABASE {safe_database}")
        return {
            "flash_category": "success",
            "flash_message": f"Database `{normalized_name}` dropped.",
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {},
        }

    if normalized_action == "modify_table_columns":
        if not normalized_name or not normalized_table:
            raise ValueError("Choose both a database and table before modifying columns.")
        if fetch_create_table_statement is None or fetch_table_columns is None:
            raise ValueError("Column metadata helpers are not available.")

        current_columns = fetch_table_columns(normalized_name, normalized_table)
        ddl_statement = fetch_create_table_statement(normalized_name, normalized_table)
        change_requests = _build_db_admin_change_requests(current_columns, ddl_statement, payload)
        if not change_requests:
            raise ValueError("No column definition changes were submitted.")

        safe_database = quote_identifier(normalized_name)
        safe_table = quote_identifier(normalized_table)
        execute_statement(
            f"ALTER TABLE {safe_database}.{safe_table} "
            + ", ".join(_build_db_admin_change_column_clauses(change_requests, quote_identifier=quote_identifier))
        )
        return {
            "flash_category": "success",
            "flash_message": (
                f"Updated {len(change_requests)} column definition(s) on "
                f"`{normalized_name}.{normalized_table}`."
            ),
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"database": normalized_name, "table": normalized_table},
        }

    if normalized_action == "fix_missing_primary_key":
        if not normalized_name or not normalized_table:
            raise ValueError("Choose both a database and table before applying the primary key fix.")
        if fix_missing_primary_key_table is None:
            raise ValueError("Primary key repair helper is not available.")

        fix_result = fix_missing_primary_key_table(normalized_name, normalized_table)
        flash_category = "success" if fix_result.get("status") in {"fixed", "already_has_primary_key"} else "error"
        return {
            "flash_category": flash_category,
            "flash_message": fix_result.get("message") or f"Processed `{normalized_name}.{normalized_table}`.",
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {
                "db_admin_tab": "missing-primary-key",
                "database": normalized_name,
                "table": normalized_table,
            },
        }

    if normalized_action == "fix_missing_primary_key_all":
        if fetch_missing_primary_key_rows is None or fix_missing_primary_key_table is None:
            raise ValueError("Primary key repair helpers are not available.")

        report = _build_missing_primary_key_report(fetch_missing_primary_key_rows())
        if not report["table_count"]:
            return {
                "flash_category": "success",
                "flash_message": "All non-system base tables already have a primary key.",
                "redirect_endpoint": "db_admin_page",
                "redirect_values": {"db_admin_tab": "missing-primary-key"},
            }

        fixed_with_auto_increment = []
        fixed_with_row_id = []
        already_fixed = []
        manual_review = [row["full_table_name"] for row in report["rows"] if not row["is_fixable"]]
        failures = []

        for row in report["rows"]:
            if not row["is_fixable"]:
                continue
            try:
                fix_result = fix_missing_primary_key_table(row["database_name"], row["table_name"])
            except Exception as error:
                failures.append(f"{row['full_table_name']}: {error}")
                continue

            status = str(fix_result.get("status") or "").strip().lower()
            strategy = str(fix_result.get("strategy") or "").strip().lower()
            if status == "already_has_primary_key":
                already_fixed.append(row["full_table_name"])
                continue
            if strategy == "use_auto_increment":
                fixed_with_auto_increment.append(row["full_table_name"])
            else:
                fixed_with_row_id.append(row["full_table_name"])

        message_parts = []
        fixed_count = len(fixed_with_auto_increment) + len(fixed_with_row_id)
        if fixed_count:
            detail_parts = []
            if fixed_with_auto_increment:
                detail_parts.append(f"{len(fixed_with_auto_increment)} reused an AUTO_INCREMENT column")
            if fixed_with_row_id:
                detail_parts.append(f"{len(fixed_with_row_id)} added invisible `my_row_id`")
            message = f"Fixed {fixed_count} table(s)"
            if detail_parts:
                message += f": {'; '.join(detail_parts)}."
            else:
                message += "."
            message_parts.append(message)
        if already_fixed:
            message_parts.append(
                f"{len(already_fixed)} already had a primary key by the time the fix ran."
            )
        if manual_review:
            message_parts.append(
                f"{len(manual_review)} require manual review: {_summarize_name_list(manual_review)}."
            )
        if failures:
            message_parts.append(
                f"{len(failures)} failed: {_summarize_name_list(failures)}."
            )
        if not message_parts:
            message_parts.append("No primary key fixes were applied.")

        flash_category = "success"
        if failures or (manual_review and not fixed_count and not already_fixed):
            flash_category = "error"
        return {
            "flash_category": flash_category,
            "flash_message": " ".join(message_parts),
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"db_admin_tab": "missing-primary-key"},
        }

    raise ValueError("Unsupported DB Admin action.")


def _empty_partition_state():
    return {
        "is_partitioned": False,
        "partition_method": "",
        "partition_expression": "",
        "subpartition_method": "",
        "subpartition_expression": "",
        "partition_count": 0,
        "rows": [],
    }


def build_db_admin_context(
    selected_database,
    selected_table,
    preview_page,
    *,
    db_admin_tab="select",
    fetch_database_inventory,
    fetch_tables_for_database,
    empty_table_preview,
    fetch_table_preview,
    fetch_create_table_statement,
    fetch_table_columns,
    fetch_table_indexes,
    fetch_table_partitions,
    fetch_missing_primary_key_rows=None,
    column_edit_payload=None,
):
    inventory = fetch_database_inventory()
    available_database_names = {row["database_name"] for row in inventory}
    normalized_database = str(selected_database or "").strip()
    normalized_table = str(selected_table or "").strip()
    missing_primary_key_report = _empty_missing_primary_key_report()

    if normalized_database and normalized_database not in available_database_names:
        if db_admin_tab == "select":
            return {
                "redirect_endpoint": "db_admin_page",
                "redirect_values": {},
                "flash_category": "error",
                "flash_message": f"Database `{normalized_database}` was not found.",
            }
        normalized_database = ""
        normalized_table = ""

    available_tables = fetch_tables_for_database(normalized_database) if normalized_database else []
    available_table_names = {row["table_name"] for row in available_tables}
    if normalized_table and normalized_table not in available_table_names:
        if db_admin_tab == "select":
            return {
                "redirect_endpoint": "db_admin_page",
                "redirect_values": {"database": normalized_database},
                "flash_category": "error",
                "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
            }
        normalized_table = ""

    preview = empty_table_preview()
    ddl_statement = ""
    columns = []
    indexes = []
    partitions = _empty_partition_state()
    column_edit_rows = []
    column_edit_unsupported_columns = []

    if db_admin_tab == "missing-primary-key":
        try:
            missing_primary_key_report = _build_missing_primary_key_report(
                fetch_missing_primary_key_rows() if fetch_missing_primary_key_rows is not None else []
            )
        except Exception as error:  # pragma: no cover - depends on server features
            missing_primary_key_report = _empty_missing_primary_key_report()
            missing_primary_key_report["error"] = str(error)

    if db_admin_tab == "select" and normalized_table:
        try:
            preview = fetch_table_preview(normalized_database, normalized_table, page=preview_page)
            ddl_statement = fetch_create_table_statement(normalized_database, normalized_table)
            columns = fetch_table_columns(normalized_database, normalized_table)
            indexes = fetch_table_indexes(normalized_database, normalized_table)
            partitions = fetch_table_partitions(normalized_database, normalized_table)
            column_edit_rows, column_edit_unsupported_columns = _build_db_admin_column_edit_rows(
                columns,
                ddl_statement,
                payload=column_edit_payload,
            )
        except pymysql.err.ProgrammingError as error:
            if error.args and error.args[0] == 1146:
                return {
                    "redirect_endpoint": "db_admin_page",
                    "redirect_values": {"database": normalized_database},
                    "flash_category": "error",
                    "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                }
            raise

    return {
        "database_inventory": inventory,
        "selected_database": normalized_database,
        "tables": available_tables,
        "selected_table": normalized_table,
        "preview": preview,
        "ddl_statement": ddl_statement,
        "columns": columns,
        "column_edit_rows": column_edit_rows,
        "column_edit_unsupported_columns": column_edit_unsupported_columns,
        "indexes": indexes,
        "partitions": partitions,
        "missing_primary_key_report": missing_primary_key_report,
    }


def build_db_admin_export(
    selected_database,
    *,
    db_admin_tab="select",
    fetch_tables_for_database,
    fetch_missing_primary_key_rows=None,
):
    if db_admin_tab == "missing-primary-key":
        report = _build_missing_primary_key_report(
            fetch_missing_primary_key_rows() if fetch_missing_primary_key_rows is not None else []
        )
        export_rows = [
            {
                "database_name": row["database_name"],
                "table_name": row["table_name"],
                "engine": row["engine"],
                "row_count": row["row_count"],
                "auto_increment_column_name": row["auto_increment_column_name"] or "",
                "fix_method": row["fix_method"],
                "fix_method_label": row["fix_method_label"],
                "can_auto_fix": "yes" if row["is_fixable"] else "no",
            }
            for row in report["rows"]
        ]
        return {
            "filename": "tables-without-primary-key.csv",
            "columns": [
                "database_name",
                "table_name",
                "engine",
                "row_count",
                "auto_increment_column_name",
                "fix_method",
                "fix_method_label",
                "can_auto_fix",
            ],
            "rows": export_rows,
        }

    normalized_database = str(selected_database or "").strip()
    rows = fetch_tables_for_database(normalized_database)
    export_rows = [
        {
            "table_name": row["table_name"],
            "engine": row["engine"],
            "row_count": row["row_count"],
            "heatwave_configured": "yes" if row["heatwave_configured"] else "no",
            "create_options": row["create_options"],
        }
        for row in rows
    ]
    return {
        "filename": f"{normalized_database or 'database'}-tables.csv",
        "columns": ["table_name", "engine", "row_count", "heatwave_configured", "create_options"],
        "rows": export_rows,
    }


def _empty_sql_workspace_result():
    return {
        "has_output": False,
        "title": "Last Result",
        "tabs": [],
    }


def _summarize_sql_text(sql_text, max_length=240):
    collapsed = " ".join(str(sql_text or "").split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max_length - 3] + "..."


def _format_duration_label(duration_ms):
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.3f} s"
    if abs(duration_ms - round(duration_ms)) < 0.01:
        return f"{int(round(duration_ms))} ms"
    return f"{duration_ms:.1f} ms"


def _format_rows_as_text_table(rows):
    if not rows:
        return "No rows returned."

    columns = [str(column) for column in rows[0].keys()]
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row.get(column, ""))))

    header = " | ".join(column.ljust(widths[column]) for column in columns)
    divider = "-+-".join("-" * widths[column] for column in columns)
    lines = [header, divider]
    for row in rows:
        lines.append(" | ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))
    return "\n".join(lines)


def _normalize_sql_workspace_export_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _normalize_sql_workspace_export_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_sql_workspace_export_value(item) for item in value]
    return str(value)


def _normalize_sql_workspace_export_rows(columns, rows):
    export_rows = []
    for row in rows or []:
        export_rows.append(
            {
                str(column): _normalize_sql_workspace_export_value(row.get(column))
                for column in columns or []
            }
        )
    return export_rows


def build_sql_workspace_result(
    action_label,
    executed_sql,
    selected_database,
    result_sets,
    duration_ms,
    *,
    use_secondary_engine="ON",
    error_message="",
):
    status_label = "Error" if error_message else "Success"
    tabs = []

    if error_message:
        tabs.append(
            {
                "key": "error",
                "label": "Error",
                "kind": "message",
                "message": error_message,
            }
        )
    elif not result_sets:
        tabs.append(
            {
                "key": "output",
                "label": "Output",
                "kind": "message",
                "message": "Statement completed without tabular result sets.",
            }
        )
    else:
        for index, result_set in enumerate(result_sets, start=1):
            tabs.append(
                {
                    "key": f"result_{index}",
                    "label": result_set.get("label") or f"Result {index}",
                    "kind": result_set.get("kind", "table"),
                    "columns": result_set.get("columns", []),
                    "rows": result_set.get("rows", []),
                    "export_rows": _normalize_sql_workspace_export_rows(
                        result_set.get("columns", []),
                        result_set.get("rows", []),
                    ),
                    "message": result_set.get("message", ""),
                    "empty_text": result_set.get("empty_text", "This result did not return any rows."),
                }
            )

    return {
        "has_output": True,
        "title": f"{action_label} Result",
        "summary_details": [
            {"label": "Action", "value": action_label},
            {"label": "Database", "value": selected_database or "Profile Default"},
            {"label": "use_secondary_engine", "value": use_secondary_engine},
            {"label": "Status", "value": status_label},
            {"label": "Duration", "value": _format_duration_label(duration_ms)},
            {"label": "SQL", "value": executed_sql},
        ],
        "tabs": tabs,
    }


def build_sql_workspace_explain_result(
    explained_sql,
    selected_database,
    text_rows,
    json_rows,
    duration_ms,
    *,
    use_secondary_engine="ON",
    json_error="",
):
    json_text = ""
    if json_rows:
        raw_json_value = next(iter(json_rows[0].values()))
        try:
            json_text = json.dumps(json.loads(str(raw_json_value or "{}")), indent=2, ensure_ascii=False)
        except (TypeError, ValueError, json.JSONDecodeError):
            json_text = str(raw_json_value or "")

    tabs = [
        {
            "key": "text",
            "label": "Text",
            "kind": "code",
            "text_output": _format_rows_as_text_table(text_rows),
        }
    ]
    if json_error:
        tabs.append(
            {
                "key": "json",
                "label": "JSON",
                "kind": "message",
                "message": json_error,
            }
        )
        tabs.append(
            {
                "key": "visual",
                "label": "Visual",
                "kind": "message",
                "message": "Graphic execution plan is unavailable because EXPLAIN FORMAT=JSON did not return a plan.",
            }
        )
    else:
        tabs.append(
            {
                "key": "json",
                "label": "JSON",
                "kind": "code",
                "text_output": json_text or "{}",
            }
        )
        tabs.append(
            {
                "key": "visual",
                "label": "Visual",
                "kind": "plan",
                "plan_json": json_text or "{}",
            }
        )

    return {
        "has_output": True,
        "title": "Explain Result",
        "summary_details": [
            {"label": "Action", "value": "Explain"},
            {"label": "Database", "value": selected_database or "Profile Default"},
            {"label": "use_secondary_engine", "value": use_secondary_engine},
            {"label": "Status", "value": "Success" if not json_error else "Partial"},
            {"label": "Duration", "value": _format_duration_label(duration_ms)},
            {"label": "SQL", "value": explained_sql},
        ],
        "tabs": tabs,
    }


def build_sql_workspace_history_entry(
    action_label,
    selected_database,
    sql_text,
    duration_ms,
    *,
    use_secondary_engine="",
    status,
    error_message="",
):
    return {
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "action_label": action_label,
        "database_name": selected_database or "Profile Default",
        "use_secondary_engine": str(use_secondary_engine or "").strip().upper(),
        "status": status,
        "duration_label": _format_duration_label(duration_ms),
        "query_preview": _summarize_sql_text(sql_text),
        "error_message": error_message,
    }


def append_sql_workspace_history(history_rows, history_entry, *, limit=20):
    rows = [history_entry]
    rows.extend(history_rows or [])
    return rows[:limit]


def build_sql_workspace_context(selected_database, sql_text, last_result, history_rows, *, fetch_database_inventory):
    database_inventory = [row for row in fetch_database_inventory() if not row["is_system"]]
    available_database_names = {row["database_name"] for row in database_inventory}
    normalized_database = str(selected_database or "").strip()
    if normalized_database and normalized_database not in available_database_names:
        normalized_database = ""

    return {
        "database_inventory": database_inventory,
        "selected_database": normalized_database,
        "sql_text": str(sql_text or ""),
        "last_result": last_result or _empty_sql_workspace_result(),
        "history_rows": history_rows or [],
    }
