from flask import flash, redirect, request, url_for

from modules.heatwave_pages import (
    build_heatwave_management_context,
    build_heatwave_tables_context,
    build_heatwave_tables_export,
    handle_heatwave_management_action,
)


def register_heatwave_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]
    build_csv_response = deps["build_csv_response"]

    @app.route("/heatwave/hw-table")
    @login_required
    def hw_table_page():
        active_tab = "lakehouse" if str(request.args.get("tab", "")).strip().lower() == "lakehouse" else "heatwave"
        report = build_heatwave_tables_context(
            fetch_heatwave_inventory_report=deps["fetch_heatwave_inventory_report"],
            fetch_heatwave_status_variable_report=deps["fetch_heatwave_status_variable_report"],
            fetch_heatwave_nodes_report=deps["fetch_heatwave_nodes_report"],
            fetch_heatwave_defined_secondary_engine_tables=deps["fetch_heatwave_defined_secondary_engine_tables"],
            fetch_lakehouse_engine_tables=deps["fetch_lakehouse_engine_tables"],
        )
        return render_dashboard(
            "hw_table.html",
            page_title="HW Table",
            active_tab=active_tab,
            **report,
        )

    @app.route("/heatwave/hw-table/download")
    @login_required
    def hw_table_download():
        report = build_heatwave_tables_context(
            fetch_heatwave_inventory_report=deps["fetch_heatwave_inventory_report"],
            fetch_heatwave_status_variable_report=deps["fetch_heatwave_status_variable_report"],
            fetch_heatwave_nodes_report=deps["fetch_heatwave_nodes_report"],
            fetch_heatwave_defined_secondary_engine_tables=deps["fetch_heatwave_defined_secondary_engine_tables"],
            fetch_lakehouse_engine_tables=deps["fetch_lakehouse_engine_tables"],
        )
        export_payload = build_heatwave_tables_export(report)
        return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])

    @app.route("/heatwave/management", methods=["GET", "POST"])
    @login_required
    def heatwave_management_page():
        active_tab = "table" if str(request.values.get("tab", "")).strip().lower() == "table" else "db"
        selected_database = str(request.values.get("database", "")).strip()
        selected_table = str(request.values.get("table", "")).strip()
        management_open_dialog = ""
        management_popup_result = None

        if request.method == "POST":
            active_tab = "table" if str(request.form.get("tab", "")).strip().lower() == "table" else "db"
            action = str(request.form.get("management_action", "")).strip()
            selected_database = str(request.form.get("database", "")).strip()
            selected_table = str(request.form.get("table", "")).strip()
            try:
                action_result = handle_heatwave_management_action(
                    action,
                    selected_database,
                    selected_table,
                    excluded_columns=request.form.getlist("excluded_columns"),
                    quote_identifier=deps["quote_identifier"],
                    execute_statement=deps["execute_statement"],
                    execute_multi_result_query=deps["execute_multi_result_query"],
                    fetch_table_columns=deps["fetch_table_columns"],
                    fetch_create_table_statement=deps["fetch_create_table_statement"],
                )
                flash(action_result["flash_message"], action_result["flash_category"])
                selected_database = str(action_result["redirect_values"].get("database", selected_database)).strip()
                selected_table = str(action_result["redirect_values"].get("table", selected_table)).strip()
                active_tab = "table" if str(action_result["redirect_values"].get("tab", active_tab)).strip().lower() == "table" else "db"
                if action_result.get("render_popup"):
                    management_open_dialog = str(action_result.get("open_dialog", "")).strip()
                    management_popup_result = action_result.get("popup_result")
                else:
                    return redirect(url_for("heatwave_management_page", **action_result["redirect_values"]))
            except Exception as error:
                flash(str(error), "error")
                if action == "exclude_columns_update":
                    management_open_dialog = "exclude-columns-dialog"

        page_context = build_heatwave_management_context(
            selected_database,
            selected_table,
            active_tab,
            fetch_database_inventory=deps["fetch_database_inventory"],
            fetch_tables_for_database=deps["fetch_tables_for_database"],
            fetch_table_columns=deps["fetch_table_columns"],
            fetch_create_table_statement=deps["fetch_create_table_statement"],
            fetch_heatwave_inventory_report=deps["fetch_heatwave_inventory_report"],
            fetch_heatwave_defined_secondary_engine_tables=deps["fetch_heatwave_defined_secondary_engine_tables"],
            execute_query=deps["execute_query"],
        )
        return render_dashboard(
            "heatwave_management.html",
            page_title="HW Admin",
            management_open_dialog=management_open_dialog,
            management_popup_result=management_popup_result,
            **page_context,
        )
