from flask import abort, flash, redirect, request, url_for


def register_admin_routes(app, deps):
    login_required = deps["login_required"]
    session_login_required = deps["session_login_required"]
    render_dashboard = deps["render_dashboard"]

    @app.route("/admin/profile", methods=["GET", "POST"])
    @session_login_required
    def profile_page():
        if not deps["is_local_admin_profile_session"]():
            abort(403)
        profiles = deps["load_profiles"]()
        selected_name = str(request.values.get("selected_profile", "")).strip()
        editing_profile = deps["get_profile_by_name"](selected_name) or deps["get_session_profile"]()

        if request.method == "POST":
            action = str(request.form.get("profile_action", "")).strip()
            profile_payload = deps["normalize_profile"](request.form)
            profile_name = profile_payload["name"]
            if action == "change_local_admin_password":
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
            elif action == "save":
                if not profile_name:
                    flash("Profile name is required.", "error")
                elif not profile_payload["socket_enabled"] and not profile_payload["host"]:
                    flash("Profile host is required unless Unix socket is enabled.", "error")
                elif profile_payload["socket_enabled"] and not profile_payload["socket_path"]:
                    flash("Unix socket path is required when Unix socket is enabled.", "error")
                elif profile_payload["socket_enabled"] and profile_payload["ssh_enabled"]:
                    flash("Unix socket profiles cannot use SSH tunneling.", "error")
                else:
                    existing_profile = deps["get_profile_by_name"](profile_name)
                    if existing_profile and not request.files.get("ssh_key_file"):
                        profile_payload["ssh_key_path"] = existing_profile.get("ssh_key_path", "")
                    try:
                        uploaded_ssh_key_path = deps["save_uploaded_profile_ssh_key"](
                            profile_name,
                            request.files.get("ssh_key_file"),
                        )
                    except Exception as error:
                        flash(str(error), "error")
                        editing_profile = profile_payload
                        profiles = deps["load_profiles"]()
                        return render_dashboard(
                            "profile.html",
                            page_title="Profile",
                            profiles=profiles,
                            selected_profile_name=selected_name,
                            editing_profile=deps["public_profile"](editing_profile),
                            local_admin_profile_name=deps["local_admin_profile_name"],
                            can_change_local_admin_password=deps["is_local_admin_profile_session"](),
                        )
                    if uploaded_ssh_key_path:
                        profile_payload["ssh_key_path"] = uploaded_ssh_key_path
                    if profile_payload["ssh_enabled"] and (
                        not profile_payload["ssh_host"]
                        or not profile_payload["ssh_user"]
                        or not profile_payload["ssh_key_path"]
                    ):
                        flash("SSH profiles require jump host, SSH user, and an uploaded private key.", "error")
                        editing_profile = profile_payload
                        profiles = deps["load_profiles"]()
                        return render_dashboard(
                            "profile.html",
                            page_title="Profile",
                            profiles=profiles,
                            selected_profile_name=selected_name,
                            editing_profile=deps["public_profile"](editing_profile),
                            local_admin_profile_name=deps["local_admin_profile_name"],
                            can_change_local_admin_password=deps["is_local_admin_profile_session"](),
                        )
                    remaining = [row for row in profiles if row["name"].lower() != profile_name.lower()]
                    remaining.append(profile_payload)
                    deps["save_profiles"](remaining)
                    if deps["get_session_profile"]()["name"].lower() == profile_name.lower():
                        deps["set_session_profile"](profile_payload)
                    flash(f"Profile `{profile_name}` saved.", "success")
                    return redirect(url_for("profile_page", selected_profile=profile_name))
            elif action == "delete":
                if not profile_name:
                    flash("Choose a profile to delete.", "error")
                else:
                    remaining = [row for row in profiles if row["name"].lower() != profile_name.lower()]
                    if len(remaining) == len(profiles):
                        flash("Profile not found.", "error")
                    else:
                        deps["save_profiles"](remaining)
                        if deps["get_session_profile"]()["name"].lower() == profile_name.lower():
                            deps["set_session_profile"](deps["normalize_profile"](deps["default_profile"]))
                        flash(f"Profile `{profile_name}` deleted.", "success")
                        return redirect(url_for("profile_page"))
            editing_profile = profile_payload
            profiles = deps["load_profiles"]()

        return render_dashboard(
            "profile.html",
            page_title="Profile",
            profiles=profiles,
            selected_profile_name=selected_name,
            editing_profile=deps["public_profile"](editing_profile),
            local_admin_profile_name=deps["local_admin_profile_name"],
            can_change_local_admin_password=deps["is_local_admin_profile_session"](),
        )

    @app.route("/admin/setup-object-storage", methods=["GET", "POST"])
    @login_required
    def setup_object_storage_page():
        config = deps["load_object_storage_config"]()
        if request.method == "POST":
            config = deps["normalize_object_storage"](request.form)
            deps["save_object_storage_config"](config)
            flash("Object Storage configuration saved.", "success")
            return redirect(url_for("setup_object_storage_page"))
        return render_dashboard(
            "setup_object_storage.html",
            page_title="Setup Object Storage",
            object_storage_config=config,
        )

    @app.route("/admin/status-variables")
    @login_required
    def admin_status_variables_page():
        active_tab = "variables" if str(request.args.get("tab", "")).strip().lower() == "variables" else "status"
        status_variable_page = deps["build_empty_status_variable_page"](active_tab)
        error_message = ""
        try:
            status_variable_page = deps["fetch_grouped_status_variables"](
                active_tab,
                execute_query=deps["execute_query"],
            )
        except Exception as error:
            error_message = str(error)
        return render_dashboard(
            "status_variables.html",
            page_title="Status and Variables",
            active_tab=active_tab,
            status_variable_page=status_variable_page,
            error_message=error_message,
        )
