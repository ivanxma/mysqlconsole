import re
from datetime import datetime, timezone


_execute_query = None
_quote_identifier = None
_mysql_connection = None


def configure_monitoring_queries(*, execute_query, quote_identifier, mysql_connection):
    global _execute_query, _quote_identifier, _mysql_connection
    _execute_query = execute_query
    _quote_identifier = quote_identifier
    _mysql_connection = mysql_connection


def execute_query(*args, **kwargs):
    if _execute_query is None:
        raise RuntimeError("monitoring query dependencies are not configured")
    return _execute_query(*args, **kwargs)


def quote_identifier(*args, **kwargs):
    if _quote_identifier is None:
        raise RuntimeError("monitoring query dependencies are not configured")
    return _quote_identifier(*args, **kwargs)


def mysql_connection(*args, **kwargs):
    if _mysql_connection is None:
        raise RuntimeError("monitoring query dependencies are not configured")
    return _mysql_connection(*args, **kwargs)


def run_report_query(sql, params=None, *, database=None):
    with mysql_connection(database_override=database) as connection:
        with connection.cursor() as cursor:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
    return {"columns": columns, "rows": rows}


def fetch_monitoring_performance_queries():
    return run_report_query(
        """
        SELECT
          QUERY_ID AS query_id,
          QUERY_TEXT AS query_text,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryStartTime", '$[0]')),
            '%%Y-%%m-%%d %%H:%%i:%%s.%%f'
          ) AS query_start,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.qexecStartTime", '$[0]')),
            '%%Y-%%m-%%d %%H:%%i:%%s.%%f'
          ) AS rapid_start,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.timeBetweenMakePushedJoinAndRpdExecMsec", '$[0]') AS queue_wait_ms,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryEndTime", '$[0]')),
            '%%Y-%%m-%%d %%H:%%i:%%s.%%f'
          ) AS query_end,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.executionTime", '$[0]') AS execution_ms,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.sessionId", '$[0]') AS connection_id
        FROM performance_schema.rpd_query_stats
        WHERE query_text NOT LIKE 'ML_%%'
        ORDER BY query_id DESC
        LIMIT 200
        """
    )


def fetch_monitoring_ml_queries(current_ml_connection_only=False):
    connection_filter = ""
    if current_ml_connection_only:
        connection_filter = """
          AND connection_id = (
            SELECT id
            FROM performance_schema.processlist
            WHERE info LIKE 'SET rapid_ml_operation%%'
            LIMIT 1
          )
        """
    return run_report_query(
        """
        SELECT
          QEXEC_TEXT->>"$.startTime" AS start_time,
          query_text,
          QEXEC_TEXT->>"$.status" AS status,
          QEXEC_TEXT->>"$.totalRunTime" AS total_run_time,
          QEXEC_TEXT->>"$.details.operation" AS operation,
          QEXEC_TEXT->>"$.completionPercentage" AS completion_percentage,
          query_id,
          connection_id
        FROM performance_schema.rpd_query_stats
        WHERE query_text LIKE 'ML_%%'
        {connection_filter}
        ORDER BY start_time DESC
        LIMIT 200
        """.format(connection_filter=connection_filter)
    )


def fetch_monitoring_load_recovery():
    return run_report_query(
        """
        SELECT
          rpd_table_id.id AS table_id,
          rpd_table_id.name AS table_name,
          rpd_tables.size_bytes AS size_bytes,
          rpd_tables.query_count AS query_count,
          rpd_tables.recovery_source AS recovery_source,
          rpd_tables.load_start_timestamp AS load_start_timestamp,
          TIME_TO_SEC(TIMEDIFF(rpd_tables.load_end_timestamp, rpd_tables.load_start_timestamp)) AS duration_seconds
        FROM performance_schema.rpd_tables
        JOIN performance_schema.rpd_table_id
          ON rpd_tables.id = rpd_table_id.id
        ORDER BY rpd_tables.size_bytes DESC
        LIMIT 200
        """
    )


def _empty_report():
    return {"columns": [], "rows": [], "error": ""}


def _safe_report(fetcher, *args, **kwargs):
    try:
        report = fetcher(*args, **kwargs)
        report["error"] = ""
        return report
    except Exception as error:
        return {"columns": [], "rows": [], "error": str(error)}


def _format_bytes(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    unit_index = 0
    while number >= 1024 and unit_index < len(units) - 1:
        number /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(number)} {units[unit_index]}"
    return f"{number:.1f} {units[unit_index]}"


def _coerce_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_numeric(value, default=None):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _parse_mysql_size_to_bytes(value):
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*([KMGTPE]?)(?:I?B?)?\s*", text)
    if not match:
        return _extract_numeric(text, None)
    number = float(match.group(1))
    suffix = match.group(2).upper()
    power_map = {
        "": 0,
        "K": 1,
        "M": 2,
        "G": 3,
        "T": 4,
        "P": 5,
        "E": 6,
    }
    return number * (1024 ** power_map.get(suffix, 0))


def _estimate_temp_tablespace_bytes_from_path(temp_data_file_path):
    file_specs = [segment.strip() for segment in str(temp_data_file_path or "").split(";") if segment.strip()]
    total_bytes = 0
    found_size = False
    for file_spec in file_specs:
        parts = [part.strip() for part in file_spec.split(":") if part.strip()]
        if len(parts) < 2:
            continue
        size_bytes = _parse_mysql_size_to_bytes(parts[1])
        if size_bytes is None:
            continue
        total_bytes += size_bytes
        found_size = True
    if not found_size:
        return None
    return total_bytes


def _format_count(value):
    number = _extract_numeric(value, None)
    if number is None:
        return "-"
    if float(number).is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def _format_milliseconds(value):
    number = _extract_numeric(value, None)
    if number is None:
        return "-"
    if number >= 60000:
        return f"{number / 60000.0:.1f} min"
    if number >= 1000:
        return f"{number / 1000.0:.1f} s"
    return f"{number:.0f} ms"


def _duration_value_to_ms(column_name, value):
    number = _extract_numeric(value, None)
    if number is None:
        return None
    lowered = str(column_name or "").lower()
    if "nanosecond" in lowered or lowered.endswith("_ns"):
        return number / 1_000_000.0
    if "microsecond" in lowered or lowered.endswith("_us"):
        return number / 1000.0
    if (lowered.endswith("_sec") or lowered.endswith("_secs") or lowered.endswith("_seconds")) and not lowered.endswith("_ms"):
        return number * 1000.0
    return number


def _report_row_map(report, key_column, value_column):
    mapping = {}
    for row in report.get("rows", []):
        key = row.get(key_column)
        if key is None:
            continue
        mapping[str(key)] = row.get(value_column)
    return mapping


def _first_available_column(column_lookup, candidates):
    for candidate in candidates:
        actual_name = column_lookup.get(candidate.lower())
        if actual_name:
            return actual_name
    return None


def _chart_card(card_id, title, subtitle, kind, *, unit="count", series=None, bars=None, details=None, error=""):
    return {
        "id": card_id,
        "title": title,
        "subtitle": subtitle,
        "kind": kind,
        "unit": unit,
        "series": series or [],
        "bars": bars or [],
        "details": details or [],
        "error": error,
    }


def _sum_report_column(report, column_name):
    total = 0
    found = False
    for row in report.get("rows", []):
        value = _coerce_int(row.get(column_name), None)
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def fetch_monitoring_global_status():
    return run_report_query(
        """
        SELECT
          variable_name AS metric_name,
          variable_value AS metric_value
        FROM performance_schema.global_status
        WHERE variable_name IN (
          'Threads_connected',
          'Threads_running',
          'Created_tmp_tables',
          'Created_tmp_disk_tables',
          'Created_tmp_files'
        )
        ORDER BY variable_name
        """
    )


def fetch_monitoring_user_processlist():
    return run_report_query(
        """
        SELECT
          id AS connection_id,
          user AS user_name,
          host AS host_name,
          db AS database_name,
          command AS command_name,
          time AS elapsed_seconds,
          state AS state_name,
          LEFT(info, 240) AS current_sql
        FROM performance_schema.processlist
        WHERE user IS NOT NULL
          AND user NOT IN ('event_scheduler', 'system user', 'mysql.session')
        ORDER BY time DESC, id DESC
        LIMIT 100
        """
    )


def fetch_monitoring_current_connections():
    return run_report_query(
        """
        SELECT
          COALESCE(user, '(internal)') AS user_name,
          SUBSTRING_INDEX(COALESCE(host, ''), ':', 1) AS host_name,
          COALESCE(db, '') AS database_name,
          COUNT(*) AS connection_count,
          SUM(CASE WHEN command <> 'Sleep' THEN 1 ELSE 0 END) AS active_count,
          MAX(time) AS max_age_seconds
        FROM performance_schema.processlist
        GROUP BY COALESCE(user, '(internal)'), SUBSTRING_INDEX(COALESCE(host, ''), ':', 1), COALESCE(db, '')
        ORDER BY connection_count DESC, active_count DESC, user_name
        LIMIT 100
        """
    )


