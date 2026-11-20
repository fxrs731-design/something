#!/usr/bin/env python3
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "F1ClassicLauncher"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    DATA_DIR = RESOURCE_DIR

for p in (str(RESOURCE_DIR), str(DATA_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from core.game_detector import GameDetector
from core.logger import LauncherLogger
from core.mod_installer import ModInstaller
from core.package_importer import PackageImporter
from core.season_manager import SeasonManager
from core.swofty_bridge import SwoftyBridge


def create_app_context(base_dir: Path | None = None) -> dict:
    root = Path(base_dir) if base_dir else DATA_DIR
    config = ConfigManager(root)
    logger = LauncherLogger(Path(config.data["logs_dir"]))
    game_detector = GameDetector(config, logger)
    backup_manager = BackupManager(Path(config.data["backups_dir"]), logger)
    season_manager = SeasonManager(config, logger)
    package_importer = PackageImporter(config, season_manager, logger)
    mod_installer = ModInstaller(config, season_manager, backup_manager, game_detector, logger)
    swofty_bridge = SwoftyBridge(RESOURCE_DIR, logger)
    return {
        "base_dir": root,
        "resource_dir": RESOURCE_DIR,
        "config": config,
        "logger": logger,
        "game_detector": game_detector,
        "backup_manager": backup_manager,
        "season_manager": season_manager,
        "package_importer": package_importer,
        "mod_installer": mod_installer,
        "swofty_bridge": swofty_bridge,
    }


def main() -> int:
    from ui.desktop_app import launch_desktop_app

    ctx = create_app_context()
    ctx["logger"].info("Starting F1 Classic Launcher (Native Qt Desktop)...")
    ctx["game_detector"].detect_installed_games()
    return launch_desktop_app(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
