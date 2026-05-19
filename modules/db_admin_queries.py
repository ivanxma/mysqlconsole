import re
from datetime import datetime


_execute_query = None
_execute_statement = None
_fetch_scalar = None
_fetch_table_column_lookup = None
_get_event_schedule_option = None
_quote_identifier = None
_quote_sql_string = None
_mysql_connection = None
_db_admin_preview_masked_base_types = set()


def configure_db_admin_queries(
    *,
    execute_query,
    execute_statement,
    fetch_scalar,
    fetch_table_column_lookup,
    get_event_schedule_option,
    quote_identifier,
    quote_sql_string,
    mysql_connection,
    db_admin_preview_masked_base_types,
):
    global _execute_query, _execute_statement, _fetch_scalar, _fetch_table_column_lookup
    global _get_event_schedule_option, _quote_identifier, _quote_sql_string
    global _mysql_connection, _db_admin_preview_masked_base_types, DB_ADMIN_PREVIEW_MASKED_BASE_TYPES
    _execute_query = execute_query
    _execute_statement = execute_statement
    _fetch_scalar = fetch_scalar
    _fetch_table_column_lookup = fetch_table_column_lookup
    _get_event_schedule_option = get_event_schedule_option
    _quote_identifier = quote_identifier
    _quote_sql_string = quote_sql_string
    _mysql_connection = mysql_connection
    _db_admin_preview_masked_base_types = set(db_admin_preview_masked_base_types or [])
    DB_ADMIN_PREVIEW_MASKED_BASE_TYPES = _db_admin_preview_masked_base_types


def execute_query(*args, **kwargs):
    if _execute_query is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _execute_query(*args, **kwargs)


def execute_statement(*args, **kwargs):
    if _execute_statement is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _execute_statement(*args, **kwargs)


def fetch_scalar(*args, **kwargs):
    if _fetch_scalar is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _fetch_scalar(*args, **kwargs)


def fetch_table_column_lookup(*args, **kwargs):
    if _fetch_table_column_lookup is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _fetch_table_column_lookup(*args, **kwargs)


def get_event_schedule_option(*args, **kwargs):
    if _get_event_schedule_option is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _get_event_schedule_option(*args, **kwargs)


def quote_identifier(*args, **kwargs):
    if _quote_identifier is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _quote_identifier(*args, **kwargs)


def mysql_connection(*args, **kwargs):
    if _mysql_connection is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _mysql_connection(*args, **kwargs)


def quote_sql_string(*args, **kwargs):
    if _quote_sql_string is None:
        raise RuntimeError("DB Admin query dependencies are not configured")
    return _quote_sql_string(*args, **kwargs)


DB_ADMIN_PREVIEW_MASKED_BASE_TYPES = _db_admin_preview_masked_base_types


def fetch_charset_collation_options():
    rows = execute_query(
        """
        SELECT
          cs.character_set_name AS character_set_name_value,
          cs.default_collate_name AS default_collation_name_value,
          c.collation_name AS collation_name_value,
          c.is_default AS is_default_value
        FROM information_schema.character_sets AS cs
        JOIN information_schema.collations AS c
          ON c.character_set_name = cs.character_set_name
        ORDER BY cs.character_set_name, c.is_default DESC, c.collation_name
        """
    )
    charset_lookup = {}
    collations = []
    for row in rows:
        charset_name = row["character_set_name_value"]
        default_collation = row["default_collation_name_value"] or ""
        if charset_name not in charset_lookup:
            charset_lookup[charset_name] = {
                "charset_name": charset_name,
                "default_collation": default_collation,
            }
        collations.append(
            {
                "charset_name": charset_name,
                "collation_name": row["collation_name_value"],
                "is_default": str(row["is_default_value"] or "").upper() == "YES",
            }
        )
    return {
        "charsets": sorted(charset_lookup.values(), key=lambda item: item["charset_name"].lower()),
        "collations": collations,
    }


def _fetch_db_admin_charset_column_rows(database_name):
    if not database_name:
        return []
    rows = execute_query(
        """
        SELECT
          c.table_name AS table_name_value,
          c.column_name AS column_name_value,
          c.column_type AS column_type_value,
          c.character_set_name AS character_set_name_value,
          c.collation_name AS collation_name_value,
          c.column_key AS column_key_value,
          c.extra AS extra_value,
          c.ordinal_position AS ordinal_position_value,
          GROUP_CONCAT(
            DISTINCT CONCAT(
              k.constraint_name,
              ' -> ',
              k.referenced_table_schema,
              '.',
              k.referenced_table_name,
              '.',
              k.referenced_column_name
            )
            ORDER BY k.constraint_name
            SEPARATOR '; '
          ) AS outgoing_foreign_keys_value
        FROM information_schema.columns AS c
        LEFT JOIN information_schema.key_column_usage AS k
          ON k.table_schema = c.table_schema
         AND k.table_name = c.table_name
         AND k.column_name = c.column_name
         AND k.referenced_table_name IS NOT NULL
        WHERE c.table_schema = %s
          AND c.character_set_name IS NOT NULL
        GROUP BY
          c.table_name,
          c.column_name,
          c.column_type,
          c.character_set_name,
          c.collation_name,
          c.column_key,
          c.extra,
          c.ordinal_position
        ORDER BY c.table_name, c.ordinal_position
        """,
        [database_name],
    )
    return [
        {
            "table_name": row["table_name_value"],
            "column_name": row["column_name_value"],
            "column_type": row["column_type_value"],
            "charset_name": row["character_set_name_value"] or "",
            "collation_name": row["collation_name_value"] or "",
            "column_key": row["column_key_value"] or "",
            "extra": row["extra_value"] or "",
            "ordinal_position": row["ordinal_position_value"],
            "outgoing_foreign_keys": row["outgoing_foreign_keys_value"] or "",
            "has_outgoing_foreign_key": bool(row["outgoing_foreign_keys_value"]),
        }
        for row in rows
    ]


