#!/usr/bin/env python3
import os
import sys
from pathlib import Path

BUNDLE_DIR = Path(__file__).resolve().parent
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))

from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from core.game_detector import GameDetector
from core.logger import LauncherLogger
from core.mod_installer import ModInstaller
from core.package_importer import PackageImporter
from core.season_manager import SeasonManager
from ui.app_server import run_server


def get_data_dir() -> Path:
    """Use a persistent per-user directory for frozen Windows builds."""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", Path.home())) / "F1ClassicLauncher"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return BUNDLE_DIR


def create_app_context(base_dir: Path | None = None) -> dict:
    root = Path(base_dir) if base_dir else get_data_dir()
    config = ConfigManager(root)
    logger = LauncherLogger(Path(config.data["logs_dir"]))
    game_detector = GameDetector(config, logger)
    backup_manager = BackupManager(Path(config.data["backups_dir"]), logger)
    season_manager = SeasonManager(config, logger)
    package_importer = PackageImporter(config, season_manager, logger)
    mod_installer = ModInstaller(config, season_manager, backup_manager, game_detector, logger)
    return {
        "base_dir": root,
        "config": config,
        "logger": logger,
        "game_detector": game_detector,
        "backup_manager": backup_manager,
        "season_manager": season_manager,
        "package_importer": package_importer,
        "mod_installer": mod_installer,
    }


if __name__ == "__main__":
    ctx = create_app_context()
    ctx["logger"].info("Starting F1 Classic Launcher...")
    ctx["game_detector"].detect_installed_games()
    run_server(ctx)
