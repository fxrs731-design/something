from pathlib import Path

from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from core.game_adapters import GAME_PROFILES, GameAdapter
from core.logger import LauncherLogger
from core.swofty_bridge import SwoftyBridge


class GameDetector:
    def __init__(
        self,
        config: ConfigManager,
        logger: LauncherLogger,
        swofty_bridge: SwoftyBridge | None = None,
        backup_manager: BackupManager | None = None,
    ):
        self.config = config
        self.logger = logger
        self.swofty_bridge = swofty_bridge
        self.backup_manager = backup_manager

    def validate_game_directory(self, game_name: str, directory_or_exe: str, preferred_exe: str = "") -> dict:
        if game_name != "F1 25":
            return {"valid": False, "executable": None, "directory": None, "reason": "Only F1 25 is supported."}
        if not directory_or_exe:
            return {"valid": False, "executable": None, "directory": None, "reason": "Path is empty."}

        p = Path(directory_or_exe)
        if p.is_file() and p.suffix.lower() == ".exe":
            if p.name.lower() != "f1_25.exe":
                return {
                    "valid": False,
                    "executable": None,
                    "directory": str(p.parent),
                    "reason": "Select F1_25.exe, not a game-service launcher executable.",
                }
            return {
                "valid": True,
                "executable": str(p),
                "directory": str(p.parent),
                "reason": "Valid game executable.",
            }

        if not p.is_dir():
            return {"valid": False, "executable": None, "directory": None, "reason": "Directory does not exist."}

        exe = GameAdapter.find_executable(game_name, p, preferred_exe=preferred_exe)
        if exe is None:
            profile = GAME_PROFILES.get(game_name)
            expected = ", ".join(profile.executables) if profile else f"{game_name}.exe"
            return {
                "valid": False,
                "executable": None,
                "directory": str(p),
                "reason": f"Executable not found in directory (expected {expected}).",
            }
        return {
            "valid": True,
            "executable": str(exe),
            "directory": str(p),
            "reason": "Valid game directory.",
        }

    def detect_installed_games(self) -> dict[str, str]:
        detected: dict[str, str] = {}
        candidate_roots: list[Path] = []

        import sys

        if sys.platform.startswith("win"):
            for drive in ("C:", "D:", "E:", "F:"):
                candidate_roots.extend([
                    Path(f"{drive}\\Program Files (x86)\\Steam\\steamapps\\common"),
                    Path(f"{drive}\\Program Files\\Steam\\steamapps\\common"),
                    Path(f"{drive}\\SteamLibrary\\steamapps\\common"),
                    Path(f"{drive}\\Program Files\\EA Games"),
                    Path(f"{drive}\\Games"),
                ])
        else:
            home = Path.home()
            candidate_roots.extend([
                home / ".local/share/Steam/steamapps/common",
                home / ".steam/steam/steamapps/common",
            ])

        for game_name in GAME_PROFILES:
            configured = self.config.get_game_path(game_name)
            pref_exe = self.config.get_game_executable(game_name)
            if configured and self.validate_game_directory(game_name, configured, preferred_exe=pref_exe)["valid"]:
                detected[game_name] = configured
                continue

            folder_variants = [game_name, game_name.replace(" ", "_"), game_name.replace(" ", "")]
            for root in candidate_roots:
                if not root.is_dir():
                    continue
                for variant in folder_variants:
                    cand = root / variant
                    if cand.is_dir() and self.validate_game_directory(game_name, str(cand))["valid"]:
                        detected[game_name] = str(cand)
                        self.config.set_game_path(game_name, str(cand))
                        self.logger.info(f"Auto-detected {game_name} at {cand}")
                        break
                if game_name in detected:
                    break

        return detected

    def launch_game(self, game_name: str, extra_args: list[str] | None = None) -> dict:
        if game_name != "F1 25":
            return {"ok": False, "error": "Only F1 25 can be launched by F1 Classic Launcher."}
        if not self.swofty_bridge:
            return {"ok": False, "error": "Swofty Core is required to launch F1 25 through this launcher."}
        game_dir_str = self.config.get_game_path(game_name)
        pref_exe = self.config.get_game_executable(game_name)
        check = self.validate_game_directory(game_name, game_dir_str, preferred_exe=pref_exe)
        if not check["valid"]:
            return {"ok": False, "error": check["reason"]}
        active_id = self.config.get_active_season(game_name)
        if (
            active_id
            and self.backup_manager
            and self.backup_manager.has_backup(game_name, active_id)
        ):
            return {
                "ok": False,
                "error": (
                    "This season was installed by the older direct-copy installer. "
                    "Deactivate it from the season page to restore the original files, then activate it again "
                    "to import it through Swofty."
                ),
            }
        modded = active_id is not None
        return self.swofty_bridge.launch_game(Path(check["directory"]), modded=modded)