def _fetch_foreign_key_definitions(database_name, *, table_name="", referenced_table_name="", selected_columns=None):
    normalized_database = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()
    normalized_referenced_table = str(referenced_table_name or "").strip()
    if not normalized_database:
        return []

    selected_column_set = {
        str(column_name or "").strip()
        for column_name in selected_columns or []
        if str(column_name or "").strip()
    }
    sql = """
        SELECT
          k.constraint_schema AS constraint_schema_value,
          k.constraint_name AS constraint_name_value,
          k.table_schema AS table_schema_value,
          k.table_name AS table_name_value,
          k.column_name AS column_name_value,
          k.ordinal_position AS ordinal_position_value,
          k.referenced_table_schema AS referenced_table_schema_value,
          k.referenced_table_name AS referenced_table_name_value,
          k.referenced_column_name AS referenced_column_name_value,
          rc.update_rule AS update_rule_value,
          rc.delete_rule AS delete_rule_value
        FROM information_schema.key_column_usage AS k
        LEFT JOIN information_schema.referential_constraints AS rc
          ON rc.constraint_schema = k.constraint_schema
         AND rc.constraint_name = k.constraint_name
         AND rc.table_name = k.table_name
        WHERE k.referenced_table_name IS NOT NULL
    """
    params = []
    if normalized_table:
        sql += " AND k.table_schema = %s AND k.table_name = %s"
        params.extend([normalized_database, normalized_table])
    elif normalized_referenced_table:
        sql += " AND k.referenced_table_schema = %s AND k.referenced_table_name = %s"
        params.extend([normalized_database, normalized_referenced_table])
    else:
        sql += " AND k.table_schema = %s"
        params.append(normalized_database)
    sql += " ORDER BY k.table_schema, k.table_name, k.constraint_name, k.ordinal_position"

    grouped = {}
    ordered_keys = []
    for row in execute_query(sql, params):
        key = (
            row["table_schema_value"],
            row["table_name_value"],
            row["constraint_name_value"],
        )
        if key not in grouped:
            grouped[key] = {
                "constraint_schema": row["constraint_schema_value"],
                "constraint_name": row["constraint_name_value"],
                "table_schema": row["table_schema_value"],
                "table_name": row["table_name_value"],
                "referenced_table_schema": row["referenced_table_schema_value"],
                "referenced_table_name": row["referenced_table_name_value"],
                "update_rule": row["update_rule_value"] or "",
                "delete_rule": row["delete_rule_value"] or "",
                "columns": [],
                "referenced_columns": [],
            }
            ordered_keys.append(key)
        grouped[key]["columns"].append(row["column_name_value"])
        grouped[key]["referenced_columns"].append(row["referenced_column_name_value"])

    definitions = []
    for key in ordered_keys:
        definition = grouped[key]
        if selected_column_set and not any(column in selected_column_set for column in definition["columns"]):
            continue
        safe_table_schema = quote_identifier(definition["table_schema"])
        safe_table = quote_identifier(definition["table_name"])
        safe_referenced_schema = quote_identifier(definition["referenced_table_schema"])
        safe_referenced_table = quote_identifier(definition["referenced_table_name"])
        column_list = ", ".join(quote_identifier(column) for column in definition["columns"])
        referenced_column_list = ", ".join(quote_identifier(column) for column in definition["referenced_columns"])
        create_statement = (
            f"ALTER TABLE {safe_table_schema}.{safe_table} "
            f"ADD CONSTRAINT {_quote_existing_mysql_identifier(definition['constraint_name'])} "
            f"FOREIGN KEY ({column_list}) "
            f"REFERENCES {safe_referenced_schema}.{safe_referenced_table} ({referenced_column_list})"
        )
        if definition["delete_rule"]:
            create_statement += f" ON DELETE {definition['delete_rule']}"
        if definition["update_rule"]:
            create_statement += f" ON UPDATE {definition['update_rule']}"
        drop_statement = (
            f"ALTER TABLE {safe_table_schema}.{safe_table} "
            f"DROP FOREIGN KEY {_quote_existing_mysql_identifier(definition['constraint_name'])}"
        )
        definitions.append(
            {
                **definition,
                "column_list": ", ".join(definition["columns"]),
                "referenced_column_list": ", ".join(definition["referenced_columns"]),
                "drop_statement": drop_statement,
                "create_statement": create_statement,
            }
        )
    return definitions


def fetch_db_admin_charset_collation_report(database_name):
    normalized_database = str(database_name or "").strip()
    report = {
        "rows": [],
        "error": "",
        "table_count": 0,
        "text_column_count": 0,
        "column_difference_count": 0,
    }
    if not normalized_database:
        return report

    table_rows = execute_query(
        """
        SELECT
          t.table_name AS table_name_value,
          t.engine AS engine_value,
          t.table_rows AS table_rows_value,
          t.table_collation AS table_collation_value,
          co.character_set_name AS table_charset_value
        FROM information_schema.tables AS t
        LEFT JOIN information_schema.collations AS co
          ON co.collation_name = t.table_collation
        WHERE t.table_schema = %s
          AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
        """,
        [normalized_database],
    )
    column_rows = _fetch_db_admin_charset_column_rows(normalized_database)
    outgoing_foreign_keys = _fetch_foreign_key_definitions(normalized_database)
    outgoing_by_table = {}
    for definition in outgoing_foreign_keys:
        outgoing_by_table.setdefault(definition["table_name"], []).append(definition)
    columns_by_table = {}
    for column in column_rows:
        columns_by_table.setdefault(column["table_name"], []).append(column)

    for row in table_rows:
        table_name = row["table_name_value"]
        incoming_foreign_keys = _fetch_foreign_key_definitions(
            normalized_database,
            referenced_table_name=table_name,
        )
        table_charset = row["table_charset_value"] or ""
        table_collation = row["table_collation_value"] or ""
        columns = []
        difference_count = 0
        for column in columns_by_table.get(table_name, []):
            column_row = dict(column)
            column_row["differs_from_table"] = bool(
                table_collation
                and (
                    column_row["charset_name"] != table_charset
                    or column_row["collation_name"] != table_collation
                )
            )
            if column_row["differs_from_table"]:
                difference_count += 1
            columns.append(column_row)

        report["rows"].append(
            {
                "database_name": normalized_database,
                "table_name": table_name,
                "engine": row["engine_value"] or "-",
                "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "table_charset": table_charset or "-",
                "table_collation": table_collation or "-",
                "text_columns": columns,
                "text_column_count": len(columns),
                "column_difference_count": difference_count,
                "has_column_differences": difference_count > 0,
                "foreign_key_definitions": outgoing_by_table.get(table_name, []),
                "referenced_by_foreign_keys": incoming_foreign_keys,
            }
        )
        report["text_column_count"] += len(columns)
        report["column_difference_count"] += difference_count

    report["table_count"] = len(report["rows"])
    return report


