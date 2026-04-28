def build_heatwave_tables_context(database_name, *, fetch_database_inventory, fetch_tables_for_database):
    schemas = [row for row in fetch_database_inventory() if not row["is_system"]]
    selected_database = str(database_name or "").strip()
    if not selected_database and schemas:
        selected_database = schemas[0]["database_name"]
    tables = fetch_tables_for_database(selected_database) if selected_database else []
    configured = [row for row in tables if row["heatwave_configured"]]
    unconfigured = [row for row in tables if not row["heatwave_configured"]]
    return {
        "database_inventory": schemas,
        "selected_database": selected_database,
        "tables": tables,
        "configured_count": len(configured),
        "unconfigured_count": len(unconfigured),
    }


def build_heatwave_tables_export(report):
    return {
        "filename": f"{report['selected_database'] or 'heatwave'}-tables.csv",
        "columns": ["table_name", "engine", "row_count", "heatwave_configured", "create_options"],
        "rows": [
            {
                "table_name": row["table_name"],
                "engine": row["engine"],
                "row_count": row["row_count"],
                "heatwave_configured": "yes" if row["heatwave_configured"] else "no",
                "create_options": row["create_options"],
            }
            for row in report["tables"]
        ],
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
