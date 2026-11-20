import json
import os
import py_compile
import sys
import threading
import time
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import _migrate_legacy_user_data, create_app_context
from core.game_process_monitor import GameProcessMonitor
from core.swofty_bridge import SwoftyBridge


class FakeSwoftyBridge:
    def __init__(self):
        self.addons = {}
        self.imported_files = []
        self.launches = []
        self.commands = []
        self.next_addon = 1
        self.imported_source_ids = {}
        self.import_count = 0
        self.imported_payloads = []
        self.fail_import = False

    @staticmethod
    def content_fingerprint(directory):
        return SwoftyBridge.content_fingerprint(directory)

    def has_addons(self, addon_ids):
        return bool(addon_ids) and all(addon_id in self.addons for addon_id in addon_ids)

    def import_mods(self, source, game_dir, profile, known_addon_ids=None):
        self.import_count += 1
        if self.fail_import:
            return {"ok": False, "error": "simulated conversion failure"}
        files = sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file())
        self.imported_files.append(files)
        self.imported_payloads.append({
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file()
        })
        if source.name not in self.imported_source_ids:
            addon_id = f"test.mod.{self.next_addon}"
            self.next_addon += 1
            self.imported_source_ids[source.name] = addon_id
        addon_id = self.imported_source_ids[source.name]
        self.addons.setdefault(addon_id, False)
        return {"ok": True, "addon_ids": [addon_id]}

    def install_addon_package(self, package_dir, addon_id, profile):
        self.commands.append(("install", addon_id))
        self.addons.setdefault(addon_id, False)
        return {"ok": True}

    def ensure_watermark_addon(self, profile):
        self.addons["f1classic.launcher"] = True
        self.commands.append(("enable", "f1classic.launcher"))
        return {"ok": True}

    def set_addon_enabled(self, addon_id, enabled, profile):
        self.addons[addon_id] = enabled
        self.commands.append(("enable" if enabled else "disable", addon_id))
        return {"ok": True}

    def reorder_addons(self, addon_ids, previous_order=None, profile="default"):
        self.commands.append(("order", tuple(addon_ids), tuple(previous_order or [])))
        return {"ok": True}

    def check_conflicts(self, game_dir, profile):
        self.commands.append(("check", str(game_dir)))
        return {"ok": True, "stdout": "No conflicts found."}

    def launch_game(self, game_dir, modded):
        self.launches.append((Path(game_dir), modded))
        return {"ok": True, "pid": 42, "profile": "default (modded)" if modded else "vanilla"}

    def get_status(self):
        return {"available": False, "native_dlls": []}


class FakeGameProcessMonitor:
    def __init__(self, running=False):
        self.running = running
        self.calls = 0

    def is_f1_25_running(self):
        self.calls += 1
        return self.running


