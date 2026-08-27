from flask import abort, flash, jsonify, redirect, request, session, url_for


def register_update_routes(app, deps):
    session_login_required = deps["session_login_required"]
    render_dashboard = deps["render_dashboard"]

    @app.route("/admin/update-dbconsole", methods=["GET", "POST"])
    @session_login_required
    def update_dbconsole_page():
        if not deps["is_local_admin_profile_session"]():
            abort(403)
        if request.method == "POST":
            action = str(request.form.get("update_action", "")).strip().lower()
            if action == "start":
                try:
                    deps["start_dbconsole_update_job"]()
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

        raw_update_status = deps["get_dbconsole_update_status"]()
        return render_dashboard(
            "update_dbconsole.html",
            page_title="Auto-Update",
            update_status=deps["public_dbconsole_update_status"](raw_update_status),
            update_poll_token=str(raw_update_status.get("poll_token") or ""),
            local_admin_profile_name=deps["local_admin_profile_name"],
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
        poll_token = request.headers.get("X-DBConsole-Update-Poll-Token", "")
        if not deps["update_poll_token_matches"](poll_token):
            if not deps["has_active_login_state"]():
                abort(401)
            if not deps["is_local_admin_profile_session"]():
                abort(403)
        update_status = deps["get_dbconsole_update_status"]()
        return jsonify(deps["public_dbconsole_update_status"](update_status))
