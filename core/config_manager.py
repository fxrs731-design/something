import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ConfigManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.config_dir = self.base_dir / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "settings.json"
        self.default_config: dict[str, Any] = {
            "seasons_dir": str(self.base_dir / "seasons"),
            "backups_dir": str(self.base_dir / "backups"),
            "imports_dir": str(self.base_dir / "imports"),
            "logs_dir": str(self.base_dir / "logs"),
            "swofty_data_dir": str(self.base_dir / "Swofty"),
            "default_game": "F1 25",
            "game_paths": {},
            "game_executables": {},
            "active_seasons": {},
            "external_launch_enabled": False,
            "external_launch_profile": None,
            "season_addons": {},
            "season_addon_fingerprints": {},
            "season_addon_orders": {},
            "swofty_direct_addons": {},
            "swofty_mod_enabled_overrides": {},
            "selected_swofty_profile_year": 2010,
            "appearance": {
                "theme": "f1-dark",
                "accent": "#e10600",
                "animations": True,
            },
            "updates": {
                "check_on_startup": False,
                "channel": "stable",
            },
            "app_version": "1.0.0",
        }
        self.data = self.load()
        self.ensure_directories()

    def load(self) -> dict[str, Any]:
        if self.config_file.exists():
            try:
                loaded = json.loads(self.config_file.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("settings must be a JSON object")
                merged = dict(self.default_config)
                merged.update(loaded)
                return merged
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"Could not load launcher settings from {self.config_file}: {exc}") from exc
        self.save(self.default_config)
        return dict(self.default_config)

    def save(self, data: dict[str, Any] | None = None):
        if data is not None:
            self.data = data
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config_dir,
                prefix=".settings-",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                json.dump(self.data, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, self.config_file)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def ensure_directories(self):
        for key in ("seasons_dir", "backups_dir", "imports_dir", "logs_dir", "swofty_data_dir"):
            Path(self.data[key]).mkdir(parents=True, exist_ok=True)

    def get_game_path(self, game_name: str) -> str:
        return self.data.get("game_paths", {}).get(game_name, "")

    def set_game_path(self, game_name: str, path: str):
        self.data.setdefault("game_paths", {})[game_name] = path
        self.save()

    def get_game_executable(self, game_name: str) -> str:
        return self.data.get("game_executables", {}).get(game_name, "")

    def set_game_executable(self, game_name: str, exe_path: str):
        self.data.setdefault("game_executables", {})[game_name] = exe_path
        self.save()

    def get_active_season(self, game_name: str) -> str | None:
        return self.data.get("active_seasons", {}).get(game_name)

    def set_active_season(self, game_name: str, season_id: str | None):
        active = self.data.setdefault("active_seasons", {})
        if season_id is None:
            active.pop(game_name, None)
        else:
            active[game_name] = season_id
        self.save()

    def get_external_launch_enabled(self) -> bool:
        return bool(self.data.get("external_launch_enabled", False))

    def set_external_launch_enabled(self, enabled: bool):
        self.data["external_launch_enabled"] = bool(enabled)
        self.save()

    def get_external_launch_profile(self) -> str | None:
        profile_id = self.data.get("external_launch_profile")
        return profile_id if isinstance(profile_id, str) and profile_id else None

    def set_external_launch_profile(self, profile_id: str | None):
        self.data["external_launch_profile"] = profile_id
        self.save()
