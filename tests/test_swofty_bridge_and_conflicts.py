import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.conflict_detector import ConflictDetector
from core.logger import LauncherLogger
from core.manifest_parser import SeasonManifest
from core.process_utils import hidden_subprocess_options
from core.season_manager import SeasonEntry
from core.swofty_bridge import SwoftyBridge


class TestSwoftyBridgeAndConflicts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_bridge_"))
        self.logger = LauncherLogger(self.tmp / "logs")

    def test_swofty_bridge_reports_missing_cli_honestly(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        status = bridge.get_status()
        self.assertFalse(status["available"])
        self.assertIn("build_swofty_and_launcher.ps1", status["message"])

    def test_swofty_bridge_detects_and_invokes_cli(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        cli_dir = self.tmp / "swofty_core"
        cli_dir.mkdir(parents=True)
        cli_bin = cli_dir / "swofty.exe"
        cli_bin.write_text("MOCK_CLI", encoding="utf-8")

        status = bridge.get_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["cli_path"], str(cli_bin))

        mock_proc = MagicMock(returncode=0, stdout="swofty 1.9.79\n", stderr="")
        with patch("core.swofty_bridge.subprocess.run", return_value=mock_proc) as mocked:
            res = bridge.run_cli(["--version"])
            self.assertTrue(res["ok"])
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args[0][0], [str(cli_bin), "--version"])

    def test_swofty_cli_subprocess_uses_hidden_windows_options(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        cli = self.tmp / "swofty_core" / "swofty.exe"
        cli.parent.mkdir()
        cli.write_text("MOCK_CLI", encoding="utf-8")
        mock_proc = MagicMock(returncode=0, stdout="ok", stderr="")
        with (
            patch("core.swofty_bridge.hidden_subprocess_options", return_value={"creationflags": 123}),
            patch("core.swofty_bridge.subprocess.run", return_value=mock_proc) as mocked,
        ):
            result = bridge.run_cli(["setup", "status"])

        self.assertTrue(result["ok"])
        self.assertEqual(mocked.call_args.kwargs["creationflags"], 123)
        self.assertEqual(mocked.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_simplified_mod_list_parser_handles_profile_state_and_converted_addons(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        mods_output = (
            " 1. [on ] not converted: needs another file mod.raw-car  Raw Car Pack\n"
            " 2. -> converted.car-pack  mod.converted-car  Converted Car Pack\n"
            " 3. [off] mod.raw-track  Raw Track Pack\n"
        )
        addons_output = (
            "converted.car-pack               1.0.0      enabled   Converted Car Pack\n"
            "other.addon                      2.0.0      disabled  Other Addon\n"
        )
        with patch.object(
            bridge,
            "run_cli",
            side_effect=[
                {"ok": True, "stdout": mods_output},
                {"ok": True, "stdout": addons_output},
            ],
        ):
            result = bridge.list_simplified_mods("f1classic_2010")

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["mods"],
            [
                {
                    "position": 1,
                    "id": "mod.raw-car",
                    "name": "Raw Car Pack",
                    "enabled": True,
                    "addon_id": None,
                    "state": "[on ]",
                },
                {
                    "position": 2,
                    "id": "mod.converted-car",
                    "name": "Converted Car Pack",
                    "enabled": True,
                    "addon_id": "converted.car-pack",
                    "state": "-> converted.car-pack",
                },
                {
                    "position": 3,
                    "id": "mod.raw-track",
                    "name": "Raw Track Pack",
                    "enabled": False,
                    "addon_id": None,
                    "state": "[off]",
                },
            ],
        )

    def test_swofty_addon_package_is_enabled_only_in_requested_profile(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        package = self.tmp / "sample.swoftyaddon"
        package.write_bytes(b"mock package")
        with patch.object(
            bridge,
            "run_cli",
            side_effect=[
                {"ok": True, "stdout": "installed sample.carpack 1.0.0\n"},
                {"ok": True, "stdout": "enabled sample.carpack in profile f1classic_2012\n"},
            ],
        ) as run_cli:
            result = bridge.install_addon_from_path(package, "f1classic_2012")

        self.assertTrue(result["ok"])
        self.assertEqual(result["addon_ids"], ["sample.carpack"])
        self.assertEqual(
            run_cli.call_args_list[0].args[0],
            ["addons", "install", str(package.resolve())],
        )
        self.assertEqual(
            run_cli.call_args_list[1].args[0],
            ["--profile", "f1classic_2012", "addons", "enable", "sample.carpack"],
        )

    def test_hidden_subprocess_options_configure_no_console_on_windows(self):
        from types import SimpleNamespace

        startupinfo = SimpleNamespace(dwFlags=0, wShowWindow=0)
        with (
            patch("core.process_utils.os.name", "nt"),
            patch.object(subprocess, "STARTUPINFO", return_value=startupinfo, create=True),
            patch.object(subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(subprocess, "SW_HIDE", 0, create=True),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            options = hidden_subprocess_options()

        self.assertEqual(options["creationflags"], 0x08000000)
        self.assertIs(options["startupinfo"], startupinfo)
        self.assertEqual(startupinfo.dwFlags, 1)

    def test_reorder_rolls_back_to_empty_profile_after_enable_failure(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        calls = []

        def set_enabled(addon_id, enabled, profile):
            calls.append((addon_id, enabled))
            if enabled:
                return {"ok": False, "error": "simulated enable failure"}
            return {"ok": True}

        with patch.object(bridge, "set_addon_enabled", side_effect=set_enabled):
            result = bridge.reorder_addons(["new.addon"], previous_order=[])

        self.assertFalse(result["ok"])
        self.assertEqual(calls, [
            ("new.addon", False),
            ("new.addon", True),
            ("new.addon", False),
        ])

    def test_launch_blocks_modded_path_and_uses_official_ea_launch_for_vanilla(self):
        bridge = SwoftyBridge(self.tmp, self.logger, self.tmp / "swofty_data")
        core_dir = self.tmp / "swofty_core"
        cli = core_dir / "swofty.exe"
        cli.parent.mkdir(parents=True)
        cli.write_text("MOCK_CLI", encoding="utf-8")
        native_dir = core_dir / "native" / "dist"
        native_dir.mkdir(parents=True)
        (native_dir / "swofty_core.dll").write_bytes(b"MOCK_DLL")
        game_dir = self.tmp / "F1 25"
        game_dir.mkdir()
        bridge.register_game = MagicMock(return_value={"ok": True})
        with patch.object(bridge, "run_cli", return_value={"ok": True, "stdout": "EA launch accepted"}) as run_cli:
            modded = bridge.launch_game(game_dir, modded=True)
            self.assertFalse(modded["ok"])
            self.assertIn("without EA AntiCheat", modded["error"])

            launched = bridge.launch_game(game_dir, modded=False)
            self.assertTrue(launched["ok"])
            self.assertEqual(run_cli.call_args.args[0], [
                "game", "launch", "--game", str(game_dir.resolve())
            ])
            self.assertEqual(run_cli.call_args.kwargs["timeout_seconds"], 360)
            self.assertIn("EA AntiCheat", launched["profile"])

    def test_modded_launch_is_blocked_without_invoking_swofty(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        core_dir = self.tmp / "swofty_core"
        cli = core_dir / "swofty.exe"
        cli.parent.mkdir(parents=True)
        cli.write_text("MOCK_CLI", encoding="utf-8")
        native_dir = core_dir / "native" / "dist"
        native_dir.mkdir(parents=True)
        (native_dir / "swofty_core.dll").write_bytes(b"MOCK_DLL")
        game_dir = self.tmp / "F1 25"
        game_dir.mkdir()
        bridge.register_game = MagicMock(return_value={"ok": True})

        with patch.object(bridge, "run_cli") as run_cli:
            result = bridge.launch_game(game_dir, modded=True)

        self.assertFalse(result["ok"])
        self.assertIn("without EA AntiCheat", result["error"])
        bridge.register_game.assert_not_called()
        run_cli.assert_not_called()

    def test_setup_reset_passes_game_folder_and_other_mods_choice(self):
        bridge = SwoftyBridge(self.tmp, self.logger)
        game_dir = self.tmp / "F1 25"
        with patch.object(bridge, "run_cli", return_value={"ok": True}) as run_cli:
            result = bridge.setup_reset(game_dir, other_mods=True)

        self.assertTrue(result["ok"])
        self.assertEqual(run_cli.call_args.args[0], [
            "setup", "reset", "--game", str(game_dir.resolve()), "--other-mods"
        ])

    def test_same_version_addon_content_update_retries_without_changing_profile_state(self):
        data_dir = self.tmp / "swofty_data"
        bridge = SwoftyBridge(self.tmp, self.logger, data_dir)
        package = self.tmp / "source_addon"
        package.mkdir()
        (package / "swofty.json").write_text(json.dumps({
            "id": "sample.addon",
            "name": "Sample",
            "version": "1.0.0",
        }), encoding="utf-8")
        (package / "payload.erp").write_text("original", encoding="utf-8")
        installed = bridge._installed_addon_dir("sample.addon")
        shutil.copytree(package, installed)
        (installed / ".swofty-files.json").write_text("generated index", encoding="utf-8")
        profile = data_dir / "profiles" / "default.json"
        profile.parent.mkdir(parents=True)
        profile.write_text('{"addons":[{"id":"sample.addon","enabled":false}]}', encoding="utf-8")
        profile_before = profile.read_bytes()

        calls = []
        fail_install = True

        def fake_run_cli(args, **kwargs):
            nonlocal fail_install
            calls.append(args)
            if args[:2] == ["addons", "install"]:
                if fail_install:
                    return {"ok": False, "error": "simulated install failure"}
                shutil.rmtree(installed)
                shutil.copytree(package, installed)
                (installed / ".swofty-files.json").write_text("new generated index", encoding="utf-8")
            return {"ok": True}

        with patch.object(bridge, "run_cli", side_effect=fake_run_cli):
            unchanged = bridge.install_addon_package(package, "sample.addon")
            self.assertTrue(unchanged["ok"])
            self.assertEqual(calls, [])

            (package / "payload.erp").write_text("updated at the same version", encoding="utf-8")
            (installed / "stale.bin").write_text("stale", encoding="utf-8")
            failed = bridge.install_addon_package(package, "sample.addon")
            self.assertFalse(failed["ok"])
            self.assertEqual((installed / "payload.erp").read_text(encoding="utf-8"), "original")
            self.assertTrue((installed / "stale.bin").is_file())
            self.assertEqual(profile.read_bytes(), profile_before)

            fail_install = False
            updated = bridge.install_addon_package(package, "sample.addon")

        self.assertTrue(updated["ok"])
        self.assertEqual((installed / "payload.erp").read_text(encoding="utf-8"), "updated at the same version")
        self.assertFalse((installed / "stale.bin").exists())
        self.assertEqual(profile.read_bytes(), profile_before)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[:2] == ["addons", "install"] for call in calls))

    def test_importer_update_can_return_known_addon_ids_when_no_new_folder_is_created(self):
        data_dir = self.tmp / "swofty_data"
        bridge = SwoftyBridge(self.tmp, self.logger, data_dir)
        addon_id = "mod.stable-season"
        installed = bridge._installed_addon_dir(addon_id)
        installed.mkdir(parents=True)
        (installed / "swofty.json").write_text(json.dumps({"id": addon_id}), encoding="utf-8")
        source = self.tmp / "stable-season"
        source.mkdir()
        (source / "cars.erp").write_text("updated", encoding="utf-8")
        game_dir = self.tmp / "F1 25"
        game_dir.mkdir()
        bridge.ensure_simplified_importer = MagicMock(return_value={"ok": True})
        bridge.register_game = MagicMock(return_value={"ok": True})
        bridge.run_cli = MagicMock(return_value={"ok": True, "stdout": "updated"})

        result = bridge.import_mods(
            source,
            game_dir,
            known_addon_ids=[addon_id],
        )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["addon_ids"], [addon_id])
        self.assertEqual(bridge.run_cli.call_count, 1)

    def test_conflict_detector_finds_overlapping_files(self):
        p1 = self.tmp / "p1"
        p2 = self.tmp / "p2"
        (p1 / "files" / "cars").mkdir(parents=True)
        (p2 / "files" / "cars").mkdir(parents=True)
        (p1 / "files" / "cars" / "ferrari.erp").write_text("A", encoding="utf-8")
        (p2 / "files" / "cars" / "ferrari.erp").write_text("B", encoding="utf-8")

        e1 = SeasonEntry(
            manifest=SeasonManifest(1, "s1", "S1", "1.0", "A", "D", "F1 25", 2010, operations=[{"type": "copy_dir", "source": "files", "target": "."}]),
            package_dir=p1, installed=False, active_for_game=False, imported_at=0.0, artwork_path=None,
        )
        e2 = SeasonEntry(
            manifest=SeasonManifest(1, "s2", "S2", "1.0", "B", "D", "F1 25", 2012, operations=[{"type": "copy_dir", "source": "files", "target": "."}]),
            package_dir=p2, installed=False, active_for_game=False, imported_at=0.0, artwork_path=None,
        )
        conflicts = ConflictDetector.detect_conflicts([e1, e2])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["packages"], ["s1", "s2"])


if __name__ == "__main__":
    unittest.main()
