import json
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import create_app_context
from ui.app_server import make_handler
import socketserver


class TestF1ClassicLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_test_"))
        self.app_dir = self.tmp / "launcher_data"
        self.ctx = create_app_context(self.app_dir)

        # Create a mock F1 2018 game directory with original vanilla files
        self.game_2018 = self.tmp / "Games" / "F1 2018"
        self.game_2018.mkdir(parents=True)
        (self.game_2018 / "F1_2018.exe").write_text("MOCK_EXE_2018", encoding="utf-8")
        (self.game_2018 / "database").mkdir()
        (self.game_2018 / "database" / "db.bin").write_text("VANILLA_DB_2018", encoding="utf-8")
        (self.game_2018 / "unrelated.txt").write_text("KEEP_ME_SAFE", encoding="utf-8")

        # Create a mock F1 2012 game directory
        self.game_2012 = self.tmp / "Games" / "F1 2012"
        self.game_2012.mkdir(parents=True)
        (self.game_2012 / "F1_2012.exe").write_text("MOCK_EXE_2012", encoding="utf-8")
        (self.game_2012 / "cars").mkdir()
        (self.game_2012 / "cars" / "livery.erp").write_text("VANILLA_2012_CAR", encoding="utf-8")

    def _create_season_zip(self, zip_name: str, manifest: dict, files: dict[str, str], include_cover: bool = True) -> Path:
        pkg_src = self.tmp / f"src_{zip_name}"
        pkg_src.mkdir(parents=True, exist_ok=True)
        (pkg_src / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if include_cover:
            (pkg_src / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nMOCKPNG")
        for rel, content in files.items():
            f = pkg_src / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")

        zip_path = self.tmp / zip_name
        with zipfile.ZipFile(zip_path, "w") as zf:
            for item in pkg_src.rglob("*"):
                if item.is_file():
                    zf.write(item, arcname=str(item.relative_to(pkg_src)))
        return zip_path

    def test_import_valid_duplicate_and_invalid_packages(self):
        manifest_2018 = {
            "schema_version": 1,
            "id": "custom.f1-2018-a",
            "name": "F1 2018 Season A",
            "version": "1.0.0",
            "author": "Tester",
            "description": "Test 2018 season",
            "game": "F1 2018",
            "season": 2018,
            "artwork": "cover.png",
            "teams": ["Ferrari"],
            "drivers": ["Sebastian Vettel"],
            "operations": [{"type": "replace_file", "source": "files/db.bin", "target": "database/db.bin"}],
        }
        z1 = self._create_season_zip("s2018a.zip", manifest_2018, {"files/db.bin": "MODDED_DB_A"})

        res = self.ctx["package_importer"].import_package(str(z1))
        self.assertTrue(res["ok"])
        self.assertEqual(len(self.ctx["season_manager"].scan_seasons()), 1)

        # Duplicate check
        dup = self.ctx["package_importer"].import_package(str(z1), allow_update=False)
        self.assertFalse(dup["ok"])
        self.assertTrue(dup.get("duplicate"))

        # Update existing
        manifest_2018["version"] = "1.1.0"
        z1_upd = self._create_season_zip("s2018a_v2.zip", manifest_2018, {"files/db.bin": "MODDED_DB_A_V2"})
        upd = self.ctx["package_importer"].import_package(str(z1_upd), allow_update=True)
        self.assertTrue(upd["ok"])
        self.assertEqual(self.ctx["season_manager"].get_season("custom.f1-2018-a").manifest.version, "1.1.0")

        # Invalid package (missing artwork & missing source file)
        bad_manifest = dict(manifest_2018, id="custom.bad")
        z_bad = self._create_season_zip("bad.zip", bad_manifest, {}, include_cover=False)
        bad_res = self.ctx["package_importer"].import_package(str(z_bad))
        self.assertFalse(bad_res["ok"])
        self.assertTrue(any("cover.png" in e for e in bad_res["errors"]))

    def test_install_backup_restore_and_season_switching(self):
        # Configure game path
        check = self.ctx["game_detector"].validate_game_directory("F1 2018", str(self.game_2018))
        self.assertTrue(check["valid"])
        self.ctx["config"].set_game_path("F1 2018", str(self.game_2018))

        m1 = {
            "schema_version": 1, "id": "s1", "name": "Season 1", "version": "1.0.0",
            "author": "A", "description": "S1", "game": "F1 2018", "season": 2018,
            "artwork": "cover.png",
            "operations": [
                {"type": "replace_file", "source": "mod/db.bin", "target": "database/db.bin"},
                {"type": "copy_file", "source": "mod/extra.txt", "target": "extra_s1.txt"},
            ],
        }
        m2 = {
            "schema_version": 1, "id": "s2", "name": "Season 2", "version": "1.0.0",
            "author": "B", "description": "S2", "game": "F1 2018", "season": 2019,
            "artwork": "cover.png",
            "operations": [
                {"type": "replace_file", "source": "mod/db.bin", "target": "database/db.bin"},
            ],
        }
        z1 = self._create_season_zip("s1.zip", m1, {"mod/db.bin": "S1_DB", "mod/extra.txt": "S1_EXTRA"})
        z2 = self._create_season_zip("s2.zip", m2, {"mod/db.bin": "S2_DB"})
        self.ctx["package_importer"].import_package(str(z1))
        self.ctx["package_importer"].import_package(str(z2))

        # Install S1
        r1 = self.ctx["mod_installer"].install_season("s1")
        self.assertTrue(r1["ok"])
        self.assertEqual((self.game_2018 / "database" / "db.bin").read_text(), "S1_DB")
        self.assertTrue((self.game_2018 / "extra_s1.txt").is_file())

        # Switch to S2 -> must restore S1's extra file & vanilla before applying S2
        r2 = self.ctx["mod_installer"].install_season("s2")
        self.assertTrue(r2["ok"])
        self.assertEqual((self.game_2018 / "database" / "db.bin").read_text(), "S2_DB")
        self.assertFalse((self.game_2018 / "extra_s1.txt").exists())
        self.assertEqual((self.game_2018 / "unrelated.txt").read_text(), "KEEP_ME_SAFE")

        # Uninstall S2 -> restores vanilla DB
        u2 = self.ctx["mod_installer"].uninstall_season("s2")
        self.assertTrue(u2["ok"])
        self.assertEqual((self.game_2018 / "database" / "db.bin").read_text(), "VANILLA_DB_2018")
        self.assertEqual((self.game_2018 / "unrelated.txt").read_text(), "KEEP_ME_SAFE")

    def test_http_ui_server_endpoints(self):
        handler = make_handler(self.ctx)
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertIn("seasons", data)
                    self.assertIn("F1 2010", data["supported_games"])
                    self.assertIn("F1 2012", data["supported_games"])
            finally:
                httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
