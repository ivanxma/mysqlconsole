import json
import re
from datetime import datetime, timezone

from modules.mysql_util import (
    OperationalError as MySQLOperationalError,
    ProgrammingError as MySQLProgrammingError,
    quote_sql_string_literal as _quote_sql_string_literal,
)


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


def build_mysql_dashboard_context(
    *,
    fetch_server_overview,
    fetch_database_inventory,
    fetch_dashboard_heatwave_summary,
    include_inventory=True,
    include_heatwave=True,
):
    overview = fetch_server_overview()
    inventory = [row for row in fetch_database_inventory() if not row["is_system"]] if include_inventory else []
    if include_heatwave:
        try:
            heatwave_summary = fetch_dashboard_heatwave_summary()
        except Exception as error:  # pragma: no cover - depends on server features
            heatwave_summary = _empty_heatwave_summary()
            heatwave_summary["error"] = str(error)
    else:
        heatwave_summary = _empty_heatwave_summary()

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


def _normalize_db_admin_comment_text(value):
    if value is None:
        return ""
    return str(value)


def _strip_column_comment_clause(column_definition):
    text = str(column_definition or "").strip()
    if not text:
        return ""

    length = len(text)
    index = 0
    in_single_quote = False
    in_backtick = False

    while index < length:
        char = text[index]

        if in_single_quote:
            if char == "\\":
                index += 2
                continue
            if char == "'":
                if index + 1 < length and text[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = False
            index += 1
            continue

        if in_backtick:
            if char == "`":
                in_backtick = False
            index += 1
            continue

        if char == "'":
            in_single_quote = True
            index += 1
            continue

        if char == "`":
            in_backtick = True
            index += 1
            continue

        if text[index : index + 7].upper() == "COMMENT":
            previous_char = text[index - 1] if index > 0 else " "
            next_char_index = index + 7
            next_char = text[next_char_index] if next_char_index < length else " "
            if (previous_char.isalnum() or previous_char == "_") or (next_char.isalnum() or next_char == "_"):
                index += 1
                continue

            literal_start = next_char_index
            while literal_start < length and text[literal_start].isspace():
                literal_start += 1
            if literal_start >= length or text[literal_start] != "'":
                index += 1
                continue

            literal_end = literal_start + 1
            while literal_end < length:
                literal_char = text[literal_end]
                if literal_char == "\\":
                    literal_end += 2
                    continue
                if literal_char == "'":
                    if literal_end + 1 < length and text[literal_end + 1] == "'":
                        literal_end += 2
                        continue
                    literal_end += 1
                    break
                literal_end += 1

            prefix = text[:index].rstrip()
            suffix = text[literal_end:].lstrip()
            if prefix and suffix:
                return f"{prefix} {suffix}".strip()
            return f"{prefix}{suffix}".strip()

        index += 1

    return text


def _compose_column_definition(column_definition, column_comment):
    normalized_definition = _strip_column_comment_clause(column_definition)
    if not normalized_definition:
        return ""
    normalized_comment = _normalize_db_admin_comment_text(column_comment)
    if normalized_comment:
        return f"{normalized_definition} COMMENT {_quote_sql_string_literal(normalized_comment)}"
    return normalized_definition


def _build_db_admin_column_edit_rows(columns, ddl_statement, *, payload=None):
    definition_lookup = _extract_column_definitions_from_create_statement(ddl_statement)
    submitted_name_by_source = {}
    submitted_definition_by_source = {}
    submitted_comment_by_source = {}

    if payload is not None and hasattr(payload, "getlist"):
        source_values = payload.getlist("source_column_name")
        new_name_values = payload.getlist("new_column_name")
        definition_values = payload.getlist("column_definition")
        comment_values = payload.getlist("column_comment")
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
            submitted_comment_by_source[source_column_name] = _normalize_db_admin_comment_text(
                comment_values[index] if index < len(comment_values) else ""
            )

    rows = []
    unsupported_columns = []
    for row in columns:
        source_column_name = str(row.get("column_name") or "").strip()
        current_definition = definition_lookup.get(source_column_name, "")
        current_column_comment = _normalize_db_admin_comment_text(row.get("column_comment"))
        supports_modify = bool(current_definition)
        if not supports_modify:
            unsupported_columns.append(source_column_name)
        rows.append(
            {
                "source_column_name": source_column_name,
                "current_definition": current_definition,
                "edited_column_name": submitted_name_by_source.get(source_column_name, source_column_name),
                "edited_definition": submitted_definition_by_source.get(
                    source_column_name,
                    _strip_column_comment_clause(current_definition),
                ),
                "current_comment": current_column_comment,
                "edited_comment": submitted_comment_by_source.get(source_column_name, current_column_comment),
                "supports_modify": supports_modify,
                "column_type": row.get("column_type") or "",
                "is_nullable": row.get("is_nullable") or "",
                "column_key": row.get("column_key") or "",
                "extra": row.get("extra") or "",
            }
        )
    return rows, unsupported_columns


def _build_db_admin_change_requests(columns, ddl_statement, payload, *, current_table_comment=""):
    if payload is None or not hasattr(payload, "getlist"):
        raise ValueError("Column update payload is missing.")

    definition_lookup = _extract_column_definitions_from_create_statement(ddl_statement)
    current_columns = [str(row.get("column_name") or "").strip() for row in columns]
    current_columns = [column_name for column_name in current_columns if column_name]
    current_column_set = set(current_columns)
    current_comment_by_source = {
        str(row.get("column_name") or "").strip(): _normalize_db_admin_comment_text(row.get("column_comment"))
        for row in columns
        if str(row.get("column_name") or "").strip()
    }

    source_values = payload.getlist("source_column_name")
    new_name_values = payload.getlist("new_column_name")
    definition_values = payload.getlist("column_definition")
    comment_values = payload.getlist("column_comment")
    normalized_current_table_comment = _normalize_db_admin_comment_text(current_table_comment)
    submitted_table_comment = _normalize_db_admin_comment_text(
        payload.get("table_comment", normalized_current_table_comment)
    )
    if not source_values:
        return {
            "column_change_requests": [],
            "table_comment_changed": submitted_table_comment != normalized_current_table_comment,
            "new_table_comment": submitted_table_comment,
        }

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
        column_comment = _normalize_db_admin_comment_text(
            comment_values[index] if index < len(comment_values) else current_comment_by_source.get(source_column_name, "")
        )
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
        current_definition_without_comment = _strip_column_comment_clause(current_definition)
        current_comment = current_comment_by_source.get(source_column_name, "")
        if (
            new_column_name != source_column_name
            or column_definition != current_definition_without_comment
            or column_comment != current_comment
        ):
            change_requests.append(
                {
                    "source_column_name": source_column_name,
                    "new_column_name": new_column_name,
                    "column_definition": _compose_column_definition(column_definition, column_comment),
                }
            )

    normalized_final_names = [column_name.lower() for column_name in final_name_by_source.values()]
    if len(set(normalized_final_names)) != len(normalized_final_names):
        raise ValueError("Column names must remain unique after the update.")

    return {
        "column_change_requests": change_requests,
        "table_comment_changed": submitted_table_comment != normalized_current_table_comment,
        "new_table_comment": submitted_table_comment,
    }


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


def _default_event_schedule_name(schedule_options):
    for option in schedule_options or []:
        value = str(option.get("value") or "").strip().lower()
        if value:
            return value
    return "once"


def _normalize_event_schedule_name(schedule_name, *, schedule_options):
    normalized_name = str(schedule_name or "").strip().lower()
    option_values = {
        str(option.get("value") or "").strip().lower()
        for option in schedule_options or []
        if str(option.get("value") or "").strip()
    }
    if normalized_name and normalized_name in option_values:
        return normalized_name
    return _default_event_schedule_name(schedule_options)


def _event_schedule_requires_at(schedule_name, *, schedule_options):
    normalized_name = _normalize_event_schedule_name(schedule_name, schedule_options=schedule_options)
    for option in schedule_options or []:
        if str(option.get("value") or "").strip().lower() != normalized_name:
            continue
        return bool(option.get("requires_at"))
    return normalized_name == "once"


def _empty_db_admin_event_form(*, schedule_options=()):
    schedule_name = _default_event_schedule_name(schedule_options)
    return {
        "database_name": "",
        "event_name": "",
        "schedule_name": schedule_name,
        "schedule_at": "",
        "body_sql": "",
        "schedule_requires_at": _event_schedule_requires_at(schedule_name, schedule_options=schedule_options),
    }


def _build_db_admin_event_form(database_inventory, *, payload=None, fallback_database="", schedule_options=()):
    form = _empty_db_admin_event_form(schedule_options=schedule_options)
    available_database_names = {
        row["database_name"]
        for row in database_inventory
        if not row.get("is_system")
    }
    fallback_name = str(fallback_database or "").strip()
    if fallback_name in available_database_names:
        form["database_name"] = fallback_name

    if payload is not None:
        submitted_database_name = str(payload.get("event_database_name", form["database_name"]) or "").strip()
        if submitted_database_name in available_database_names:
            form["database_name"] = submitted_database_name
        form["event_name"] = str(payload.get("event_name", form["event_name"]) or "").strip()
        form["schedule_name"] = _normalize_event_schedule_name(
            payload.get("event_schedule_name", form["schedule_name"]),
            schedule_options=schedule_options,
        )
        form["schedule_at"] = str(payload.get("event_schedule_at", form["schedule_at"]) or "").strip()
        form["body_sql"] = str(payload.get("event_body_sql", form["body_sql"]) or "")

    form["schedule_requires_at"] = _event_schedule_requires_at(
        form["schedule_name"],
        schedule_options=schedule_options,
    )
    return form


def _order_db_admin_event_rows(rows, *, focused_event_database="", focused_event_name=""):
    ordered_rows = [dict(row) for row in rows or []]
    for row in ordered_rows:
        row["is_focus"] = False

    ordered_rows.sort(
        key=lambda row: (
            str(row.get("database_name") or "").lower(),
            str(row.get("event_name") or "").lower(),
        )
    )
    ordered_rows.sort(key=lambda row: str(row.get("sort_created_value") or ""), reverse=True)

    focused_database = str(focused_event_database or "").strip()
    focused_event = str(focused_event_name or "").strip()
    if focused_database and focused_event:
        focused_key = (focused_database, focused_event)
        ordered_rows.sort(
            key=lambda row: (
                str(row.get("database_name") or "").strip(),
                str(row.get("event_name") or "").strip(),
            ) != focused_key
        )
        for row in ordered_rows:
            row["is_focus"] = (
                str(row.get("database_name") or "").strip() == focused_database
                and str(row.get("event_name") or "").strip() == focused_event
            )
    return ordered_rows


def _build_missing_primary_key_fix_result(rows, fix_missing_primary_key_table):
    fixed_with_auto_increment = []
    fixed_with_row_id = []
    already_fixed = []
    manual_review = [row["full_table_name"] for row in rows if not row["is_fixable"]]
    failures = []

    for row in rows:
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
    return flash_category, " ".join(message_parts)


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
    fetch_tables_for_database=None,
    fetch_missing_primary_key_rows=None,
    fix_missing_primary_key_table=None,
    create_db_event=None,
    set_db_events_enabled=None,
    delete_db_events=None,
    modify_charset_collation=None,
    preview_charset_collation=None,
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

    if normalized_action == "delete_tables":
        if not normalized_name:
            raise ValueError("Choose a database before deleting tables.")
        if normalized_name in system_schemas:
            raise ValueError("System schema tables cannot be deleted here.")
        if payload is None or not hasattr(payload, "getlist"):
            raise ValueError("Choose one or more tables to delete.")

        selected_tables = []
        seen_tables = set()
        for raw_table in payload.getlist("selected_table"):
            table_name = str(raw_table or "").strip()
            if table_name and table_name not in seen_tables:
                selected_tables.append(table_name)
                seen_tables.add(table_name)
        if not selected_tables:
            raise ValueError("Choose one or more tables to delete.")

        if fetch_tables_for_database is not None:
            available_tables = {
                str(row.get("table_name") or "").strip()
                for row in fetch_tables_for_database(normalized_name)
            }
            missing_tables = [table_name for table_name in selected_tables if table_name not in available_tables]
            if missing_tables:
                raise ValueError(
                    "Selected table(s) were not found in the current database: "
                    + ", ".join(missing_tables)
                )

        safe_database = quote_identifier(normalized_name)
        for table_name in selected_tables:
            safe_table = quote_identifier(table_name)
            execute_statement(f"DROP TABLE {safe_database}.{safe_table}")
        return {
            "flash_category": "success",
            "flash_message": f"Deleted {len(selected_tables)} table(s) from `{normalized_name}`.",
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"db_admin_tab": "select", "database": normalized_name},
        }

    if normalized_action == "modify_table_columns":
        if not normalized_name or not normalized_table:
            raise ValueError("Choose both a database and table before modifying columns.")
        if fetch_create_table_statement is None or fetch_table_columns is None:
            raise ValueError("Column metadata helpers are not available.")

        current_columns = fetch_table_columns(normalized_name, normalized_table)
        ddl_statement = fetch_create_table_statement(normalized_name, normalized_table)
        current_table_comment = ""
        if fetch_tables_for_database is not None:
            for row in fetch_tables_for_database(normalized_name):
                if str(row.get("table_name") or "").strip() == normalized_table:
                    current_table_comment = _normalize_db_admin_comment_text(row.get("table_comment"))
                    break
        change_request_payload = _build_db_admin_change_requests(
            current_columns,
            ddl_statement,
            payload,
            current_table_comment=current_table_comment,
        )
        change_requests = change_request_payload["column_change_requests"]
        alter_clauses = _build_db_admin_change_column_clauses(change_requests, quote_identifier=quote_identifier)
        if change_request_payload["table_comment_changed"]:
            alter_clauses.append(f"COMMENT = {_quote_sql_string_literal(change_request_payload['new_table_comment'])}")
        if not alter_clauses:
            raise ValueError("No column or table comment changes were submitted.")

        safe_database = quote_identifier(normalized_name)
        safe_table = quote_identifier(normalized_table)
        execute_statement(f"ALTER TABLE {safe_database}.{safe_table} " + ", ".join(alter_clauses))
        updated_parts = []
        if change_requests:
            updated_parts.append(f"{len(change_requests)} column definition(s)")
        if change_request_payload["table_comment_changed"]:
            updated_parts.append("table comment")
        return {
            "flash_category": "success",
            "flash_message": (
                f"Updated {' and '.join(updated_parts)} on "
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

    if normalized_action == "fix_missing_primary_key_selected":
        if fetch_missing_primary_key_rows is None or fix_missing_primary_key_table is None:
            raise ValueError("Primary key repair helpers are not available.")
        if payload is None or not hasattr(payload, "getlist"):
            raise ValueError("Choose one or more tables before applying the primary key fix.")

        selected_keys = {
            str(value or "").strip()
            for value in payload.getlist("selected_missing_primary_key")
            if str(value or "").strip()
        }
        if not selected_keys:
            raise ValueError("Choose one or more tables before applying the primary key fix.")

        report = _build_missing_primary_key_report(fetch_missing_primary_key_rows())
        selected_rows = [
            row
            for row in report["rows"]
            if f"{row['database_name']}.{row['table_name']}" in selected_keys
        ]
        if not selected_rows:
            raise ValueError("The selected tables were not found in the current missing-primary-key report.")

        flash_category, flash_message = _build_missing_primary_key_fix_result(
            selected_rows,
            fix_missing_primary_key_table,
        )
        return {
            "flash_category": flash_category,
            "flash_message": flash_message,
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"db_admin_tab": "missing-primary-key"},
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

        flash_category, flash_message = _build_missing_primary_key_fix_result(
            report["rows"],
            fix_missing_primary_key_table,
        )
        return {
            "flash_category": flash_category,
            "flash_message": flash_message,
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"db_admin_tab": "missing-primary-key"},
        }

    if normalized_action == "create_event":
        if create_db_event is None or payload is None:
            raise ValueError("Event creation helper is not available.")
        return create_db_event(
            payload.get("event_database_name", ""),
            payload.get("event_name", ""),
            payload.get("event_schedule_name", ""),
            payload.get("event_schedule_at", ""),
            payload.get("event_body_sql", ""),
        )

    if normalized_action in {"enable_events", "disable_events"}:
        if set_db_events_enabled is None or payload is None or not hasattr(payload, "getlist"):
            raise ValueError("Event status helper is not available.")
        return set_db_events_enabled(
            payload.getlist("selected_event_key"),
            enabled=normalized_action == "enable_events",
        )

    if normalized_action == "delete_events":
        if delete_db_events is None or payload is None or not hasattr(payload, "getlist"):
            raise ValueError("Event delete helper is not available.")
        return delete_db_events(payload.getlist("selected_event_key"))

    if normalized_action == "modify_charset_collation":
        if modify_charset_collation is None or payload is None:
            raise ValueError("Charset/collation update helper is not available.")
        result = modify_charset_collation(normalized_name, payload)
        return {
            "flash_category": "success",
            "flash_message": result.get("message") or f"Updated charset/collation in `{normalized_name}`.",
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {
                "db_admin_tab": "charset-collation",
                "database": result.get("database_name") or normalized_name,
            },
        }

    if normalized_action == "preview_charset_collation":
        if preview_charset_collation is None or payload is None:
            raise ValueError("Charset/collation preview helper is not available.")
        return {
            "flash_category": "success",
            "flash_message": "Charset/collation change plan generated.",
            "charset_collation_preview": preview_charset_collation(normalized_name, payload),
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
    table_info_tab="columns",
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
    fetch_event_rows=None,
    event_form_payload=None,
    event_schedule_options=(),
    focused_event_database="",
    focused_event_name="",
    fetch_charset_collation_report=None,
    fetch_charset_collation_options=None,
    charset_collation_payload=None,
):
    inventory = fetch_database_inventory()
    available_database_names = {row["database_name"] for row in inventory}
    normalized_database = str(selected_database or "").strip()
    normalized_table = str(selected_table or "").strip()
    active_table_info_tab = str(table_info_tab or "columns").strip().lower()
    if active_table_info_tab not in {"columns", "ddl", "indexes", "partitions", "preview", "modify-columns"}:
        active_table_info_tab = "columns"
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
    selected_table_row = {}
    table_edit_comment = ""
    column_edit_rows = []
    column_edit_unsupported_columns = []
    selected_table_error = ""
    event_rows = []
    event_error = ""
    charset_collation_report = {
        "rows": [],
        "error": "",
        "table_count": 0,
        "text_column_count": 0,
        "column_difference_count": 0,
    }
    charset_collation_options = {"charsets": [], "collations": []}
    charset_collation_form = {
        "target_charset": "utf8mb4",
        "target_collation": "utf8mb4_0900_ai_ci",
        "foreign_key_checks": "on",
        "drop_foreign_keys": False,
        "selected_tables": set(),
        "selected_columns": set(),
    }
    event_form = _build_db_admin_event_form(
        inventory,
        fallback_database=normalized_database,
        schedule_options=event_schedule_options,
    )

    if db_admin_tab == "missing-primary-key":
        try:
            missing_primary_key_report = _build_missing_primary_key_report(
                fetch_missing_primary_key_rows() if fetch_missing_primary_key_rows is not None else []
            )
        except Exception as error:  # pragma: no cover - depends on server features
            missing_primary_key_report = _empty_missing_primary_key_report()
            missing_primary_key_report["error"] = str(error)

    if db_admin_tab == "event":
        event_form = _build_db_admin_event_form(
            inventory,
            payload=event_form_payload,
            fallback_database=focused_event_database or normalized_database,
            schedule_options=event_schedule_options,
        )
        try:
            event_rows = _order_db_admin_event_rows(
                fetch_event_rows() if fetch_event_rows is not None else [],
                focused_event_database=focused_event_database,
                focused_event_name=focused_event_name,
            )
        except Exception as error:  # pragma: no cover - depends on server features
            event_rows = []
            event_error = str(error)

    if db_admin_tab == "charset-collation":
        try:
            if charset_collation_payload is not None and hasattr(charset_collation_payload, "getlist"):
                charset_collation_form["target_charset"] = str(
                    charset_collation_payload.get("target_charset", charset_collation_form["target_charset"]) or ""
                ).strip()
                charset_collation_form["target_collation"] = str(
                    charset_collation_payload.get("target_collation", charset_collation_form["target_collation"]) or ""
                ).strip()
                charset_collation_form["foreign_key_checks"] = str(
                    charset_collation_payload.get("foreign_key_checks", charset_collation_form["foreign_key_checks"]) or "on"
                ).strip()
                charset_collation_form["drop_foreign_keys"] = (
                    str(charset_collation_payload.get("drop_foreign_keys", "")).strip().lower()
                    in {"1", "true", "yes", "on"}
                )
                charset_collation_form["selected_tables"] = {
                    str(value or "").strip()
                    for value in charset_collation_payload.getlist("selected_charset_table")
                    if str(value or "").strip()
                }
                selected_columns = set()
                for raw_value in charset_collation_payload.getlist("selected_charset_column"):
                    try:
                        column_payload = json.loads(str(raw_value or ""))
                    except json.JSONDecodeError:
                        continue
                    table_name = str(column_payload.get("table") or "").strip()
                    column_name = str(column_payload.get("column") or "").strip()
                    if table_name and column_name:
                        selected_columns.add(f"{table_name}.{column_name}")
                charset_collation_form["selected_columns"] = selected_columns
            if fetch_charset_collation_options is not None:
                charset_collation_options = fetch_charset_collation_options()
            if normalized_database and fetch_charset_collation_report is not None:
                charset_collation_report = fetch_charset_collation_report(normalized_database)
                for row in charset_collation_report.get("rows", []):
                    table_name = str(row.get("table_name") or "")
                    row["is_selected"] = table_name in charset_collation_form["selected_tables"]
                    for column in row.get("text_columns", []):
                        column["is_selected"] = (
                            f"{table_name}.{column.get('column_name')}" in charset_collation_form["selected_columns"]
                        )
        except Exception as error:  # pragma: no cover - depends on server metadata
            charset_collation_report["error"] = str(error)

    if db_admin_tab == "select" and normalized_table:
        try:
            selected_table_row = next(
                (
                    row
                    for row in available_tables
                    if str(row.get("table_name") or "").strip() == normalized_table
                ),
                {},
            )
            table_edit_comment = _normalize_db_admin_comment_text(selected_table_row.get("table_comment"))
            if column_edit_payload is not None:
                table_edit_comment = _normalize_db_admin_comment_text(
                    column_edit_payload.get("table_comment", table_edit_comment)
                )
        except MySQLProgrammingError as error:
            if error.args and error.args[0] == 1146:
                return {
                    "redirect_endpoint": "db_admin_page",
                    "redirect_values": {"database": normalized_database},
                    "flash_category": "error",
                    "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                }
            raise

        metadata_errors = []
        if active_table_info_tab in {"ddl", "modify-columns"}:
            try:
                ddl_statement = fetch_create_table_statement(normalized_database, normalized_table)
            except MySQLProgrammingError as error:
                if error.args and error.args[0] == 1146:
                    return {
                        "redirect_endpoint": "db_admin_page",
                        "redirect_values": {"database": normalized_database},
                        "flash_category": "error",
                        "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                    }
                metadata_errors.append(f"SHOW CREATE TABLE failed: {error}")
            except MySQLOperationalError as error:
                metadata_errors.append(f"SHOW CREATE TABLE failed: {error}")
        if active_table_info_tab in {"columns", "modify-columns"}:
            try:
                columns = fetch_table_columns(normalized_database, normalized_table)
            except MySQLProgrammingError as error:
                if error.args and error.args[0] == 1146:
                    return {
                        "redirect_endpoint": "db_admin_page",
                        "redirect_values": {"database": normalized_database},
                        "flash_category": "error",
                        "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                    }
                metadata_errors.append(f"Column metadata failed: {error}")
            except MySQLOperationalError as error:
                metadata_errors.append(f"Column metadata failed: {error}")
        if active_table_info_tab == "indexes":
            try:
                indexes = fetch_table_indexes(normalized_database, normalized_table)
            except MySQLProgrammingError as error:
                if error.args and error.args[0] == 1146:
                    return {
                        "redirect_endpoint": "db_admin_page",
                        "redirect_values": {"database": normalized_database},
                        "flash_category": "error",
                        "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                    }
                metadata_errors.append(f"Index metadata failed: {error}")
            except MySQLOperationalError as error:
                metadata_errors.append(f"Index metadata failed: {error}")
        if active_table_info_tab == "partitions":
            try:
                partitions = fetch_table_partitions(normalized_database, normalized_table)
            except MySQLProgrammingError as error:
                if error.args and error.args[0] == 1146:
                    return {
                        "redirect_endpoint": "db_admin_page",
                        "redirect_values": {"database": normalized_database},
                        "flash_category": "error",
                        "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                    }
                metadata_errors.append(f"Partition metadata failed: {error}")
            except MySQLOperationalError as error:
                metadata_errors.append(f"Partition metadata failed: {error}")
        if active_table_info_tab == "modify-columns" and (columns or ddl_statement):
            column_edit_rows, column_edit_unsupported_columns = _build_db_admin_column_edit_rows(
                columns,
                ddl_statement,
                payload=column_edit_payload,
            )
        if active_table_info_tab == "preview":
            try:
                preview = fetch_table_preview(normalized_database, normalized_table, page=preview_page)
            except MySQLProgrammingError as error:
                if error.args and error.args[0] == 1146:
                    return {
                        "redirect_endpoint": "db_admin_page",
                        "redirect_values": {"database": normalized_database},
                        "flash_category": "error",
                        "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
                    }
                selected_table_error = f"Preview failed: {error}"
            except MySQLOperationalError as error:
                selected_table_error = f"Preview failed: {error}"
        if metadata_errors:
            selected_table_error = " ".join([selected_table_error, *metadata_errors]).strip()

    return {
        "database_inventory": inventory,
        "selected_database": normalized_database,
        "tables": available_tables,
        "selected_table": normalized_table,
        "preview": preview,
        "ddl_statement": ddl_statement,
        "columns": columns,
        "selected_table_row": selected_table_row,
        "selected_table_error": selected_table_error,
        "table_edit_comment": table_edit_comment,
        "column_edit_rows": column_edit_rows,
        "column_edit_unsupported_columns": column_edit_unsupported_columns,
        "indexes": indexes,
        "partitions": partitions,
        "missing_primary_key_report": missing_primary_key_report,
        "event_rows": event_rows,
        "event_error": event_error,
        "event_form": event_form,
        "charset_collation_report": charset_collation_report,
        "charset_collation_options": charset_collation_options,
        "charset_collation_form": charset_collation_form,
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
            "table_comment": row.get("table_comment", ""),
            "heatwave_configured": "yes" if row["heatwave_configured"] else "no",
            "create_options": row["create_options"],
        }
        for row in rows
    ]
    return {
        "filename": f"{normalized_database or 'database'}-tables.csv",
        "columns": ["table_name", "engine", "row_count", "table_comment", "heatwave_configured", "create_options"],
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

    if error_message and not result_sets:
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
    for index, result_set in enumerate(result_sets or [], start=1):
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
                "statement": result_set.get("statement", ""),
                "text_output": result_set.get("text_output", ""),
                "empty_text": result_set.get("empty_text", "This result did not return any rows."),
            }
        )
    if error_message and result_sets:
        tabs.append(
            {
                "key": "error",
                "label": "Error",
                "kind": "message",
                "message": error_message,
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
