from flask import abort, flash, jsonify, redirect, request, session, url_for


def register_update_routes(app, deps):
    session_login_required = deps["session_login_required"]
    render_dashboard = deps["render_dashboard"]

    @app.route("/admin/update-dbconsole", methods=["GET", "POST"])
    @session_login_required
    def update_dbconsole_page():
        bootstrap_required = deps["local_admin_profile_needs_bootstrap"]()
        update_start_allowed = deps["is_local_admin_profile_session"]() or bootstrap_required
        if not update_start_allowed:
            abort(403)
        if request.method == "POST":
            action = str(request.form.get("update_action", "")).strip().lower()
            if action == "start":
                if not update_start_allowed:
                    abort(403)
                try:
                    if bootstrap_required:
                        local_admin_password_reset = deps["normalize_update_local_admin_bootstrap_credentials"](
                            request.form,
                            require_password=True,
                        )
                    elif any(field_name in request.form for field_name in deps["update_local_admin_reset_fields"]):
                        raise ValueError(
                            "Use the local-admin password change page to change an existing localadmin password."
                        )
                    else:
                        local_admin_password_reset = {}
                    deps["start_dbconsole_update_job"](local_admin_password_reset=local_admin_password_reset)
                    flash("Auto-update started.", "success")
                except Exception as error:
                    flash(str(error), "error")
            elif action == "retrieve-version":
                version_check = deps["refresh_repo_version_check"]()
                if version_check.get("error"):
                    flash(f"Repository version check failed: {version_check['error']}", "error")
                elif version_check.get("update_available"):
                    flash(
                        f"Repository version {version_check.get('repo_version')} differs from local version {version_check.get('local_version')}.",
                        "success",
                    )
                else:
                    flash("Repository version matches the local app version.", "success")
            return redirect(url_for("update_dbconsole_page"))

        return render_dashboard(
            "update_dbconsole.html",
            page_title="Auto-Update",
            update_status=deps["public_dbconsole_update_status"](deps["get_dbconsole_update_status"]()),
            update_poll_token=deps["ensure_dbconsole_update_poll_token"](),
            local_admin_profile_name=deps["local_admin_profile_name"],
            local_admin_mysql_user=str(
                (deps["get_session_profile"]().get("username") if deps["is_local_admin_profile_session"]() else "")
                or "localadmin"
            ),
            bootstrap_required=bootstrap_required,
            update_start_allowed=update_start_allowed,
            app_version_info=session.get(deps["version_check_session_key"])
            or {
                "local_version": deps["get_local_app_version"](),
                "repo_version": "-",
                "update_available": False,
                "checked_at": "",
                "error": "",
                "version_url": deps["infer_app_version_url"](),
            },
        )

    @app.route("/admin/update-dbconsole/status")
    def update_dbconsole_status():
        update_status = deps["get_dbconsole_update_status"]()
        if not session.get("logged_in") and not deps["update_status_poll_token_is_valid"](update_status):
            return jsonify({"error": "Log in to continue."}), 401
        return jsonify(deps["public_dbconsole_update_status"](update_status))
