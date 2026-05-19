from flask import flash, redirect, request, session, url_for


def register_mysql_import_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]

    @app.route("/mysql/imprt", methods=["GET", "POST"])
    @login_required
    def mysql_import_page():
        database_inventory = [row for row in deps["fetch_database_inventory"]() if not row["is_system"]]
        existing_plan_id = str(session.get("mysql_import_plan_id", "")).strip()
        plan = deps["load_mysql_import_plan"](existing_plan_id) if existing_plan_id else None
        if existing_plan_id and plan is None:
            session.pop("mysql_import_plan_id", None)

        page_state = deps["build_mysql_import_page_state"](
            plan,
            database_inventory,
            fetch_table_exists=deps["fetch_table_exists"],
        )

        if request.method == "POST":
            action = str(request.form.get("import_action", "")).strip()

            if action == "clear":
                if existing_plan_id:
                    deps["delete_mysql_import_plan"](existing_plan_id)
                session.pop("mysql_import_plan_id", None)
                flash("Import draft cleared.", "success")
                return redirect(url_for("mysql_import_page"))

            if action == "preview":
                upload_storage = request.files.get("import_file")
                try:
                    plan = deps["save_mysql_import_plan"](
                        deps["build_mysql_import_plan"](
                            upload_storage,
                            request.form,
                            database_inventory,
                            quote_identifier=deps["quote_identifier"],
                        )
                    )
                    session["mysql_import_plan_id"] = plan["plan_id"]
                    if existing_plan_id and existing_plan_id != plan["plan_id"]:
                        deps["delete_mysql_import_plan"](existing_plan_id)
                    flash(f"Loaded {plan['row_count']} rows from `{plan['source_filename']}`.", "success")
                    return redirect(url_for("mysql_import_page"))
                except Exception as error:
                    page_state = deps["build_mysql_import_page_state"](
                        plan,
                        database_inventory,
                        fetch_table_exists=deps["fetch_table_exists"],
                        payload=request.form,
                    )
                    flash(str(error), "error")

            elif action == "import":
                if plan is None:
                    flash("Upload a CSV or JSON file to preview before importing.", "error")
                    return redirect(url_for("mysql_import_page"))
                try:
                    import_request = deps["validate_mysql_import_request"](
                        request.form,
                        plan,
                        database_inventory,
                        quote_identifier=deps["quote_identifier"],
                        fetch_table_exists=deps["fetch_table_exists"],
                        fetch_database_exists=deps["fetch_database_exists"],
                    )
                    deps["run_mysql_import"](
                        plan,
                        import_request,
                        quote_identifier=deps["quote_identifier"],
                        execute_statement=deps["execute_statement"],
                        mysql_connection=deps["mysql_connection"],
                    )
                    if existing_plan_id:
                        deps["delete_mysql_import_plan"](existing_plan_id)
                    session.pop("mysql_import_plan_id", None)
                    flash(
                        f"Imported {plan.get('row_count', 0)} rows into "
                        f"`{import_request['effective_database_name']}.{import_request['table_name']}`.",
                        "success",
                    )
                    return redirect(
                        url_for(
                            "db_admin_page",
                            database=import_request["effective_database_name"],
                            table=import_request["table_name"],
                        )
                    )
                except Exception as error:
                    page_state = deps["build_mysql_import_page_state"](
                        plan,
                        database_inventory,
                        fetch_table_exists=deps["fetch_table_exists"],
                        payload=request.form,
                    )
                    flash(str(error), "error")
            else:
                flash("Unsupported import action.", "error")

        return render_dashboard(
            "mysql_import.html",
            page_title="Import",
            import_page=page_state,
        )
