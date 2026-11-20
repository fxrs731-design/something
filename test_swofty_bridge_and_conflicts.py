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
