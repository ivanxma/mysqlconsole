from flask import abort, flash, redirect, request, url_for

from modules.mysqlsh_option_profiles import (
    delete_option_profile,
    get_option_profile,
    list_option_profiles,
    save_option_profile,
)
from modules.mysqlsh_par_store import create_par, list_pars, remove_par
from modules.mysqlsh_jobs import par_is_in_use
from modules.mysqlsh_option_form import (
    ANALYZE_OPTIONS,
    COMPATIBILITY_OPTIONS,
    COMPRESSION_OPTIONS,
    DEFER_INDEX_OPTIONS,
    DIALECT_OPTIONS,
    FILTER_TYPES,
    GRANT_ERROR_OPTIONS,
    GTID_OPTIONS,
    build_options,
    defaults,
    fetch_filter_catalog,
    form_state,
)


def register_mysqlsh_configuration_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]
    option_store = deps["option_profile_store"]
    par_store = deps["par_store"]

    @app.route("/mysql-shell/option-profiles", methods=["GET", "POST"])
    @login_required
    def mysqlsh_option_profiles_page():
        if not deps["can_access_mysqlsh"]():
            abort(403)
        kind = str(request.values.get("kind") or "dump").strip().lower()
        if kind not in {"dump", "load"}:
            kind = "dump"
        selected_name = str(request.values.get("profile_name") or "").strip()
        selected = get_option_profile(option_store, kind, selected_name) if selected_name else None
        form = {
            "kind": kind,
            "name": str(request.values.get("edit_name") or selected_name or ""),
        }
        options = dict(selected["options"] if selected else defaults(kind))
        state = form_state(kind, options)
        if selected:
            form["name"] = selected["name"]

        if request.method == "POST":
            action = str(request.form.get("profile_action") or "save").strip()
            try:
                if action == "save":
                    options = build_options(kind, request.form)
                    saved = save_option_profile(
                        option_store,
                        kind,
                        form["name"],
                        options,
                    )
                    flash(f"{kind.title()} option profile `{saved['name']}` saved.", "success")
                    return redirect(url_for("mysqlsh_option_profiles_page", kind=kind, profile_name=saved["name"]))
                if action == "delete":
                    if not delete_option_profile(option_store, kind, selected_name):
                        raise ValueError("Choose an existing option profile to delete.")
                    flash(f"{kind.title()} option profile `{selected_name}` deleted.", "success")
                    return redirect(url_for("mysqlsh_option_profiles_page", kind=kind))
                raise ValueError("Unsupported option profile action.")
            except Exception as error:
                flash(str(error), "error")
                try:
                    state = form_state(kind, build_options(kind, request.form))
                except Exception:
                    state = form_state(kind, options)

        catalog = fetch_filter_catalog(deps["mysql_connection"])

        return render_dashboard(
            "mysqlsh_option_profiles.html",
            page_title="MySQL Shell Option Profiles",
            form=form,
            profiles=list_option_profiles(option_store, kind),
            selected_profile_name=selected_name,
            state=state,
            filter_catalog=catalog,
            filter_types=FILTER_TYPES,
            compression_options=COMPRESSION_OPTIONS,
            dialect_options=DIALECT_OPTIONS,
            compatibility_options=COMPATIBILITY_OPTIONS,
            analyze_options=ANALYZE_OPTIONS,
            defer_index_options=DEFER_INDEX_OPTIONS,
            grant_error_options=GRANT_ERROR_OPTIONS,
            gtid_options=GTID_OPTIONS,
        )

    @app.route("/mysql-shell/pars", methods=["GET", "POST"])
    @login_required
    def mysqlsh_pars_page():
        if not deps["can_access_mysqlsh"]():
            abort(403)
        store = deps["load_object_storage_config"]()
        selected_name = str(request.values.get("object_storage_profile") or store.get("active_profile_name") or "")
        target = deps["select_object_storage_config"](selected_name)
        target_error = ""
        try:
            target = deps["validate_object_storage_target"](target)
        except Exception as error:
            target_error = str(error)
        form = {
            "object_storage_profile": selected_name,
            "name": str(request.values.get("name") or ""),
            "prefix": str(request.values.get("prefix") or target.get("bucket_prefix") or ""),
            "access_type": str(request.values.get("access_type") or "AnyObjectReadWrite"),
            "delete_after_use": request.method == "GET" or bool(request.values.get("delete_after_use")),
            "expiry_hours": str(request.values.get("expiry_hours") or "24"),
        }
        if request.method == "POST":
            action = str(request.form.get("par_action") or "create").strip()
            try:
                if target_error:
                    raise ValueError(target_error)
                if action == "create":
                    entry = create_par(
                        par_store,
                        target,
                        name=form["name"],
                        prefix=form["prefix"],
                        access_type=form["access_type"],
                        delete_after_use=form["delete_after_use"],
                        expiry_hours=form["expiry_hours"],
                    )
                    flash(f"PAR `{entry['name']}` created with Instance Principal authentication.", "success")
                    return redirect(url_for("mysqlsh_pars_page", object_storage_profile=selected_name))
                if action == "delete":
                    entry_id = request.form.get("par_entry_id")
                    if par_is_in_use(entry_id):
                        raise ValueError("This PAR is assigned to an active MySQL Shell job and cannot be revoked.")
                    entry = remove_par(par_store, entry_id, revoke=True)
                    flash(f"PAR `{entry['name']}` revoked and removed.", "success")
                    return redirect(url_for("mysqlsh_pars_page", object_storage_profile=selected_name))
                raise ValueError("Unsupported PAR action.")
            except Exception as error:
                flash(str(error), "error")

        return render_dashboard(
            "mysqlsh_pars.html",
            page_title="MySQL Shell PAR Setup",
            form=form,
            object_storage_profiles=store.get("profiles", []),
            selected_target=target,
            par_entries=list_pars(par_store, target) if not target_error else [],
            storage_configured=not target_error,
            storage_configuration_error=target_error,
        )