def fetch_monitoring_innodb_memory_usage():
    return run_report_query(
        """
        SELECT
          REPLACE(event_name, 'memory/innodb/', '') AS event_name,
          current_count_used AS allocation_count,
          current_number_of_bytes_used AS current_bytes,
          high_number_of_bytes_used AS high_bytes
        FROM performance_schema.memory_summary_global_by_event_name
        WHERE event_name LIKE 'memory/innodb/%%'
        ORDER BY current_number_of_bytes_used DESC
        LIMIT 25
        """
    )


def fetch_monitoring_lock_waits():
    return run_report_query(
        """
        SELECT
          COALESCE(waiting_lock.object_schema, blocking_lock.object_schema) AS object_schema,
          COALESCE(waiting_lock.object_name, blocking_lock.object_name) AS object_name,
          waiting_thread.processlist_id AS waiting_connection_id,
          waiting_thread.processlist_user AS waiting_user,
          waiting_thread.processlist_time AS waiting_seconds,
          waiting_lock.lock_type AS waiting_lock_type,
          waiting_lock.lock_mode AS waiting_lock_mode,
          blocking_thread.processlist_id AS blocking_connection_id,
          blocking_thread.processlist_user AS blocking_user,
          blocking_thread.processlist_time AS blocking_seconds,
          blocking_lock.lock_type AS blocking_lock_type,
          blocking_lock.lock_mode AS blocking_lock_mode
        FROM performance_schema.data_lock_waits AS waits
        JOIN performance_schema.data_locks AS waiting_lock
          ON waits.requesting_engine_lock_id = waiting_lock.engine_lock_id
        JOIN performance_schema.data_locks AS blocking_lock
          ON waits.blocking_engine_lock_id = blocking_lock.engine_lock_id
        LEFT JOIN performance_schema.threads AS waiting_thread
          ON waiting_lock.thread_id = waiting_thread.thread_id
        LEFT JOIN performance_schema.threads AS blocking_thread
          ON blocking_lock.thread_id = blocking_thread.thread_id
        ORDER BY object_schema, object_name, waiting_seconds DESC
        LIMIT 100
        """
    )


def fetch_monitoring_lock_table_detail(lock_schema, lock_table):
    return run_report_query(
        """
        SELECT
          object_schema,
          object_name,
          index_name,
          lock_type,
          lock_mode,
          lock_status,
          lock_data,
          thread.processlist_id AS connection_id,
          thread.processlist_user AS user_name,
          thread.processlist_db AS database_name,
          thread.processlist_time AS elapsed_seconds
        FROM performance_schema.data_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.thread_id = thread.thread_id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
        ORDER BY connection_id, index_name, lock_mode
        LIMIT 200
        """,
        [lock_schema, lock_table],
    )


def fetch_monitoring_lock_connection_detail(connection_id):
    return run_report_query(
        """
        SELECT
          thread.processlist_id AS connection_id,
          thread.processlist_user AS user_name,
          thread.processlist_db AS database_name,
          thread.processlist_state AS state_name,
          thread.processlist_time AS elapsed_seconds,
          locks.object_schema,
          locks.object_name,
          locks.index_name,
          locks.lock_type,
          locks.lock_mode,
          locks.lock_status,
          locks.lock_data
        FROM performance_schema.data_locks AS locks
        JOIN performance_schema.threads AS thread
          ON locks.thread_id = thread.thread_id
        WHERE thread.processlist_id = %s
        ORDER BY locks.object_schema, locks.object_name, locks.index_name
        LIMIT 200
        """,
        [connection_id],
    )


def fetch_monitoring_metadata_locks():
    return run_report_query(
        """
        SELECT
          object_type,
          object_schema,
          object_name,
          lock_type,
          lock_duration,
          lock_status,
          source,
          owner_thread_id,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE object_schema IS NOT NULL
        ORDER BY CASE WHEN lock_status = 'PENDING' THEN 0 ELSE 1 END, object_schema, object_name
        LIMIT 200
        """
    )


def fetch_monitoring_metadata_object_detail(lock_schema, lock_name):
    return run_report_query(
        """
        SELECT
          object_type,
          object_schema,
          object_name,
          lock_type,
          lock_duration,
          lock_status,
          source,
          owner_thread_id,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
        ORDER BY CASE WHEN lock_status = 'PENDING' THEN 0 ELSE 1 END, owner_connection_id
        LIMIT 200
        """,
        [lock_schema, lock_name],
    )


def fetch_monitoring_metadata_connection_detail(connection_id):
    return run_report_query(
        """
        SELECT
          object_type,
          object_schema,
          object_name,
          lock_type,
          lock_duration,
          lock_status,
          source,
          owner_thread_id,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE thread.processlist_id = %s
        ORDER BY CASE WHEN lock_status = 'PENDING' THEN 0 ELSE 1 END, object_schema, object_name
        LIMIT 200
        """,
        [connection_id],
    )


def fetch_monitoring_process_connection_detail(connection_id):
    return run_report_query(
        """
        SELECT
          id AS connection_id,
          user AS user_name,
          host AS host_name,
          db AS database_name,
          command AS command_name,
          time AS elapsed_seconds,
          state AS state_name,
          LEFT(info, 500) AS current_sql
        FROM performance_schema.processlist
        WHERE id = %s
        LIMIT 1
        """,
        [connection_id],
    )


def fetch_monitoring_row_lock_source_detail(lock_schema, lock_table, blocking_connection_id):
    return run_report_query(
        """
        SELECT
          waits.blocking_connection_id,
          waits.blocking_user,
          waits.blocking_seconds,
          waits.blocking_lock_type,
          waits.blocking_lock_mode,
          held_locks.index_name,
          held_locks.lock_type AS held_lock_type,
          held_locks.lock_mode AS held_lock_mode,
          held_locks.lock_status AS held_lock_status,
          held_locks.lock_data
        FROM (
          SELECT
            COALESCE(waiting_lock.object_schema, blocking_lock.object_schema) AS object_schema,
            COALESCE(waiting_lock.object_name, blocking_lock.object_name) AS object_name,
            blocking_thread.processlist_id AS blocking_connection_id,
            blocking_thread.processlist_user AS blocking_user,
            blocking_thread.processlist_time AS blocking_seconds,
            blocking_lock.lock_type AS blocking_lock_type,
            blocking_lock.lock_mode AS blocking_lock_mode,
            blocking_lock.thread_id AS blocking_thread_id
          FROM performance_schema.data_lock_waits AS lock_waits
          JOIN performance_schema.data_locks AS waiting_lock
            ON lock_waits.requesting_engine_lock_id = waiting_lock.engine_lock_id
          JOIN performance_schema.data_locks AS blocking_lock
            ON lock_waits.blocking_engine_lock_id = blocking_lock.engine_lock_id
          LEFT JOIN performance_schema.threads AS blocking_thread
            ON blocking_lock.thread_id = blocking_thread.thread_id
        ) AS waits
        LEFT JOIN performance_schema.data_locks AS held_locks
          ON waits.blocking_thread_id = held_locks.thread_id
         AND held_locks.object_schema = waits.object_schema
         AND held_locks.object_name = waits.object_name
        WHERE waits.object_schema = %s
          AND waits.object_name = %s
          AND waits.blocking_connection_id = %s
        ORDER BY held_locks.index_name, held_locks.lock_mode
        LIMIT 200
        """,
        [lock_schema, lock_table, blocking_connection_id],
    )


def fetch_monitoring_row_lock_impacted_detail(lock_schema, lock_table, waiting_connection_id):
    return run_report_query(
        """
        SELECT
          waits.waiting_connection_id,
          waits.waiting_user,
          waits.waiting_seconds,
          waits.waiting_lock_type,
          waits.waiting_lock_mode,
          process.command AS waiting_command,
          process.state AS waiting_state,
          LEFT(process.info, 500) AS waiting_sql
        FROM (
          SELECT
            COALESCE(waiting_lock.object_schema, blocking_lock.object_schema) AS object_schema,
            COALESCE(waiting_lock.object_name, blocking_lock.object_name) AS object_name,
            waiting_thread.processlist_id AS waiting_connection_id,
            waiting_thread.processlist_user AS waiting_user,
            waiting_thread.processlist_time AS waiting_seconds,
            waiting_lock.lock_type AS waiting_lock_type,
            waiting_lock.lock_mode AS waiting_lock_mode
          FROM performance_schema.data_lock_waits AS lock_waits
          JOIN performance_schema.data_locks AS waiting_lock
            ON lock_waits.requesting_engine_lock_id = waiting_lock.engine_lock_id
          JOIN performance_schema.data_locks AS blocking_lock
            ON lock_waits.blocking_engine_lock_id = blocking_lock.engine_lock_id
          LEFT JOIN performance_schema.threads AS waiting_thread
            ON waiting_lock.thread_id = waiting_thread.thread_id
        ) AS waits
        LEFT JOIN performance_schema.processlist AS process
          ON waits.waiting_connection_id = process.id
        WHERE waits.object_schema = %s
          AND waits.object_name = %s
          AND waits.waiting_connection_id = %s
        ORDER BY waits.waiting_seconds DESC
        LIMIT 50
        """,
        [lock_schema, lock_table, waiting_connection_id],
    )


