import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import create_app_context


class TestSwoftyByDaniXCompat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_swofty_"))
        self.ctx = create_app_context(self.tmp / "launcher_data")

    def test_swofty_json_bydanix_folder_import_and_install(self):
        pkg_2010 = self.tmp / "bydanix.f1-2010-season"
        (pkg_2010 / "files" / "cars").mkdir(parents=True)
        (pkg_2010 / "parts").mkdir()
        (pkg_2010 / "registry").mkdir()
        (pkg_2010 / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\nICON2010")
        (pkg_2010 / "files" / "cars" / "rb6.erp").write_text("BYDANIX_2010_RB6", encoding="utf-8")
        (pkg_2010 / "swofty.json").write_text(json.dumps({
            "id": "bydanix.f1-2010-season",
            "name": "ByDaniX F1 2010 Season",
            "version": "1.0.0",
            "author": "ByDaniX",
            "description": "F1 2010 season mod",
        }), encoding="utf-8")

        res = self.ctx["package_importer"].import_package(str(pkg_2010))
        self.assertTrue(res["ok"])
        entry = self.ctx["season_manager"].get_season("bydanix.f1-2010-season")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.manifest.season, 2010)
        self.assertEqual(entry.manifest.game, "F1 25")
        self.assertIsNotNone(entry.artwork_path)
        self.assertEqual(entry.artwork_path.name, "icon.png")

        game_25 = self.tmp / "Games" / "F1 25"
        game_25.mkdir(parents=True)
        (game_25 / "F1_25.exe").write_text("MOCK_EXE_25", encoding="utf-8")
        self.ctx["config"].set_game_path("F1 25", str(game_25))

        inst = self.ctx["mod_installer"].install_season("bydanix.f1-2010-season")
        self.assertTrue(inst["ok"])
        self.assertTrue((game_25 / "cars" / "rb6.erp").is_file())

        uninst = self.ctx["mod_installer"].uninstall_season("bydanix.f1-2010-season")
        self.assertTrue(uninst["ok"])
        self.assertFalse((game_25 / "cars" / "rb6.erp").exists())

    def test_eaanticheat_launcher_exe_support_for_f1_25(self):
        game_25 = self.tmp / "Games" / "F1 25 Custom"
        game_25.mkdir(parents=True)
        ea_exe = game_25 / "EAAntiCheat.GameServiceLauncher.exe"
        ea_exe.write_text("MOCK_EA_ANTICHEAT_LAUNCHER", encoding="utf-8")
        (game_25 / "preloader_l.dll").write_text("MOCK_PRELOADER", encoding="utf-8")

        # Validate by directory
        check_dir = self.ctx["game_detector"].validate_game_directory("F1 25", str(game_25))
        self.assertTrue(check_dir["valid"])
        self.assertEqual(Path(check_dir["executable"]).name, "EAAntiCheat.GameServiceLauncher.exe")

        # Validate by direct .exe path
        check_exe = self.ctx["game_detector"].validate_game_directory("F1 25", str(ea_exe))
        self.assertTrue(check_exe["valid"])
        self.assertEqual(check_exe["directory"], str(game_25))
        self.assertEqual(check_exe["executable"], str(ea_exe))



    def test_quoted_path_bom_and_multi_season_folder_import(self):
        parent = self.tmp / "F1 2010 - 2015 car mod"
        for yr, slug in ((2010, "bydanix.f1-2010-season"), (2012, "bydanix.f1-2012-season")):
            d = parent / slug
            (d / "parts").mkdir(parents=True)
            (d / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\nICON")
            # Write with UTF-8 BOM and dict-shaped dependencies/author
            (d / "swofty.json").write_text(
                json.dumps({
                    "id": slug,
                    "name": f"ByDaniX F1 {yr} Season",
                    "version": "1.0.0",
                    "author": {"name": "ByDaniX"},
                    "dependencies": {"simplified.for-swofty": ">=1.0.0"},
                }),
                encoding="utf-8-sig",
            )
        # Pass quoted path like Windows Explorer "Copy as path"
        res = self.ctx["package_importer"].import_package(f'"{parent}"')
        self.assertTrue(res["ok"])
        seasons = self.ctx["season_manager"].scan_seasons()
        self.assertEqual(len(seasons), 2)
        self.assertEqual(seasons[0].manifest.author, "ByDaniX")
        self.assertIsInstance(seasons[0].manifest.dependencies, list)


if __name__ == "__main__":
    unittest.main()
