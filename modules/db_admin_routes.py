import re

from flask import Response, flash, redirect, request, session, url_for


def register_db_admin_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]
    default_tab = deps["db_admin_default_tab"]

    @app.route("/mysql/db-admin", methods=["GET", "POST"])
    @login_required
    def db_admin_page():
        db_admin_tab = deps["normalize_db_admin_tab"](request.values.get("db_admin_tab", default_tab))
        selected_database = str(request.values.get("database", "")).strip()
        selected_table = str(request.values.get("table", "")).strip()
        focus_event_database = str(request.args.get("focus_event_database", "")).strip()
        focus_event_name = str(request.args.get("focus_event_name", "")).strip()
        preview_page = deps["normalize_page_number"](request.args.get("page", "1"))
        table_info_tab = deps["normalize_db_admin_table_info_tab"](
            request.values.get("table_info_tab", deps["db_admin_table_info_default_tab"])
        )
        if str(request.args.get("dialog", "")).strip() == "modify-columns":
            table_info_tab = "modify-columns"
        db_admin_edit_payload = None
        db_admin_event_form_payload = None
        db_admin_charset_collation_payload = None
        charset_collation_preview = None
        event_action_output = session.pop(deps["db_admin_event_output_session_key"], None)

        if request.method == "POST":
            action = str(request.form.get("db_action", "")).strip()
            db_admin_tab = deps["normalize_db_admin_tab"](request.form.get("db_admin_tab", db_admin_tab))
            selected_database = str(request.form.get("database_name", selected_database)).strip()
            selected_table = str(request.form.get("table_name", selected_table)).strip()
            if action == "create_event":
                selected_database = str(request.form.get("event_database_name", selected_database)).strip()
            if action == "download_charset_collation_script":
                try:
                    plan = deps["preview_charset_collation"](selected_database, request.form)
                    script_text = deps["build_charset_collation_script"](plan)
                    filename_database = re.sub(r"[^A-Za-z0-9_.-]+", "_", plan["database_name"]).strip("._") or "database"
                    response = Response(script_text, mimetype="application/sql")
                    response.headers["Content-Disposition"] = (
                        f"attachment; filename={filename_database}-charset-collation-plan.sql"
                    )
                    return response
                except Exception as error:
                    flash(str(error), "error")
                    return redirect(
                        url_for(
                            "db_admin_page",
                            db_admin_tab="charset-collation",
                            database=selected_database,
                        )
                    )
            try:
                action_result = deps["handle_db_admin_action"](
                    action,
                    request.form.get("database_name", ""),
                    table_name=request.form.get("table_name", ""),
                    payload=request.form,
                    quote_identifier=deps["quote_identifier"],
                    execute_statement=deps["execute_statement"],
                    system_schemas=deps["system_schemas"],
                    fetch_create_table_statement=deps["fetch_create_table_statement"],
                    fetch_table_columns=deps["fetch_table_columns"],
                    fetch_tables_for_database=deps["fetch_tables_for_database"],
                    fetch_missing_primary_key_rows=deps["fetch_tables_without_primary_key"],
                    fix_missing_primary_key_table=deps["fix_table_without_primary_key"],
                    create_db_event=deps["create_db_admin_event"],
                    set_db_events_enabled=deps["set_db_admin_events_enabled"],
                    delete_db_events=deps["delete_db_admin_events"],
                    modify_charset_collation=deps["modify_db_admin_charset_collation"],
                    preview_charset_collation=deps["preview_charset_collation"],
                )
                if action_result.get("charset_collation_preview"):
                    db_admin_charset_collation_payload = request.form
                    charset_collation_preview = action_result["charset_collation_preview"]
                    flash(action_result["flash_message"], action_result["flash_category"])
                else:
                    if action_result.get("event_action_output"):
                        session[deps["db_admin_event_output_session_key"]] = action_result["event_action_output"]
                    flash(action_result["flash_message"], action_result["flash_category"])
                    redirect_values = dict(action_result["redirect_values"])
                    redirect_values.setdefault("db_admin_tab", default_tab)
                    return redirect(url_for(action_result["redirect_endpoint"], **redirect_values))
            except Exception as error:
                flash(str(error), "error")
                if action == "modify_table_columns":
                    table_info_tab = "modify-columns"
                    db_admin_edit_payload = request.form
                elif action == "create_event":
                    db_admin_event_form_payload = request.form
                    event_action_output = {
                        "title": "Create Event",
                        "category": "error",
                        "message": str(error),
                    }
                elif action in {"enable_events", "disable_events", "delete_events"}:
                    event_action_output = {
                        "title": "Delete Event" if action == "delete_events" else "Event Status",
                        "category": "error",
                        "message": str(error),
                    }
                elif action in {"modify_charset_collation", "preview_charset_collation"}:
                    db_admin_charset_collation_payload = request.form

        page_context = deps["build_db_admin_context"](
            selected_database,
            selected_table,
            preview_page,
            db_admin_tab=db_admin_tab,
            table_info_tab=table_info_tab,
            fetch_database_inventory=deps["fetch_database_inventory"],
            fetch_tables_for_database=deps["fetch_tables_for_database"],
            empty_table_preview=deps["empty_table_preview"],
            fetch_table_preview=deps["fetch_table_preview"],
            fetch_create_table_statement=deps["fetch_create_table_statement"],
            fetch_table_columns=deps["fetch_table_columns"],
            fetch_table_indexes=deps["fetch_table_indexes"],
            fetch_table_partitions=deps["fetch_table_partitions"],
            fetch_missing_primary_key_rows=deps["fetch_tables_without_primary_key"],
            column_edit_payload=db_admin_edit_payload,
            fetch_event_rows=deps["fetch_db_admin_event_rows"],
            event_form_payload=db_admin_event_form_payload,
            event_schedule_options=deps["event_schedule_options"],
            focused_event_database=focus_event_database,
            focused_event_name=focus_event_name,
            fetch_charset_collation_report=deps["fetch_db_admin_charset_collation_report"],
            fetch_charset_collation_options=deps["fetch_charset_collation_options"],
            charset_collation_payload=db_admin_charset_collation_payload,
        )
        if page_context.get("redirect_endpoint"):
            flash(page_context["flash_message"], page_context["flash_category"])
            redirect_values = dict(page_context["redirect_values"])
            redirect_values.setdefault("db_admin_tab", default_tab)
            return redirect(url_for(page_context["redirect_endpoint"], **redirect_values))

        return render_dashboard(
            "db_admin.html",
            page_title="DB Admin",
            db_admin_tab=db_admin_tab,
            table_info_tab=table_info_tab,
            event_schedule_options=deps["event_schedule_options"],
            event_action_output=event_action_output,
            charset_collation_preview=charset_collation_preview,
            **page_context,
        )

    @app.route("/mysql/db-admin/download")
    @login_required
    def db_admin_download():
        selected_database = str(request.args.get("database", "")).strip()
        db_admin_tab = deps["normalize_db_admin_tab"](request.args.get("db_admin_tab", default_tab))
        export_payload = deps["build_db_admin_export"](
            selected_database,
            db_admin_tab=db_admin_tab,
            fetch_tables_for_database=deps["fetch_tables_for_database"],
            fetch_missing_primary_key_rows=deps["fetch_tables_without_primary_key"],
        )
        return deps["build_csv_response"](export_payload["filename"], export_payload["columns"], export_payload["rows"])
