from time import perf_counter

from flask import flash, redirect, render_template, request, session, url_for

from modules.mysql_pages import (
    append_sql_workspace_history,
    build_sql_workspace_context,
    build_sql_workspace_explain_result,
    build_sql_workspace_history_entry,
    build_sql_workspace_result,
)


SQL_WORKSPACE_HISTORY_SESSION_KEY = "sql_workspace_history"
SQL_WORKSPACE_SECONDARY_ENGINE_OPTIONS = ("OFF", "ON", "FORCED")


def normalize_sql_workspace_secondary_engine(value):
    normalized = str(value or "").strip().upper()
    if normalized not in SQL_WORKSPACE_SECONDARY_ENGINE_OPTIONS:
        return "ON"
    return normalized


def apply_query_session_options(cursor, *, use_secondary_engine=""):
    normalized_secondary_engine = normalize_sql_workspace_secondary_engine(use_secondary_engine) if use_secondary_engine else ""
    if normalized_secondary_engine:
        cursor.execute(f"SET SESSION use_secondary_engine = {normalized_secondary_engine}")


def _collect_sql_workspace_cursor_results(cursor, sql, statement_index):
    result_sets = []
    result_index = 1
    while True:
        columns = [item[0] for item in cursor.description] if cursor.description else []
        if columns:
            rows = cursor.fetchall()
            label = f"Statement {statement_index}"
            if result_index > 1:
                label = f"Statement {statement_index}.{result_index}"
            result_sets.append(
                {
                    "label": label,
                    "columns": columns,
                    "rows": rows,
                    "statement": sql,
                }
            )
            result_index += 1
        else:
            rowcount = cursor.rowcount
            if rowcount is not None and rowcount >= 0:
                result_sets.append(
                    {
                        "label": f"Statement {statement_index}",
                        "kind": "message",
                        "message": f"Statement completed. Rows affected: {rowcount}.",
                        "statement": sql,
                    }
                )
        if not cursor.nextset():
            break

    if not result_sets:
        result_sets.append(
            {
                "label": f"Statement {statement_index}",
                "kind": "message",
                "message": "Statement completed without a result set.",
                "statement": sql,
            }
        )
    return result_sets


def execute_sql_workspace_statements(
    statements,
    *,
    mysql_connection,
    database=None,
    use_secondary_engine="",
):
    result_sets = []
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
            for statement_index, statement in enumerate(statements, start=1):
                cursor.execute(statement)
                result_sets.extend(_collect_sql_workspace_cursor_results(cursor, statement, statement_index))
    return result_sets


def _normalize_sql_workspace_statement(sql_text):
    statement = str(sql_text or "").strip()
    if not statement:
        raise ValueError("Enter a SQL statement.")
    return statement


def split_sql_workspace_statements(sql_text, *, require_terminator=False):
    text = str(sql_text or "")
    statements = []
    current = []
    quote_char = ""
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    last_statement_terminated = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_line_comment:
            current.append(char)
            if char in "\r\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if quote_char:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                if next_char == quote_char:
                    current.append(next_char)
                    index += 2
                    continue
                quote_char = ""
            index += 1
            continue

        if in_backtick:
            current.append(char)
            if char == "`":
                if next_char == "`":
                    current.append(next_char)
                    index += 2
                    continue
                in_backtick = False
            index += 1
            continue

        if char == "-" and next_char == "-" and (index + 2 >= length or text[index + 2].isspace()):
            current.append(char)
            current.append(next_char)
            in_line_comment = True
            index += 2
            continue
        if char == "#":
            current.append(char)
            in_line_comment = True
            index += 1
            continue
        if char == "/" and next_char == "*":
            current.append(char)
            current.append(next_char)
            in_block_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote_char = char
            escaped = False
            index += 1
            continue
        if char == "`":
            current.append(char)
            in_backtick = True
            index += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            last_statement_terminated = True
            index += 1
            continue

        if not char.isspace():
            last_statement_terminated = False
        current.append(char)
        index += 1

    if quote_char or in_backtick or in_block_comment:
        raise ValueError("SQL text contains an unterminated quote, identifier, or block comment.")

    trailing_statement = "".join(current).strip()
    if trailing_statement:
        if require_terminator and not last_statement_terminated:
            raise ValueError("Every SQL statement must be terminated by ';'.")
        statements.append(trailing_statement)

    if not statements:
        raise ValueError("Enter a SQL statement.")
    return statements


def _normalize_sql_workspace_explain_statement(sql_text):
    statements = split_sql_workspace_statements(sql_text)
    if len(statements) != 1:
        raise ValueError("Explain supports one statement at a time.")
    statement = statements[0].rstrip().rstrip(";").strip()
    if not statement:
        raise ValueError("Enter a SQL statement.")
    if statement.lower().startswith("explain"):
        raise ValueError("Enter the SQL statement itself. Explain adds EXPLAIN automatically.")
    return statement


