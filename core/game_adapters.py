from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GameProfile:
    name: str
    executables: tuple[str, ...]
    expected_subdirs: tuple[str, ...]
    steam_app_id: str = ""
    era: str = "ego"


GAME_PROFILES: dict[str, GameProfile] = {
    "F1 2011": GameProfile("F1 2011", ("F1_2011.exe", "f1_2011.exe"), ("cars", "tracks", "database"), "44360", "ego-classic"),
    "F1 2013": GameProfile("F1 2013", ("F1_2013.exe", "f1_2013.exe"), ("cars", "tracks", "database"), "223670", "ego-classic"),
    "F1 2014": GameProfile("F1 2014", ("F1_2014.exe", "f1_2014.exe"), ("cars", "tracks", "database"), "226580", "ego-classic"),
    "F1 2015": GameProfile("F1 2015", ("F1_2015.exe",), ("asset_groups",), "286570", "ego-modern"),
    "F1 2016": GameProfile("F1 2016", ("F1_2016.exe",), ("asset_groups",), "391040", "ego-modern"),
    "F1 2017": GameProfile("F1 2017", ("F1_2017.exe",), ("asset_groups",), "515220", "ego-modern"),
    "F1 2018": GameProfile("F1 2018", ("F1_2018.exe", "F1_2018_dx12.exe"), ("asset_groups",), "737800", "ego-modern"),
    "F1 2019": GameProfile("F1 2019", ("F1_2019.exe", "F1_2019_dx12.exe"), ("asset_groups",), "928600", "ego-modern"),
    "F1 2020": GameProfile("F1 2020", ("F1_2020.exe", "F1_2020_dx12.exe"), ("asset_groups",), "1080110", "ego-modern"),
    "F1 2021": GameProfile("F1 2021", ("F1_2021_dx12.exe", "F1_2021.exe"), ("asset_groups",), "1134570", "ego-modern"),
    "F1 22": GameProfile("F1 22", ("F1_22.exe", "EAAntiCheat.GameServiceLauncher.exe"), ("asset_groups",), "1692250", "ego-modern"),
    "F1 23": GameProfile("F1 23", ("F1_23.exe", "EAAntiCheat.GameServiceLauncher.exe"), ("asset_groups",), "2108330", "ego-modern"),
    "F1 24": GameProfile("F1 24", ("F1_24.exe", "EAAntiCheat.GameServiceLauncher.exe"), ("asset_groups",), "2488620", "ego-modern"),
    "F1 25": GameProfile("F1 25", ("EAAntiCheat.GameServiceLauncher.exe", "F1_25.exe"), ("asset_groups",), "3059520", "ego-modern"),
}


class GameAdapter:
    @staticmethod
    def list_supported_games() -> list[str]:
        return list(GAME_PROFILES.keys())

    @staticmethod
    def get_profile(game_name: str) -> GameProfile | None:
        return GAME_PROFILES.get(game_name)

    @staticmethod
    def validate_compatibility(manifest_game: str, target_game: str) -> tuple[bool, str]:
        if manifest_game.strip().lower() != target_game.strip().lower():
            return (
                False,
                f"Incompatible game: season targets '{manifest_game}', but selected game is '{target_game}'.",
            )
        return True, "Compatible"

    @staticmethod
    def find_executable(game_name: str, game_dir: Path, preferred_exe: str = "") -> Path | None:
        if preferred_exe:
            pref_path = Path(preferred_exe)
            if pref_path.is_file():
                return pref_path
            if game_dir.is_dir() and (game_dir / preferred_exe).is_file():
                return game_dir / preferred_exe

        if not game_dir.is_dir():
            return None

        profile = GAME_PROFILES.get(game_name)
        if profile:
            for exe in profile.executables:
                candidate = game_dir / exe
                if candidate.is_file():
                    return candidate
        for candidate in game_dir.glob("*.exe"):
            if "f1" in candidate.name.lower() or "eaanticheat" in candidate.name.lower():
                return candidate
        return None
