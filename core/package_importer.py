import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from core.config_manager import ConfigManager
from core.logger import LauncherLogger
from core.manifest_parser import ManifestParser, ManifestValidationError
from core.season_manager import SeasonManager


class PackageImporter:
    def __init__(self, config: ConfigManager, season_manager: SeasonManager, logger: LauncherLogger):
        self.config = config
        self.season_manager = season_manager
        self.logger = logger

    def _clean_path_str(self, raw_path: str) -> str:
        s = raw_path.strip()
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            s = s[1:-1].strip()
        return s

    def _find_manifest_roots(self, root_dir: Path) -> list[Path]:
        if (root_dir / "manifest.json").is_file() or (root_dir / "swofty.json").is_file():
            return [root_dir]

        found: list[Path] = []
        seen: set[Path] = set()
        required_manifest_fields = {
            "schema_version", "id", "name", "version", "author",
            "description", "game", "season",
        }
        for current, directories, filenames in os.walk(root_dir):
            current_path = Path(current)
            depth = len(current_path.relative_to(root_dir).parts)
            directories[:] = sorted(
                name for name in directories
                if name != "__MACOSX" and depth < 3
            )
            for filename in ("manifest.json", "swofty.json"):
                if filename not in filenames:
                    continue
                candidate = current_path
                if candidate in seen:
                    continue
                try:
                    data = json.loads((candidate / filename).read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if filename == "manifest.json":
                    is_package = required_manifest_fields.issubset(data)
                else:
                    is_package = bool(data.get("id") or data.get("name"))
                if is_package:
                    seen.add(candidate)
                    found.append(candidate)
        return found

    @staticmethod
    def _safe_extract(zf: zipfile.ZipFile, destination: Path, progress_cb=None, progress_start: int = 0, progress_end: int = 100):
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        members = zf.infolist()
        for index, member in enumerate(members):
            raw_name = member.filename.replace("\\", "/")
            pure = PurePosixPath(raw_name)
            parts = [part for part in pure.parts if part not in ("", ".")]
            if (
                not parts
                or pure.is_absolute()
                or any(part == ".." for part in parts)
                or (len(raw_name) > 1 and raw_name[1] == ":")
            ):
                if not parts:
                    continue
                raise ValueError(f"Unsafe path in ZIP archive: {member.filename}")
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"Symbolic links are not allowed in ZIP archives: {member.filename}")
            target = destination.joinpath(*parts)
            try:
                target.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Unsafe path in ZIP archive: {member.filename}") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            if progress_cb:
                percent = progress_start + int((index + 1) / max(len(members), 1) * (progress_end - progress_start))
                progress_cb(percent, f"Extracting package ({index + 1}/{len(members)} entries)...")

    @staticmethod
    def _copy_raw_folder(source: Path, destination: Path, progress_cb=None, progress_start: int = 0, progress_end: int = 100):
        destination.mkdir(parents=True, exist_ok=True)
        files = list(source.rglob("*"))
        for index, item in enumerate(files):
            if item.is_symlink():
                raise ValueError(f"Symbolic links are not allowed in imported mod folders: {item}")
            if not item.is_file():
                continue
            relative = item.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            if progress_cb and item.is_file():
                percent = progress_start + int((index + 1) / max(len(files), 1) * (progress_end - progress_start))
                progress_cb(percent, f"Copying mod files ({index + 1}/{len(files)} entries)...")

    def _import_raw_mod(
        self,
        source: Path,
        season_year: int | None,
        allow_update: bool,
        display_name: str | None = None,
        progress_cb=None,
    ) -> dict:
        if season_year not in (2010, 2012):
            return {"ok": False, "errors": ["Choose whether this F1 25 mod belongs to the 2010 or 2012 season."]}
        name = display_name or (source.stem if source.is_file() else source.name)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "imported-mod"

        with tempfile.TemporaryDirectory(prefix="f1classic_raw_mod_") as temp:
            package_root = Path(temp) / "package"
            mod_source = package_root / "mod_source"
            try:
                if progress_cb:
                    progress_cb(5, "Extracting or copying raw mod files...")
                if source.is_file():
                    with zipfile.ZipFile(source, "r") as zf:
                        self._safe_extract(zf, mod_source, progress_cb, 5, 50)
                else:
                    self._copy_raw_folder(source, mod_source, progress_cb, 5, 50)
                if not any(path.is_file() for path in mod_source.rglob("*")):
                    return {"ok": False, "errors": ["The selected mod archive or folder contains no files."]}

                if progress_cb:
                    progress_cb(60, "Validating raw F1 25 mod files...")
                manifest = {
                    "schema_version": 1,
                    "id": f"f1classic.mod.{season_year}.{slug}",
                    "name": name,
                    "version": "1.0.0",
                    "author": "Unknown",
                    "description": f"Imported F1 25 mod for the {season_year} season.",
                    "game": "F1 25",
                    "season": season_year,
                    "artwork": "",
                    "operations": [{"type": "bydanix_merge", "source": "mod_source", "target": "."}],
                }
                (package_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                result = self._import_single_root(
                    package_root,
                    allow_update=allow_update,
                    progress_cb=progress_cb,
                )
                if result.get("ok"):
                    result["import_type"] = "raw_f1_25_mod"
                return result
            except (OSError, zipfile.BadZipFile, ValueError) as exc:
                self.logger.exception(f"Could not stage raw mod '{name}': {exc}")
                return {"ok": False, "errors": [str(exc)]}

    def _import_single_root(
        self,
        pkg_root: Path,
        allow_update: bool,
        default_season: int | None = None,
        progress_cb=None,
    ) -> dict:
        manifest = ManifestParser.parse_package(pkg_root, default_season=default_season)
        existing = self.season_manager.get_season(manifest.id)

        if existing and not allow_update:
            return {
                "ok": False,
                "duplicate": True,
                "existing_version": existing.manifest.version,
                "new_version": manifest.version,
                "season_id": manifest.id,
                "errors": [
                    f"Duplicate season ID '{manifest.id}' already exists (installed v{existing.manifest.version}, package v{manifest.version}). Confirm update to replace it."
                ],
            }

        safe_folder = manifest.id.replace("/", "_").replace("\\", "_")
        dest_dir = Path(self.config.data["seasons_dir"]) / safe_folder

        if existing and existing.installed:
            return {
                "ok": False,
                "errors": [
                    f"Season '{manifest.id}' is currently installed in {manifest.game}. Uninstall it before updating."
                ],
            }

        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".f1classic-import-", dir=dest_dir.parent) as temp:
            temp_root = Path(temp)
            staged_dir = temp_root / "package"
            if progress_cb:
                progress_cb(75, f"Copying package '{manifest.name}' into launcher storage...")
            shutil.copytree(pkg_root, staged_dir)
            if not (pkg_root / "manifest.json").is_file() and (pkg_root / "swofty.json").is_file():
                launcher_manifest = {
                    "schema_version": 1,
                    "id": manifest.id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "author": manifest.author,
                    "description": manifest.description,
                    "game": manifest.game,
                    "season": manifest.season,
                    "artwork": manifest.artwork,
                    "teams": manifest.teams,
                    "drivers": manifest.drivers,
                    "operations": manifest.operations,
                    "dependencies": manifest.dependencies,
                    "adapter": "swofty_addon_compat",
                    "requires_swofty_core": True,
                }
                (staged_dir / "manifest.json").write_text(
                    json.dumps(launcher_manifest, indent=2),
                    encoding="utf-8",
                )

            backup_dir = temp_root / "previous"
            if dest_dir.exists():
                os.replace(dest_dir, backup_dir)
            try:
                os.replace(staged_dir, dest_dir)
            except OSError:
                if backup_dir.exists() and not dest_dir.exists():
                    os.replace(backup_dir, dest_dir)
                raise

        action = "Updated" if existing else "Imported"
        if progress_cb:
            progress_cb(95, f"Finalizing package '{manifest.name}'...")
        self.logger.info(f"{action} season package '{manifest.name}' ({manifest.id} v{manifest.version}).")
        return {
            "ok": True,
            "action": action.lower(),
            "season_id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "game": manifest.game,
            "season": manifest.season,
        }

    def import_package(
        self,
        source_path: str,
        allow_update: bool = False,
        season_year: int | None = None,
        progress_cb=None,
    ) -> dict:
        last_progress = -1

        def report(percent: int, message: str):
            nonlocal last_progress
            if progress_cb:
                bounded_percent = max(0, min(percent, 100))
                if bounded_percent != last_progress or bounded_percent in (0, 100):
                    progress_cb(bounded_percent, message)
                    last_progress = bounded_percent

        cleaned = self._clean_path_str(source_path)
        if not cleaned:
            return {"ok": False, "errors": ["Please enter or select a .zip or folder path."]}

        src = Path(cleaned)
        if not src.exists():
            return {"ok": False, "errors": [f"Source path does not exist: {cleaned}"]}

        temp_dir: Path | None = None
        try:
            report(2, "Inspecting source package...")
            if src.is_file() and src.suffix.lower() == ".zip":
                temp_dir = Path(tempfile.mkdtemp(prefix="f1classic_import_"))
                with zipfile.ZipFile(src, "r") as zf:
                    self._safe_extract(zf, temp_dir, report, 5, 45)
                report(55, "Locating and validating package manifests...")
                pkg_roots = self._find_manifest_roots(temp_dir)
                if not pkg_roots:
                    return self._import_raw_mod(temp_dir, season_year, allow_update, src.stem, report)
            elif src.is_dir():
                report(20, "Scanning selected folder for package manifests...")
                pkg_roots = self._find_manifest_roots(src)
                if not pkg_roots:
                    return self._import_raw_mod(src, season_year, allow_update, progress_cb=report)
            else:
                return {
                    "ok": False,
                    "errors": ["Unsupported package format. Provide a .zip archive or a folder."],
                }

            if len(pkg_roots) == 1:
                return self._import_single_root(
                    pkg_roots[0],
                    allow_update=allow_update,
                    default_season=season_year,
                    progress_cb=report,
                )

            # Multi-season pack (e.g. a folder or zip containing both 2010 and 2012)
            imported = []
            errors = []
            for root in pkg_roots:
                try:
                    res = self._import_single_root(
                        root,
                        allow_update=allow_update,
                        default_season=season_year,
                        progress_cb=report,
                    )
                    if res.get("ok"):
                        imported.append(res["name"])
                    else:
                        errors.extend(res.get("errors", []))
                except ManifestValidationError as exc:
                    errors.extend(exc.errors)
            if imported:
                return {
                    "ok": True,
                    "action": "imported",
                    "name": f"{len(imported)} seasons ({', '.join(imported)})",
                    "game": "Multiple",
                    "season": "",
                    "warnings": errors,
                }
            return {"ok": False, "errors": errors or ["No valid season manifests found."]}

        except ManifestValidationError as exc:
            self.logger.warning(f"Package validation failed for {src.name}: {exc}")
            return {"ok": False, "errors": exc.errors}
        except zipfile.BadZipFile:
            return {"ok": False, "errors": ["Corrupted or invalid ZIP archive."]}
        except Exception as exc:
            self.logger.exception(f"Unexpected import error for '{src}': {exc}")
            return {"ok": False, "errors": [str(exc)]}
        finally:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