def _validate_charset_collation_pair(charset_name, collation_name):
    normalized_charset = str(charset_name or "").strip()
    normalized_collation = str(collation_name or "").strip()
    if not normalized_charset:
        raise ValueError("Choose a target character set.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", normalized_charset):
        raise ValueError("Target character set is invalid.")
    if normalized_collation and not re.fullmatch(r"[A-Za-z0-9_]+", normalized_collation):
        raise ValueError("Target collation is invalid.")

    if not normalized_collation:
        normalized_collation = fetch_scalar(
            """
            SELECT default_collate_name
            FROM information_schema.character_sets
            WHERE character_set_name = %s
            """,
            [normalized_charset],
            default="",
        )
        normalized_collation = str(normalized_collation or "").strip()
    if not normalized_collation:
        raise ValueError(f"Character set `{normalized_charset}` was not found.")

    match_count = fetch_scalar(
        """
        SELECT COUNT(*)
        FROM information_schema.collations
        WHERE character_set_name = %s
          AND collation_name = %s
        """,
        [normalized_charset, normalized_collation],
        default=0,
    )
    if not match_count:
        raise ValueError(f"Collation `{normalized_collation}` does not belong to character set `{normalized_charset}`.")
    return normalized_charset, normalized_collation


def _parse_charset_column_selection(raw_values):
    selected_columns = []
    seen = set()
    for raw_value in raw_values or []:
        try:
            payload = json.loads(str(raw_value or ""))
        except json.JSONDecodeError as error:
            raise ValueError("One or more selected columns are invalid.") from error
        table_name = str(payload.get("table") or "").strip()
        column_name = str(payload.get("column") or "").strip()
        if not table_name or not column_name:
            raise ValueError("One or more selected columns are invalid.")
        quote_identifier(table_name)
        quote_identifier(column_name)
        key = (table_name, column_name)
        if key in seen:
            continue
        selected_columns.append({"table_name": table_name, "column_name": column_name})
        seen.add(key)
    return selected_columns


def _extract_column_definitions_from_create_statement(create_table_statement):
    definitions = {}
    for line in str(create_table_statement or "").splitlines():
        match = re.match(r"^\s*`([^`]+)`\s+(.*?)(?:,)?\s*$", line.rstrip())
        if match:
            definitions[match.group(1)] = match.group(2).strip()
    return definitions