def fetch_monitoring_metadata_source_detail(lock_schema, lock_name, owner_connection_id):
    return run_report_query(
        """
        SELECT
          locks.object_type,
          locks.object_schema,
          locks.object_name,
          locks.lock_type,
          locks.lock_duration,
          locks.lock_status,
          locks.source,
          thread.processlist_id AS owner_connection_id,
          thread.processlist_user AS owner_user,
          thread.processlist_db AS owner_database,
          thread.processlist_state AS owner_state,
          thread.processlist_time AS owner_elapsed_seconds
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
          AND thread.processlist_id = %s
        ORDER BY locks.lock_status, locks.lock_type
        LIMIT 200
        """,
        [lock_schema, lock_name, owner_connection_id],
    )


def fetch_monitoring_metadata_impacted_detail(lock_schema, lock_name):
    return run_report_query(
        """
        SELECT
          locks.object_type,
          locks.object_schema,
          locks.object_name,
          locks.lock_type,
          locks.lock_duration,
          locks.lock_status,
          thread.processlist_id AS connection_id,
          thread.processlist_user AS user_name,
          thread.processlist_db AS database_name,
          thread.processlist_state AS state_name,
          thread.processlist_time AS elapsed_seconds,
          process.command AS command_name,
          LEFT(process.info, 500) AS current_sql
        FROM performance_schema.metadata_locks AS locks
        LEFT JOIN performance_schema.threads AS thread
          ON locks.owner_thread_id = thread.thread_id
        LEFT JOIN performance_schema.processlist AS process
          ON thread.processlist_id = process.id
        WHERE locks.object_schema = %s
          AND locks.object_name = %s
          AND locks.lock_status = 'PENDING'
        ORDER BY elapsed_seconds DESC, connection_id
        LIMIT 200
        """,
        [lock_schema, lock_name],
    )


def fetch_monitoring_innodb_storage_usage():
    return run_report_query(
        """
        SELECT
          table_schema,
          COUNT(*) AS table_count,
          SUM(data_length) AS data_bytes,
          SUM(index_length) AS index_bytes,
          SUM(data_length + index_length) AS total_bytes
        FROM information_schema.tables
        WHERE engine = 'InnoDB'
        GROUP BY table_schema
        ORDER BY total_bytes DESC
        LIMIT 100
        """
    )


def fetch_monitoring_temp_storage_usage():
    return run_report_query(
        """
        SELECT
          variable_name AS setting_name,
          variable_value AS setting_value
        FROM performance_schema.global_variables
        WHERE variable_name IN (
          'tmp_table_size',
          'max_heap_table_size',
          'temptable_max_ram',
          'innodb_temp_data_file_path'
        )
        ORDER BY variable_name
        """
    )


def fetch_monitoring_temp_table_usage():
    return run_dynamic_projection_report(
        "information_schema",
        "innodb_temp_table_info",
        [
            ("table_id", "table_id"),
            ("name", "name"),
            ("n_cols", "column_count"),
            ("space", "tablespace_id"),
            ("per_table_tablespace", "per_table_tablespace"),
            ("is_compressed", "is_compressed"),
        ],
        order_by=["table_id"],
        limit=100,
    )


