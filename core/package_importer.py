import shutil
import tempfile
import zipfile
from pathlib import Path

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
        for fname in ("manifest.json", "swofty.json"):
            for match in root_dir.rglob(fname):
                if "__MACOSX" in match.parts:
                    continue
                parent = match.parent
                if parent not in seen:
                    seen.add(parent)
                    found.append(parent)
        return found if found else [root_dir]

    def _import_single_root(self, pkg_root: Path, allow_update: bool) -> dict:
        manifest = ManifestParser.parse_package(pkg_root)
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

        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(pkg_root, dest_dir)

        action = "Updated" if existing else "Imported"
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

    def import_package(self, source_path: str, allow_update: bool = False) -> dict:
        cleaned = self._clean_path_str(source_path)
        if not cleaned:
            return {"ok": False, "errors": ["Please enter or select a .zip or folder path."]}

        src = Path(cleaned)
        if not src.exists():
            return {"ok": False, "errors": [f"Source path does not exist: {cleaned}"]}

        temp_dir: Path | None = None
        try:
            if src.is_file() and src.suffix.lower() == ".zip":
                temp_dir = Path(tempfile.mkdtemp(prefix="f1classic_import_"))
                with zipfile.ZipFile(src, "r") as zf:
                    zf.extractall(temp_dir)
                pkg_roots = self._find_manifest_roots(temp_dir)
            elif src.is_dir():
                pkg_roots = self._find_manifest_roots(src)
            else:
                return {
                    "ok": False,
                    "errors": ["Unsupported package format. Provide a .zip file or a season folder containing manifest.json or swofty.json."],
                }

            if len(pkg_roots) == 1:
                return self._import_single_root(pkg_roots[0], allow_update=allow_update)

            # Multi-season pack (e.g. a folder or zip containing both 2010 and 2012)
            imported = []
            errors = []
            for root in pkg_roots:
                try:
                    res = self._import_single_root(root, allow_update=allow_update)
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
            self.logger.error(f"Package validation failed for {src.name}: {exc}")
            return {"ok": False, "errors": exc.errors}
        except zipfile.BadZipFile:
            return {"ok": False, "errors": ["Corrupted or invalid ZIP archive."]}
        except Exception as exc:
            self.logger.error(f"Unexpected import error: {exc}")
            return {"ok": False, "errors": [str(exc)]}
        finally:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
