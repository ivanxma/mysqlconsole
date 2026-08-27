import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class UiActionContractTests(unittest.TestCase):
    def test_status_variables_and_mysqlsh_catalog_receive_distinct_query_dependencies(self):
        app_source = (ROOT_DIR / "app.py").read_text(encoding="utf-8")
        admin_block = app_source.split("register_admin_routes(", 1)[1].split("register_update_routes(", 1)[0]
        mysqlsh_config_block = app_source.split("register_mysqlsh_configuration_routes(", 1)[1].split("register_db_admin_routes(", 1)[0]
        self.assertIn('"execute_query": execute_query', admin_block)
        self.assertNotIn('"mysql_connection": mysql_connection', admin_block)
        self.assertIn('"mysql_connection": mysql_connection', mysqlsh_config_block)

    def test_shared_dashboard_renderer_does_not_fetch_expensive_server_overview(self):
        app_source = (ROOT_DIR / "app.py").read_text(encoding="utf-8")
        render_block = app_source.split("def render_dashboard(", 1)[1].split("register_auth_routes(", 1)[0]
        self.assertNotIn("fetch_server_overview()", render_block)
        dashboard_routes = (ROOT_DIR / "modules" / "dashboard_routes.py").read_text(encoding="utf-8")
        self.assertIn('sections={dashboard_tab}', dashboard_routes)

    def test_shared_icon_contract_has_target_focus_and_danger_states(self):
        stylesheet = (ROOT_DIR / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("Shared action-control contract", stylesheet)
        self.assertIn("min-width: 44px", stylesheet)
        self.assertIn(".chart-icon-button:focus-visible", stylesheet)
        self.assertIn(".icon-button.danger", stylesheet)

    def test_delete_event_uses_the_danger_variant(self):
        template = (ROOT_DIR / "templates" / "db_admin.html").read_text(encoding="utf-8")
        self.assertIn('class="icon-button danger db-admin-event-action"', template)

    def test_destructive_actions_use_the_shared_confirmation_dialog(self):
        base = (ROOT_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        db_admin = (ROOT_DIR / "templates" / "db_admin.html").read_text(encoding="utf-8")
        mysql_import = (ROOT_DIR / "templates" / "mysql_import.html").read_text(encoding="utf-8")
        self.assertIn('id="destructive-action-dialog"', base)
        self.assertIn('class="button primary" type="button" data-destructive-action-confirm', base)
        self.assertNotIn('class="button danger"', base)
        self.assertIn("data-confirm-message", db_admin)
        self.assertIn("syncReplaceConfirmation", mysql_import)
        self.assertNotIn("confirm(", db_admin)
        self.assertNotIn("confirm(", mysql_import)

    def test_modify_columns_exposes_ordered_primary_key_definition_controls(self):
        db_admin = (ROOT_DIR / "templates" / "db_admin.html").read_text(encoding="utf-8")
        self.assertIn("Primary Key Definition", db_admin)
        self.assertIn('name="primary_key_column"', db_admin)
        self.assertIn('name="primary_key_position"', db_admin)
        self.assertIn('name="primary_key_definition_present"', db_admin)
        self.assertIn("Define primary key", db_admin)


if __name__ == "__main__":
    unittest.main()
