import time
from functools import wraps

from flask import abort, flash, redirect, request, url_for

from modules.mysqlsh_jobs import (
    build_owner_id,
    cancel_job,
    cleanup_job,
    finalize_job_par,
    job_snapshot,
    list_jobs,
    par_has_expired,
    par_is_in_use,
    read_job_log,
    record_cleanup_failure,
    record_par_expired,
    submit_job,
)
from modules.mysqlsh_option_profiles import get_option_profile, list_option_profiles
from modules.mysqlsh_option_form import fetch_lakehouse_table_exclusions, merge_lakehouse_exclusions
from modules.mysqlsh_par_store import get_par, list_pars
from modules.mysqlsh_runner import build_operation_request, get_mysqlsh_status, operation_preview


OPERATIONS = {"dump_instance", "dump_schemas", "load_dump"}
REVIEW_STATE_KEY = "mysqlsh_submission_review"
REVIEW_TTL_SECONDS = 10 * 60


def _option_payload(form, operation):
    try:
        threads = int(form.get("threads", "4"))
    except ValueError as error:
        raise ValueError("Threads must be a whole number.") from error
    if not 1 <= threads <= 64:
        raise ValueError("Threads must be between 1 and 64.")
    options = {"threads": threads}
    if operation != "load_dump" and form.get("consistent"):
        options["consistent"] = True
    return options


def _operation_label(operation):
    return {
        "dump_instance": "Dump Instance",
        "dump_schemas": "Dump Schemas",
        "load_dump": "Load Dump",
    }[operation]


def _target_snapshot(target):
    return {
        "profile_name": str(target.get("profile_name") or ""),
        "region": str(target.get("region") or ""),
        "namespace": str(target.get("namespace") or ""),
        "bucket_name": str(target.get("bucket_name") or ""),
        "bucket_prefix": str(target.get("bucket_prefix") or ""),
    }


def _review_plan(operation, profile_name, schemas, options, target, option_profile_name, par_entry):
    return {
        "operation": operation,
        "mysql_profile": str(profile_name or ""),
        "schemas": list(schemas),
        "options": dict(options),
        "storage_target": _target_snapshot(target),
        "option_profile_name": str(option_profile_name or ""),
        "par_entry_id": str((par_entry or {}).get("id") or ""),
        "par_id": str((par_entry or {}).get("par_id") or ""),
        "storage_prefix": str((par_entry or {}).get("prefix") or ""),
        "delete_after_use": bool((par_entry or {}).get("delete_after_use")),
        "par_expires_at": str((par_entry or {}).get("expires_at") or ""),
    }


