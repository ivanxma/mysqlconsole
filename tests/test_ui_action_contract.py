import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class UiActionContractTests(unittest.TestCase):
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
        self.assertIn("data-confirm-message", db_admin)
        self.assertIn("syncReplaceConfirmation", mysql_import)
        self.assertNotIn("confirm(", db_admin)
        self.assertNotIn("confirm(", mysql_import)


if __name__ == "__main__":
    unittest.main()
