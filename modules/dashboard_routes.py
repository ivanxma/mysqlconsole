from datetime import datetime, timezone

from flask import Response, render_template, request, session

from modules.mysql_pages import build_mysql_dashboard_context


def register_dashboard_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]

    def build_mysql_dashboard_snapshot_context(
        selected_error_log_priorities,
        selected_error_log_period,
        selected_error_log_code,
        selected_error_log_message_like,
        *,
        sections=None,
    ):
        return build_mysql_dashboard_context(
            fetch_server_overview=lambda: deps["fetch_server_overview"](
                recent_error_log_priorities=selected_error_log_priorities,
                recent_error_log_period=selected_error_log_period["value"],
                recent_error_log_code=selected_error_log_code,
                recent_error_log_message_like=selected_error_log_message_like,
                sections=sections,
            ),
            fetch_database_inventory=deps["fetch_database_inventory"],
            fetch_dashboard_heatwave_summary=deps["fetch_dashboard_heatwave_summary"],
            include_inventory=sections is None or "server-database" in sections or "heatwave" in sections,
            include_heatwave=sections is None or "heatwave" in sections,
        )

    @app.route("/mysql/dashboard")
    @login_required
    def mysql_dashboard_page():
        dashboard_tab = str(request.args.get("dashboard_tab", "server-database")).strip().lower()
        if dashboard_tab == "error-log":
            dashboard_tab = "logs"
        if dashboard_tab not in {"server-database", "logs", "security", "heatwave", "replication"}:
            dashboard_tab = "server-database"
        selected_error_log_priorities = deps["normalize_error_log_priorities"](request.args.getlist("error_prio"))
        selected_error_log_period = deps["normalize_error_log_period"](request.args.get("error_period"))
        selected_error_log_code = deps["normalize_error_log_code"](request.args.get("error_code"))
        selected_error_log_message_like = deps["normalize_error_log_message_like"](request.args.get("message_like"))
        dashboard_context = build_mysql_dashboard_snapshot_context(
            selected_error_log_priorities,
            selected_error_log_period,
            selected_error_log_code,
            selected_error_log_message_like,
            sections={dashboard_tab},
        )
        return render_dashboard(
            "mysql_dashboard.html",
            page_title="Admin Dashboard",
            dashboard_tab=dashboard_tab,
            **dashboard_context,
        )

    @app.route("/mysql/dashboard/download")
    @login_required
    def mysql_dashboard_download_page():
        selected_error_log_priorities = deps["normalize_error_log_priorities"](request.args.getlist("error_prio"))
        selected_error_log_period = deps["normalize_error_log_period"](request.args.get("error_period"))
        selected_error_log_code = deps["normalize_error_log_code"](request.args.get("error_code"))
        selected_error_log_message_like = deps["normalize_error_log_message_like"](request.args.get("message_like"))
        dashboard_context = build_mysql_dashboard_snapshot_context(
            selected_error_log_priorities,
            selected_error_log_period,
            selected_error_log_code,
            selected_error_log_message_like,
            sections={"server-database", "logs", "security", "heatwave", "replication"},
        )
        generated_at = datetime.now(timezone.utc)
        profile = deps["get_session_profile"]()
        global_variables = []
        global_variables_error = ""
        global_status = []
        global_status_error = ""
        try:
            global_variables = deps["fetch_all_show_variable_rows"]("VARIABLES")
        except Exception as error:  # pragma: no cover - depends on server privileges
            global_variables_error = str(error)
        try:
            global_status = deps["fetch_all_show_variable_rows"]("STATUS")
        except Exception as error:  # pragma: no cover - depends on server privileges
            global_status_error = str(error)
        html = render_template(
            "mysql_dashboard_export.html",
            app_title=deps["app_title"],
            generated_at=generated_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            app_version=deps["get_local_app_version"](),
            current_user=deps["get_session_username"](),
            current_profile_name=session.get("profile_name", ""),
            connection_summary=f"{profile['host'] or '-'}:{profile['port']}" if profile else "-",
            setup_status=deps["fetch_setup_status"](),
            global_variables=global_variables,
            global_variables_error=global_variables_error,
            global_status=global_status,
            global_status_error=global_status_error,
            **dashboard_context,
        )
        filename = f"admin-dashboard-{generated_at.strftime('%Y%m%d-%H%M%S')}.html"
        return Response(
            html,
            mimetype="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