def register_sql_workspace_routes(app, deps):
    login_required = deps["login_required"]
    render_dashboard = deps["render_dashboard"]
    get_session_profile = deps["get_session_profile"]
    is_system_schema_name = deps["is_system_schema_name"]
    fetch_database_inventory = deps["fetch_database_inventory"]
    execute_query = deps["execute_query"]
    mysql_connection = deps["mysql_connection"]

    @app.route("/mysql/sql-workspace", methods=["GET", "POST"])
    @login_required
    def sql_workspace_page():
        workspace_output_tab = str(request.values.get("output_tab", "execution-result")).strip().lower()
        if workspace_output_tab not in {"execution-result", "history"}:
            workspace_output_tab = "execution-result"
        use_secondary_engine = normalize_sql_workspace_secondary_engine(
            request.values.get("use_secondary_engine", "ON")
        )
        default_database = str(get_session_profile().get("database", "") or "").strip()
        if is_system_schema_name(default_database):
            default_database = ""

        selected_database = str(
            request.values.get("database", default_database if request.method == "GET" else "")
        ).strip()
        sql_text = str(request.values.get("sql_text", ""))
        history_rows = session.get(SQL_WORKSPACE_HISTORY_SESSION_KEY, [])
        if not isinstance(history_rows, list):
            history_rows = []

        last_result = None
        if request.method == "POST":
            action = str(request.form.get("workspace_action", "execute")).strip().lower()
            selected_database = str(request.form.get("database", "")).strip()
            sql_text = str(request.form.get("sql_text", ""))
            use_secondary_engine = normalize_sql_workspace_secondary_engine(
                request.form.get("use_secondary_engine", "ON")
            )
            if action == "clear_history":
                session[SQL_WORKSPACE_HISTORY_SESSION_KEY] = []
                flash("SQL workspace history cleared.", "success")
                redirect_values = {"output_tab": "history", "use_secondary_engine": use_secondary_engine}
                if selected_database:
                    redirect_values["database"] = selected_database
                if sql_text:
                    redirect_values["sql_text"] = sql_text
                return redirect(url_for("sql_workspace_page", **redirect_values))

            started_at = perf_counter()
            workspace_output_tab = "execution-result"

            if action == "explain":
                normalized_statement = sql_text
                try:
                    normalized_statement = _normalize_sql_workspace_explain_statement(sql_text)
                    text_rows = execute_query(
                        f"EXPLAIN {normalized_statement}",
                        database=selected_database or None,
                        use_secondary_engine=use_secondary_engine,
                    )
                    json_rows = []
                    json_error = ""
                    try:
                        json_rows = execute_query(
                            f"EXPLAIN FORMAT=JSON {normalized_statement}",
                            database=selected_database or None,
                            use_secondary_engine=use_secondary_engine,
                        )
                    except Exception as error:  # pragma: no cover - depends on server features
                        json_error = str(error)
                    duration_ms = (perf_counter() - started_at) * 1000
                    last_result = build_sql_workspace_explain_result(
                        normalized_statement,
                        selected_database,
                        text_rows,
                        json_rows,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        json_error=json_error,
                    )
                    history_rows = append_sql_workspace_history(
                        history_rows,
                        build_sql_workspace_history_entry(
                            "Explain",
                            selected_database,
                            normalized_statement,
                            duration_ms,
                            use_secondary_engine=use_secondary_engine,
                            status="success" if not json_error else "partial",
                            error_message=json_error,
                        ),
                    )
                except Exception as error:
                    duration_ms = (perf_counter() - started_at) * 1000
                    last_result = build_sql_workspace_result(
                        "Explain",
                        normalized_statement,
                        selected_database,
                        [],
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        error_message=str(error),
                    )
                    history_rows = append_sql_workspace_history(
                        history_rows,
                        build_sql_workspace_history_entry(
                            "Explain",
                            selected_database,
                            normalized_statement,
                            duration_ms,
                            use_secondary_engine=use_secondary_engine,
                            status="error",
                            error_message=str(error),
                        ),
                    )
                    flash(str(error), "error")
            else:
                normalized_statement = sql_text
                result_sets = []
                try:
                    normalized_statement = _normalize_sql_workspace_statement(sql_text)
                    statements = split_sql_workspace_statements(normalized_statement)
                    result_sets = execute_sql_workspace_statements(
                        statements,
                        mysql_connection=mysql_connection,
                        database=selected_database or None,
                        use_secondary_engine=use_secondary_engine,
                    )
                    duration_ms = (perf_counter() - started_at) * 1000
                    last_result = build_sql_workspace_result(
                        "Execute",
                        normalized_statement,
                        selected_database,
                        result_sets,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                    )
                    history_rows = append_sql_workspace_history(
                        history_rows,
                        build_sql_workspace_history_entry(
                            "Execute",
                            selected_database,
                            normalized_statement,
                            duration_ms,
                            use_secondary_engine=use_secondary_engine,
                            status="success",
                        ),
                    )
                except Exception as error:
                    duration_ms = (perf_counter() - started_at) * 1000
                    last_result = build_sql_workspace_result(
                        "Execute",
                        normalized_statement,
                        selected_database,
                        result_sets,
                        duration_ms,
                        use_secondary_engine=use_secondary_engine,
                        error_message=str(error),
                    )
                    history_rows = append_sql_workspace_history(
                        history_rows,
                        build_sql_workspace_history_entry(
                            "Execute",
                            selected_database,
                            normalized_statement,
                            duration_ms,
                            use_secondary_engine=use_secondary_engine,
                            status="error",
                            error_message=str(error),
                        ),
                    )
                    flash(str(error), "error")

            session[SQL_WORKSPACE_HISTORY_SESSION_KEY] = history_rows

        page_context = build_sql_workspace_context(
            selected_database,
            sql_text,
            last_result,
            history_rows,
            fetch_database_inventory=fetch_database_inventory,
        )
        return render_dashboard(
            "sql_workspace.html",
            page_title="SQL Workspace",
            workspace_output_tab=workspace_output_tab,
            use_secondary_engine=use_secondary_engine,
            secondary_engine_modes=SQL_WORKSPACE_SECONDARY_ENGINE_OPTIONS,
            **page_context,
        )
