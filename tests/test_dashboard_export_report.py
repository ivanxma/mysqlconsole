import unittest
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from modules import dashboard_queries


class DashboardExportReportTests(unittest.TestCase):
    def test_innodb_inventory_includes_rows_and_size_labels(self):
        database_row = {
            "database_name_value": "mydb01",
            "table_name_value": "employees01",
            "engine_value": "InnoDB",
            "table_rows_value": 25,
            "data_bytes_value": 2048,
            "index_bytes_value": 1024,
            "total_bytes_value": 3072,
        }
        with patch.object(dashboard_queries, "execute_query", return_value=[database_row]) as execute_query:
            rows = dashboard_queries.fetch_dashboard_innodb_table_rows()

        self.assertIn("data_length", execute_query.call_args.args[0])
        self.assertIn("index_length", execute_query.call_args.args[0])
        self.assertEqual(rows[0]["table_rows"], 25)
        self.assertEqual(rows[0]["data_size_label"], "2.0 KiB")
        self.assertEqual(rows[0]["index_size_label"], "1.0 KiB")
        self.assertEqual(rows[0]["total_size_label"], "3.0 KiB")

    def test_export_log_limit_is_capped_at_two_thousand(self):
        with (
            patch.object(dashboard_queries, "fetch_scalar", side_effect=["9.0", "db-host"]),
            patch.object(dashboard_queries, "fetch_recent_error_log_rows", return_value=[]) as fetch_logs,
            patch.object(dashboard_queries, "empty_replication_overview_info", return_value={}),
            patch.object(dashboard_queries, "get_session_profile", return_value={"host": "db", "port": 3306}),
        ):
            dashboard_queries.fetch_server_overview(
                recent_error_log_limit=5000,
                sections={"logs"},
            )

        self.assertEqual(fetch_logs.call_args.kwargs["limit"], 2000)
        self.assertEqual(fetch_logs.call_args.kwargs["priorities"], [])

    def test_export_template_contract(self):
        root = Path(__file__).resolve().parents[1]
        Environment(loader=FileSystemLoader(root / "templates")).get_template("mysql_dashboard_export.html")
        source = (root / "templates" / "mysql_dashboard_export.html").read_text(encoding="utf-8")
        route_source = (root / "modules" / "dashboard_routes.py").read_text(encoding="utf-8")

        self.assertIn("DASHBOARD_EXPORT_ERROR_LOG_LIMIT = 2000", route_source)
        self.assertIn('data-report-section="global-variables"', source)
        self.assertIn('data-report-section="global-status"', source)
        self.assertIn('data-report-section="error-log"', source)
        self.assertNotIn('data-report-section="global-variables" open', source)
        self.assertIn("Exclude Note and System", source)
        self.assertIn('<option value="all">ALL</option>', source)
        self.assertIn("data-error-log-row", source)
        self.assertIn("<h2>Events</h2>", source)
        self.assertGreater(source.index("<h2>Events</h2>"), source.index("<h2>Stored Procedures / Functions</h2>"))

    def test_lakehouse_template_exposes_folder_population(self):
        root = Path(__file__).resolve().parents[1]
        Environment(loader=FileSystemLoader(root / "templates")).get_template("heatwave_external_lakehouse.html")
        source = (root / "templates" / "heatwave_external_lakehouse.html").read_text(encoding="utf-8")
        self.assertIn('name="populate_folders" value="1"', source)
        self.assertIn('params.set("populate_folders", "1")', source)


if __name__ == "__main__":
    unittest.main()
