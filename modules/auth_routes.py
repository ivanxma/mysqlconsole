from flask import abort, flash, redirect, render_template, request, url_for


def register_auth_routes(app, deps):
    @app.route("/", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            picked_profile = deps["get_profile_by_name"](request.form.get("profile_picker", ""))
            if picked_profile:
                profile_payload = dict(picked_profile)
            else:
                profile_payload = deps["normalize_profile"](deps["default_profile"])
            profile = deps["normalize_profile"](profile_payload)
            username = str(request.form.get("username", "")).strip()
            if username:
                profile["username"] = username
            password = request.form.get("password", "")
            if not profile.get("socket_enabled") and not profile["host"]:
                flash("Choose a saved profile.", "error")
            elif not username:
                flash("MySQL username is required.", "error")
            else:
                try:
                    deps["clear_login_state"](keep_profile=False)
                    deps["set_session_profile"](profile)
                    deps["set_session_credentials"](username, password)
                    with deps["mysql_connection"](connect_timeout=5):
                        pass
                    deps["set_logged_in"](True)
                    flash("Connected to MySQL.", "success")
                    if deps["local_admin_password_change_required"]():
                        return redirect(url_for("local_admin_password_page"))
                    version_check = deps["refresh_repo_version_check"]()
                    if version_check.get("update_available"):
                        if deps["is_local_admin_profile_session"]():
                            flash(
                                f"DBConsole update available: {version_check.get('local_version')} -> {version_check.get('repo_version')}.",
                                "success",
                            )
                        elif deps["local_admin_profile_needs_bootstrap"]():
                            flash(
                                "DBConsole update available. Use Auto-Update to complete first-time local-admin-profile bootstrap with a temporary localadmin password.",
                                "success",
                            )
                        else:
                            flash(
                                "DBConsole update available. Sign in with local-admin-profile to run Auto-Update.",
                                "success",
                            )
                    elif version_check.get("error"):
                        if deps["is_local_admin_profile_session"]():
                            flash(
                                "Repository version check could not complete. Review the Auto-Update page for details.",
                                "error",
                            )
                        else:
                            flash("Repository version check could not complete.", "error")
                    if deps["should_show_update_page_after_login"](version_check):
                        return redirect(url_for("update_dbconsole_page"))
                    return redirect(url_for("mysql_dashboard_page"))
                except Exception as error:
                    deps["clear_login_state"](keep_profile=True)
                    flash(f"Unable to connect: {error}", "error")

        selected_name = str(request.args.get("profile", "")).strip()
        selected_profile = deps["get_profile_by_name"](selected_name) or deps["get_session_profile"]()
        visible_profiles = deps["public_profiles"](deps["load_profiles"]())
        return render_template(
            "login.html",
            app_title=deps["app_title"],
            page_title="Login",
            logged_in=False,
            profiles=visible_profiles,
            selected_profile=deps["public_profile"](selected_profile),
            selected_profile_name=selected_name or selected_profile.get("name", ""),
        )

    @app.route("/logout", methods=["POST"])
    def logout():
        deps["clear_login_state"](keep_profile=False)
        flash("Logged out.", "success")
        return redirect(url_for("login"))

    @app.route("/admin/local-admin-password", methods=["GET", "POST"])
    @deps["session_login_required"]
    def local_admin_password_page():
        if not deps["is_local_admin_profile_session"]():
            abort(403)
        if request.method == "POST":
            new_password = request.form.get("new_local_admin_password", "")
            confirm_password = request.form.get("confirm_local_admin_password", "")
            if new_password != confirm_password:
                flash("Local admin profile password confirmation does not match.", "error")
            else:
                try:
                    deps["change_local_admin_profile_password"](new_password)
                    deps["clear_local_admin_password_change_required"]()
                    deps["clear_login_state"](keep_profile=False)
                    flash("Password changed. Sign in again.", "success")
                    return redirect(url_for("login"))
                except Exception as error:
                    flash(str(error), "error")
        return render_template(
            "local_admin_password.html",
            app_title=deps["app_title"],
            page_title="Change Local Admin Password",
            logged_in=False,
            local_admin_profile_name=deps["local_admin_profile_name"],
        )
