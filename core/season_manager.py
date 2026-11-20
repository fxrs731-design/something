import shutil
from dataclasses import dataclass
from pathlib import Path

from core.config_manager import ConfigManager
from core.logger import LauncherLogger
from core.manifest_parser import ManifestParser, ManifestValidationError, SeasonManifest


@dataclass
class SeasonEntry:
    manifest: SeasonManifest
    package_dir: Path
    installed: bool
    active_for_game: bool
    imported_at: float
    artwork_path: Path | None


class SeasonManager:
    def __init__(self, config: ConfigManager, logger: LauncherLogger):
        self.config = config
        self.logger = logger
        self.seasons_dir = Path(self.config.data["seasons_dir"])
        self.seasons_dir.mkdir(parents=True, exist_ok=True)
        self.invalid_packages: list[dict] = []

    def scan_seasons(self) -> list[SeasonEntry]:
        self.seasons_dir = Path(self.config.data["seasons_dir"])
        self.seasons_dir.mkdir(parents=True, exist_ok=True)
        entries: list[SeasonEntry] = []
        self.invalid_packages = []

        for item in sorted(self.seasons_dir.iterdir()):
            if not item.is_dir():
                continue
            manifest_file = item / "manifest.json"
            swofty_file = item / "swofty.json"
            active_manifest_file = manifest_file if manifest_file.is_file() else swofty_file
            if not active_manifest_file.is_file():
                self.invalid_packages.append({
                    "folder": item.name,
                    "errors": ["Missing manifest.json or swofty.json"],
                })
                continue
            try:
                manifest = ManifestParser.parse_package(item)
                active_id = self.config.get_active_season(manifest.game)
                is_active = active_id == manifest.id
                art_path = (item / manifest.artwork) if manifest.artwork else None
                if art_path and not art_path.is_file():
                    art_path = None
                entries.append(
                    SeasonEntry(
                        manifest=manifest,
                        package_dir=item,
                        installed=is_active,
                        active_for_game=is_active,
                        imported_at=active_manifest_file.stat().st_mtime,
                        artwork_path=art_path,
                    )
                )
            except ManifestValidationError as exc:
                self.logger.warning(f"Invalid season package in {item.name}: {exc}")
                self.invalid_packages.append({
                    "folder": item.name,
                    "errors": exc.errors,
                })

        return entries

    def get_season(self, season_id: str) -> SeasonEntry | None:
        for entry in self.scan_seasons():
            if entry.manifest.id == season_id:
                return entry
        return None

    def query_seasons(
        self,
        search: str = "",
        year: int | None = None,
        game: str = "",
        sort_by: str = "season_desc",
    ) -> list[SeasonEntry]:
        entries = self.scan_seasons()
        q = search.strip().lower()
        if q:
            entries = [
                e for e in entries
                if q in e.manifest.name.lower()
                or q in e.manifest.author.lower()
                or q in e.manifest.description.lower()
                or q in e.manifest.game.lower()
                or q in str(e.manifest.season)
            ]
        if year is not None:
            entries = [e for e in entries if e.manifest.season == year]
        if game:
            entries = [e for e in entries if e.manifest.game.lower() == game.lower()]

        if sort_by == "season_asc":
            entries.sort(key=lambda e: (e.manifest.season, e.manifest.name.lower()))
        elif sort_by == "name_asc":
            entries.sort(key=lambda e: e.manifest.name.lower())
        elif sort_by == "recent":
            entries.sort(key=lambda e: e.imported_at, reverse=True)
        else:
            entries.sort(key=lambda e: (-e.manifest.season, e.manifest.name.lower()))
        return entries

    def remove_season(self, season_id: str) -> dict:
        entry = self.get_season(season_id)
        if not entry:
            return {"ok": False, "error": f"Season '{season_id}' not found."}
        if entry.installed:
            return {
                "ok": False,
                "error": f"Season '{entry.manifest.name}' is currently installed in {entry.manifest.game}. Uninstall it first.",
            }
        shutil.rmtree(entry.package_dir, ignore_errors=True)
        self.logger.info(f"Removed season package '{season_id}'.")
        return {"ok": True}