def register_mysqlsh_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]

    def mysqlsh_access_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not deps["can_access_mysqlsh"]():
                abort(403)
            return view(*args, **kwargs)

        return wrapped_view

    def owner():
        profile = deps["get_session_profile"]()
        profile_name = str(profile.get("name") or "")
        username_getter = deps.get("get_session_username")
        username = username_getter() if username_getter else deps["get_session_credentials"]().get("username", "")
        return (
            build_owner_id(profile_name, username),
            profile_name,
            deps["get_server_session_id"](),
        )

    @app.route("/mysql-shell/operations", methods=["GET", "POST"])
    @login_required
    @mysqlsh_access_required
    def mysqlsh_operations_page():
        profile = deps["get_session_profile"]()
        store = deps["load_object_storage_config"]()
        selected_name = str(request.values.get("object_storage_profile") or store.get("active_profile_name") or "")
        requested_operation = str(request.values.get("operation") or "dump_schemas")
        operation = requested_operation if requested_operation in OPERATIONS else "dump_schemas"
        option_kind = "load" if operation == "load_dump" else "dump"
        form = {
            "operation": operation,
            "object_storage_profile": selected_name,
            "schemas": str(request.values.get("schemas") or ""),
            "threads": str(request.values.get("threads") or "4"),
            "consistent": bool(request.values.get("consistent")),
            "exclude_lakehouse_tables": bool(request.values.get("exclude_lakehouse_tables")),
            "option_profile_name": str(request.values.get("option_profile_name") or ""),
            "par_entry_id": str(request.values.get("par_entry_id") or ""),
            "confirm_load": bool(request.values.get("confirm_load")),
        }
        preview = ""
        confirmation = None
        owner_id, profile_name, session_id = owner()
        target = {}
        storage_configuration_error = ""
        try:
            target = deps["validate_object_storage_target"](deps["select_object_storage_config"](selected_name))
        except Exception as error:
            storage_configuration_error = str(error)
        option_profiles = list_option_profiles(deps["option_profile_store"], option_kind)
        active_pars = (
            list_pars(deps["par_store"], target, option_kind, active_only=True)
            if not storage_configuration_error
            else []
        )

        if request.method == "GET":
            deps["pop_server_session_state"](REVIEW_STATE_KEY)

        if request.method == "POST":
            try:
                if storage_configuration_error:
                    raise ValueError(storage_configuration_error)
                schemas = [item.strip() for item in form["schemas"].split(",") if item.strip()]
                target = deps["validate_object_storage_target"](target)
                option_profile = None
                if form["option_profile_name"]:
                    option_profile = get_option_profile(
                        deps["option_profile_store"], option_kind, form["option_profile_name"]
                    )
                    if option_profile is None:
                        raise ValueError(f"Choose an existing {option_kind} option profile.")
                options = dict(option_profile["options"]) if option_profile else _option_payload(request.form, operation)
                if operation != "load_dump" and form["exclude_lakehouse_tables"]:
                    options["excludeLakehouseTables"] = True
                applied_lakehouse_exclusions = []
                if operation != "load_dump" and options.get("excludeLakehouseTables"):
                    lakehouse_exclusions = fetch_lakehouse_table_exclusions(
                        deps["mysql_connection"],
                        schemas if operation == "dump_schemas" else None,
                    )
                    options, applied_lakehouse_exclusions = merge_lakehouse_exclusions(options, lakehouse_exclusions)
                else:
                    options, applied_lakehouse_exclusions = merge_lakehouse_exclusions(options, [])
                par_entry = get_par(
                    deps["par_store"],
                    form["par_entry_id"],
                    target=target,
                    purpose=option_kind,
                    active_only=True,
                )
                if par_entry is None:
                    requirement = "read/write" if option_kind == "dump" else "read or read/write"
                    raise ValueError(f"Choose an active {requirement} PAR for {option_kind}.")
                plan = _review_plan(
                    operation,
                    profile_name,
                    schemas,
                    options,
                    target,
                    form["option_profile_name"],
                    par_entry,
                )

                action = str(request.form.get("mysqlsh_action") or "preview")
                if action == "preview":
                    display_url = "https://objectstorage.example/[scoped-par]"
                    operation_request = build_operation_request(
                        operation,
                        storage_url=display_url,
                        schema_names=schemas,
                        options=options,
                    )
                    preview = operation_preview(operation_request)
                    confirmation = {
                        "operation": _operation_label(operation),
                        "mysql_profile": profile_name,
                        "storage_profile": target.get("profile_name", ""),
                        "bucket": target.get("bucket_name", ""),
                        "prefix": par_entry.get("prefix", ""),
                        "schemas": ", ".join(schemas),
                        "option_profile_name": form["option_profile_name"] or "Default options",
                        "par_name": par_entry.get("name", ""),
                        "delete_after_use": par_entry.get("delete_after_use", False),
                        "par_expires_at": par_entry.get("expires_at", ""),
                        "lakehouse_excluded_count": len(applied_lakehouse_exclusions),
                    }
                    deps["set_server_session_state"](
                        REVIEW_STATE_KEY,
                        {"created_at": time.time(), "plan": plan},
                    )
                elif action == "submit":
                    reviewed = deps["pop_server_session_state"](REVIEW_STATE_KEY)
                    if not isinstance(reviewed, dict) or reviewed.get("plan") != plan:
                        raise ValueError("Preview and review the MySQL Shell operation before submitting.")
                    try:
                        review_age = time.time() - float(reviewed.get("created_at"))
                    except (TypeError, ValueError):
                        review_age = REVIEW_TTL_SECONDS + 1
                    if review_age < 0 or review_age > REVIEW_TTL_SECONDS:
                        raise ValueError("The MySQL Shell preview expired. Preview the operation again before submitting.")
                    if operation == "load_dump" and not request.form.get("confirm_load"):
                        raise ValueError("Confirm the Load Dump target before submitting.")
                    access = deps["test_instance_principal_access"](target)
                    if not access.get("ok"):
                        raise ValueError(access.get("message") or "Instance Principal Object Storage access failed.")
                    if par_entry.get("delete_after_use") and par_is_in_use(par_entry["id"]):
                        raise ValueError("This Delete after used PAR is already assigned to an active job.")
                    job_par = {
                        "id": par_entry["par_id"],
                        "prefix": par_entry["prefix"],
                        "expires_at": par_entry["expires_at"],
                        "delete_after_use": par_entry["delete_after_use"],
                        "registry_entry_id": par_entry["id"],
                        "registry_path": str(deps["par_store"]),
                    }
                    operation_request = build_operation_request(
                        operation,
                        storage_url=par_entry["par_url"],
                        schema_names=schemas,
                        options=options,
                    )
                    job = submit_job(
                        profile,
                        deps["get_session_credentials"](),
                        operation_request,
                        owner_session_id=session_id,
                        owner_id=owner_id,
                        storage_target=target,
                        par=job_par,
                        operation_label=_operation_label(operation),
                    )
                    flash(f"MySQL Shell job `{job['job_id']}` submitted.", "success")
                    return redirect(url_for("mysqlsh_job_detail_page", job_id=job["job_id"]))
                else:
                    raise ValueError("Unsupported MySQL Shell action.")
            except Exception as error:
                flash(str(error), "error")

        return render_dashboard(
            "mysqlsh_operations.html",
            page_title="MySQL Shell Dump/Load",
            form=form,
            preview=preview,
            confirmation=confirmation,
            mysqlsh_status=get_mysqlsh_status(),
            object_storage_profiles=store.get("profiles", []),
            active_object_storage_profile=store.get("active_profile_name", ""),
            option_profiles=option_profiles,
            active_pars=active_pars,
            option_kind=option_kind,
            storage_configured=not storage_configuration_error,
            storage_configuration_error=storage_configuration_error,
            can_manage_mysqlsh_configuration=True,
        )

    @app.route("/mysql-shell/jobs")
    @login_required
    @mysqlsh_access_required
    def mysqlsh_jobs_page():
        owner_id, profile_name, _session_id = owner()
        include_all = deps.get("is_local_admin_profile_session", lambda: False)()
        jobs = list_jobs(owner_session_id=owner_id, owner_profile_name=profile_name, include_all=include_all)
        return render_dashboard(
            "mysqlsh_jobs.html",
            page_title="MySQL Shell Jobs",
            jobs=jobs,
            has_active_jobs=any(job.get("status") in {"submitted", "running", "finalizing", "cancel_requested"} for job in jobs),
        )

    @app.route("/mysql-shell/jobs/<job_id>")
    @login_required
    @mysqlsh_access_required
    def mysqlsh_job_detail_page(job_id):
        owner_id, profile_name, _session_id = owner()
        include_all = deps.get("is_local_admin_profile_session", lambda: False)()
        job = job_snapshot(
            job_id,
            owner_session_id=owner_id,
            owner_profile_name=profile_name,
            include_all=include_all,
        )
        if job is None:
            abort(404)
        retained_active = (
            job.get("par_delete_after_use") is False
            and bool(job.get("par_id"))
            and not job.get("par_revoked_at")
            and not job.get("par_expired_at")
            and not par_has_expired(job)
        )
        return render_dashboard(
            "mysqlsh_job_detail.html",
            page_title="MySQL Shell Job",
            job=job,
            stdout=read_job_log(job, "stdout"),
            stderr=read_job_log(job, "stderr"),
            par_retained_active=retained_active,
        )

    @app.route("/mysql-shell/jobs/<job_id>/action", methods=["POST"])
    @login_required
    @mysqlsh_access_required
    def mysqlsh_job_action(job_id):
        owner_id, profile_name, _session_id = owner()
        include_all = deps.get("is_local_admin_profile_session", lambda: False)()
        action = str(request.form.get("job_action") or "")
        try:
            if action == "cancel":
                cancel_job(
                    job_id,
                    owner_session_id=owner_id,
                    owner_profile_name=profile_name,
                    include_all=include_all,
                )
                flash("MySQL Shell job canceled.", "success")
            elif action == "cleanup":
                job = job_snapshot(
                    job_id,
                    owner_session_id=owner_id,
                    owner_profile_name=profile_name,
                    include_all=include_all,
                )
                if job is None:
                    raise ValueError("MySQL Shell job was not found.")
                if job.get("par_delete_after_use") is False and not par_has_expired(job):
                    raise ValueError(
                        f"The scoped PAR is retained until {job.get('par_expires_at') or 'its configured expiry'}."
                    )
                if job.get("par_delete_after_use") is False and par_has_expired(job):
                    record_par_expired(job_id)
                elif job.get("par_id") and not job.get("par_revoked_at") and not job.get("par_expired_at"):
                    try:
                        finalize_job_par(job_id)
                    except Exception as revoke_error:
                        record_cleanup_failure(job_id, revoke_error)
                        raise RuntimeError(f"Scoped PAR revocation failed; local job files were retained: {revoke_error}") from revoke_error
                cleanup_job(
                    job_id,
                    owner_session_id=owner_id,
                    owner_profile_name=profile_name,
                    include_all=include_all,
                )
                flash("MySQL Shell job files were cleaned up after PAR completion.", "success")
            else:
                raise ValueError("Unsupported MySQL Shell job action.")
        except Exception as error:
            flash(str(error), "error")
        return redirect(url_for("mysqlsh_job_detail_page", job_id=job_id))

    @app.route("/mysql-shell/validation")
    @login_required
    @mysqlsh_access_required
    def mysqlsh_validation_page():
        return render_dashboard("mysqlsh_validation.html", page_title="MySQL Shell Validation")