def _strip_charset_collation_clauses(column_definition):
    text = str(column_definition or "").strip()
    text = re.sub(r"\s+CHARACTER\s+SET\s+`?[A-Za-z0-9_]+`?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+CHARSET\s+`?[A-Za-z0-9_]+`?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+COLLATE\s+`?[A-Za-z0-9_]+`?", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _build_column_charset_definition(current_definition, column_type, charset_name, collation_name):
    cleaned_definition = _strip_charset_collation_clauses(current_definition)
    normalized_column_type = str(column_type or "").strip()
    if not cleaned_definition or not normalized_column_type:
        raise ValueError("Unable to determine the current column definition.")
    if cleaned_definition[: len(normalized_column_type)].lower() != normalized_column_type.lower():
        raise ValueError(f"Unable to safely rewrite column definition `{cleaned_definition}`.")
    remainder = cleaned_definition[len(normalized_column_type) :].strip()
    new_definition = f"{normalized_column_type} CHARACTER SET {charset_name} COLLATE {collation_name}"
    if remainder:
        new_definition = f"{new_definition} {remainder}"
    return new_definition


def _fetch_outgoing_foreign_key_names(database_name, table_name, *, selected_columns=None):
    params = [database_name, table_name]
    selected_column_set = {
        str(column_name or "").strip()
        for column_name in selected_columns or []
        if str(column_name or "").strip()
    }
    rows = execute_query(
        """
        SELECT
          constraint_name AS constraint_name_value,
          column_name AS column_name_value
        FROM information_schema.key_column_usage
        WHERE table_schema = %s
          AND table_name = %s
          AND referenced_table_name IS NOT NULL
        ORDER BY constraint_name, ordinal_position
        """,
        params,
    )
    foreign_key_names = []
    seen = set()
    for row in rows:
        column_name = row["column_name_value"]
        if selected_column_set and column_name not in selected_column_set:
            continue
        constraint_name = row["constraint_name_value"]
        if constraint_name in seen:
            continue
        seen.add(constraint_name)
        foreign_key_names.append(constraint_name)
    return foreign_key_names


def _quote_existing_mysql_identifier(identifier):
    return "`" + str(identifier or "").replace("`", "``") + "`"


def build_db_admin_charset_collation_plan(database_name, payload):
    normalized_database = str(database_name or "").strip()
    if not normalized_database:
        raise ValueError("Choose a database before modifying charset or collation.")
    if is_system_schema_name(normalized_database):
        raise ValueError("System schemas cannot be changed here.")
    if payload is None or not hasattr(payload, "getlist"):
        raise ValueError("Charset/collation update payload is missing.")

    target_charset, target_collation = _validate_charset_collation_pair(
        payload.get("target_charset", ""),
        payload.get("target_collation", ""),
    )
    selected_tables = []
    seen_tables = set()
    for raw_table in payload.getlist("selected_charset_table"):
        table_name = str(raw_table or "").strip()
        if not table_name or table_name in seen_tables:
            continue
        quote_identifier(table_name)
        selected_tables.append(table_name)
        seen_tables.add(table_name)

    selected_columns = _parse_charset_column_selection(payload.getlist("selected_charset_column"))
    selected_columns = [
        row for row in selected_columns
        if row["table_name"] not in seen_tables
    ]
    if not selected_tables and not selected_columns:
        raise ValueError("Choose at least one table or column to modify.")

    available_tables = {row["table_name"] for row in fetch_tables_for_database(normalized_database)}
    missing_tables = [
        table_name
        for table_name in selected_tables + [row["table_name"] for row in selected_columns]
        if table_name not in available_tables
    ]
    if missing_tables:
        raise ValueError(f"Selected table was not found: `{missing_tables[0]}`.")

    drop_foreign_keys = str(payload.get("drop_foreign_keys", "")).strip().lower() in {"1", "true", "yes", "on"}
    disable_fk_checks = str(payload.get("foreign_key_checks", "on")).strip().lower() == "off"
    safe_database = quote_identifier(normalized_database)

    alter_statements = []
    foreign_key_definitions = []
    for table_name in selected_tables:
        safe_table = quote_identifier(table_name)
        if drop_foreign_keys:
            foreign_key_definitions.extend(
                _fetch_foreign_key_definitions(normalized_database, table_name=table_name)
            )
        alter_statements.append(
            f"ALTER TABLE {safe_database}.{safe_table} CONVERT TO CHARACTER SET {target_charset} COLLATE {target_collation}"
        )

    columns_by_table = {}
    for column in selected_columns:
        columns_by_table.setdefault(column["table_name"], []).append(column["column_name"])

    for table_name, column_names in columns_by_table.items():
        safe_table = quote_identifier(table_name)
        column_rows = {
            row["column_name"]: row
            for row in _fetch_db_admin_charset_column_rows(normalized_database)
            if row["table_name"] == table_name
        }
        ddl_statement = fetch_create_table_statement(normalized_database, table_name)
        definition_lookup = _extract_column_definitions_from_create_statement(ddl_statement)
        if drop_foreign_keys:
            foreign_key_definitions.extend(
                _fetch_foreign_key_definitions(
                    normalized_database,
                    table_name=table_name,
                    selected_columns=column_names,
                )
            )
        for column_name in column_names:
            column_row = column_rows.get(column_name)
            if not column_row:
                raise ValueError(f"Column `{table_name}.{column_name}` was not found or is not character-based.")
            current_definition = definition_lookup.get(column_name, "")
            new_definition = _build_column_charset_definition(
                current_definition,
                column_row["column_type"],
                target_charset,
                target_collation,
            )
            alter_statements.append(
                f"ALTER TABLE {safe_database}.{safe_table} MODIFY COLUMN {quote_identifier(column_name)} {new_definition}"
            )

    if not alter_statements:
        raise ValueError("No charset/collation changes were submitted.")

    deduped_foreign_keys = []
    seen_foreign_keys = set()
    for definition in foreign_key_definitions:
        key = (
            definition["table_schema"],
            definition["table_name"],
            definition["constraint_name"],
        )
        if key in seen_foreign_keys:
            continue
        seen_foreign_keys.add(key)
        if not definition.get("drop_statement") or not definition.get("create_statement"):
            raise ValueError(
                f"Unable to generate full drop/recreate SQL for foreign key `{definition.get('constraint_name')}`."
            )
        deduped_foreign_keys.append(definition)

    drop_statements = [definition["drop_statement"] for definition in deduped_foreign_keys]
    recreate_statements = [definition["create_statement"] for definition in deduped_foreign_keys]
    changed_parts = []
    if selected_tables:
        changed_parts.append(f"{len(selected_tables)} table(s)")
    if selected_columns:
        changed_parts.append(f"{len(selected_columns)} column(s)")

    return {
        "database_name": normalized_database,
        "target_charset": target_charset,
        "target_collation": target_collation,
        "selected_table_count": len(selected_tables),
        "selected_column_count": len(selected_columns),
        "changed_parts": changed_parts,
        "disable_fk_checks": disable_fk_checks,
        "drop_foreign_keys": drop_foreign_keys,
        "drop_statements": drop_statements,
        "alter_statements": alter_statements,
        "recreate_statements": recreate_statements,
        "foreign_key_definitions": deduped_foreign_keys,
    }


def preview_db_admin_charset_collation(database_name, payload):
    return build_db_admin_charset_collation_plan(database_name, payload)


def build_db_admin_charset_collation_script(plan):
    lines = [
        "-- DBConsole charset/collation change script",
        f"-- Database: {plan['database_name']}",
        f"-- Target character set: {plan['target_charset']}",
        f"-- Target collation: {plan['target_collation']}",
        f"-- Selected tables: {plan['selected_table_count']}",
        f"-- Selected columns: {plan['selected_column_count']}",
        "",
    ]
    if plan["disable_fk_checks"]:
        lines.extend([
            "-- Foreign key checks disabled for this script.",
            "SET FOREIGN_KEY_CHECKS = 0;",
            "",
        ])
    if plan["drop_statements"]:
        lines.append("-- Drop foreign keys before charset/collation changes.")
        lines.extend(f"{statement};" for statement in plan["drop_statements"])
        lines.append("")
    lines.append("-- Apply charset/collation changes.")
    lines.extend(f"{statement};" for statement in plan["alter_statements"])
    lines.append("")
    if plan["recreate_statements"]:
        lines.append("-- Recreate foreign keys after charset/collation changes.")
        lines.extend(f"{statement};" for statement in plan["recreate_statements"])
        lines.append("")
    if plan["disable_fk_checks"]:
        lines.extend([
            "-- Restore foreign key checks.",
            "SET FOREIGN_KEY_CHECKS = 1;",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def modify_db_admin_charset_collation(database_name, payload):
    plan = build_db_admin_charset_collation_plan(database_name, payload)
    executed_drops = 0
    executed_recreates = 0
    with mysql_connection(database_override=plan["database_name"]) as connection:
        with connection.cursor() as cursor:
            if plan["disable_fk_checks"]:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for statement in plan["drop_statements"]:
                    cursor.execute(statement)
                    executed_drops += 1
                for statement in plan["alter_statements"]:
                    cursor.execute(statement)
                for statement in plan["recreate_statements"]:
                    cursor.execute(statement)
                    executed_recreates += 1
            finally:
                if plan["disable_fk_checks"]:
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    message = (
        f"Updated charset/collation to `{plan['target_charset']}` / `{plan['target_collation']}` "
        f"for {' and '.join(plan['changed_parts'])} in `{plan['database_name']}`."
    )
    if executed_drops:
        message += f" Dropped and recreated {executed_recreates} of {executed_drops} outgoing foreign key constraint(s)."
    if plan["disable_fk_checks"]:
        message += " FOREIGN_KEY_CHECKS was disabled for this execution and restored afterward."
    return {
        "message": message,
        "database_name": plan["database_name"],
    }


def _format_datetime_label(value, *, empty="-"):
    if not value:
        return empty
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _summarize_identifier_list(items, *, max_items=3):
    normalized_items = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not normalized_items:
        return ""
    if len(normalized_items) <= max_items:
        return ", ".join(normalized_items)
    remaining_count = len(normalized_items) - max_items
    return ", ".join(normalized_items[:max_items]) + f", and {remaining_count} more"


def _build_event_schedule_label(
    *,
    event_type="",
    execute_at=None,
    interval_value=None,
    interval_field="",
    starts=None,
    ends=None,
):
    normalized_event_type = str(event_type or "").strip().upper()
    if normalized_event_type == "ONE TIME":
        execute_at_label = _format_datetime_label(execute_at, empty="")
        return f"At {execute_at_label}" if execute_at_label else "One Time"

    interval_field_label = str(interval_field or "").strip().replace("_", " ").lower()
    try:
        interval_number = int(interval_value)
    except (TypeError, ValueError):
        interval_number = 0
    if not interval_field_label:
        schedule_label = "Recurring"
    elif interval_number == 1:
        schedule_label = f"Every 1 {interval_field_label}"
    else:
        plural_label = interval_field_label if interval_field_label.endswith("s") else f"{interval_field_label}s"
        schedule_label = f"Every {interval_number or interval_value} {plural_label}"

    starts_label = _format_datetime_label(starts, empty="")
    ends_label = _format_datetime_label(ends, empty="")
    if starts_label:
        schedule_label += f" starting {starts_label}"
    if ends_label:
        schedule_label += f" until {ends_label}"
    return schedule_label


def _parse_event_schedule_at(raw_value):
    normalized_value = str(raw_value or "").strip()
    if not normalized_value:
        raise ValueError("Choose a date and time for one-time event schedules.")
    try:
        schedule_at = datetime.fromisoformat(normalized_value)
    except ValueError as error:
        raise ValueError("Choose a valid date and time for one-time event schedules.") from error
    return schedule_at.strftime("%Y-%m-%d %H:%M:%S")


def _parse_selected_event_keys(raw_values):
    event_keys = []
    seen_keys = set()
    for raw_value in raw_values or []:
        database_name, separator, event_name = str(raw_value or "").strip().partition(".")
        if not separator or not database_name or not event_name:
            raise ValueError("One or more selected events are invalid.")
        quote_identifier(database_name)
        quote_identifier(event_name)
        event_key = (database_name, event_name)
        if event_key in seen_keys:
            continue
        seen_keys.add(event_key)
        event_keys.append(event_key)
    return event_keys


def fetch_db_admin_event_rows():
    rows = execute_query(
        """
        SELECT
          event_schema AS database_name_value,
          event_name AS event_name_value,
          status AS status_value,
          event_type AS event_type_value,
          execute_at AS execute_at_value,
          interval_value AS interval_value_value,
          interval_field AS interval_field_value,
          starts AS starts_value,
          ends AS ends_value,
          on_completion AS on_completion_value,
          definer AS definer_value,
          created AS created_value,
          last_altered AS last_altered_value,
          last_executed AS last_executed_value,
          COALESCE(created, last_altered, starts, execute_at) AS sort_created_value
        FROM information_schema.events
        WHERE event_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND event_schema NOT LIKE 'mysql@_%%' ESCAPE '@'
        ORDER BY COALESCE(created, last_altered, starts, execute_at) DESC, event_schema, event_name
        """
    )
    event_rows = []
    for row in rows:
        database_name = row["database_name_value"]
        event_name = row["event_name_value"]
        status = str(row["status_value"] or "").strip().upper()
        event_rows.append(
            {
                "database_name": database_name,
                "event_name": event_name,
                "event_key": f"{database_name}.{event_name}",
                "status": status,
                "status_label": status.replace("_", " ").title() if status else "-",
                "is_enabled": status == "ENABLED",
                "event_type": row["event_type_value"] or "-",
                "schedule_label": _build_event_schedule_label(
                    event_type=row["event_type_value"],
                    execute_at=row["execute_at_value"],
                    interval_value=row["interval_value_value"],
                    interval_field=row["interval_field_value"],
                    starts=row["starts_value"],
                    ends=row["ends_value"],
                ),
                "created": _format_datetime_label(row["created_value"]),
                "last_altered": _format_datetime_label(row["last_altered_value"]),
                "last_executed": _format_datetime_label(row["last_executed_value"]),
                "on_completion": row["on_completion_value"] or "-",
                "definer": row["definer_value"] or "-",
                "sort_created_value": _format_datetime_label(row["sort_created_value"], empty=""),
            }
        )
    return event_rows


def create_db_admin_event(database_name, event_name, schedule_name, schedule_at, body_sql):
    normalized_database = str(database_name or "").strip()
    normalized_event_name = str(event_name or "").strip()
    normalized_body_sql = str(body_sql or "").strip()
    normalized_body_statement = normalized_body_sql.rstrip().rstrip(";").strip()

    if not normalized_database:
        raise ValueError("Choose a database for the event.")
    if is_system_schema_name(normalized_database):
        raise ValueError("System schemas cannot be changed here.")
    if not fetch_database_exists(normalized_database):
        raise ValueError(f"Database `{normalized_database}` was not found.")
    if not normalized_event_name:
        raise ValueError("Event name is required.")
    if not normalized_body_statement:
        raise ValueError("Event content is required.")

    schedule_option = get_event_schedule_option(schedule_name)
    schedule_label = schedule_option["label"]
    if schedule_option["requires_at"]:
        schedule_at_value = _parse_event_schedule_at(schedule_at)
        schedule_clause = f"AT TIMESTAMP('{schedule_at_value}')"
        schedule_label = f"{schedule_option['label']} at {schedule_at_value}"
    else:
        schedule_clause = (
            f"EVERY {int(schedule_option['interval_value'])} {schedule_option['interval_field']} "
            "STARTS CURRENT_TIMESTAMP"
        )

    safe_database = quote_identifier(normalized_database)
    safe_event_name = quote_identifier(normalized_event_name)
    statement = (
        f"CREATE EVENT {safe_database}.{safe_event_name} "
        f"ON SCHEDULE {schedule_clause} "
        "ON COMPLETION PRESERVE "
        "ENABLE "
        f"DO {normalized_body_statement}"
    )
    execute_statement(statement, database=normalized_database)

    message = f"Created event `{normalized_database}.{normalized_event_name}` with schedule {schedule_label}."
    return {
        "flash_category": "success",
        "flash_message": message,
        "redirect_endpoint": "db_admin_page",
        "redirect_values": {
            "db_admin_tab": "event",
            "database": normalized_database,
            "focus_event_database": normalized_database,
            "focus_event_name": normalized_event_name,
        },
        "event_action_output": {
            "title": "Create Event",
            "category": "success",
            "message": message,
        },
    }


def set_db_admin_events_enabled(selected_event_keys, *, enabled):
    event_keys = _parse_selected_event_keys(selected_event_keys)
    if not event_keys:
        action_label = "enable" if enabled else "disable"
        raise ValueError(f"Select at least one event to {action_label}.")

    status_keyword = "ENABLE" if enabled else "DISABLE"
    action_label = "enabled" if enabled else "disabled"
    qualified_event_names = []
    for database_name, event_name in event_keys:
        if is_system_schema_name(database_name):
            raise ValueError("System schema events cannot be changed here.")
        execute_statement(
            f"ALTER EVENT {quote_identifier(database_name)}.{quote_identifier(event_name)} {status_keyword}",
            database=database_name,
        )
        qualified_event_names.append(f"`{database_name}.{event_name}`")

    message = f"{action_label.title()} {len(event_keys)} event(s): {_summarize_identifier_list(qualified_event_names)}."
    redirect_values = {"db_admin_tab": "event"}
    if len(event_keys) == 1:
        redirect_values["database"] = event_keys[0][0]
        redirect_values["focus_event_database"] = event_keys[0][0]
        redirect_values["focus_event_name"] = event_keys[0][1]

    return {
        "flash_category": "success",
        "flash_message": message,
        "redirect_endpoint": "db_admin_page",
        "redirect_values": redirect_values,
        "event_action_output": {
            "title": "Event Status",
            "category": "success",
            "message": message,
        },
    }


def delete_db_admin_events(selected_event_keys):
    event_keys = _parse_selected_event_keys(selected_event_keys)
    if not event_keys:
        raise ValueError("Select at least one event to delete.")

    qualified_event_names = []
    for database_name, event_name in event_keys:
        if is_system_schema_name(database_name):
            raise ValueError("System schema events cannot be changed here.")
        execute_statement(
            f"DROP EVENT {quote_identifier(database_name)}.{quote_identifier(event_name)}",
            database=database_name,
        )
        qualified_event_names.append(f"`{database_name}.{event_name}`")

    message = f"Deleted {len(event_keys)} event(s): {_summarize_identifier_list(qualified_event_names)}."
    redirect_values = {"db_admin_tab": "event"}
    if len(event_keys) == 1:
        redirect_values["database"] = event_keys[0][0]

    return {
        "flash_category": "success",
        "flash_message": message,
        "redirect_endpoint": "db_admin_page",
        "redirect_values": redirect_values,
        "event_action_output": {
            "title": "Delete Event",
            "category": "success",
            "message": message,
        },
    }


def _fetch_primary_key_status_rows(*, database_name="", table_name="", only_missing_primary_key):
    sql = """
        SELECT
          t.table_schema AS database_name_value,
          t.table_name AS table_name_value,
          t.engine AS engine_value,
          t.table_rows AS table_rows_value,
          COALESCE(primary_keys.has_primary_key, 0) AS has_primary_key_value,
          COALESCE(auto_increment_columns.auto_increment_column_name, '') AS auto_increment_column_name_value,
          COALESCE(row_id_columns.has_my_row_id, 0) AS has_my_row_id_value
        FROM information_schema.tables AS t
        LEFT JOIN (
          SELECT
            table_schema,
            table_name,
            1 AS has_primary_key
          FROM information_schema.statistics
          WHERE index_name = 'PRIMARY'
          GROUP BY table_schema, table_name
        ) AS primary_keys
          ON primary_keys.table_schema = t.table_schema
         AND primary_keys.table_name = t.table_name
        LEFT JOIN (
          SELECT
            table_schema,
            table_name,
            MIN(column_name) AS auto_increment_column_name
          FROM information_schema.columns
          WHERE LOWER(COALESCE(extra, '')) LIKE '%%auto_increment%%'
          GROUP BY table_schema, table_name
        ) AS auto_increment_columns
          ON auto_increment_columns.table_schema = t.table_schema
         AND auto_increment_columns.table_name = t.table_name
        LEFT JOIN (
          SELECT
            table_schema,
            table_name,
            MAX(CASE WHEN LOWER(column_name) = 'my_row_id' THEN 1 ELSE 0 END) AS has_my_row_id
          FROM information_schema.columns
          GROUP BY table_schema, table_name
        ) AS row_id_columns
          ON row_id_columns.table_schema = t.table_schema
         AND row_id_columns.table_name = t.table_name
        WHERE t.table_type = 'BASE TABLE'
          AND t.table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
          AND t.table_schema NOT LIKE 'mysql@_%%' ESCAPE '@'
    """
    params = []
    normalized_database = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()
    if normalized_database:
        sql += " AND t.table_schema = %s"
        params.append(normalized_database)
    if normalized_table:
        sql += " AND t.table_name = %s"
        params.append(normalized_table)
    if only_missing_primary_key:
        sql += " AND COALESCE(primary_keys.has_primary_key, 0) = 0"
    sql += " ORDER BY t.table_schema, t.table_name"

    rows = execute_query(sql, params or None)
    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "database_name": row["database_name_value"],
                "table_name": row["table_name_value"],
                "engine": row["engine_value"] or "-",
                "row_count": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "has_primary_key": bool(row["has_primary_key_value"]),
                "auto_increment_column_name": row["auto_increment_column_name_value"] or "",
                "has_my_row_id": bool(row["has_my_row_id_value"]),
            }
        )
    return normalized_rows


def fetch_tables_without_primary_key():
    return _fetch_primary_key_status_rows(only_missing_primary_key=True)


def fetch_table_primary_key_status(database_name, table_name):
    rows = _fetch_primary_key_status_rows(
        database_name=database_name,
        table_name=table_name,
        only_missing_primary_key=False,
    )
    if not rows:
        return None
    return rows[0]


def fix_table_without_primary_key(database_name, table_name):
    normalized_database = str(database_name or "").strip()
    normalized_table = str(table_name or "").strip()
    if not normalized_database or not normalized_table:
        raise ValueError("Choose both a database and table before applying the primary key fix.")
    if is_system_schema_name(normalized_database):
        raise ValueError("System schemas cannot be changed here.")

    primary_key_status = fetch_table_primary_key_status(normalized_database, normalized_table)
    if primary_key_status is None:
        raise ValueError(f"Table `{normalized_database}.{normalized_table}` was not found.")
    if primary_key_status["has_primary_key"]:
        return {
            "status": "already_has_primary_key",
            "strategy": "none",
            "message": f"Table `{normalized_database}.{normalized_table}` already has a primary key.",
        }

    safe_database = quote_identifier(normalized_database)
    safe_table = quote_identifier(normalized_table)
    auto_increment_column_name = primary_key_status["auto_increment_column_name"]
    if auto_increment_column_name:
        execute_statement(
            f"ALTER TABLE {safe_database}.{safe_table} "
            f"ADD PRIMARY KEY ({quote_identifier(auto_increment_column_name)})"
        )
        return {
            "status": "fixed",
            "strategy": "use_auto_increment",
            "message": (
                f"Added PRIMARY KEY on `{normalized_database}.{normalized_table}` "
                f"using existing AUTO_INCREMENT column `{auto_increment_column_name}`."
            ),
        }

    row_id_column = quote_identifier("my_row_id")
    if primary_key_status["has_my_row_id"]:
        raise ValueError(
            f"Table `{normalized_database}.{normalized_table}` already contains `my_row_id`, "
            "so the automatic invisible-column fix cannot be applied."
        )

    execute_statement(
        f"ALTER TABLE {safe_database}.{safe_table} "
        f"ADD COLUMN {row_id_column} BIGINT UNSIGNED NOT NULL AUTO_INCREMENT INVISIBLE, "
        f"ADD PRIMARY KEY ({row_id_column})"
    )
    return {
        "status": "fixed",
        "strategy": "add_invisible_my_row_id",
        "message": (
            f"Added invisible AUTO_INCREMENT column `my_row_id` and PRIMARY KEY on "
            f"`{normalized_database}.{normalized_table}`."
        ),
    }


def fetch_full_table_report(schema_name, table_name, *, order_by_candidates=None, limit=None):
    column_names = fetch_table_column_names(schema_name, table_name)
    if not column_names:
        raise ValueError(f"No columns found for {schema_name}.{table_name}.")

    column_lookup = {column_name.lower(): column_name for column_name in column_names}
    selected_columns = [
        f"{quote_identifier(column_name)} AS {quote_identifier(column_name)}"
        for column_name in column_names
    ]
    sql = (
        f"SELECT {', '.join(selected_columns)} "
        f"FROM {quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    )

    order_clauses = []
    for candidate in order_by_candidates or []:
        actual_name = column_lookup.get(str(candidate or "").strip().lower())
        if actual_name:
            order_clauses.append(quote_identifier(actual_name))
    if order_clauses:
        sql += " ORDER BY " + ", ".join(order_clauses)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return run_report_query(sql)

def fetch_table_columns(database_name, table_name):
    if not database_name or not table_name:
        return []
    rows = execute_query(
        """
        SELECT
          column_name AS column_name_value,
          column_type AS column_type_value,
          is_nullable AS is_nullable_value,
          column_key AS column_key_value,
          extra AS extra_value,
          column_comment AS column_comment_value
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        [database_name, table_name],
    )
    return [
        {
            "column_name": row["column_name_value"],
            "column_type": row["column_type_value"],
            "is_nullable": row["is_nullable_value"],
            "column_key": row["column_key_value"],
            "extra": row["extra_value"],
            "column_comment": row["column_comment_value"] or "",
        }
        for row in rows
    ]


def fetch_table_indexes(database_name, table_name):
    if not database_name or not table_name:
        return []
    rows = execute_query(
        """
        SELECT
          index_name AS index_name_value,
          non_unique AS non_unique_value,
          index_type AS index_type_value,
          seq_in_index AS seq_in_index_value,
          column_name AS column_name_value,
          sub_part AS sub_part_value,
          cardinality AS cardinality_value,
          index_comment AS index_comment_value,
          is_visible AS is_visible_value
        FROM information_schema.statistics
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY index_name, seq_in_index
        """,
        [database_name, table_name],
    )
    index_lookup = {}
    ordered_indexes = []
    for row in rows:
        index_name = row["index_name_value"]
        if index_name not in index_lookup:
            index_lookup[index_name] = {
                "index_name": index_name,
                "is_unique": row["non_unique_value"] == 0,
                "index_type": row["index_type_value"] or "-",
                "is_visible": row["is_visible_value"] or "-",
                "cardinality": row["cardinality_value"] if row["cardinality_value"] is not None else "-",
                "index_comment": row["index_comment_value"] or "-",
                "columns": [],
            }
            ordered_indexes.append(index_lookup[index_name])
        column_name = row["column_name_value"] or "-"
        if row["sub_part_value"] is not None:
            column_name = f"{column_name}({row['sub_part_value']})"
        index_lookup[index_name]["columns"].append(column_name)
    return ordered_indexes


def fetch_table_partitions(database_name, table_name):
    if not database_name or not table_name:
        return {
            "is_partitioned": False,
            "partition_method": "",
            "partition_expression": "",
            "subpartition_method": "",
            "subpartition_expression": "",
            "partition_count": 0,
            "rows": [],
        }
    rows = execute_query(
        """
        SELECT
          partition_name AS partition_name_value,
          subpartition_name AS subpartition_name_value,
          partition_method AS partition_method_value,
          partition_expression AS partition_expression_value,
          subpartition_method AS subpartition_method_value,
          subpartition_expression AS subpartition_expression_value,
          partition_description AS partition_description_value,
          partition_ordinal_position AS partition_ordinal_position_value,
          subpartition_ordinal_position AS subpartition_ordinal_position_value,
          table_rows AS table_rows_value,
          data_length AS data_length_value,
          index_length AS index_length_value,
          data_free AS data_free_value
        FROM information_schema.partitions
        WHERE table_schema = %s
          AND table_name = %s
          AND partition_name IS NOT NULL
        ORDER BY partition_ordinal_position, subpartition_ordinal_position
        """,
        [database_name, table_name],
    )
    if not rows:
        return {
            "is_partitioned": False,
            "partition_method": "",
            "partition_expression": "",
            "subpartition_method": "",
            "subpartition_expression": "",
            "partition_count": 0,
            "rows": [],
        }

    first_row = rows[0]
    partitions = []
    partition_names = set()
    for row in rows:
        partition_name = row["partition_name_value"] or "-"
        partition_names.add(partition_name)
        partitions.append(
            {
                "partition_name": partition_name,
                "subpartition_name": row["subpartition_name_value"] or "-",
                "partition_description": row["partition_description_value"] or "-",
                "table_rows": row["table_rows_value"] if row["table_rows_value"] is not None else "-",
                "data_length": row["data_length_value"] if row["data_length_value"] is not None else "-",
                "index_length": row["index_length_value"] if row["index_length_value"] is not None else "-",
                "data_free": row["data_free_value"] if row["data_free_value"] is not None else "-",
            }
        )

    return {
        "is_partitioned": True,
        "partition_method": first_row["partition_method_value"] or "-",
        "partition_expression": first_row["partition_expression_value"] or "-",
        "subpartition_method": first_row["subpartition_method_value"] or "-",
        "subpartition_expression": first_row["subpartition_expression_value"] or "-",
        "partition_count": len(partition_names),
        "rows": partitions,
    }


def _normalize_mysql_base_type(column_type):
    normalized = str(column_type or "").strip().lower()
    if not normalized:
        return ""
    return normalized.split("(", 1)[0].split()[0]


def _build_table_preview_select_list(column_definitions):
    select_clauses = []
    masked_columns = []
    for column in column_definitions or []:
        column_name = column["column_name"]
        column_type = column.get("column_type", "")
        safe_column_name = quote_identifier(column_name)
        base_type = _normalize_mysql_base_type(column_type)
        if base_type in DB_ADMIN_PREVIEW_MASKED_BASE_TYPES:
            placeholder = f"[{base_type.upper()}]"
            select_clauses.append(f"CAST('{placeholder}' AS CHAR(32)) AS {safe_column_name}")
            masked_columns.append({"column_name": column_name, "column_type": column_type})
            continue
        select_clauses.append(safe_column_name)
    return select_clauses, masked_columns


def fetch_table_preview(database_name, table_name, page=1, page_size=25):
    if not database_name or not table_name:
        return {
            "columns": [],
            "rows": [],
            "page": 1,
            "page_size": page_size,
            "total_rows": 0,
            "has_previous": False,
            "has_next": False,
            "masked_columns": [],
        }
    safe_database = quote_identifier(database_name)
    safe_table = quote_identifier(table_name)
    page = normalize_page_number(page)
    offset = (page - 1) * page_size
    total_rows = fetch_scalar(f"SELECT COUNT(*) FROM {safe_database}.{safe_table}", default=0)
    column_definitions = fetch_table_columns(database_name, table_name)
    select_clauses, masked_columns = _build_table_preview_select_list(column_definitions)
    with mysql_connection(database_override=database_name) as connection:
        with connection.cursor() as cursor:
            select_list_sql = ", ".join(select_clauses) if select_clauses else "*"
            cursor.execute(
                f"SELECT {select_list_sql} FROM {safe_database}.{safe_table} LIMIT %s OFFSET %s",
                [page_size, offset],
            )
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
    return {
        "columns": columns,
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows or 0,
        "has_previous": page > 1,
        "has_next": offset + len(rows) < (total_rows or 0),
        "masked_columns": masked_columns,
    }


def fetch_create_table_statement(database_name, table_name):
    if not database_name or not table_name:
        return ""
    safe_table = quote_identifier(table_name)
    with mysql_connection(database_override=database_name) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW CREATE TABLE {safe_table}")
            row = cursor.fetchone() or {}
    return row.get("Create Table", "")


def empty_table_preview(page_size=25):
    return {
        "columns": [],
        "rows": [],
        "page": 1,
        "page_size": page_size,
        "total_rows": 0,
        "has_previous": False,
        "has_next": False,
        "masked_columns": [],
    }