def fetch_table_column_names(schema_name, table_name):
    rows = execute_query(
        """
        SELECT
          column_name AS column_name_value
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        [schema_name, table_name],
        database="information_schema",
    )
    return [row["column_name_value"] for row in rows]


def fetch_table_column_lookup(schema_name, table_name):
    return {
        column_name.lower(): column_name
        for column_name in fetch_table_column_names(schema_name, table_name)
    }


def run_dynamic_projection_report(schema_name, table_name, projections, *, order_by=None, limit=None):
    available_columns = fetch_table_column_names(schema_name, table_name)
    available_column_lookup = {column_name.lower(): column_name for column_name in available_columns}
    selected_columns = []
    output_columns = []

    for source_name, alias in projections:
        actual_source_name = available_column_lookup.get(str(source_name).lower())
        if not actual_source_name:
            continue
        safe_source = quote_identifier(actual_source_name)
        safe_alias = quote_identifier(alias)
        selected_columns.append(f"{safe_source} AS {safe_alias}")
        output_columns.append(alias)

    if not selected_columns:
        raise ValueError(f"No expected columns were found on {schema_name}.{table_name}.")

    sql = f"SELECT {', '.join(selected_columns)} FROM {quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    if order_by:
        order_clauses = []
        for column_name in order_by:
            if column_name in output_columns:
                order_clauses.append(quote_identifier(column_name))
        if order_clauses:
            sql += " ORDER BY " + ", ".join(order_clauses)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return run_report_query(sql)


def _join_non_empty_labels(values, empty_label="-"):
    labels = []
    seen = set()
    for value in values:
        label = str(value if value is not None else "").strip()
        if not label or label == "-":
            continue
        if label not in seen:
            labels.append(label)
            seen.add(label)
    return ", ".join(labels) if labels else empty_label


def _parse_mysql_datetime(value):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text or text.startswith("0000-00-00"):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _milliseconds_between_timestamps(start_value, end_value):
    start_dt = _parse_mysql_datetime(start_value)
    end_dt = _parse_mysql_datetime(end_value)
    if not start_dt or not end_dt:
        return None
    return max((end_dt - start_dt).total_seconds() * 1000.0, 0.0)


def fetch_monitoring_replication_connection_status():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_connection_status",
        [
            ("channel_name", "channel_name"),
            ("service_state", "service_state"),
            ("thread_id", "thread_id"),
            ("received_transaction_set", "received_transaction_set"),
            ("last_heartbeat_timestamp", "last_heartbeat_timestamp"),
            ("last_error_number", "last_error_number"),
            ("last_error_message", "last_error_message"),
        ],
        order_by=["channel_name"],
    )


def fetch_monitoring_replication_applier_coordinator():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_applier_status_by_coordinator",
        [
            ("channel_name", "channel_name"),
            ("thread_id", "thread_id"),
            ("service_state", "service_state"),
            ("last_processed_transaction", "last_processed_transaction"),
            ("last_processed_transaction_original_commit_timestamp", "last_processed_transaction_original_commit_timestamp"),
            ("last_processed_transaction_immediate_commit_timestamp", "last_processed_transaction_immediate_commit_timestamp"),
            ("last_processed_transaction_start_buffer_timestamp", "last_processed_transaction_start_buffer_timestamp"),
            ("last_processed_transaction_end_buffer_timestamp", "last_processed_transaction_end_buffer_timestamp"),
            ("last_processed_transaction_start_apply_timestamp", "last_processed_transaction_start_apply_timestamp"),
            ("last_processed_transaction_end_apply_timestamp", "last_processed_transaction_end_apply_timestamp"),
            ("last_error_number", "last_error_number"),
            ("last_error_message", "last_error_message"),
        ],
        order_by=["channel_name"],
    )


def fetch_monitoring_replication_applier_workers():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_applier_status_by_worker",
        [
            ("channel_name", "channel_name"),
            ("worker_id", "worker_id"),
            ("thread_id", "thread_id"),
            ("service_state", "service_state"),
            ("last_applied_transaction", "last_applied_transaction"),
            ("last_applied_transaction_original_commit_timestamp", "last_applied_transaction_original_commit_timestamp"),
            ("last_applied_transaction_immediate_commit_timestamp", "last_applied_transaction_immediate_commit_timestamp"),
            ("last_applied_transaction_start_apply_timestamp", "last_applied_transaction_start_apply_timestamp"),
            ("last_applied_transaction_end_apply_timestamp", "last_applied_transaction_end_apply_timestamp"),
            ("applying_transaction", "applying_transaction"),
            ("applying_transaction_original_commit_timestamp", "applying_transaction_original_commit_timestamp"),
            ("applying_transaction_immediate_commit_timestamp", "applying_transaction_immediate_commit_timestamp"),
            ("last_error_number", "last_error_number"),
            ("last_error_message", "last_error_message"),
        ],
        order_by=["channel_name", "worker_id"],
        limit=200,
    )


def fetch_group_replication_member_rows():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_group_members",
        [
            ("channel_name", "channel_name"),
            ("member_id", "member_id"),
            ("member_host", "member_host"),
            ("member_port", "member_port"),
            ("member_state", "member_state"),
            ("member_role", "member_role"),
            ("member_version", "member_version"),
            ("member_communication_stack", "member_communication_stack"),
        ],
        order_by=["member_host", "member_port"],
        limit=100,
    )


def fetch_group_replication_member_stats_rows():
    return run_dynamic_projection_report(
        "performance_schema",
        "replication_group_member_stats",
        [
            ("channel_name", "channel_name"),
            ("member_id", "member_id"),
            ("count_transactions_in_queue", "count_transactions_in_queue"),
            ("count_transactions_checked", "count_transactions_checked"),
            ("count_conflicts_detected", "count_conflicts_detected"),
            ("count_transactions_rows_validating", "count_transactions_rows_validating"),
            ("transactions_committed_all_members", "transactions_committed_all_members"),
            ("last_conflict_free_transaction", "last_conflict_free_transaction"),
        ],
        order_by=["member_id"],
        limit=100,
    )


def fetch_replication_overview_info():
    def fetch_replica_status_report():
        rows = fetch_replica_status_rows()
        columns = list(rows[0].keys()) if rows else []
        return {"columns": columns, "rows": rows}

    replica_status_report = _safe_report(fetch_replica_status_report)
    replication_connection = _safe_report(fetch_monitoring_replication_connection_status)
    replication_applier = _safe_report(fetch_monitoring_replication_applier_coordinator)
    replication_workers = _safe_report(fetch_monitoring_replication_applier_workers)
    group_members = _safe_report(fetch_group_replication_member_rows)
    group_member_stats = _safe_report(fetch_group_replication_member_stats_rows)

    replica_rows = replica_status_report.get("rows", [])
    io_running_values = []
    sql_running_values = []
    lag_values = []
    for row in replica_rows:
        io_running_values.append(row.get("Replica_IO_Running") or row.get("Slave_IO_Running") or "-")
        sql_running_values.append(row.get("Replica_SQL_Running") or row.get("Slave_SQL_Running") or "-")
        lag_values.append(row.get("Seconds_Behind_Source") if "Seconds_Behind_Source" in row else row.get("Seconds_Behind_Master", "-"))

    replication_connection_rows = replication_connection.get("rows", []) if not replication_connection.get("error") else []
    replication_applier_rows = replication_applier.get("rows", []) if not replication_applier.get("error") else []
    replication_worker_rows = replication_workers.get("rows", []) if not replication_workers.get("error") else []
    group_member_rows = group_members.get("rows", []) if not group_members.get("error") else []

    if not io_running_values:
        io_running_values = [
            row.get("service_state") or "-"
            for row in replication_connection_rows
        ]
    if not sql_running_values:
        sql_running_values = [
            row.get("service_state") or "-"
            for row in replication_applier_rows
        ] or [
            row.get("service_state") or "-"
            for row in replication_worker_rows
        ]
    if not io_running_values and not sql_running_values and group_member_rows:
        io_running_values = ["Group Replication"]
        sql_running_values = [
            row.get("member_state") or "-"
            for row in group_member_rows
        ]

    replica_channel_count = len(replica_rows) or len(replication_connection_rows)
    performance_schema_channel_count = len(replication_connection_rows) if not replication_connection.get("error") else "-"
    group_member_count = len(group_member_rows) if not group_members.get("error") else "-"

    return {
        "replica_status": replica_status_report,
        "replication_connection": replication_connection,
        "replication_applier": replication_applier,
        "replication_workers": replication_workers,
        "group_members": group_members,
        "group_member_stats": group_member_stats,
        "replica_channel_count": replica_channel_count,
        "replica_io_running_label": _join_non_empty_labels(io_running_values),
        "replica_sql_running_label": _join_non_empty_labels(sql_running_values),
        "replica_lag_label": _join_non_empty_labels(lag_values),
        "performance_schema_channel_count": performance_schema_channel_count,
        "group_member_count": group_member_count,
    }


def empty_replication_report():
    return {"columns": [], "rows": [], "error": ""}


def empty_replication_overview_info():
    return {
        "replica_status": empty_replication_report(),
        "replication_connection": empty_replication_report(),
        "replication_applier": empty_replication_report(),
        "replication_workers": empty_replication_report(),
        "group_members": empty_replication_report(),
        "group_member_stats": empty_replication_report(),
        "replica_channel_count": "-",
        "replica_io_running_label": "-",
        "replica_sql_running_label": "-",
        "replica_lag_label": "-",
        "performance_schema_channel_count": "-",
        "group_member_count": "-",
    }


def fetch_monitoring_storage_totals():
    rows = execute_query(
        """
        SELECT
          COALESCE(SUM(data_length), 0) AS data_bytes,
          COALESCE(SUM(index_length), 0) AS index_bytes,
          COUNT(*) AS table_count,
          COUNT(DISTINCT table_schema) AS schema_count
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
        """
    )
    return rows[0] if rows else {"data_bytes": 0, "index_bytes": 0, "table_count": 0, "schema_count": 0}


def fetch_monitoring_temp_tablespace_summary():
    column_lookup = fetch_table_column_lookup("information_schema", "files")
    allocated_size_column = _first_available_column(
        column_lookup,
        [
            "allocated_size",
            "file_size",
            "data_length",
            "max_data_length",
            "initial_size",
            "maximum_size",
        ],
    )
    total_extents_column = _first_available_column(column_lookup, ["total_extents"])
    free_extents_column = _first_available_column(column_lookup, ["free_extents"])
    extent_size_column = _first_available_column(column_lookup, ["extent_size"])
    tablespace_column = _first_available_column(column_lookup, ["tablespace_name"])
    file_name_column = _first_available_column(column_lookup, ["file_name"])

    size_expression = ""
    size_source = ""
    if allocated_size_column:
        size_expression = f"COALESCE({quote_identifier(allocated_size_column)}, 0)"
        size_source = f"information_schema.files.{allocated_size_column}"
    elif total_extents_column and free_extents_column and extent_size_column:
        size_expression = (
            "GREATEST("
            f"COALESCE({quote_identifier(total_extents_column)}, 0) - "
            f"COALESCE({quote_identifier(free_extents_column)}, 0), "
            "0"
            ") * "
            f"COALESCE({quote_identifier(extent_size_column)}, 0)"
        )
        size_source = (
            f"information_schema.files.({total_extents_column}-{free_extents_column})*{extent_size_column}"
        )
    elif total_extents_column and extent_size_column:
        size_expression = (
            f"COALESCE({quote_identifier(total_extents_column)}, 0) * "
            f"COALESCE({quote_identifier(extent_size_column)}, 0)"
        )
        size_source = f"information_schema.files.{total_extents_column}*{extent_size_column}"

    conditions = []
    if tablespace_column:
        safe_tablespace = quote_identifier(tablespace_column)
        conditions.append(
            "("
            f"LOWER({safe_tablespace}) IN ('innodb_temporary', 'innodb_temp') "
            f"OR LOWER({safe_tablespace}) LIKE 'innodb_temporary%%' "
            f"OR LOWER({safe_tablespace}) LIKE '%%ibtmp%%'"
            ")"
        )
    if file_name_column:
        safe_file_name = quote_identifier(file_name_column)
        conditions.append(
            "("
            f"LOWER({safe_file_name}) LIKE '%%ibtmp%%' "
            f"OR LOWER({safe_file_name}) LIKE '%%#innodb_temp%%'"
            ")"
        )

    if size_expression and conditions:
        rows = execute_query(
            """
            SELECT
              COALESCE(SUM({size_expression}), 0) AS temp_bytes
            FROM information_schema.files
            WHERE {conditions}
            """.format(size_expression=size_expression, conditions=" OR ".join(conditions)),
            database="information_schema",
        )
        temp_bytes = _extract_numeric(rows[0].get("temp_bytes"), None) if rows else None
        if temp_bytes is not None:
            return {"temp_bytes": temp_bytes, "source": size_source}

    temp_settings = _report_row_map(fetch_monitoring_temp_storage_usage(), "setting_name", "setting_value")
    estimated_bytes = _estimate_temp_tablespace_bytes_from_path(temp_settings.get("innodb_temp_data_file_path"))
    if estimated_bytes is not None:
        return {
            "temp_bytes": estimated_bytes,
            "source": "performance_schema.global_variables.innodb_temp_data_file_path",
            "estimated": True,
        }
    return {"temp_bytes": 0, "source": "unavailable", "estimated": True}


def fetch_show_binary_logs_summary():
    with mysql_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW BINARY LOGS")
            rows = cursor.fetchall()
    total_bytes = 0
    for row in rows:
        total_bytes += _extract_numeric(row.get("File_size") or row.get("file_size"), 0) or 0
    return {
        "file_count": len(rows),
        "total_bytes": total_bytes,
    }


def fetch_replica_status_rows():
    errors = []
    for sql in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
        try:
            with mysql_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    return cursor.fetchall()
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise ValueError(errors[0])
    return []


def fetch_replication_channel_lag_rows():
    channels = []
    try:
        replica_rows = fetch_replica_status_rows()
    except Exception:
        replica_rows = []
    for index, row in enumerate(replica_rows, start=1):
        channel_name = (
            row.get("Channel_Name")
            or row.get("Channel_name")
            or row.get("Connection_name")
            or row.get("Source_Host")
            or f"Channel {index}"
        )
        lag_seconds = _extract_numeric(row.get("Seconds_Behind_Source"), None)
        if lag_seconds is None:
            lag_seconds = _extract_numeric(row.get("Seconds_Behind_Master"), 0)
        relay_space = _extract_numeric(row.get("Relay_Log_Space"), 0) or 0
        channels.append(
            {
                "label": str(channel_name).strip() or f"Channel {index}",
                "lag_ms": (lag_seconds or 0) * 1000.0,
                "relay_log_bytes": relay_space,
            }
        )
    if channels:
        return channels

    fallback_channels = {}
    applier_report = _safe_report(fetch_monitoring_replication_applier_coordinator)
    worker_report = _safe_report(fetch_monitoring_replication_applier_workers)
    for row in applier_report.get("rows", []) if not applier_report.get("error") else []:
        channel_name = str(row.get("channel_name") or "default").strip() or "default"
        lag_ms = _milliseconds_between_timestamps(
            row.get("last_processed_transaction_original_commit_timestamp"),
            row.get("last_processed_transaction_end_apply_timestamp"),
        )
        fallback_channels[channel_name] = {
            "label": channel_name,
            "lag_ms": lag_ms or 0.0,
            "relay_log_bytes": 0,
        }
    for row in worker_report.get("rows", []) if not worker_report.get("error") else []:
        channel_name = str(row.get("channel_name") or "default").strip() or "default"
        lag_ms = _milliseconds_between_timestamps(
            row.get("last_applied_transaction_original_commit_timestamp"),
            row.get("last_applied_transaction_end_apply_timestamp"),
        )
        existing = fallback_channels.get(channel_name)
        if not existing or (lag_ms or 0.0) > existing["lag_ms"]:
            fallback_channels[channel_name] = {
                "label": channel_name,
                "lag_ms": lag_ms or 0.0,
                "relay_log_bytes": 0,
            }
    if fallback_channels:
        return sorted(fallback_channels.values(), key=lambda item: item["label"])
    return channels


def fetch_heatwave_load_distribution():
    def normalize_progress(value):
        numeric = _extract_numeric(value, None)
        if numeric is None:
            return None
        if 0.0 <= numeric <= 1.0:
            return numeric * 100.0
        return numeric

    column_lookup = fetch_table_column_lookup("performance_schema", "rpd_tables")
    progress_column = _first_available_column(
        column_lookup,
        [
            "load_progress",
            "load_percentage",
            "load_percent",
            "percent_loaded",
            "load_pct",
            "availability_percentage",
            "availability_percent",
        ],
    )
    if progress_column:
        rows = execute_query(
            f"SELECT {quote_identifier(progress_column)} AS progress_value FROM performance_schema.rpd_tables"
        )
        loaded = partial = not_loaded = 0
        for row in rows:
            progress_value = normalize_progress(row.get("progress_value"))
            if progress_value is None:
                not_loaded += 1
                continue
            if progress_value >= 99.999:
                loaded += 1
            elif progress_value > 0:
                partial += 1
            else:
                not_loaded += 1
        return {
            "loaded": loaded,
            "partial": partial,
            "not_loaded": not_loaded,
            "total_tables": loaded + partial + not_loaded,
            "source": progress_column,
        }

    status_column = _first_available_column(
        column_lookup,
        [
            "load_status",
            "status",
            "recovery_status",
            "availability_status",
        ],
    )
    if status_column:
        rows = execute_query(
            f"SELECT {quote_identifier(status_column)} AS status_value FROM performance_schema.rpd_tables"
        )
        loaded = partial = not_loaded = 0
        for row in rows:
            status_value = str(row.get("status_value") or "").strip().lower()
            numeric_status = normalize_progress(status_value)
            if numeric_status is not None:
                if numeric_status >= 99.999:
                    loaded += 1
                elif numeric_status > 0:
                    partial += 1
                else:
                    not_loaded += 1
                continue
            if any(token in status_value for token in ("not loaded", "unloaded", "pending", "init")):
                not_loaded += 1
            elif any(token in status_value for token in ("partial", "loading", "recover", "progress", "sync")):
                partial += 1
            elif any(token in status_value for token in ("loaded", "complete", "available", "active")):
                loaded += 1
            else:
                not_loaded += 1
        return {
            "loaded": loaded,
            "partial": partial,
            "not_loaded": not_loaded,
            "total_tables": loaded + partial + not_loaded,
            "source": status_column,
        }

    start_column = _first_available_column(column_lookup, ["load_start_timestamp"])
    end_column = _first_available_column(column_lookup, ["load_end_timestamp"])
    if start_column or end_column:
        selected_columns = []
        if start_column:
            selected_columns.append(f"{quote_identifier(start_column)} AS load_start_value")
        if end_column:
            selected_columns.append(f"{quote_identifier(end_column)} AS load_end_value")
        rows = execute_query(
            "SELECT {columns} FROM performance_schema.rpd_tables".format(columns=", ".join(selected_columns))
        )
        loaded = partial = not_loaded = 0
        for row in rows:
            if row.get("load_end_value") not in (None, ""):
                loaded += 1
            elif row.get("load_start_value") not in (None, ""):
                partial += 1
            else:
                not_loaded += 1
        return {
            "loaded": loaded,
            "partial": partial,
            "not_loaded": not_loaded,
            "total_tables": loaded + partial + not_loaded,
            "source": "load timestamps",
        }

    raise ValueError("Unable to determine HeatWave load state columns from performance_schema.rpd_tables.")


def fetch_heatwave_node_memory_rows():
    column_lookup = fetch_table_column_lookup("performance_schema", "rpd_nodes")
    node_id_column = _first_available_column(column_lookup, ["id", "node_id"])
    ip_column = _first_available_column(column_lookup, ["ip", "ip_address", "address", "host", "hostname", "host_name"])
    port_column = _first_available_column(column_lookup, ["port"])
    memory_usage_column = _first_available_column(
        column_lookup,
        [
            "memory_usage",
            "memory_used_bytes",
            "used_memory_bytes",
            "current_memory_bytes",
            "memory_bytes",
            "allocated_memory_bytes",
            "alloc_pool_memory_bytes",
            "memory",
        ],
    )
    memory_total_column = _first_available_column(
        column_lookup,
        [
            "memory_total",
            "total_memory_bytes",
            "memory_total_bytes",
            "configured_memory_bytes",
            "node_memory_bytes",
        ],
    )
    baserel_memory_usage_column = _first_available_column(
        column_lookup,
        [
            "baserel_memory_usage",
            "base_rel_memory_usage",
        ],
    )
    status_column = _first_available_column(column_lookup, ["status"])
    if not memory_usage_column:
        raise ValueError("Unable to determine MEMORY_USAGE from performance_schema.rpd_nodes.")

    selected_columns = []
    if node_id_column:
        selected_columns.append(f"{quote_identifier(node_id_column)} AS node_id_value")
    if ip_column:
        selected_columns.append(f"{quote_identifier(ip_column)} AS ip_value")
    if port_column:
        selected_columns.append(f"{quote_identifier(port_column)} AS port_value")
    selected_columns.append(f"{quote_identifier(memory_usage_column)} AS memory_usage_value")
    if memory_total_column:
        selected_columns.append(f"{quote_identifier(memory_total_column)} AS memory_total_value")
    if baserel_memory_usage_column:
        selected_columns.append(f"{quote_identifier(baserel_memory_usage_column)} AS baserel_memory_usage_value")
    if status_column:
        selected_columns.append(f"{quote_identifier(status_column)} AS status_value")

    order_clauses = []
    if node_id_column:
        order_clauses.append(quote_identifier(node_id_column))
    if ip_column:
        order_clauses.append(quote_identifier(ip_column))
    if port_column:
        order_clauses.append(quote_identifier(port_column))
    if not order_clauses:
        order_clauses.append(f"{quote_identifier(memory_usage_column)} DESC")

    rows = execute_query(
        """
        SELECT {columns}
        FROM performance_schema.rpd_nodes
        ORDER BY {order_by}
        LIMIT 200
        """.format(
            columns=", ".join(selected_columns),
            order_by=", ".join(order_clauses),
        )
    )
    normalized_rows = []
    for index, row in enumerate(rows, start=1):
        node_id_value = _coerce_int(row.get("node_id_value"), None)
        ip_value = str(row.get("ip_value") or "").strip()
        port_value = _coerce_int(row.get("port_value"), None)
        memory_usage_value = _extract_numeric(row.get("memory_usage_value"), 0) or 0
        memory_total_value = _extract_numeric(row.get("memory_total_value"), None)
        baserel_memory_usage_value = _extract_numeric(row.get("baserel_memory_usage_value"), None)
        status_value = str(row.get("status_value") or "").strip()

        node_label = f"Node {node_id_value}" if node_id_value is not None else f"Node {index}"
        if ip_value and port_value is not None:
            node_label = f"{node_label} ({ip_value}:{port_value})"
        elif ip_value:
            node_label = f"{node_label} ({ip_value})"

        normalized_rows.append(
            {
                "label": node_label,
                "memory_usage_bytes": memory_usage_value,
                "memory_total_bytes": memory_total_value,
                "baserel_memory_usage_bytes": baserel_memory_usage_value,
                "status": status_value,
            }
        )
    return normalized_rows


def fetch_heatwave_query_timing_summary():
    rows = execute_query(
        """
        SELECT
          QUERY_ID AS query_id_value,
          QUERY_TEXT AS query_text_value,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryStartTime", '$[0]')),
            '%Y-%m-%d %H:%i:%s.%f'
          ) AS query_start_value,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.qexecStartTime", '$[0]')),
            '%Y-%m-%d %H:%i:%s.%f'
          ) AS rpd_start_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.timeBetweenMakePushedJoinAndRpdExecMsec", '$[0]') AS queue_wait_ms_value,
          STR_TO_DATE(
            JSON_UNQUOTE(JSON_EXTRACT(QEXEC_TEXT->>"$**.queryEndTime", '$[0]')),
            '%Y-%m-%d %H:%i:%s.%f'
          ) AS query_end_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.changePropagationSync.msec", '$[0]') AS change_propagation_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.waitTime", '$[0]') AS total_wait_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.executionTime", '$[0]') AS total_exec_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.totalQueryTimeBreakdown.optimizationTime", '$[0]') AS total_opt_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.rpdExec.msec", '$[0]') AS rpd_exec_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.getResults.msec", '$[0]') AS get_result_ms_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.sessionId", '$[0]') AS connection_id_value,
          JSON_EXTRACT(QEXEC_TEXT->>"$**.qkrnActualRows[*].actRows", '$[0]') AS act_rows_value
        FROM performance_schema.rpd_query_stats
        WHERE query_text NOT LIKE 'ML_%'
        ORDER BY query_id DESC
        LIMIT 60
        """
    )

    metric_values = {
        "queue_wait_ms": [],
        "total_wait_ms": [],
        "total_exec_ms": [],
        "total_opt_ms": [],
        "rpd_exec_ms": [],
        "get_result_ms": [],
        "change_propagation_ms": [],
    }

    for row in rows:
        for metric_name in metric_values:
            value = _extract_numeric(row.get(f"{metric_name}_value"), None)
            if value is not None:
                metric_values[metric_name].append(value)

    latest_row = rows[0] if rows else {}
    latest_query_text = " ".join(str(latest_row.get("query_text_value") or "").split())
    if len(latest_query_text) > 120:
        latest_query_text = latest_query_text[:117].rstrip() + "..."

    latest_query_start = latest_row.get("query_start_value")
    latest_query_end = latest_row.get("query_end_value")
    latest_elapsed_ms = None
    if latest_query_start and latest_query_end:
        try:
            latest_elapsed_ms = max((latest_query_end - latest_query_start).total_seconds() * 1000.0, 0.0)
        except TypeError:
            latest_elapsed_ms = None

    return {
        "sample_count": len(rows),
        "latest_query_id": latest_row.get("query_id_value", ""),
        "latest_connection_id": _extract_numeric(latest_row.get("connection_id_value"), None),
        "latest_query_text": latest_query_text,
        "latest_act_rows": _extract_numeric(latest_row.get("act_rows_value"), None),
        "latest_elapsed_ms": latest_elapsed_ms,
        "avg_queue_wait_ms": sum(metric_values["queue_wait_ms"]) / len(metric_values["queue_wait_ms"])
        if metric_values["queue_wait_ms"]
        else 0,
        "avg_total_wait_ms": sum(metric_values["total_wait_ms"]) / len(metric_values["total_wait_ms"])
        if metric_values["total_wait_ms"]
        else 0,
        "avg_total_exec_ms": sum(metric_values["total_exec_ms"]) / len(metric_values["total_exec_ms"])
        if metric_values["total_exec_ms"]
        else 0,
        "avg_total_opt_ms": sum(metric_values["total_opt_ms"]) / len(metric_values["total_opt_ms"])
        if metric_values["total_opt_ms"]
        else 0,
        "avg_rpd_exec_ms": sum(metric_values["rpd_exec_ms"]) / len(metric_values["rpd_exec_ms"])
        if metric_values["rpd_exec_ms"]
        else 0,
        "avg_get_result_ms": sum(metric_values["get_result_ms"]) / len(metric_values["get_result_ms"])
        if metric_values["get_result_ms"]
        else 0,
        "avg_change_propagation_ms": sum(metric_values["change_propagation_ms"])
        / len(metric_values["change_propagation_ms"])
        if metric_values["change_propagation_ms"]
        else 0,
        "max_queue_wait_ms": max(metric_values["queue_wait_ms"]) if metric_values["queue_wait_ms"] else 0,
        "max_total_wait_ms": max(metric_values["total_wait_ms"]) if metric_values["total_wait_ms"] else 0,
        "max_total_exec_ms": max(metric_values["total_exec_ms"]) if metric_values["total_exec_ms"] else 0,
        "max_total_opt_ms": max(metric_values["total_opt_ms"]) if metric_values["total_opt_ms"] else 0,
        "max_rpd_exec_ms": max(metric_values["rpd_exec_ms"]) if metric_values["rpd_exec_ms"] else 0,
        "max_get_result_ms": max(metric_values["get_result_ms"]) if metric_values["get_result_ms"] else 0,
        "max_change_propagation_ms": max(metric_values["change_propagation_ms"])
        if metric_values["change_propagation_ms"]
        else 0,
    }


def build_monitoring_connections_chart_card():
    title = "Connections"
    subtitle = "Active connections and currently running processes."
    try:
        status_map = _report_row_map(fetch_monitoring_global_status(), "metric_name", "metric_value")
        active_connections = _extract_numeric(status_map.get("Threads_connected"), 0) or 0
        running_processes = _extract_numeric(status_map.get("Threads_running"), 0) or 0
        return _chart_card(
            "connections",
            title,
            subtitle,
            "timeseries",
            unit="count",
            series=[
                {
                    "key": "active_connections",
                    "label": "Active Connections",
                    "color": "#a93a1a",
                    "value": active_connections,
                    "display": _format_count(active_connections),
                },
                {
                    "key": "running_processes",
                    "label": "Running Processes",
                    "color": "#1d4e89",
                    "value": running_processes,
                    "display": _format_count(running_processes),
                },
            ],
            details=[
                f"Threads_connected: {_format_count(active_connections)}",
                f"Threads_running: {_format_count(running_processes)}",
            ],
        )
    except Exception as error:
        return _chart_card("connections", title, subtitle, "timeseries", unit="count", error=str(error))


def build_monitoring_locks_chart_card():
    title = "Locks"
    subtitle = "Current row lock waits and pending metadata locks."
    try:
        row_lock_waits = fetch_monitoring_lock_waits()
        metadata_locks = fetch_monitoring_metadata_locks()
        row_wait_count = len(row_lock_waits.get("rows", []))
        pending_metadata_count = sum(
            1
            for row in metadata_locks.get("rows", [])
            if str(row.get("lock_status") or "").strip().upper() == "PENDING"
        )
        return _chart_card(
            "locks",
            title,
            subtitle,
            "timeseries",
            unit="count",
            series=[
                {
                    "key": "row_lock_waits",
                    "label": "Row Lock Waits",
                    "color": "#8f2d56",
                    "value": row_wait_count,
                    "display": _format_count(row_wait_count),
                },
                {
                    "key": "pending_metadata_locks",
                    "label": "Pending Metadata Locks",
                    "color": "#3d5a80",
                    "value": pending_metadata_count,
                    "display": _format_count(pending_metadata_count),
                },
            ],
            details=[
                f"Row lock wait rows: {_format_count(row_wait_count)}",
                f"Pending metadata locks: {_format_count(pending_metadata_count)}",
            ],
        )
    except Exception as error:
        return _chart_card("locks", title, subtitle, "timeseries", unit="count", error=str(error))


def build_monitoring_storage_chart_card():
    title = "DB Size and Index Size"
    subtitle = "Total data and index bytes across non-system schemas."
    try:
        totals = fetch_monitoring_storage_totals()
        data_bytes = _extract_numeric(totals.get("data_bytes"), 0) or 0
        index_bytes = _extract_numeric(totals.get("index_bytes"), 0) or 0
        table_count = _extract_numeric(totals.get("table_count"), 0) or 0
        schema_count = _extract_numeric(totals.get("schema_count"), 0) or 0
        return _chart_card(
            "storage",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "data_bytes",
                    "label": "Data Bytes",
                    "color": "#355070",
                    "value": data_bytes,
                    "display": _format_bytes(data_bytes),
                },
                {
                    "key": "index_bytes",
                    "label": "Index Bytes",
                    "color": "#bc6c25",
                    "value": index_bytes,
                    "display": _format_bytes(index_bytes),
                },
            ],
            details=[
                f"Tables counted: {_format_count(table_count)}",
                f"Schemas counted: {_format_count(schema_count)}",
                f"Total footprint: {_format_bytes(data_bytes + index_bytes)}",
            ],
        )
    except Exception as error:
        return _chart_card("storage", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_innodb_memory_chart_card():
    title = "InnoDB Memory Usage"
    subtitle = "Current and peak instrumented InnoDB memory usage."
    try:
        report = fetch_monitoring_innodb_memory_usage()
        current_bytes = _sum_report_column(report, "current_bytes") or 0
        high_bytes = _sum_report_column(report, "high_bytes") or 0
        top_consumer = report.get("rows", [{}])[0]
        top_consumer_name = top_consumer.get("event_name") or "-"
        return _chart_card(
            "innodb_memory",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "current_bytes",
                    "label": "Current Bytes",
                    "color": "#588157",
                    "value": current_bytes,
                    "display": _format_bytes(current_bytes),
                },
                {
                    "key": "high_bytes",
                    "label": "Peak Bytes",
                    "color": "#a68a64",
                    "value": high_bytes,
                    "display": _format_bytes(high_bytes),
                },
            ],
            details=[
                f"Top consumer: {top_consumer_name}",
                f"Instrument rows: {_format_count(len(report.get('rows', [])))}",
            ],
        )
    except Exception as error:
        return _chart_card("innodb_memory", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_temp_space_chart_card():
    title = "Temp Table Space Usage"
    subtitle = "InnoDB temp tablespace bytes against the configured temp RAM ceiling."
    try:
        temp_summary = fetch_monitoring_temp_tablespace_summary()
        temp_settings = _report_row_map(fetch_monitoring_temp_storage_usage(), "setting_name", "setting_value")
        temp_table_report = _safe_report(fetch_monitoring_temp_table_usage)
        temp_bytes = _extract_numeric(temp_summary.get("temp_bytes"), 0) or 0
        configured_max_ram = _extract_numeric(temp_settings.get("temptable_max_ram"), 0) or 0
        temp_table_count = len(temp_table_report.get("rows", [])) if not temp_table_report.get("error") else 0
        details = [
            f"Active temp tables: {_format_count(temp_table_count)}",
            f"innodb_temp_data_file_path: {temp_settings.get('innodb_temp_data_file_path') or '-'}",
        ]
        if temp_summary.get("source"):
            source_label = temp_summary["source"]
            if temp_summary.get("estimated"):
                source_label += " (estimated)"
            details.append(f"Temp space source: {source_label}")
        return _chart_card(
            "temp_space",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "temp_space_bytes",
                    "label": "Temp Tablespace Bytes",
                    "color": "#2a9d8f",
                    "value": temp_bytes,
                    "display": _format_bytes(temp_bytes),
                },
                {
                    "key": "temptable_max_ram",
                    "label": "Temp Max RAM",
                    "color": "#264653",
                    "value": configured_max_ram,
                    "display": _format_bytes(configured_max_ram),
                },
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("temp_space", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_binlog_relay_chart_card():
    title = "Binlog and Relay Log Usage"
    subtitle = "Current binary log footprint and relay log space from replica channels."
    try:
        binlog_summary = fetch_show_binary_logs_summary()
        replica_status_error = ""
        try:
            replica_rows = fetch_replica_status_rows()
        except Exception as error:
            replica_rows = []
            replica_status_error = str(error)
        fallback_channels = fetch_replication_channel_lag_rows() if not replica_rows else []
        binlog_bytes = _extract_numeric(binlog_summary.get("total_bytes"), 0) or 0
        relay_bytes = sum(_extract_numeric(row.get("Relay_Log_Space"), 0) or 0 for row in replica_rows)
        channel_count = len(replica_rows) or len(fallback_channels)
        details = [
            f"Binary log files: {_format_count(binlog_summary.get('file_count', 0))}",
            f"Replica channels: {_format_count(channel_count)}",
        ]
        if replica_status_error and fallback_channels:
            details.append("Relay log bytes unavailable from SHOW REPLICA STATUS; channel count uses Performance Schema.")
        return _chart_card(
            "binlog_relay",
            title,
            subtitle,
            "timeseries",
            unit="bytes",
            series=[
                {
                    "key": "binlog_bytes",
                    "label": "Binlog Bytes",
                    "color": "#6d597a",
                    "value": binlog_bytes,
                    "display": _format_bytes(binlog_bytes),
                },
                {
                    "key": "relay_log_bytes",
                    "label": "Relay Log Bytes",
                    "color": "#e76f51",
                    "value": relay_bytes,
                    "display": _format_bytes(relay_bytes),
                },
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("binlog_relay", title, subtitle, "timeseries", unit="bytes", error=str(error))


def build_monitoring_replication_latency_chart_card():
    title = "Replication Channel Latency"
    subtitle = "Current lag per replica channel."
    try:
        channels = fetch_replication_channel_lag_rows()
        bars = [
            {
                "label": row["label"],
                "value": row["lag_ms"],
                "display": _format_milliseconds(row["lag_ms"]),
                "color": "#457b9d",
            }
            for row in channels[:12]
        ]
        details = []
        if channels:
            max_lag_ms = max(row["lag_ms"] for row in channels)
            details.append(f"Max lag: {_format_milliseconds(max_lag_ms)}")
            details.append(f"Channels: {_format_count(len(channels))}")
        else:
            details.append("No replica channels were returned.")
        return _chart_card(
            "replication_latency",
            title,
            subtitle,
            "bars",
            unit="ms",
            bars=bars,
            details=details,
        )
    except Exception as error:
        return _chart_card("replication_latency", title, subtitle, "bars", unit="ms", error=str(error))


def build_heatwave_load_state_chart_card():
    title = "HeatWave Load State"
    subtitle = "Loaded, partial, and not-loaded HeatWave tables."
    try:
        distribution = fetch_heatwave_load_distribution()
        return _chart_card(
            "heatwave_load_state",
            title,
            subtitle,
            "bars",
            unit="count",
            bars=[
                {
                    "label": "Loaded (100%)",
                    "value": distribution["loaded"],
                    "display": _format_count(distribution["loaded"]),
                    "color": "#2a9d8f",
                },
                {
                    "label": "Partial (>0 <100%)",
                    "value": distribution["partial"],
                    "display": _format_count(distribution["partial"]),
                    "color": "#f4a261",
                },
                {
                    "label": "Not Loaded (0%)",
                    "value": distribution["not_loaded"],
                    "display": _format_count(distribution["not_loaded"]),
                    "color": "#e76f51",
                },
            ],
            details=[
                f"Tracked tables: {_format_count(distribution['total_tables'])}",
                f"Source field: {distribution['source']}",
            ],
        )
    except Exception as error:
        return _chart_card("heatwave_load_state", title, subtitle, "bars", unit="count", error=str(error))


def build_heatwave_node_memory_chart_card():
    title = "HeatWave Node Memory"
    subtitle = "Current MEMORY_USAGE by HeatWave node from performance_schema.rpd_nodes."
    try:
        rows = fetch_heatwave_node_memory_rows()
        total_used_bytes = sum(row["memory_usage_bytes"] for row in rows)
        total_capacity_bytes = sum((row["memory_total_bytes"] or 0) for row in rows)
        total_baserel_bytes = sum((row["baserel_memory_usage_bytes"] or 0) for row in rows)
        unavailable_nodes = [
            row for row in rows if row["status"] and not str(row["status"]).strip().upper().startswith("AVAIL_")
        ]
        highest_node = max(rows, key=lambda item: item["memory_usage_bytes"], default=None)

        details = [
            f"Nodes returned: {_format_count(len(rows))}",
            "Source: performance_schema.rpd_nodes",
        ]
        if total_capacity_bytes > 0:
            cluster_usage_pct = (total_used_bytes / total_capacity_bytes) * 100.0
            details.append(
                f"Cluster memory usage: {_format_bytes(total_used_bytes)} of {_format_bytes(total_capacity_bytes)} ({cluster_usage_pct:.1f}%)"
            )
        else:
            details.append(f"Cluster memory usage: {_format_bytes(total_used_bytes)}")
        if total_baserel_bytes > 0:
            details.append(f"Base relation memory usage: {_format_bytes(total_baserel_bytes)}")
        if highest_node:
            details.append(
                f"Top node: {highest_node['label']} at {_format_bytes(highest_node['memory_usage_bytes'])}"
            )
        if unavailable_nodes:
            details.append(f"Nodes with non-AVAIL status: {_format_count(len(unavailable_nodes))}")
        else:
            details.append("All node statuses are AVAIL_.")

        return _chart_card(
            "heatwave_node_memory",
            title,
            subtitle,
            "bars",
            unit="bytes",
            bars=[
                {
                    "label": row["label"],
                    "value": row["memory_usage_bytes"],
                    "display": (
                        f"{_format_bytes(row['memory_usage_bytes'])} of {_format_bytes(row['memory_total_bytes'])} "
                        f"({(row['memory_usage_bytes'] / row['memory_total_bytes']) * 100.0:.1f}%)"
                        if row["memory_total_bytes"]
                        else _format_bytes(row["memory_usage_bytes"])
                    ),
                    "color": "#2a9d8f"
                    if str(row["status"]).strip().upper().startswith("AVAIL_")
                    else "#e76f51",
                }
                for row in rows
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("heatwave_node_memory", title, subtitle, "bars", unit="bytes", error=str(error))


def build_heatwave_query_timing_chart_card():
    title = "HeatWave Query Timing"
    subtitle = "Recent queue, execution, wait, and RPD timings from performance_schema.rpd_query_stats."
    try:
        summary = fetch_heatwave_query_timing_summary()
        details = [
            f"Recent samples: {_format_count(summary['sample_count'])}",
            "Source: performance_schema.rpd_query_stats",
        ]
        if summary["latest_query_id"] not in (None, ""):
            details.append(f"Latest query id: {summary['latest_query_id']}")
        if summary["latest_connection_id"] is not None:
            details.append(f"Latest connection id: {_format_count(summary['latest_connection_id'])}")
        if summary["latest_query_text"]:
            details.append(f"Latest query: {summary['latest_query_text']}")
        if summary["latest_act_rows"] is not None:
            details.append(f"Latest actual rows: {_format_count(summary['latest_act_rows'])}")
        if summary["latest_elapsed_ms"] is not None:
            details.append(f"Latest end-to-end: {_format_milliseconds(summary['latest_elapsed_ms'])}")
        details.append(f"Avg change propagation: {_format_milliseconds(summary['avg_change_propagation_ms'])}")
        details.append(f"Avg get results: {_format_milliseconds(summary['avg_get_result_ms'])}")
        details.append(f"Peak queue wait: {_format_milliseconds(summary['max_queue_wait_ms'])}")
        details.append(f"Peak total execution: {_format_milliseconds(summary['max_total_exec_ms'])}")
        details.append(f"Peak total wait: {_format_milliseconds(summary['max_total_wait_ms'])}")
        details.append(f"Peak RPD execution: {_format_milliseconds(summary['max_rpd_exec_ms'])}")
        details.append(f"Peak optimization: {_format_milliseconds(summary['max_total_opt_ms'])}")
        details.append(f"Peak get results: {_format_milliseconds(summary['max_get_result_ms'])}")
        details.append(f"Peak change propagation: {_format_milliseconds(summary['max_change_propagation_ms'])}")
        return _chart_card(
            "heatwave_query_timing",
            title,
            subtitle,
            "timeseries",
            unit="ms",
            series=[
                {
                    "key": "avg_queue_wait_ms",
                    "label": "Avg Queue Wait",
                    "color": "#6d597a",
                    "value": summary["avg_queue_wait_ms"],
                    "display": _format_milliseconds(summary["avg_queue_wait_ms"]),
                },
                {
                    "key": "avg_total_exec_ms",
                    "label": "Avg Total Exec",
                    "color": "#b56576",
                    "value": summary["avg_total_exec_ms"],
                    "display": _format_milliseconds(summary["avg_total_exec_ms"]),
                },
                {
                    "key": "avg_total_wait_ms",
                    "label": "Avg Total Wait",
                    "color": "#355070",
                    "value": summary["avg_total_wait_ms"],
                    "display": _format_milliseconds(summary["avg_total_wait_ms"]),
                },
                {
                    "key": "avg_total_opt_ms",
                    "label": "Avg Optimization",
                    "color": "#a68a64",
                    "value": summary["avg_total_opt_ms"],
                    "display": _format_milliseconds(summary["avg_total_opt_ms"]),
                },
                {
                    "key": "avg_rpd_exec_ms",
                    "label": "Avg RPD Exec",
                    "color": "#2a9d8f",
                    "value": summary["avg_rpd_exec_ms"],
                    "display": _format_milliseconds(summary["avg_rpd_exec_ms"]),
                },
            ],
            details=details,
        )
    except Exception as error:
        return _chart_card("heatwave_query_timing", title, subtitle, "timeseries", unit="ms", error=str(error))


def build_monitoring_chart_snapshot():
    tab_key_by_id = {
        "connections": "general",
        "locks": "general",
        "storage": "general",
        "innodb_memory": "general",
        "temp_space": "general",
        "binlog_relay": "replication",
        "replication_latency": "replication",
        "heatwave_load_state": "heatwave",
        "heatwave_node_memory": "heatwave",
        "heatwave_query_timing": "heatwave",
    }
    cards = [
        build_monitoring_connections_chart_card(),
        build_monitoring_locks_chart_card(),
        build_monitoring_storage_chart_card(),
        build_monitoring_innodb_memory_chart_card(),
        build_monitoring_temp_space_chart_card(),
        build_monitoring_binlog_relay_chart_card(),
        build_monitoring_replication_latency_chart_card(),
        build_heatwave_load_state_chart_card(),
        build_heatwave_node_memory_chart_card(),
        build_heatwave_query_timing_chart_card(),
    ]
    for card in cards:
        card["tab_key"] = tab_key_by_id.get(card.get("id"), "general")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cards": cards,
    }


def build_monitoring_dashboard_context():
    global_status = _safe_report(fetch_monitoring_global_status)
    user_processes = _safe_report(fetch_monitoring_user_processlist)
    current_connections = _safe_report(fetch_monitoring_current_connections)
    innodb_memory = _safe_report(fetch_monitoring_innodb_memory_usage)
    innodb_storage = _safe_report(fetch_monitoring_innodb_storage_usage)
    temp_storage = _safe_report(fetch_monitoring_temp_storage_usage)
    temp_tables = _safe_report(fetch_monitoring_temp_table_usage)
    replication_connection = _safe_report(fetch_monitoring_replication_connection_status)
    replication_applier = _safe_report(fetch_monitoring_replication_applier_coordinator)
    replication_workers = _safe_report(fetch_monitoring_replication_applier_workers)

    global_status_map = {
        row["metric_name"]: row["metric_value"]
        for row in global_status.get("rows", [])
        if row.get("metric_name") is not None
    }
    metrics = [
        {
            "label": "User Processes",
            "value": len(user_processes.get("rows", [])) if not user_processes.get("error") else "-",
            "subtitle": "Top 100 non-system processlist rows",
        },
        {
            "label": "Current Connections",
            "value": global_status_map.get("Threads_connected", "-"),
            "subtitle": f"Threads running: {global_status_map.get('Threads_running', '-')}",
        },
        {
            "label": "InnoDB Memory",
            "value": _format_bytes(_sum_report_column(innodb_memory, "current_bytes")),
            "subtitle": "Total current bytes from memory/innodb instruments",
        },
        {
            "label": "Temp Disk Tables",
            "value": global_status_map.get("Created_tmp_disk_tables", "-"),
            "subtitle": f"Created tmp tables: {global_status_map.get('Created_tmp_tables', '-')}",
        },
        {
            "label": "InnoDB Storage",
            "value": _format_bytes(_sum_report_column(innodb_storage, "total_bytes")),
            "subtitle": "Summed across InnoDB schemas",
        },
        {
            "label": "Replica Channels",
            "value": len(replication_connection.get("rows", [])) if not replication_connection.get("error") else "-",
            "subtitle": "performance_schema replication_connection_status",
        },
    ]

    return {
        "metrics": metrics,
        "global_status": global_status,
        "user_processes": user_processes,
        "current_connections": current_connections,
        "innodb_memory": innodb_memory,
        "innodb_storage": innodb_storage,
        "temp_storage": temp_storage,
        "temp_tables": temp_tables,
        "replication_connection": replication_connection,
        "replication_applier": replication_applier,
        "replication_workers": replication_workers,
    }


def build_monitoring_locks_context(
    *,
    row_lock_schema="",
    row_lock_table="",
    row_blocking_connection_id=None,
    row_waiting_connection_id=None,
    mdl_schema="",
    mdl_name="",
    mdl_owner_connection_id=None,
    lock_focus="row",
):
    row_lock_schema = str(row_lock_schema or "").strip()
    row_lock_table = str(row_lock_table or "").strip()
    row_blocking_connection_id = _coerce_int(row_blocking_connection_id)
    row_waiting_connection_id = _coerce_int(row_waiting_connection_id)
    mdl_schema = str(mdl_schema or "").strip()
    mdl_name = str(mdl_name or "").strip()
    mdl_owner_connection_id = _coerce_int(mdl_owner_connection_id)
    lock_focus = str(lock_focus or "row").strip().lower()
    if lock_focus not in {"row", "meta"}:
        lock_focus = "row"

    row_locks = _safe_report(fetch_monitoring_lock_waits)
    metadata_locks = _safe_report(fetch_monitoring_metadata_locks)
    row_lock_source = _empty_report()
    row_lock_source_process = _empty_report()
    row_lock_impacted = _empty_report()
    row_lock_impacted_process = _empty_report()
    metadata_lock_source = _empty_report()
    metadata_lock_source_process = _empty_report()
    metadata_lock_impacted = _empty_report()

    if row_lock_schema and row_lock_table and row_blocking_connection_id is not None:
        row_lock_source = _safe_report(
            fetch_monitoring_row_lock_source_detail,
            row_lock_schema,
            row_lock_table,
            row_blocking_connection_id,
        )
        row_lock_source_process = _safe_report(fetch_monitoring_process_connection_detail, row_blocking_connection_id)

    if row_lock_schema and row_lock_table and row_waiting_connection_id is not None:
        row_lock_impacted = _safe_report(
            fetch_monitoring_row_lock_impacted_detail,
            row_lock_schema,
            row_lock_table,
            row_waiting_connection_id,
        )
        row_lock_impacted_process = _safe_report(fetch_monitoring_process_connection_detail, row_waiting_connection_id)

    if mdl_schema and mdl_name and mdl_owner_connection_id is not None:
        metadata_lock_source = _safe_report(
            fetch_monitoring_metadata_source_detail,
            mdl_schema,
            mdl_name,
            mdl_owner_connection_id,
        )
        metadata_lock_source_process = _safe_report(fetch_monitoring_process_connection_detail, mdl_owner_connection_id)

    if mdl_schema and mdl_name:
        metadata_lock_impacted = _safe_report(fetch_monitoring_metadata_impacted_detail, mdl_schema, mdl_name)

    return {
        "lock_focus": lock_focus,
        "row_locks": row_locks,
        "metadata_locks": metadata_locks,
        "row_lock_source": row_lock_source,
        "row_lock_source_process": row_lock_source_process,
        "row_lock_impacted": row_lock_impacted,
        "row_lock_impacted_process": row_lock_impacted_process,
        "metadata_lock_source": metadata_lock_source,
        "metadata_lock_source_process": metadata_lock_source_process,
        "metadata_lock_impacted": metadata_lock_impacted,
        "selected_row_lock_schema": row_lock_schema,
        "selected_row_lock_table": row_lock_table,
        "selected_row_blocking_connection_id": row_blocking_connection_id,
        "selected_row_waiting_connection_id": row_waiting_connection_id,
        "selected_mdl_schema": mdl_schema,
        "selected_mdl_name": mdl_name,
        "selected_mdl_owner_connection_id": mdl_owner_connection_id,
    }
