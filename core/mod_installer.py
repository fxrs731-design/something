import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from core.game_detector import GameDetector
from core.logger import LauncherLogger
from core.season_manager import SeasonEntry, SeasonManager
from core.swofty_bridge import SwoftyBridge, WATERMARK_ADDON_ID


class ModInstaller:
    PROFILE = "default"
    SEASON_PROFILES = {2010: "f1classic_2010", 2012: "f1classic_2012"}

    def __init__(
        self,
        config: ConfigManager,
        season_manager: SeasonManager,
        game_detector: GameDetector,
        swofty_bridge: SwoftyBridge,
        backup_manager: BackupManager,
        logger: LauncherLogger,
    ):
        self.config = config
        self.season_manager = season_manager
        self.game_detector = game_detector
        self.swofty_bridge = swofty_bridge
        self.backup_manager = backup_manager
        self.logger = logger

    @classmethod
    def profile_for_season(cls, season_year: int) -> str:
        try:
            return cls.SEASON_PROFILES[season_year]
        except KeyError as exc:
            raise ValueError("Only the 2010 and 2012 F1 25 mod profiles are supported.") from exc

    @staticmethod
    def _is_swofty_addon_source(source: Path) -> bool:
        if source.is_dir():
            return (source / "swofty.json").is_file()
        if source.suffix.lower() == ".swoftyaddon":
            return True
        if source.suffix.lower() != ".zip":
            return False
        try:
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    normalized = member.filename.replace("\\", "/").strip("/")
                    parts = normalized.split("/")
                    if (
                        not parts
                        or parts[-1].casefold() != "swofty.json"
                        or len(parts) > 4
                        or member.file_size > 1024 * 1024
                    ):
                        continue
                    try:
                        manifest = json.loads(archive.read(member).decode("utf-8-sig"))
                    except (zipfile.BadZipFile, EOFError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(manifest, dict) and all(
                        isinstance(manifest.get(key), str) and manifest[key]
                        for key in ("id", "name", "version")
                    ):
                        return True
        except (OSError, zipfile.BadZipFile):
            return False
        return False

    def get_swofty_mod_profile(self, season_year: int) -> dict:
        profile = self.profile_for_season(season_year)
        enabled = self.swofty_bridge.ensure_simplified_importer(profile)
        if not enabled["ok"]:
            return {
                **enabled,
                "error": enabled.get("error") or enabled.get("stderr") or enabled.get("stdout") or "Could not prepare the Simplified Importer for this Swofty profile.",
            }
        state = self.swofty_bridge.list_simplified_mods(profile)
        if not state["ok"]:
            return {
                **state,
                "error": state.get("error") or state.get("stderr") or state.get("stdout") or "Could not read this Swofty profile.",
            }
        overrides_by_profile = self.config.data.get("swofty_mod_enabled_overrides", {})
        overrides = overrides_by_profile.get(profile, {}) if isinstance(overrides_by_profile, dict) else {}
        if not isinstance(overrides, dict):
            overrides = {}
        for mod in state["mods"]:
            mod["state_unknown"] = mod.get("enabled") is None
            if mod["state_unknown"] and isinstance(overrides.get(mod["id"]), bool):
                mod["enabled"] = overrides[mod["id"]]
        direct_by_profile = self.config.data.get("swofty_direct_addons", {})
        direct_ids = direct_by_profile.get(profile, []) if isinstance(direct_by_profile, dict) else []
        if not isinstance(direct_ids, list):
            direct_ids = []
        direct_addons = [
            state["addons"][addon_id]
            for addon_id in direct_ids
            if addon_id in state["addons"]
        ]
        return {
            "ok": True,
            "profile": profile,
            "mods": state["mods"],
            "direct_addons": direct_addons,
        }

    def import_swofty_mod(
        self,
        source_path: str | Path,
        season_year: int,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> dict:
        try:
            profile = self.profile_for_season(season_year)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        source = Path(source_path).expanduser()
        if not source.exists():
            return {"ok": False, "error": f"Selected mod package does not exist: {source}"}
        source = source.resolve()
        if source.is_file() and source.suffix.casefold() in (".erp", ".serps"):
            return {
                "ok": False,
                "error": (
                    "A standalone .erp file has no game-folder destination, and .serps is package metadata, "
                    "not a mod by itself. Import the original mod folder or its ZIP, 7z, or RAR archive."
                ),
            }

        if progress_cb:
            progress_cb(5, "Validating the package for the selected Swofty profile...")
        if self._is_swofty_addon_source(source):
            result = self.swofty_bridge.install_addon_from_path(source, profile)
            addon_ids = result.get("addon_ids") or ([result["addon_id"]] if result.get("addon_id") else [])
            if not result["ok"]:
                return {
                    **result,
                    "error": result.get("error") or result.get("stderr") or result.get("stdout") or "Swofty could not install this addon package.",
                }
            direct_by_profile = self.config.data.get("swofty_direct_addons")
            if not isinstance(direct_by_profile, dict):
                direct_by_profile = {}
                self.config.data["swofty_direct_addons"] = direct_by_profile
            saved_ids = direct_by_profile.get(profile)
            if not isinstance(saved_ids, list):
                saved_ids = []
                direct_by_profile[profile] = saved_ids
            for addon_id in addon_ids:
                if addon_id not in saved_ids:
                    saved_ids.append(addon_id)
            self.config.data["selected_swofty_profile_year"] = season_year
            self.config.save()
            return {
                "ok": True,
                "kind": "addon",
                "profile": profile,
                "addon_ids": addon_ids,
                "name": source.stem,
                "stdout": result.get("stdout", ""),
            }

        game_dir_str = self.config.get_game_path("F1 25")
        game_check = self.game_detector.validate_game_directory("F1 25", game_dir_str)
        if not game_check["valid"]:
            return {
                "ok": False,
                "error": f"Configure a valid F1 25 game folder before importing mods: {game_check['reason']}",
            }
        if progress_cb:
            progress_cb(15, "Simplified Importer is validating and converting the mod...")
        imported = self.swofty_bridge.import_mods(source, Path(game_check["directory"]), profile)
        if not imported["ok"]:
            return {
                **imported,
                "error": imported.get("error") or imported.get("stderr") or "Simplified Importer rejected the mod package.",
            }
        if progress_cb:
            progress_cb(95, "Saving the selected F1 25 profile...")
        self.config.data["selected_swofty_profile_year"] = season_year
        self.config.save()
        self.logger.info(
            f"Imported F1 25 mod source '{source.name}' into Swofty profile {profile}; "
            f"addon IDs: {', '.join(imported.get('addon_ids', [])) or 'none reported'}."
        )
        return {
            "ok": True,
            "kind": "mod",
            "profile": profile,
            "addon_ids": imported.get("addon_ids", []),
            "name": source.name,
            "stdout": imported.get("stdout", ""),
        }

    def set_swofty_mod_enabled(
        self,
        season_year: int,
        mod_id: str,
        enabled: bool,
        remember_unknown: bool = False,
    ) -> dict:
        try:
            profile = self.profile_for_season(season_year)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = self.swofty_bridge.set_simplified_mod_enabled(mod_id, enabled, profile)
        if result["ok"] and remember_unknown:
            overrides = self.config.data.get("swofty_mod_enabled_overrides")
            if not isinstance(overrides, dict):
                overrides = {}
                self.config.data["swofty_mod_enabled_overrides"] = overrides
            profile_overrides = overrides.get(profile)
            if not isinstance(profile_overrides, dict):
                profile_overrides = {}
                overrides[profile] = profile_overrides
            profile_overrides[mod_id] = bool(enabled)
            self.config.save()
        return result

    def set_swofty_addon_enabled(self, season_year: int, addon_id: str, enabled: bool) -> dict:
        try:
            profile = self.profile_for_season(season_year)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return self.swofty_bridge.set_addon_enabled(addon_id, enabled, profile)

    def move_swofty_mod(self, season_year: int, mod_id: str, position: int) -> dict:
        try:
            profile = self.profile_for_season(season_year)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return self.swofty_bridge.move_simplified_mod(mod_id, position, profile)

    def check_swofty_profile_conflicts(self, season_year: int) -> dict:
        try:
            profile = self.profile_for_season(season_year)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        game_dir_str = self.config.get_game_path("F1 25")
        game_check = self.game_detector.validate_game_directory("F1 25", game_dir_str)
        if not game_check["valid"]:
            return {"ok": False, "error": f"Configure a valid F1 25 game folder first: {game_check['reason']}"}
        return self.swofty_bridge.check_conflicts(Path(game_check["directory"]), profile)

    @staticmethod
    def _safe_relative_path(raw_path: str) -> Path:
        normalized = str(raw_path).replace("\\", "/")
        pure = PurePosixPath(normalized)
        parts = [part for part in pure.parts if part not in ("", ".")]
        if (
            not normalized
            or pure.is_absolute()
            or not parts
            or any(part == ".." for part in parts)
            or (len(normalized) > 1 and normalized[1] == ":")
        ):
            raise ValueError(f"Unsafe package path: {raw_path}")
        return Path(*parts)

    @staticmethod
    def _within(root: Path, candidate: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _resolve_source(self, entry: SeasonEntry, source: str) -> Path:
        relative = self._safe_relative_path(source)
        path = entry.package_dir / relative
        if not self._within(entry.package_dir, path) or not path.exists():
            raise ValueError(f"Package source is missing or outside the package: {source}")
        return path

    def _copy_file(self, source: Path, stage: Path, target: str) -> int:
        relative_target = self._safe_relative_path(target)
        destination = stage / relative_target
        if not self._within(stage, destination):
            raise ValueError(f"Unsafe game target path: {target}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return 1

    def _copy_tree(self, source: Path, stage: Path, target: str) -> int:
        relative_target = Path() if target in ("", ".") else self._safe_relative_path(target)
        count = 0
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            if item.is_symlink() or not self._within(source, item):
                raise ValueError(f"Package contains a file outside its source folder: {item}")
            destination = stage / relative_target / item.relative_to(source)
            if not self._within(stage, destination):
                raise ValueError(f"Unsafe game target path: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            count += 1
        return count

    def _extract_zip(self, archive: Path, stage: Path, target: str) -> int:
        relative_target = Path() if target in ("", ".") else self._safe_relative_path(target)
        count = 0
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                relative_member = self._safe_relative_path(member.filename.rstrip("/"))
                destination = stage / relative_target / relative_member
                if not self._within(stage, destination):
                    raise ValueError(f"Unsafe path in package archive: {member.filename}")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                mode = member.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    raise ValueError(f"Symbolic links are not allowed in package archives: {member.filename}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, destination.open("wb") as target_file:
                    shutil.copyfileobj(source, target_file)
                count += 1
        return count

    def _build_stage(self, entry: SeasonEntry, stage: Path) -> int:
        file_count = 0
        for operation in entry.manifest.operations:
            op_type = operation["type"]
            source_rel = operation.get("source", "")
            target_rel = operation.get("target", ".")
            if op_type in ("copy_file", "replace_file"):
                source = self._resolve_source(entry, source_rel)
                if not source.is_file():
                    raise ValueError(f"Expected a file for operation source: {source_rel}")
                target_file = source_rel if target_rel in ("", ".") else target_rel
                file_count += self._copy_file(source, stage, target_file)
            elif op_type in ("copy_dir", "bydanix_merge"):
                source = self._resolve_source(entry, source_rel)
                if not source.is_dir():
                    raise ValueError(f"Expected a folder for operation source: {source_rel}")
                file_count += self._copy_tree(source, stage, target_rel)
            elif op_type == "extract_zip":
                source = self._resolve_source(entry, source_rel)
                if not source.is_file():
                    raise ValueError(f"Expected a ZIP file for operation source: {source_rel}")
                file_count += self._extract_zip(source, stage, target_rel)
        return file_count

    def _season_addons(self) -> dict[str, list[str]]:
        return self.config.data.setdefault("season_addons", {})

    def _season_addon_orders(self) -> dict[str, list[str]]:
        return self.config.data.setdefault("season_addon_orders", {})

    def _season_addon_fingerprints(self) -> dict[str, str]:
        return self.config.data.setdefault("season_addon_fingerprints", {})

    @staticmethod
    def _import_stage_name(entry: SeasonEntry, existing: list[str]) -> str:
        for addon_id in existing:
            if addon_id.startswith("mod."):
                old_source_id = addon_id[4:].split(".", 1)[0]
                if old_source_id.startswith("f1classic-swofty-import-"):
                    return old_source_id
        source_hash = hashlib.sha256(entry.manifest.id.encode("utf-8")).hexdigest()[:20]
        return f"f1classic_swofty_import_{source_hash}"

    def _import_entry(self, entry: SeasonEntry, game_dir: Path) -> list[str]:
        existing = list(self._season_addons().get(entry.manifest.id, []))

        if entry.manifest.adapter == "swofty_addon_compat" and (entry.package_dir / "swofty.json").is_file():
            installed = self.swofty_bridge.install_addon_package(
                entry.package_dir,
                entry.manifest.id,
                self.PROFILE,
            )
            if not installed["ok"]:
                raise RuntimeError(installed.get("stderr") or installed.get("error") or "Swofty addon installation failed.")
            return [entry.manifest.id]

        fingerprint = self.swofty_bridge.content_fingerprint(entry.package_dir)
        known_fingerprint = self._season_addon_fingerprints().get(entry.manifest.id)
        if (
            existing
            and known_fingerprint == fingerprint
            and self.swofty_bridge.has_addons(existing)
        ):
            return existing

        with tempfile.TemporaryDirectory(prefix="f1classic_swofty_import_") as temp:
            stage = Path(temp) / self._import_stage_name(entry, existing)
            stage.mkdir()
            count = self._build_stage(entry, stage)
            if count == 0:
                raise ValueError(f"'{entry.manifest.name}' contains no game files to import.")
            imported = self.swofty_bridge.import_mods(
                stage,
                game_dir,
                self.PROFILE,
                known_addon_ids=existing,
            )
            if not imported["ok"]:
                raise RuntimeError(imported.get("stderr") or imported.get("error") or "Simplified Importer failed.")

        addon_ids = list(imported["addon_ids"])
        if not addon_ids:
            raise RuntimeError("Simplified Importer returned no addon IDs for the imported package.")
        self._season_addon_fingerprints()[entry.manifest.id] = fingerprint
        return addon_ids

    def _year_addons(
        self,
        season_year: int,
        entries: list[SeasonEntry] | None = None,
    ) -> list[str]:
        mapping = self._season_addons()
        discovered: list[str] = []
        season_entries = entries if entries is not None else self.season_manager.scan_seasons()
        for entry in sorted(season_entries, key=lambda item: (item.imported_at, item.manifest.id)):
            if entry.manifest.game.lower() != "f1 25" or entry.manifest.season != season_year:
                continue
            for addon_id in mapping.get(entry.manifest.id, []):
                if addon_id not in discovered:
                    discovered.append(addon_id)
        saved = self._season_addon_orders().get(str(season_year), [])
        ordered = [addon_id for addon_id in saved if addon_id in discovered]
        ordered.extend(addon_id for addon_id in discovered if addon_id not in ordered)
        return ordered

    def get_season_addon_order(self, season_year: int) -> list[str]:
        return self._year_addons(season_year)

    def install_season(
        self,
        season_id: str,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> dict:
        def report(pct: int, message: str):
            if progress_cb:
                progress_cb(pct, message)

        all_entries = self.season_manager.scan_seasons()
        entry = self.season_manager.get_season(season_id, all_entries)
        if not entry:
            return {"ok": False, "error": f"Season '{season_id}' not found."}
        if entry.manifest.game.lower() != "f1 25":
            return {"ok": False, "error": "Only mods targeting F1 25 can be activated."}

        game_dir_str = self.config.get_game_path("F1 25")
        game_check = self.game_detector.validate_game_directory("F1 25", game_dir_str)
        if not game_check["valid"]:
            return {"ok": False, "error": f"F1 25 directory is invalid or not configured: {game_check['reason']}"}
        game_dir = Path(game_check["directory"])
        legacy_active_id = self.config.get_active_season("F1 25")
        if legacy_active_id and self.backup_manager.has_backup("F1 25", legacy_active_id):
            return {
                "ok": False,
                "error": (
                    "This season still has a direct-copy backup from an older launcher version. "
                    "Deactivate it first to restore the original game files, then activate it again through Swofty."
                ),
            }

        available_ids = {season.manifest.id for season in all_entries}
        missing_deps = [
            dependency
            for dependency in entry.manifest.dependencies
            if not dependency.lower().startswith("swofty.") and dependency not in available_ids
        ]
        if missing_deps:
            return {"ok": False, "error": f"Missing required dependencies: {', '.join(missing_deps)}"}

        entries = [
            season
            for season in all_entries
            if season.manifest.game.lower() == "f1 25" and season.manifest.season == entry.manifest.season
        ]
        old_active_id = self.config.get_active_season("F1 25")
        old_entry = self.season_manager.get_season(old_active_id, all_entries) if old_active_id else None
        old_addon_ids = self._year_addons(old_entry.manifest.season, all_entries) if old_entry else []
        imported_this_run: list[str] = []
        resolved: dict[str, list[str]] = {}

        try:
            for index, season in enumerate(entries):
                report(10 + int((index / max(len(entries), 1)) * 55), f"Converting {season.manifest.name} with Simplified Importer...")
                before = set(self._season_addons().get(season.manifest.id, []))
                addon_ids = self._import_entry(season, game_dir)
                imported_this_run.extend(addon_id for addon_id in addon_ids if addon_id not in before)
                resolved[season.manifest.id] = addon_ids
        except Exception as exc:
            for addon_id in imported_this_run:
                self.swofty_bridge.set_addon_enabled(addon_id, False, self.PROFILE)
            if resolved:
                self._season_addons().update(resolved)
                self.config.save()
            self.logger.exception(f"Swofty conversion failed for season '{season_id}': {exc}")
            return {"ok": False, "error": str(exc)}

        self._season_addons().update(resolved)
        self.config.save()
        new_addon_ids: list[str] = []
        for season in sorted(entries, key=lambda item: (item.imported_at, item.manifest.id)):
            for addon_id in resolved.get(season.manifest.id, []):
                if addon_id not in new_addon_ids:
                    new_addon_ids.append(addon_id)
        report(70, "Switching Swofty profile to the selected season...")
        for addon_id in old_addon_ids:
            if addon_id not in new_addon_ids:
                disabled = self.swofty_bridge.set_addon_enabled(addon_id, False, self.PROFILE)
                if not disabled["ok"]:
                    for rollback_id in old_addon_ids:
                        self.swofty_bridge.set_addon_enabled(rollback_id, True, self.PROFILE)
                    return {"ok": False, "error": disabled.get("stderr") or disabled.get("error") or f"Could not disable {addon_id}."}

        for addon_id in new_addon_ids:
            enabled = self.swofty_bridge.set_addon_enabled(addon_id, True, self.PROFILE)
            if not enabled["ok"]:
                for rollback_id in new_addon_ids:
                    self.swofty_bridge.set_addon_enabled(rollback_id, False, self.PROFILE)
                for rollback_id in old_addon_ids:
                    self.swofty_bridge.set_addon_enabled(rollback_id, True, self.PROFILE)
                return {"ok": False, "error": enabled.get("stderr") or enabled.get("error") or f"Could not enable {addon_id}."}

        watermark = self.swofty_bridge.ensure_watermark_addon(self.PROFILE)
        if not watermark["ok"]:
            for addon_id in new_addon_ids:
                self.swofty_bridge.set_addon_enabled(addon_id, False, self.PROFILE)
            for addon_id in old_addon_ids:
                self.swofty_bridge.set_addon_enabled(addon_id, True, self.PROFILE)
            return {"ok": False, "error": watermark.get("stderr") or watermark.get("error") or "Could not enable the watermark addon."}

        reordered = self.swofty_bridge.reorder_addons(new_addon_ids, old_addon_ids, self.PROFILE)
        if not reordered["ok"]:
            if not old_addon_ids:
                self.swofty_bridge.set_addon_enabled(WATERMARK_ADDON_ID, False, self.PROFILE)
            return {"ok": False, "error": reordered.get("stderr") or reordered.get("error") or "Could not set Swofty addon load order."}

        report(85, "Checking Swofty build and mod conflicts...")
        conflicts = self.swofty_bridge.check_conflicts(game_dir, self.PROFILE)
        if not conflicts["ok"]:
            for addon_id in new_addon_ids:
                if addon_id not in old_addon_ids:
                    self.swofty_bridge.set_addon_enabled(addon_id, False, self.PROFILE)
            if old_addon_ids:
                self.swofty_bridge.reorder_addons(old_addon_ids, [], self.PROFILE)
            if old_addon_ids:
                self.swofty_bridge.set_addon_enabled(WATERMARK_ADDON_ID, True, self.PROFILE)
            else:
                watermark = self.swofty_bridge.set_addon_enabled(WATERMARK_ADDON_ID, False, self.PROFILE)
                if not watermark["ok"]:
                    return {"ok": False, "error": watermark.get("stderr") or watermark.get("error") or "Could not disable the watermark addon."}
            return {
                "ok": False,
                "error": conflicts.get("stderr") or conflicts.get("error") or "Swofty could not build the selected season.",
                "stdout": conflicts.get("stdout", ""),
            }

        saved_order = self._season_addon_orders().get(str(entry.manifest.season), [])
        self._season_addon_orders()[str(entry.manifest.season)] = [
            addon_id for addon_id in saved_order if addon_id in new_addon_ids
        ] + [addon_id for addon_id in new_addon_ids if addon_id not in saved_order]
        self.config.set_active_season("F1 25", season_id)
        self.config.save()
        conflict_output = (conflicts.get("stdout") or "").strip()
        self.logger.info(
            f"Activated F1 {entry.manifest.season} season using Swofty addon(s): {', '.join(new_addon_ids)}. "
            f"Conflict check: {conflict_output or 'no conflicts reported'}"
        )
        report(100, f"Swofty profile ready ({len(new_addon_ids)} addon(s)).")
        return {
            "ok": True,
            "files_modified": 0,
            "addon_count": len(new_addon_ids),
            "season": entry.manifest.season,
            "conflicts": conflict_output,
        }

    def reorder_season_addons(self, season_id: str, addon_ids: list[str]) -> dict:
        entries = self.season_manager.scan_seasons()
        entry = self.season_manager.get_season(season_id, entries)
        if not entry or entry.manifest.game.lower() != "f1 25":
            return {"ok": False, "error": "Select an imported F1 25 season first."}
        previous = self._year_addons(entry.manifest.season, entries)
        if len(addon_ids) != len(set(addon_ids)) or set(addon_ids) != set(previous):
            return {"ok": False, "error": "The requested load order must contain each addon for this season exactly once."}
        active_id = self.config.get_active_season("F1 25")
        active_entry = self.season_manager.get_season(active_id, entries) if active_id else None
        if not active_entry or active_entry.manifest.season != entry.manifest.season:
            return {"ok": False, "error": "Activate this season before changing its Swofty load order."}

        reordered = self.swofty_bridge.reorder_addons(addon_ids, previous, self.PROFILE)
        if not reordered["ok"]:
            return reordered
        game_dir_str = self.config.get_game_path("F1 25")
        if not game_dir_str or not Path(game_dir_str).is_dir():
            self.swofty_bridge.reorder_addons(previous, addon_ids, self.PROFILE)
            return {"ok": False, "error": "Configure a valid F1 25 directory before changing addon load order."}
        game_dir = Path(game_dir_str)
        conflicts = self.swofty_bridge.check_conflicts(game_dir, self.PROFILE)
        if not conflicts["ok"]:
            self.swofty_bridge.reorder_addons(previous, addon_ids, self.PROFILE)
            return {"ok": False, "error": conflicts.get("stderr") or conflicts.get("error") or "Swofty could not rebuild the profile."}

        self._season_addon_orders()[str(entry.manifest.season)] = list(addon_ids)
        self.config.save()
        return {"ok": True, "conflicts": (conflicts.get("stdout") or "").strip()}

    def uninstall_season(self, season_id: str) -> dict:
        entries = self.season_manager.scan_seasons()
        entry: SeasonEntry | None = self.season_manager.get_season(season_id, entries)
        if not entry:
            return {"ok": False, "error": f"Season '{season_id}' not found."}
        if entry.manifest.game.lower() != "f1 25":
            return {"ok": False, "error": "Only F1 25 mods can be deactivated."}

        active_id = self.config.get_active_season("F1 25")
        active_entry = self.season_manager.get_season(active_id, entries) if active_id else None
        if not active_entry or active_entry.manifest.season != entry.manifest.season:
            return {"ok": True, "restored": 0, "disabled_addons": 0}

        if self.backup_manager.has_backup("F1 25", active_id):
            game_dir_str = self.config.get_game_path("F1 25")
            if not game_dir_str or not Path(game_dir_str).is_dir():
                return {"ok": False, "error": "Configure the F1 25 game directory before restoring the older direct-copy install."}
            restored = self.backup_manager.restore_backup("F1 25", active_id, Path(game_dir_str))
            if not restored["ok"]:
                return restored
            self.config.set_active_season("F1 25", None)
            self.logger.info(f"Restored legacy direct-copy season {active_id} to the original F1 25 files.")
            return {"ok": True, "restored": restored.get("restored", 0), "disabled_addons": 0}

        addon_ids = self._year_addons(entry.manifest.season, entries)
        for addon_id in addon_ids:
            result = self.swofty_bridge.set_addon_enabled(addon_id, False, self.PROFILE)
            if not result["ok"]:
                return {"ok": False, "error": result.get("stderr") or result.get("error") or f"Could not disable {addon_id}."}
        watermark = self.swofty_bridge.set_addon_enabled(WATERMARK_ADDON_ID, False, self.PROFILE)
        if not watermark["ok"]:
            for addon_id in addon_ids:
                self.swofty_bridge.set_addon_enabled(addon_id, True, self.PROFILE)
            return {"ok": False, "error": watermark.get("stderr") or watermark.get("error") or "Could not disable the watermark addon."}

        self.config.set_active_season("F1 25", None)
        self.logger.info(
            f"Deactivated F1 {entry.manifest.season} season in Swofty; imported addons remain stored but disabled."
        )
        return {"ok": True, "restored": 0, "disabled_addons": len(addon_ids)}
