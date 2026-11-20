#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = BASE_DIR

for p in (str(BASE_DIR), str(RESOURCE_DIR)):
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


def _migrate_legacy_user_data(legacy_root: Path, user_root: Path):
    legacy_settings = Path(legacy_root) / "config" / "settings.json"
    user_settings = Path(user_root) / "config" / "settings.json"
    if user_settings.exists() or not legacy_settings.is_file():
        return

    try:
        settings = json.loads(legacy_settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not migrate existing launcher settings from {legacy_settings}: {exc}") from exc
    if not isinstance(settings, dict):
        raise RuntimeError(f"Could not migrate existing launcher settings from {legacy_settings}: expected a JSON object.")

    destinations = {
        "seasons_dir": Path(user_root) / "seasons",
        "backups_dir": Path(user_root) / "backups",
        "imports_dir": Path(user_root) / "imports",
        "logs_dir": Path(user_root) / "logs",
        "swofty_data_dir": Path(user_root) / "Swofty",
    }
    for key, destination in destinations.items():
        source = Path(settings.get(key) or (Path(legacy_root) / key))
        if not source.is_absolute():
            source = Path(legacy_root) / source
        if source.resolve() != destination.resolve() and source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        settings[key] = str(destination)

    user_settings.parent.mkdir(parents=True, exist_ok=True)
    temporary_settings = user_settings.with_suffix(".json.tmp")
    temporary_settings.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(temporary_settings, user_settings)


def create_app_context(base_dir: Path | None = None) -> dict:
    if base_dir:
        root = Path(base_dir)
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = (
            Path(local_app_data) / "F1ClassicLauncher"
            if local_app_data
            else Path.home() / ".local" / "share" / "F1ClassicLauncher"
        )
        _migrate_legacy_user_data(BASE_DIR, root)
    config = ConfigManager(root)
    logger = LauncherLogger(Path(config.data["logs_dir"]))
    swofty_bridge = SwoftyBridge(BASE_DIR, logger, Path(config.data["swofty_data_dir"]))
    backup_manager = BackupManager(Path(config.data["backups_dir"]), logger)
    season_manager = SeasonManager(config, logger)
    game_detector = GameDetector(config, logger, swofty_bridge, backup_manager)
    package_importer = PackageImporter(config, season_manager, logger)
    mod_installer = ModInstaller(config, season_manager, game_detector, swofty_bridge, backup_manager, logger)
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
