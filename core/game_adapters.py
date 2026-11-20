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
    "F1 25": GameProfile("F1 25", ("F1_25.exe",), ("asset_groups",), "3059520", "ego-modern"),
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
        game_dir = Path(game_dir)
        if preferred_exe:
            pref_path = Path(preferred_exe)
            if not pref_path.is_absolute():
                pref_path = game_dir / pref_path
            profile = GAME_PROFILES.get(game_name)
            if (
                profile
                and pref_path.is_file()
                and pref_path.name.lower() in {name.lower() for name in profile.executables}
                and pref_path.resolve().parent == game_dir.resolve()
            ):
                return pref_path

        if not game_dir.is_dir():
            return None

        profile = GAME_PROFILES.get(game_name)
        if profile:
            for exe in profile.executables:
                candidate = game_dir / exe
                if candidate.is_file():
                    return candidate
        return None
