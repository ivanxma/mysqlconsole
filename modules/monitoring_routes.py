from flask import jsonify, request, url_for

from modules.monitoring_pages import (
    build_monitoring_charts_data,
    build_monitoring_charts_page_context,
    build_monitoring_dashboard_page_context,
    build_monitoring_locks_page_context,
    build_monitoring_report_download,
    build_monitoring_report_page,
)


def register_monitoring_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]
    build_csv_response = deps["build_csv_response"]
    normalize_checkbox = deps["normalize_checkbox"]
    chart_tab_options = deps["monitoring_chart_tab_options"]

    @app.route("/monitoring/dashboard")
    @login_required
    def monitoring_dashboard_page():
        return render_dashboard(
            "monitoring_dashboard.html",
            page_title="Monitoring Dashboard",
            **build_monitoring_dashboard_page_context(
                build_monitoring_dashboard_context=deps["build_monitoring_dashboard_context"],
            ),
        )

    @app.route("/monitoring/charts")
    @login_required
    def monitoring_charts_page():
        monitoring_chart_tab = str(request.args.get("chart_tab", "general")).strip().lower()
        allowed_chart_tabs = {option[0] for option in chart_tab_options}
        if monitoring_chart_tab not in allowed_chart_tabs:
            monitoring_chart_tab = "general"
        return render_dashboard(
            "monitoring_charts.html",
            page_title="Monitoring Charts",
            monitoring_chart_tab=monitoring_chart_tab,
            monitoring_chart_tabs=[{"key": key, "label": label} for key, label in chart_tab_options],
            **build_monitoring_charts_page_context(
                build_monitoring_chart_snapshot=deps["build_monitoring_chart_snapshot"],
                charts_data_url=url_for("monitoring_charts_data"),
            ),
        )

    @app.route("/monitoring/charts/data")
    @login_required
    def monitoring_charts_data():
        return jsonify(build_monitoring_charts_data(build_monitoring_chart_snapshot=deps["build_monitoring_chart_snapshot"]))

    @app.route("/monitoring/locks")
    @login_required
    def monitoring_locks_page():
        return render_dashboard(
            "monitoring_locks.html",
            page_title="Locks",
            **build_monitoring_locks_page_context(
                build_monitoring_locks_context=deps["build_monitoring_locks_context"],
                filters={
                    "row_lock_schema": request.args.get("row_lock_schema", ""),
                    "row_lock_table": request.args.get("row_lock_table", ""),
                    "row_blocking_connection_id": request.args.get("row_blocking_connection_id", ""),
                    "row_waiting_connection_id": request.args.get("row_waiting_connection_id", ""),
                    "mdl_schema": request.args.get("mdl_schema", ""),
                    "mdl_name": request.args.get("mdl_name", ""),
                    "mdl_owner_connection_id": request.args.get("mdl_owner_connection_id", ""),
                    "lock_focus": request.args.get("lock_focus", "row"),
                },
            ),
        )

    @app.route("/monitoring/performance-query")
    @login_required
    def monitoring_performance_page():
        return render_dashboard(
            "monitoring_report.html",
            **build_monitoring_report_page(
                deps["fetch_monitoring_performance_queries"],
                page_title="Performance Query",
                report_title="HeatWave Performance Query",
                report_description="Direct monitoring view for HeatWave query activity from performance_schema.",
                download_endpoint="monitoring_performance_download",
            ),
        )

    @app.route("/monitoring/performance-query/download")
    @login_required
    def monitoring_performance_download():
        export_payload = build_monitoring_report_download(
            deps["fetch_monitoring_performance_queries"],
            "monitoring-performance-query.csv",
        )
        return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])

    @app.route("/monitoring/ml-query")
    @login_required
    def monitoring_ml_page():
        current_ml_connection_only = normalize_checkbox(request.args.get("current_ml_connection_only", ""))
        return render_dashboard(
            "monitoring_report.html",
            **build_monitoring_report_page(
                deps["fetch_monitoring_ml_queries"],
                page_title="ML Query",
                report_title="HeatWave ML Query",
                report_description="Direct monitoring view for HeatWave ML jobs from performance_schema.",
                download_endpoint="monitoring_ml_download",
                fetch_kwargs={"current_ml_connection_only": current_ml_connection_only},
                extra_context={"current_ml_connection_only": current_ml_connection_only},
            ),
        )

    @app.route("/monitoring/ml-query/download")
    @login_required
    def monitoring_ml_download():
        current_ml_connection_only = normalize_checkbox(request.args.get("current_ml_connection_only", ""))
        export_payload = build_monitoring_report_download(
            deps["fetch_monitoring_ml_queries"],
            "monitoring-ml-query.csv",
            fetch_kwargs={"current_ml_connection_only": current_ml_connection_only},
        )
        return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])

    @app.route("/monitoring/table-load-recovery")
    @login_required
    def monitoring_load_recovery_page():
        return render_dashboard(
            "monitoring_report.html",
            **build_monitoring_report_page(
                deps["fetch_monitoring_load_recovery"],
                page_title="Table Load Recovery",
                report_title="HeatWave Table Load Recovery",
                report_description="Direct monitoring view for HeatWave table load and recovery state.",
                download_endpoint="monitoring_load_recovery_download",
            ),
        )

    @app.route("/monitoring/table-load-recovery/download")
    @login_required
    def monitoring_load_recovery_download():
        export_payload = build_monitoring_report_download(
            deps["fetch_monitoring_load_recovery"],
            "monitoring-table-load-recovery.csv",
        )
        return build_csv_response(export_payload["filename"], export_payload["columns"], export_payload["rows"])
