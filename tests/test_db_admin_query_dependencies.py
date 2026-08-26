import unittest

from modules import db_admin_queries


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


if __name__ == "__main__":
    unittest.main()
