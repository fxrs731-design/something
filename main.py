#!/usr/bin/env python3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from core.game_detector import GameDetector
from core.logger import LauncherLogger
from core.mod_installer import ModInstaller
from core.package_importer import PackageImporter
from core.season_manager import SeasonManager
from ui.app_server import run_server


def create_app_context(base_dir: Path | None = None) -> dict:
    root = Path(base_dir) if base_dir else BASE_DIR
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
    ctx = create_app_context(BASE_DIR)
    ctx["logger"].info("Starting F1 Classic Launcher...")
    ctx["game_detector"].detect_installed_games()
    run_server(ctx)
