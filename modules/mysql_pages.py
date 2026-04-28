import pymysql


def build_mysql_dashboard_context(*, fetch_server_overview, fetch_database_inventory):
    overview = fetch_server_overview()
    inventory = fetch_database_inventory()
    return {
        "server_overview": overview,
        "database_inventory": inventory,
        "non_system_databases": [row for row in inventory if not row["is_system"]],
    }


def handle_db_admin_action(action, database_name, *, quote_identifier, execute_statement, system_schemas):
    normalized_action = str(action or "").strip()
    normalized_name = str(database_name or "").strip()

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
    fetch_database_inventory,
    fetch_tables_for_database,
    empty_table_preview,
    fetch_table_preview,
    fetch_create_table_statement,
    fetch_table_columns,
    fetch_table_indexes,
    fetch_table_partitions,
):
    inventory = fetch_database_inventory()
    available_database_names = {row["database_name"] for row in inventory}
    normalized_database = str(selected_database or "").strip()
    normalized_table = str(selected_table or "").strip()

    if normalized_database and normalized_database not in available_database_names:
        return {
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {},
            "flash_category": "error",
            "flash_message": f"Database `{normalized_database}` was not found.",
        }

    available_tables = fetch_tables_for_database(normalized_database) if normalized_database else []
    available_table_names = {row["table_name"] for row in available_tables}
    if normalized_table and normalized_table not in available_table_names:
        return {
            "redirect_endpoint": "db_admin_page",
            "redirect_values": {"database": normalized_database},
            "flash_category": "error",
            "flash_message": f"Table `{normalized_database}.{normalized_table}` was not found.",
        }

    preview = empty_table_preview()
    ddl_statement = ""
    columns = []
    indexes = []
    partitions = _empty_partition_state()

    if normalized_table:
        try:
            preview = fetch_table_preview(normalized_database, normalized_table, page=preview_page)
            ddl_statement = fetch_create_table_statement(normalized_database, normalized_table)
            columns = fetch_table_columns(normalized_database, normalized_table)
            indexes = fetch_table_indexes(normalized_database, normalized_table)
            partitions = fetch_table_partitions(normalized_database, normalized_table)
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
        "indexes": indexes,
        "partitions": partitions,
    }


def build_db_admin_export(selected_database, *, fetch_tables_for_database):
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
