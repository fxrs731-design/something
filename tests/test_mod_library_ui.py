import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from main import create_app_context


class TestModLibraryUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_mod_library_ui_"))
        self.ctx = create_app_context(self.tmp / "launcher-data")
        package = self.tmp / "season-pack.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({
                    "name": "2010 Helmet Pack",
                    "game": "F1 25",
                    "season": 2010,
                    "category": "Helmets",
                    "version": "1.0",
                    "author": "UI Test",
                }),
            )
            archive.writestr("asset_groups/helmets/helmet.erp", b"asset")
        self.mod = self.ctx["mod_library"].import_package(package)["mod"]
        self.ctx["mod_library"].add_mod_to_profile("f1classic_2010", self.mod["id"], enabled=False)

    def _wait_for_idle(self, window):
        deadline = time.monotonic() + 10
        while window.operation_worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertIsNone(window.operation_worker, "Background UI operation did not finish.")

    def test_library_and_profiles_are_native_pages_and_report_truthful_mod_states(self):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            from ui.desktop_app import F1ClassicLauncherWindow

            window = F1ClassicLauncherWindow(self.ctx)
            self._wait_for_idle(window)
            window.switch_tab(6)
            self.assertEqual(window.stack.currentWidget(), window.page_mod_library)
            self.assertIn("Applied: No", window.mod_library_status.text())
            self.assertIn("Verified: No", window.mod_library_status.text())
            library_item = window.mod_library_list.currentItem()

            window.switch_tab(7)
            self.assertEqual(window.stack.currentWidget(), window.page_profiles)
            self.assertIn("Library only", window.managed_profile_combo.currentText())
            self.assertIn("Disabled", window.profile_contents_list.item(0).text())
            self.assertIn("Applied: No", window.profile_contents_list.item(0).text())
            self.assertTrue(window.enable_profile_mod_button.isEnabled())
            window._set_selected_profile_mod_enabled(True)
            self._wait_for_idle(window)
            self.assertTrue(self.ctx["mod_library"].get_mod_status(self.mod["id"], "f1classic_2010")["enabled"])
            self.assertFalse(self.ctx["mod_library"].get_mod_status(self.mod["id"], "f1classic_2012")["enabled"])
            self.assertIs(window.mod_library_list.currentItem(), library_item)
            self.assertIn("Enabled in selected profile: Yes", window.mod_library_status.text())
            window.close()

    def test_package_preview_displays_inspected_file_contents(self):
        from PySide6.QtWidgets import QPlainTextEdit
        from ui.desktop_app import ModPackagePreviewDialog

        preview = self.ctx["mod_library"].inspect_package(self.tmp / "season-pack.zip")
        dialog = ModPackagePreviewDialog(preview)

        contents = dialog.findChild(QPlainTextEdit)
        self.assertIsNotNone(contents)
        self.assertIn("asset_groups/helmets/helmet.erp", contents.toPlainText())


if __name__ == "__main__":
    unittest.main()
