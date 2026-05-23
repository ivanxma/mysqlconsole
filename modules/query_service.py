import csv
import io
import re

from flask import Response


IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$]+$")


def quote_identifier(identifier):
    candidate = str(identifier or "").strip()
    if not IDENTIFIER_RE.fullmatch(candidate):
        raise ValueError(f"Invalid identifier: {candidate!r}")
    return f"`{candidate}`"


def quote_sql_string(value):
    return "'" + str(value or "").replace("\\", "\\\\").replace("'", "''") + "'"


class DatabaseQueryService:
    def __init__(self, *, mysql_connection, apply_query_session_options):
        self.mysql_connection = mysql_connection
        self.apply_query_session_options = apply_query_session_options

    def execute_query(self, sql, params=None, *, database=None, use_secondary_engine=""):
        with self.mysql_connection(database_override=database) as connection:
            with connection.cursor() as cursor:
                self.apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
                if params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, params)
                return cursor.fetchall()

    def execute_multi_result_query(self, sql, params=None, *, database=None, use_secondary_engine=""):
        result_sets = []
        with self.mysql_connection(database_override=database) as connection:
            with connection.cursor() as cursor:
                self.apply_query_session_options(cursor, use_secondary_engine=use_secondary_engine)
                if params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, params)

                result_index = 1
                while True:
                    columns = [item[0] for item in cursor.description] if cursor.description else []
                    rows = cursor.fetchall() if columns else []
                    if columns or rows:
                        result_sets.append(
                            {
                                "label": f"Result {result_index}",
                                "columns": columns,
                                "rows": rows,
                            }
                        )
                        result_index += 1
                    if not cursor.nextset():
                        break
        return result_sets

    def execute_statement(self, sql, params=None, *, database=None):
        with self.mysql_connection(database_override=database) as connection:
            with connection.cursor() as cursor:
                if params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, params)
                rowcount = cursor.rowcount
                while cursor.nextset():
                    pass
                return rowcount

    def fetch_scalar(self, sql, params=None, *, database=None, default=None):
        rows = self.execute_query(sql, params=params, database=database)
        if not rows:
            return default
        return next(iter(rows[0].values()))

    def fetch_database_exists(self, database_name):
        if not database_name:
            return False
        return bool(
            self.fetch_scalar(
                """
                SELECT COUNT(*) AS database_count_value
                FROM information_schema.schemata
                WHERE schema_name = %s
                """,
                [database_name],
                default=0,
            )
        )

    def fetch_table_exists(self, database_name, table_name):
        if not database_name or not table_name:
            return False
        return bool(
            self.fetch_scalar(
                """
                SELECT COUNT(*) AS table_count_value
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_name = %s
                """,
                [database_name, table_name],
                default=0,
            )
        )


def build_csv_response(filename, columns, rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in rows:
        if isinstance(row, dict):
            writer.writerow([row.get(column, "") for column in columns])
        else:
            writer.writerow(list(row))
    return Response(
        stream.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
