import subprocess
import sys
from pathlib import Path

from core.config_manager import ConfigManager
from core.game_adapters import GAME_PROFILES, GameAdapter
from core.logger import LauncherLogger


class GameDetector:
    def __init__(self, config: ConfigManager, logger: LauncherLogger):
        self.config = config
        self.logger = logger

    def validate_game_directory(self, game_name: str, directory_or_exe: str, preferred_exe: str = "") -> dict:
        if not directory_or_exe:
            return {"valid": False, "executable": None, "directory": None, "reason": "Path is empty."}

        p = Path(directory_or_exe)
        if p.is_file() and p.suffix.lower() == ".exe":
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
        game_dir_str = self.config.get_game_path(game_name)
        pref_exe = self.config.get_game_executable(game_name)
        check = self.validate_game_directory(game_name, game_dir_str, preferred_exe=pref_exe)
        if not check["valid"]:
            return {"ok": False, "error": check["reason"]}

        exe_path = Path(check["executable"])
        cmd = [str(exe_path)]
        if extra_args:
            cmd.extend(extra_args)

        try:
            proc = subprocess.Popen(cmd, cwd=str(exe_path.parent))
            self.logger.info(f"Launched {game_name} (PID {proc.pid}) using {exe_path}")
            return {"ok": True, "pid": proc.pid, "executable": str(exe_path)}
        except Exception as exc:
            self.logger.error(f"Failed to launch {game_name}: {exc}")
            return {"ok": False, "error": str(exc)}
