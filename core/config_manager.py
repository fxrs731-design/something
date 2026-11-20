import json
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
            "season_addons": {},
            "season_addon_fingerprints": {},
            "season_addon_orders": {},
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
                merged = dict(self.default_config)
                merged.update(loaded)
                return merged
            except Exception:
                pass
        self.save(self.default_config)
        return dict(self.default_config)

    def save(self, data: dict[str, Any] | None = None):
        if data is not None:
            self.data = data
        self.config_file.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

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
