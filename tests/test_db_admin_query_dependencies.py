import unittest
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from modules import db_admin_queries, mysql_pages


class DbAdminQueryDependencyTests(unittest.TestCase):
    def setUp(self):
        self.statements = []

        def execute_query(_sql, _params=None):
            return [
                {
                    "database_name_value": "mydb01",
                    "table_name_value": "employees01",
                    "engine_value": "InnoDB",
                    "table_rows_value": 1,
                    "has_primary_key_value": 0,
                    "auto_increment_column_name_value": "employee_id",
                    "has_my_row_id_value": 0,
                }
            ]

        db_admin_queries.configure_db_admin_queries(
            execute_query=execute_query,
            execute_statement=lambda sql, **_kwargs: self.statements.append(sql),
            fetch_scalar=lambda *_args, **_kwargs: None,
            fetch_table_column_lookup=lambda *_args, **_kwargs: {},
            get_event_schedule_option=lambda *_args, **_kwargs: {},
            quote_identifier=lambda value: "`" + str(value).replace("`", "``") + "`",
            quote_sql_string=lambda value: str(value),
            mysql_connection=lambda *_args, **_kwargs: None,
            is_system_schema_name=lambda value: str(value).strip().lower() in {
                "information_schema",
                "mysql",
                "performance_schema",
                "sys",
            },
            db_admin_preview_masked_base_types=set(),
        )

    def test_primary_key_fix_uses_injected_system_schema_guard(self):
        with self.assertRaisesRegex(ValueError, "System schemas"):
            db_admin_queries.fix_table_without_primary_key("mysql", "user")
        self.assertEqual(self.statements, [])

    def test_primary_key_fix_executes_for_application_schema(self):
        result = db_admin_queries.fix_table_without_primary_key("mydb01", "employees01")

        self.assertEqual(result["status"], "fixed")
        self.assertEqual(result["strategy"], "use_auto_increment")
        self.assertEqual(
            self.statements,
            ["ALTER TABLE `mydb01`.`employees01` ADD PRIMARY KEY (`employee_id`)"],
        )

    def test_lakehouse_primary_key_fix_requires_manual_column_selection(self):
        status = {
            "engine": "Lakehouse", "has_primary_key": False,
            "auto_increment_column_name": "", "has_my_row_id": False,
        }
        with patch.object(db_admin_queries, "fetch_table_primary_key_status", return_value=status), self.assertRaisesRegex(
            ValueError, "manually selected primary key"
        ):
            db_admin_queries.fix_table_without_primary_key("mydb01", "lake_table")
        self.assertEqual(self.statements, [])

    def test_lakehouse_missing_primary_key_report_is_manual_only(self):
        report = mysql_pages._build_missing_primary_key_report(
            [{
                "database_name": "mydb01", "table_name": "lake_table", "engine": "Lakehouse",
                "row_count": 10, "auto_increment_column_name": "", "has_my_row_id": False,
            }]
        )
        row = report["rows"][0]
        self.assertFalse(row["is_fixable"])
        self.assertEqual(row["fix_method"], "manual_review")
        self.assertNotIn("my_row_id", row["fix_method_label"])
        self.assertEqual(report["invisible_row_id_fix_count"], 0)

    def test_primary_key_definition_supports_composite_order_and_renamed_column(self):
        columns = [
            {"column_name": "account_id", "column_key": "PRI"},
            {"column_name": "region_id", "column_key": ""},
            {"column_name": "payload", "column_key": ""},
        ]
        indexes = [{"index_name": "PRIMARY", "columns": ["account_id"]}]
        payload = MultiDict([
            ("primary_key_definition_present", "1"),
            ("primary_key_column", "account_id"), ("primary_key_column", "region_id"),
            ("primary_key_source_order", "account_id"), ("primary_key_source_order", "region_id"),
            ("primary_key_source_order", "payload"),
            ("primary_key_position", "2"), ("primary_key_position", "1"),
            ("primary_key_position", "3"),
        ])
        clauses, selected = mysql_pages._build_db_admin_primary_key_clauses(
            columns,
            indexes,
            payload,
            {"account_id": "account_id", "region_id": "region_code", "payload": "payload"},
            quote_identifier=lambda value: f"`{value}`",
        )
        self.assertEqual(selected, ["region_code", "account_id"])
        self.assertEqual(clauses, ["DROP PRIMARY KEY", "ADD PRIMARY KEY (`region_code`, `account_id`)"])


if __name__ == "__main__":
    unittest.main()
