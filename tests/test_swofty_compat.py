import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import create_app_context
from core.game_adapters import GameAdapter


class TestSwoftyByDaniXCompat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_swofty_"))
        self.ctx = create_app_context(self.tmp / "launcher_data")
        self.bridge = Mock()
        self.bridge.install_addon_package.return_value = {"ok": True}
        self.bridge.ensure_watermark_addon.return_value = {"ok": True}
        self.bridge.set_addon_enabled.return_value = {"ok": True}
        self.bridge.reorder_addons.return_value = {"ok": True}
        self.bridge.check_conflicts.return_value = {"ok": True, "stdout": "No conflicts found."}
        self.ctx["mod_installer"].swofty_bridge = self.bridge

    def test_season_templates_import_for_f1_25(self):
        supported_games = GameAdapter.list_supported_games()
        self.assertEqual(supported_games, ["F1 25"])

        for year in (2010, 2012):
            with self.subTest(season=year):
                package_dir = self.tmp / f"f1-{year}-template"
                package_dir.mkdir()
                (package_dir / "mod_files").mkdir()
                (package_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nCOVER")
                shutil.copyfile(
                    ROOT_DIR / f"bydanix_f1_{year}_manifest_template.json",
                    package_dir / "manifest.json",
                )

                result = self.ctx["package_importer"].import_package(str(package_dir))
                self.assertTrue(result["ok"], result.get("errors"))
                self.assertEqual(result["game"], "F1 25")
                self.assertEqual(result["season"], year)

                entry = self.ctx["season_manager"].get_season(f"bydanix.f1-{year}-season")
                self.assertIsNotNone(entry)
                self.assertEqual(entry.manifest.game, "F1 25")
                self.assertEqual(entry.manifest.season, year)

    def test_swofty_addon_import_preserves_metadata_and_uses_core(self):
        package = self.tmp / "bydanix.f1-2010-season"
        (package / "files" / "cars").mkdir(parents=True)
        (package / "parts").mkdir()
        (package / "registry").mkdir()
        (package / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\nICON2010")
        (package / "files" / "cars" / "rb6.erp").write_text("BYDANIX_2010_RB6", encoding="utf-8")
        (package / "swofty.json").write_text(json.dumps({
            "id": "bydanix.f1-2010-season",
            "name": "ByDaniX F1 2010 Season",
            "version": "1.0.0",
            "author": "ByDaniX",
            "description": "F1 2010 season mod",
            "compat": {"game": "f1_25"},
            "teams": ["Red Bull"],
            "drivers": ["Sebastian Vettel"],
        }), encoding="utf-8")

        result = self.ctx["package_importer"].import_package(str(package))
        self.assertTrue(result["ok"], result.get("errors"))
        entry = self.ctx["season_manager"].get_season("bydanix.f1-2010-season")
        self.assertEqual(entry.manifest.season, 2010)
        self.assertEqual(entry.manifest.game, "F1 25")
        self.assertEqual(entry.manifest.teams, ["Red Bull"])
        self.assertEqual(entry.manifest.drivers, ["Sebastian Vettel"])
        self.assertTrue(entry.manifest.requires_swofty_core)
        self.assertTrue((entry.package_dir / "swofty.json").is_file())
        self.assertEqual(json.loads((entry.package_dir / "manifest.json").read_text())["season"], 2010)

        game_25 = self.tmp / "Games" / "F1 25"
        game_25.mkdir(parents=True)
        (game_25 / "F1_25.exe").write_text("MOCK_EXE_25", encoding="utf-8")
        self.ctx["config"].set_game_path("F1 25", str(game_25))
        self.bridge.ensure_watermark_addon.return_value = {"ok": True}

        activated = self.ctx["mod_installer"].install_season("bydanix.f1-2010-season")
        self.assertTrue(activated["ok"], activated.get("error"))
        self.bridge.install_addon_package.assert_called_once_with(
            entry.package_dir,
            "bydanix.f1-2010-season",
            "default",
        )
        self.assertFalse((game_25 / "cars" / "rb6.erp").exists())

        deactivated = self.ctx["mod_installer"].uninstall_season("bydanix.f1-2010-season")
        self.assertTrue(deactivated["ok"], deactivated.get("error"))
        self.bridge.set_addon_enabled.assert_any_call("bydanix.f1-2010-season", False, "default")
        self.bridge.set_addon_enabled.assert_any_call("f1classic.launcher", False, "default")

        updated_source = self.tmp / "updated-addon-package"
        shutil.copytree(entry.package_dir, updated_source)
        (updated_source / "files" / "cars" / "rb6.erp").write_text("UPDATED_2010_RB6", encoding="utf-8")
        update_result = self.ctx["package_importer"].import_package(
            str(updated_source),
            allow_update=True,
        )
        self.assertTrue(update_result["ok"], update_result.get("errors"))
        self.assertEqual(update_result["version"], "1.0.0")
        refreshed = self.ctx["mod_installer"].install_season("bydanix.f1-2010-season")
        self.assertTrue(refreshed["ok"], refreshed.get("error"))
        self.assertEqual(self.bridge.install_addon_package.call_count, 2)

    def test_f1_25_addon_uses_selected_year_when_metadata_has_no_season(self):
        package = self.tmp / "modern_carpack"
        package.mkdir()
        (package / "swofty.json").write_text(json.dumps({
            "id": "custom.f125.carpack",
            "name": "F1 25 Car Pack",
            "version": "1.0.0",
            "compat": {"game": "f1_25"},
        }), encoding="utf-8")

        imported = self.ctx["package_importer"].import_package(str(package), season_year=2012)
        self.assertTrue(imported["ok"], imported.get("errors"))
        season = self.ctx["season_manager"].get_season("custom.f125.carpack")
        self.assertEqual(season.manifest.game, "F1 25")
        self.assertEqual(season.manifest.season, 2012)

    def test_ea_service_launcher_path_resolves_to_f1_25_installation(self):
        game_25 = self.tmp / "Games" / "F1 25 Custom"
        game_25.mkdir(parents=True)
        game_exe = game_25 / "F1_25.exe"
        game_exe.write_text("MOCK_F1_25", encoding="utf-8")
        ea_exe = game_25 / "EAAntiCheat.GameServiceLauncher.exe"
        ea_exe.write_text("MOCK_EA_ANTICHEAT_LAUNCHER", encoding="utf-8")

        result = self.ctx["game_detector"].validate_game_directory("F1 25", str(ea_exe))
        self.assertTrue(result["valid"])
        self.assertEqual(result["directory"], str(game_25))
        self.assertEqual(result["executable"], str(game_exe))

    def test_game_path_validation_does_not_accept_executable_from_another_install(self):
        configured = self.tmp / "Games" / "F1 25 Configured"
        other = self.tmp / "Games" / "F1 25 Other"
        configured.mkdir(parents=True)
        other.mkdir(parents=True)
        unrelated_exe = other / "F1_25.exe"
        unrelated_exe.write_text("MOCK_F1_25", encoding="utf-8")

        result = self.ctx["game_detector"].validate_game_directory(
            "F1 25",
            str(configured),
            preferred_exe=str(unrelated_exe),
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["directory"], str(configured))

    def test_quoted_path_bom_and_multi_season_folder_import(self):
        parent = self.tmp / "F1 2010 - 2012 car mod"
        for year, slug in ((2010, "bydanix.f1-2010-season"), (2012, "bydanix.f1-2012-season")):
            addon = parent / slug
            (addon / "parts").mkdir(parents=True)
            (addon / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\nICON")
            (addon / "swofty.json").write_text(
                json.dumps({
                    "id": slug,
                    "name": f"ByDaniX F1 {year} Season",
                    "version": "1.0.0",
                    "author": {"name": "ByDaniX"},
                    "compat": {"game": "f1_25"},
                    "dependencies": {"simplified.for-swofty": ">=1.0.0"},
                }),
                encoding="utf-8-sig",
            )

        result = self.ctx["package_importer"].import_package(f'"{parent}"')
        self.assertTrue(result["ok"])
        seasons = self.ctx["season_manager"].scan_seasons()
        self.assertEqual(len(seasons), 2)
        self.assertEqual({season.manifest.game for season in seasons}, {"F1 25"})
        self.assertEqual({season.manifest.season for season in seasons}, {2010, 2012})
        self.assertTrue(all(season.manifest.requires_swofty_core for season in seasons))


if __name__ == "__main__":
    unittest.main()