class TestF1ClassicLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_test_"))
        self.app_dir = self.tmp / "launcher_data"
        self.ctx = create_app_context(self.app_dir)
        self.bridge = FakeSwoftyBridge()
        self.ctx["mod_installer"].swofty_bridge = self.bridge
        self.ctx["game_detector"].swofty_bridge = self.bridge

        self.game_25 = self.tmp / "Games" / "F1 25"
        self.game_25.mkdir(parents=True)
        (self.game_25 / "F1_25.exe").write_text("MOCK_EXE_25", encoding="utf-8")
        (self.game_25 / "asset_groups").mkdir()
        (self.game_25 / "unrelated.txt").write_text("KEEP_ME_SAFE", encoding="utf-8")
        self.ctx["config"].set_game_path("F1 25", str(self.game_25))

    def test_swofty_mod_profiles_are_separate_for_f1_25_seasons(self):
        installer = self.ctx["mod_installer"]
        bridge = MagicMock()
        bridge.ensure_simplified_importer.return_value = {"ok": True}
        bridge.list_simplified_mods.side_effect = [
            {"ok": True, "mods": [{"id": "mod.2010", "enabled": False}], "addons": {}},
            {"ok": True, "mods": [{"id": "mod.2012", "enabled": True}], "addons": {}},
        ]
        installer.swofty_bridge = bridge

        profile_2010 = installer.get_swofty_mod_profile(2010)
        profile_2012 = installer.get_swofty_mod_profile(2012)

        self.assertEqual(profile_2010["profile"], "f1classic_2010")
        self.assertEqual(profile_2012["profile"], "f1classic_2012")
        self.assertEqual(
            [call.args for call in bridge.ensure_simplified_importer.call_args_list],
            [("f1classic_2010",), ("f1classic_2012",)],
        )

    def test_swofty_mod_import_uses_official_importer_and_persists_profile_selection(self):
        installer = self.ctx["mod_installer"]
        bridge = MagicMock()
        bridge.import_mods.return_value = {"ok": True, "addon_ids": ["mod.carpack"]}
        installer.swofty_bridge = bridge
        archive = self.tmp / "F1 25 mod.7z"
        archive.write_bytes(b"test archive")

        result = installer.import_swofty_mod(archive, 2012)

        self.assertTrue(result["ok"], result.get("error"))
        bridge.import_mods.assert_called_once_with(archive.resolve(), self.game_25, "f1classic_2012")
        self.assertEqual(self.ctx["config"].data["selected_swofty_profile_year"], 2012)
        self.assertEqual(result["addon_ids"], ["mod.carpack"])

    def test_swofty_addon_import_is_recorded_under_only_the_selected_season_profile(self):
        installer = self.ctx["mod_installer"]
        bridge = MagicMock()
        bridge.install_addon_from_path.return_value = {
            "ok": True,
            "addon_ids": ["sample.direct-addon"],
        }
        installer.swofty_bridge = bridge
        package = self.tmp / "sample.swoftyaddon"
        package.write_bytes(b"test package")

        result = installer.import_swofty_mod(package, 2010)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(
            self.ctx["config"].data["swofty_direct_addons"],
            {"f1classic_2010": ["sample.direct-addon"]},
        )
        self.assertEqual(
            bridge.install_addon_from_path.call_args.args,
            (package.resolve(), "f1classic_2010"),
        )

    def test_swofty_mod_state_is_retained_when_the_importer_reports_it_as_pending(self):
        installer = self.ctx["mod_installer"]
        bridge = MagicMock()
        bridge.set_simplified_mod_enabled.return_value = {"ok": True}
        bridge.ensure_simplified_importer.return_value = {"ok": True}
        bridge.list_simplified_mods.return_value = {
            "ok": True,
            "mods": [{"id": "mod.pending", "enabled": None, "state_unknown": True}],
            "addons": {},
        }
        installer.swofty_bridge = bridge

        updated = installer.set_swofty_mod_enabled(2012, "mod.pending", True, remember_unknown=True)
        profile = installer.get_swofty_mod_profile(2012)

        self.assertTrue(updated["ok"])
        self.assertEqual(profile["mods"][0]["enabled"], True)
        self.assertTrue(profile["mods"][0]["state_unknown"])
        self.assertEqual(
            self.ctx["config"].data["swofty_mod_enabled_overrides"]["f1classic_2012"]["mod.pending"],
            True,
        )

    def test_standalone_serps_and_erp_files_are_rejected_with_actionable_errors(self):
        installer = self.ctx["mod_installer"]
        bridge = MagicMock()
        installer.swofty_bridge = bridge
        for suffix in (".erp", ".serps"):
            source = self.tmp / f"orphan{suffix}"
            source.write_bytes(b"not a package")
            with self.subTest(suffix=suffix):
                result = installer.import_swofty_mod(source, 2010)
                self.assertFalse(result["ok"])
                self.assertIn("Import the original mod folder", result["error"])
        bridge.import_mods.assert_not_called()

    def _create_season_package(self, season_id, year, files):
        source = self.tmp / f"source_{season_id}"
        source.mkdir(parents=True, exist_ok=True)
        (source / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "id": season_id,
            "name": f"F1 {year} Season",
            "version": "1.0.0",
            "author": "Tester",
            "description": f"Test {year} season mod",
            "game": "F1 25",
            "season": year,
            "artwork": "",
            "operations": [{"type": "bydanix_merge", "source": "files", "target": "."}],
        }), encoding="utf-8")
        for relative_path, content in files.items():
            path = source / "files" / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return self.ctx["package_importer"].import_package(str(source))

    def test_import_validation_and_duplicate_handling(self):
        result = self._create_season_package("custom.f1-2010-test", 2010, {"cars/a.erp": "MOD"})
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(self.ctx["season_manager"].get_season("custom.f1-2010-test").manifest.game, "F1 25")

        duplicate = self._create_season_package("custom.f1-2010-test", 2010, {"cars/a.erp": "MOD2"})
        self.assertFalse(duplicate["ok"])
        self.assertTrue(duplicate.get("duplicate"))

        invalid = self.tmp / "invalid"
        invalid.mkdir()
        (invalid / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "id": "bad", "name": "Bad", "version": "1",
            "author": "A", "description": "Wrong game", "game": "F1 2010", "season": 2010,
            "artwork": "",
            "operations": [],
        }), encoding="utf-8")
        bad_result = self.ctx["package_importer"].import_package(str(invalid))
        self.assertFalse(bad_result["ok"])
        self.assertTrue(any("Only F1 25" in error for error in bad_result["errors"]))

    def test_failed_package_update_preserves_imported_copy(self):
        first = self.tmp / "update_v1"
        first.mkdir()
        (first / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "id": "safe.update", "name": "Safe Update", "version": "1.0",
            "author": "A", "description": "First", "game": "F1 25", "season": 2010,
            "artwork": "", "operations": [],
        }), encoding="utf-8")
        (first / "payload.txt").write_text("original", encoding="utf-8")
        self.assertTrue(self.ctx["package_importer"].import_package(str(first))["ok"])

        updated = self.tmp / "update_v2"
        updated.mkdir()
        (updated / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "id": "safe.update", "name": "Safe Update", "version": "2.0",
            "author": "A", "description": "Updated", "game": "F1 25", "season": 2010,
            "artwork": "", "operations": [],
        }), encoding="utf-8")
        (updated / "payload.txt").write_text("replacement", encoding="utf-8")
        with patch("core.package_importer.shutil.copytree", side_effect=OSError("simulated copy failure")):
            result = self.ctx["package_importer"].import_package(str(updated), allow_update=True)

        self.assertFalse(result["ok"])
        stored = Path(self.ctx["config"].data["seasons_dir"]) / "safe.update"
        self.assertEqual((stored / "payload.txt").read_text(encoding="utf-8"), "original")

    def test_failed_package_removal_is_reported_instead_of_succeeding_silently(self):
        imported = self._create_season_package("remove.failure", 2010, {"cars/a.erp": "MOD"})
        self.assertTrue(imported["ok"], imported.get("errors"))
        with patch("core.season_manager.shutil.rmtree", side_effect=OSError("simulated delete failure")):
            result = self.ctx["season_manager"].remove_season("remove.failure")

        self.assertFalse(result["ok"])
        self.assertIn("simulated delete failure", result["error"])
        self.assertTrue(self.ctx["season_manager"].get_season("remove.failure"))

    def test_raw_nested_mod_import_activation_deactivation_and_vanilla_launch(self):
        archive = self.tmp / "nested_mod.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("outer/F1 25/asset_groups/cars/test.erp", "CAR_DATA")
            package.writestr("outer/F1 25/manifest.json", '{"game_file": "not a launcher package"}')

        imported = self.ctx["package_importer"].import_package(str(archive), season_year=2010)
        self.assertTrue(imported["ok"], imported.get("errors"))
        season = self.ctx["season_manager"].get_season(imported["season_id"])
        self.assertEqual(season.manifest.game, "F1 25")
        self.assertEqual(season.manifest.season, 2010)

        activated = self.ctx["mod_installer"].install_season(imported["season_id"])
        self.assertTrue(activated["ok"], activated.get("error"))
        self.assertEqual(activated["files_modified"], 0)
        self.assertEqual(len(self.bridge.imported_files), 1)
        self.assertIn("outer/F1 25/asset_groups/cars/test.erp", self.bridge.imported_files[0])
        self.assertIn("outer/F1 25/manifest.json", self.bridge.imported_files[0])
        self.assertFalse((self.game_25 / "asset_groups" / "cars" / "test.erp").exists())
        self.assertTrue(self.bridge.addons["test.mod.1"])
        self.assertTrue(self.bridge.addons["f1classic.launcher"])

        modded = self.ctx["game_detector"].launch_game("F1 25")
        self.assertFalse(modded["ok"])
        self.assertIn("without EA AntiCheat", modded["error"])
        self.assertEqual(self.bridge.launches, [])

        deactivated = self.ctx["mod_installer"].uninstall_season(imported["season_id"])
        self.assertTrue(deactivated["ok"], deactivated.get("error"))
        self.assertFalse(self.bridge.addons["test.mod.1"])
        self.assertFalse(self.bridge.addons["f1classic.launcher"])
        self.assertIsNone(self.ctx["config"].get_active_season("F1 25"))

        vanilla = self.ctx["game_detector"].launch_game("F1 25")
        self.assertTrue(vanilla["ok"])
        self.assertEqual(self.bridge.launches[-1][1], False)
        self.assertEqual((self.game_25 / "unrelated.txt").read_text(encoding="utf-8"), "KEEP_ME_SAFE")

    def test_switching_seasons_keeps_2010_and_2012_addons_separate(self):
        self._create_season_package("season.2010", 2010, {"cars/car.erp": "2010"})
        self._create_season_package("season.2012", 2012, {"cars/car.erp": "2012"})
        first = self.ctx["mod_installer"].install_season("season.2010")
        self.assertTrue(first["ok"], first.get("error"))
        self.assertTrue(self.bridge.addons["test.mod.1"])

        second = self.ctx["mod_installer"].install_season("season.2012")
        self.assertTrue(second["ok"], second.get("error"))
        self.assertFalse(self.bridge.addons["test.mod.1"])
        self.assertTrue(self.bridge.addons["test.mod.2"])
        self.assertEqual(self.ctx["config"].get_active_season("F1 25"), "season.2012")

    def test_addon_load_order_is_saved_for_each_season_group(self):
        self._create_season_package("season.2010.a", 2010, {"cars/a.erp": "A"})
        self._create_season_package("season.2010.b", 2010, {"cars/b.erp": "B"})
        activated = self.ctx["mod_installer"].install_season("season.2010.a")
        self.assertTrue(activated["ok"], activated.get("error"))

        original_order = self.ctx["mod_installer"].get_season_addon_order(2010)
        requested_order = list(reversed(original_order))
        reordered = self.ctx["mod_installer"].reorder_season_addons("season.2010.a", requested_order)
        self.assertTrue(reordered["ok"], reordered.get("error"))
        self.assertEqual(self.ctx["mod_installer"].get_season_addon_order(2010), requested_order)

    def test_inactive_season_update_refreshes_same_addon_and_failure_is_recoverable(self):
        package = self._create_season_package(
            "season.update",
            2010,
            {"cars/car.erp": "original"},
        )
        self.assertTrue(package["ok"], package.get("errors"))
        activated = self.ctx["mod_installer"].install_season("season.update")
        self.assertTrue(activated["ok"], activated.get("error"))
        addon_id = self.ctx["config"].data["season_addons"]["season.update"][0]
        self.ctx["config"].data["season_addon_orders"]["2010"] = [addon_id]
        self.ctx["config"].save()
        deactivated = self.ctx["mod_installer"].uninstall_season("season.update")
        self.assertTrue(deactivated["ok"], deactivated.get("error"))
        self.assertFalse(self.bridge.addons[addon_id])

        updated = self.tmp / "updated_season"
        (updated / "files" / "cars").mkdir(parents=True)
        (updated / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "id": "season.update",
            "name": "F1 2010 Season",
            "version": "1.0.0",
            "author": "Tester",
            "description": "Updated content",
            "game": "F1 25",
            "season": 2010,
            "artwork": "",
            "operations": [{"type": "bydanix_merge", "source": "files", "target": "."}],
        }), encoding="utf-8")
        (updated / "files" / "cars" / "car.erp").write_text("replacement", encoding="utf-8")
        imported_update = self.ctx["package_importer"].import_package(
            str(updated),
            allow_update=True,
        )
        self.assertTrue(imported_update["ok"], imported_update.get("errors"))
        self.assertFalse(self.ctx["season_manager"].get_season("season.update").installed)

        old_fingerprint = self.ctx["config"].data["season_addon_fingerprints"]["season.update"]
        self.bridge.fail_import = True
        failed = self.ctx["mod_installer"].install_season("season.update")
        self.assertFalse(failed["ok"])
        self.assertEqual(self.ctx["config"].data["season_addons"]["season.update"], [addon_id])
        self.assertEqual(
            self.ctx["config"].data["season_addon_fingerprints"]["season.update"],
            old_fingerprint,
        )
        self.assertFalse(self.bridge.addons[addon_id])

        self.bridge.fail_import = False
        refreshed = self.ctx["mod_installer"].install_season("season.update")
        self.assertTrue(refreshed["ok"], refreshed.get("error"))
        self.assertEqual(self.bridge.import_count, 3)
        self.assertEqual(self.ctx["config"].data["season_addons"]["season.update"], [addon_id])
        self.assertEqual(self.ctx["mod_installer"].get_season_addon_order(2010), [addon_id])
        self.assertTrue(self.bridge.addons[addon_id])
        self.assertIn(b"replacement", self.bridge.imported_payloads[-1].values())

    def test_default_user_data_stays_outside_the_executable_directory(self):
        user_data = self.tmp / "LocalAppData"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(user_data)}):
            context = create_app_context()
        self.assertEqual(Path(context["config"].data["seasons_dir"]), user_data / "F1ClassicLauncher" / "seasons")
        self.assertEqual(Path(context["config"].data["swofty_data_dir"]), user_data / "F1ClassicLauncher" / "Swofty")

    def test_config_save_keeps_previous_state_if_atomic_replace_fails(self):
        config = self.ctx["config"]
        original = config.config_file.read_bytes()
        config.data["default_game"] = "Broken partial write"
        with patch("core.config_manager.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                config.save()

        self.assertEqual(config.config_file.read_bytes(), original)
        self.assertEqual(list(config.config_dir.glob(".settings-*.tmp")), [])

    def test_invalid_settings_are_preserved_and_reported(self):
        config_dir = self.tmp / "invalid_settings" / "config"
        config_dir.mkdir(parents=True)
        settings = config_dir / "settings.json"
        settings.write_text("{invalid", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Could not load launcher settings"):
            from core.config_manager import ConfigManager
            ConfigManager(config_dir.parent)

        self.assertEqual(settings.read_text(encoding="utf-8"), "{invalid")

    def test_external_launch_profile_and_enabled_state_persist(self):
        self.ctx["config"].set_external_launch_enabled(True)
        self.ctx["config"].set_external_launch_profile("season.f1-25-2012")

        from core.config_manager import ConfigManager
        reloaded = ConfigManager(self.app_dir)

        self.assertTrue(reloaded.get_external_launch_enabled())
        self.assertEqual(reloaded.get_external_launch_profile(), "season.f1-25-2012")

    def test_f1_25_process_monitor_detects_independent_start_and_shutdown_without_subprocess(self):
        process_names = []
        monitor = GameProcessMonitor(lambda: list(process_names))

        with patch("subprocess.Popen") as popen:
            self.assertFalse(monitor.is_f1_25_running())
            process_names.append(r"C:\Program Files\EA Games\F1 25\F1_25.exe")
            self.assertTrue(monitor.is_f1_25_running())
            self.assertTrue(monitor.is_f1_25_running())
            process_names.clear()
            self.assertFalse(monitor.is_f1_25_running())

        popen.assert_not_called()

    def test_external_launch_detects_existing_game_and_updates_only_on_state_changes(self):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            from PySide6.QtWidgets import QApplication
            from ui.desktop_app import F1ClassicLauncherWindow

            self._create_season_package("season.2010.external", 2010, {"cars/a.erp": "2010"})
            self._create_season_package("season.2012.external", 2012, {"cars/b.erp": "2012"})
            self.ctx["config"].set_external_launch_enabled(True)
            self.ctx["config"].set_external_launch_profile("season.2010.external")
            self.ctx["config"].set_active_season("F1 25", "season.2010.external")
            process_monitor = FakeGameProcessMonitor(running=True)
            self.ctx["game_process_monitor"] = process_monitor

            app = QApplication.instance() or QApplication([])
            window = F1ClassicLauncherWindow(self.ctx)
            deadline = time.monotonic() + 10
            while window.operation_worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()

            self.assertTrue(window.external_game_timer.isActive())
            self.assertEqual(window.external_game_timer.interval(), 3000)
            self.assertEqual(window.external_game_status.text(), "F1 25 status: Running")
            self.assertEqual(window.external_profile_status.text(), "Selected profile: 2010")
            self.assertEqual(window.external_mod_status.text(), "Mod status: Not verified")
            self.assertEqual(process_monitor.calls, 1)

            window._poll_external_game()
            self.assertEqual(process_monitor.calls, 2)
            log_text = self.ctx["logger"].log_file.read_text(encoding="utf-8")
            self.assertEqual(log_text.count("External F1 25 process started."), 1)

            index = window.external_profile_combo.findData("season.2012.external")
            window.external_profile_combo.setCurrentIndex(index)
            self.assertEqual(self.ctx["config"].get_external_launch_profile(), "season.2012.external")
            self.assertEqual(window.external_profile_status.text(), "Selected profile: 2012")
            self.assertEqual(window.external_mod_status.text(), "Mod status: Inactive")

            process_monitor.running = False
            window._poll_external_game()
            self.assertEqual(window.external_game_status.text(), "F1 25 status: Not running")
            log_text = self.ctx["logger"].log_file.read_text(encoding="utf-8")
            self.assertIn("External F1 25 process closed.", log_text)

            calls_before_disable = process_monitor.calls
            window.external_launch_enabled_checkbox.setChecked(False)
            self.assertFalse(window.external_game_timer.isActive())
            self.assertEqual(window.external_launch_status.text(), "External launch: Disabled")
            window._poll_external_game()
            self.assertEqual(process_monitor.calls, calls_before_disable)

            window.close()
            self.assertFalse(window.external_game_timer.isActive())

    def test_existing_launcher_data_migrates_to_user_data_directory(self):
        legacy = self.tmp / "old_install"
        user_data = self.tmp / "LocalAppData" / "F1ClassicLauncher"
        config_dir = legacy / "config"
        config_dir.mkdir(parents=True)
        paths = {}
        for key in ("seasons_dir", "backups_dir", "imports_dir", "logs_dir", "swofty_data_dir"):
            directory = legacy / key
            directory.mkdir()
            (directory / "keep.txt").write_text(key, encoding="utf-8")
            paths[key] = str(directory)
        old_settings = {
            **paths,
            "active_seasons": {"F1 25": "season.2010"},
            "game_paths": {"F1 25": "C:\\Games\\F1 25"},
        }
        (config_dir / "settings.json").write_text(json.dumps(old_settings), encoding="utf-8")

        _migrate_legacy_user_data(legacy, user_data)

        migrated = json.loads((user_data / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(migrated["active_seasons"], {"F1 25": "season.2010"})
        self.assertEqual(migrated["game_paths"], {"F1 25": "C:\\Games\\F1 25"})
        self.assertEqual(
            (user_data / "seasons" / "keep.txt").read_text(encoding="utf-8"),
            "seasons_dir",
        )
        self.assertEqual(
            (user_data / "Swofty" / "keep.txt").read_text(encoding="utf-8"),
            "swofty_data_dir",
        )

    def test_native_qt_ui_modules_compile_and_no_web_server(self):
        for py_file in (ROOT_DIR / "main.py", ROOT_DIR / "ui" / "styles.py", ROOT_DIR / "ui" / "desktop_app.py"):
            self.assertTrue(py_file.is_file())
            py_compile.compile(str(py_file), doraise=True)
        self.assertFalse((ROOT_DIR / "app_server.py").exists())
        self.assertFalse((ROOT_DIR / "frontend.html").exists())
        self.assertIn("pythonw.exe", (ROOT_DIR / "Launch_F1_Classic_Launcher.vbs").read_text(encoding="utf-8"))
        main_src = (ROOT_DIR / "main.py").read_text(encoding="utf-8")
        self.assertIn("QApplication", (ROOT_DIR / "ui" / "desktop_app.py").read_text(encoding="utf-8"))
        self.assertNotIn("http.server", main_src)
        self.assertNotIn("webbrowser", main_src)
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            from PySide6.QtWidgets import QApplication
            from ui.desktop_app import F1ClassicLauncherWindow

            app = QApplication.instance() or QApplication([])
            self._create_season_package("ui.2010.mod", 2010, {"cars/ui.erp": "UI"})
            activated = self.ctx["mod_installer"].install_season("ui.2010.mod")
            self.assertTrue(activated["ok"], activated.get("error"))
            window = F1ClassicLauncherWindow(self.ctx)
            window._open_season_details("ui.2010.mod")
            deadline = time.monotonic() + 10
            while window.operation_worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()
            self.assertEqual(window.windowTitle(), "F1 Classic Launcher")
            self.assertEqual(window.season_addon_order.count(), 1)
            window.close()

    def test_background_operations_are_exclusive_and_keep_ui_responsive(self):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            from PySide6.QtWidgets import QApplication
            from ui.desktop_app import F1ClassicLauncherWindow

            app = QApplication.instance() or QApplication([])
            window = F1ClassicLauncherWindow(self.ctx)
            deadline = time.monotonic() + 10
            while window.operation_worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)

            entered = threading.Event()
            release = threading.Event()

            def blocked_operation(_progress):
                entered.set()
                release.wait(5)
                return {"ok": True}

            self.assertTrue(window._start_operation("Test operation", blocked_operation))
            self.assertTrue(entered.wait(2))
            self.assertFalse(window._start_operation("Duplicate operation", lambda _progress: {"ok": True}))
            self.assertFalse(window.centralWidget().isEnabled())
            app.processEvents()
            self.assertIn("Wait for", window.operation_status.text())

            release.set()
            deadline = time.monotonic() + 5
            while window.operation_worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            self.assertIsNone(window.operation_worker)
            self.assertTrue(window.centralWidget().isEnabled())
            window.close()

    def test_background_worker_reports_and_logs_operation_exceptions(self):
        from ui.desktop_app import OperationWorker

        def fail(_progress):
            raise RuntimeError("simulated worker failure")

        worker = OperationWorker(fail, self.ctx["logger"])
        worker.run()

        self.assertFalse(worker.result["ok"])
        self.assertEqual(worker.result["error"], "simulated worker failure")
        log_text = self.ctx["logger"].log_file.read_text(encoding="utf-8")
        self.assertIn("Background launcher operation failed", log_text)
        self.assertIn("RuntimeError: simulated worker failure", log_text)


if __name__ == "__main__":
    unittest.main()
