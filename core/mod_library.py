import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


MAX_PACKAGE_FILES = 50_000
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
PREVIEW_FILE_LIMIT = 500
SEASON_PROFILES = {
    "f1classic_2010": {"id": "f1classic_2010", "name": "2010 F1 25", "kind": "season", "season": 2010, "mods": {}},
    "f1classic_2012": {"id": "f1classic_2012", "name": "2012 F1 25", "kind": "season", "season": 2012, "mods": {}},
}


class ModLibraryError(ValueError):
    pass


def _safe_archive_path(raw_path: str) -> str:
    value = raw_path.replace("\\", "/")
    path = PurePosixPath(value)
    reserved_windows_names = {
        "con", "prn", "aux", "nul",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or any(re.search(r'[<>:"|?*]', part) or part.endswith((".", " ")) for part in path.parts)
        or any(part.split(".", 1)[0].casefold() in reserved_windows_names for part in path.parts)
    ):
        raise ModLibraryError(f"Unsafe package path: {raw_path}")
    return path.as_posix()


def _normalize_game(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    game = value.strip()
    compact = re.sub(r"[\s_-]+", "", game).casefold()
    if compact in ("f125", "f12025"):
        return "F1 25"
    return game


def _category(value: Any, paths: list[str], name: str) -> str:
    labels = {
        "livery": ("livery", "liveries", "car-skin", "carskin", "paint"),
        "helmet": ("helmet", "helmets"),
        "driver suit": ("driversuit", "driver-suit", "race-suit", "racesuit", "racewear"),
        "driver numbers": ("driver-number", "drivernumber", "number-plate", "numberplate"),
        "performance": ("performance", "handling", "physics", "ai-behavior", "aiperformance"),
        "season modification": ("season", "database", "roster", "2010", "2012"),
    }
    if isinstance(value, str) and value.strip():
        return value.strip()
    text = " ".join((name, *paths)).casefold().replace("_", "-")
    for label, terms in labels.items():
        if any(term in text for term in terms):
            return label.title()
    return "Uncategorized"


class ModLibraryManager:
    def __init__(self, base_dir: Path, logger):
        self.root = Path(base_dir)
        self.root = self.root.expanduser().resolve()
        self.packages_dir = self.root / "packages"
        self.data_file = self.root / "library.json"
        self.logger = logger
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self._document = self._load()
        self._recover_package_storage()

    @staticmethod
    def _default_document() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mods": {},
            "profiles": copy.deepcopy(SEASON_PROFILES),
            "selected_profile_id": "f1classic_2010",
        }

    def _load(self) -> dict[str, Any]:
        if not self.data_file.exists():
            return self._default_document()
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not load mod library data from {self.data_file}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise RuntimeError(f"Unsupported or malformed mod library data: {self.data_file}")
        if not isinstance(data.get("mods"), dict) or not isinstance(data.get("profiles"), dict):
            raise RuntimeError(f"Mod library data must contain mods and profiles objects: {self.data_file}")
        for profile_id, profile in data["profiles"].items():
            if (
                not isinstance(profile_id, str)
                or not isinstance(profile, dict)
                or not isinstance(profile.get("mods", {}), dict)
            ):
                raise RuntimeError(f"Malformed profile data in mod library: {self.data_file}")
        defaults = self._default_document()
        for profile_id, profile in defaults["profiles"].items():
            data["profiles"].setdefault(profile_id, profile)
        if data.get("selected_profile_id") not in data["profiles"]:
            data["selected_profile_id"] = "f1classic_2010"
        return data

    def _recover_package_storage(self):
        indexed_mods = self._document["mods"]
        for path in self.packages_dir.iterdir():
            if path.name.startswith(".staging-"):
                match = re.match(r"^\.staging-(\d+)-", path.name)
                if not match or self._is_process_running(int(match.group(1))):
                    self.logger.warning(f"Leaving active or unrecognized import staging untouched: {path}")
                    continue
                self._remove_recovery_path(path, "incomplete import staging")
                continue
            if path.name.startswith(".removed-"):
                match = re.match(r"^\.removed-(mod-[0-9a-f]{20})-(\d+)-", path.name)
                if not match:
                    self.logger.warning(f"Leaving unrecognized mod-library recovery path untouched: {path}")
                    continue
                mod_id = match.group(1)
                if self._is_process_running(int(match.group(2))):
                    self.logger.warning(f"Leaving active mod removal untouched: {path}")
                    continue
                package_path = self.packages_dir / mod_id
                if mod_id in indexed_mods and not package_path.exists():
                    try:
                        os.replace(path, package_path)
                        self.logger.info(f"Restored interrupted mod removal for '{mod_id}'.")
                    except OSError as exc:
                        self.logger.exception(f"Could not restore interrupted mod removal '{path}': {exc}")
                else:
                    self._remove_recovery_path(path, "completed mod removal")
                continue
            if re.fullmatch(r"mod-[0-9a-f]{20}", path.name) and path.name not in indexed_mods:
                if path.is_symlink() or not path.is_dir():
                    self.logger.warning(f"Leaving unrecognized unindexed package data untouched: {path}")
                    continue
                candidates = [
                    child for child in path.iterdir()
                    if not child.is_symlink()
                    and (
                        (child.is_file() and child.suffix.casefold() in (".zip", ".swoftyaddon"))
                        or (child.is_dir() and child.name == "source")
                    )
                ]
                if len(candidates) != 1:
                    self.logger.warning(f"Leaving unrecognized unindexed package data untouched: {path}")
                    continue
                candidate = candidates[0]
                inspection = self.inspect_package(candidate)
                if not inspection.get("ok") or f"mod-{inspection.get('fingerprint', '')[:20]}" != path.name:
                    self.logger.warning(f"Leaving unverified unindexed package data untouched: {path}")
                    continue
                saved_source = str(
                    Path("packages") / path.name / (candidate.name if candidate.is_file() else "source")
                )
                indexed_mods[path.name] = self._stored_mod_record(
                    inspection,
                    path.name,
                    saved_source,
                )
                try:
                    self._save()
                    self.logger.info(f"Recovered interrupted import '{inspection['name']}'.")
                except OSError as exc:
                    del indexed_mods[path.name]
                    self.logger.exception(f"Could not recover interrupted import '{path}': {exc}")

    @staticmethod
    def _is_process_running(process_id: int) -> bool:
        try:
            os.kill(process_id, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _remove_recovery_path(self, path: Path, reason: str):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            self.logger.info(f"Removed {reason} data from the mod library.")
        except OSError as exc:
            self.logger.exception(f"Could not remove {reason} data '{path}': {exc}")

    def _save(self):
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=".library-",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                json.dump(self._document, output, indent=2, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.data_file)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _read_metadata(raw: bytes, filename: str) -> dict[str, Any]:
        try:
            metadata = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModLibraryError(f"Malformed {filename}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ModLibraryError(f"{filename} must contain a JSON object.")
        return metadata

    @staticmethod
    def _metadata_fields(metadata: dict[str, Any] | None, source: Path, file_paths: list[str]) -> dict[str, Any]:
        metadata = metadata or {}
        compat = metadata.get("compat")
        game_value = metadata.get("game")
        if not game_value and isinstance(compat, dict):
            game_value = compat.get("game")
        game = _normalize_game(game_value)
        if game and game.casefold() != "f1 25":
            raise ModLibraryError(f"This package declares support for {game}, not F1 25.")
        game_versions = (
            metadata.get("game_version")
            or metadata.get("gameVersion")
            or metadata.get("game_versions")
            or metadata.get("compatible_game_versions")
        )
        if isinstance(game_versions, (str, int, float)):
            game_versions = [str(game_versions)]
        if isinstance(game_versions, list):
            declared_years = set()
            for game_version in game_versions:
                match = re.search(r"\bf1[\s_-]*(20\d{2}|2[0-9])\b", str(game_version), re.IGNORECASE)
                if match:
                    year = match.group(1)
                    declared_years.add(int(year) if len(year) == 4 else 2000 + int(year))
            if declared_years and 2025 not in declared_years:
                years = ", ".join(str(year) for year in sorted(declared_years))
                raise ModLibraryError(
                    f"This package targets F1 {years}, not F1 25."
                )

        name = metadata.get("name")
        if not isinstance(name, str) or not name.strip():
            name = source.stem if source.is_file() else source.name
        season = metadata.get("season")
        try:
            season = int(season) if season is not None else None
        except (ValueError, TypeError):
            raise ModLibraryError("Package season metadata must be a year.")
        if season is not None and not 1900 <= season <= 2100:
            raise ModLibraryError("Package season metadata is outside the supported year range.")

        clues = " ".join(file_paths).casefold().replace("\\", "/")
        f1_structure = (
            (".erp" in clues and ("asset_groups/" in clues or "mod.serps" in clues))
            or "asset_groups/" in clues
        )
        compatibility = (
            "Declared F1 25" if game == "F1 25"
            else "F1 25-like structure (unverified)" if f1_structure
            else "Unverified"
        )
        dependencies = metadata.get("dependencies", [])
        if not isinstance(dependencies, list):
            dependencies = []
        requires = metadata.get("requires", [])
        if isinstance(requires, list):
            dependencies.extend(
                item.get("id")
                for item in requires
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
        dependencies = list(dict.fromkeys(
            item.strip() for item in dependencies if isinstance(item, str) and item.strip()
        ))
        description = metadata.get("description")
        if not isinstance(description, str):
            description = ""
        author = metadata.get("author") or metadata.get("authors") or "Unknown"
        if isinstance(author, list):
            author = ", ".join(
                str(item.get("name", item)) if isinstance(item, dict) else str(item)
                for item in author
            )
        elif isinstance(author, dict):
            author = str(author.get("name") or "Unknown")
        version = metadata.get("version")
        if not isinstance(version, (str, int, float)):
            version = "Unknown"

        return {
            "name": name.strip(),
            "category": _category(metadata.get("category"), file_paths, name),
            "version": str(version),
            "author": str(author),
            "description": description.strip(),
            "game": game,
            "season": season,
            "dependencies": dependencies,
            "compatibility": compatibility,
        }

    def inspect_package(self, source_path: str | Path) -> dict[str, Any]:
        source = Path(source_path).expanduser()
        if not source.exists():
            return {"ok": False, "error": f"Package does not exist: {source}"}
        try:
            source = source.resolve(strict=True)
            if source.is_dir():
                return self._inspect_folder(source)
            if source.is_file() and source.suffix.casefold() in (".zip", ".swoftyaddon"):
                return self._inspect_zip(source)
            return {
                "ok": False,
                "error": "The mod library accepts folders, ZIP archives, and ZIP-based .swoftyaddon packages.",
            }
        except (OSError, zipfile.BadZipFile, ModLibraryError, RuntimeError) as exc:
            self.logger.warning(f"Could not inspect mod package '{source}': {exc}")
            return {"ok": False, "error": str(exc)}

    def _inspect_zip(self, source: Path) -> dict[str, Any]:
        file_paths: list[str] = []
        seen: set[str] = set()
        total_size = 0
        metadata_candidates: list[tuple[int, str, zipfile.ZipInfo]] = []
        archive_entries: list[tuple[str, zipfile.ZipInfo]] = []
        digest = hashlib.sha256()
        try:
            with zipfile.ZipFile(source, "r") as archive:
                members = archive.infolist()
                if not members:
                    raise ModLibraryError("The ZIP archive is empty.")
                if len(members) > MAX_PACKAGE_FILES:
                    raise ModLibraryError(f"The archive exceeds the {MAX_PACKAGE_FILES:,}-file inspection limit.")
                for member in members:
                    if member.is_dir():
                        continue
                    relative = _safe_archive_path(member.filename)
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise ModLibraryError(f"Symbolic links are not allowed in ZIP archives: {member.filename}")
                    if member.flag_bits & 0x1:
                        raise ModLibraryError(f"Encrypted ZIP entries are not supported: {member.filename}")
                    normalized = relative.casefold()
                    if normalized in seen:
                        raise ModLibraryError(f"Duplicate or case-colliding ZIP entry: {member.filename}")
                    seen.add(normalized)
                    file_paths.append(relative)
                    archive_entries.append((relative, member))
                    total_size += member.file_size
                    if total_size > MAX_PACKAGE_BYTES:
                        raise ModLibraryError("The archive expands beyond the 2 GiB safety limit.")
                    leaf = PurePosixPath(relative).name.casefold()
                    if leaf in ("manifest.json", "swofty.json"):
                        metadata_candidates.append((len(PurePosixPath(relative).parts), relative, member))
                if not file_paths:
                    raise ModLibraryError("The ZIP archive contains no files.")
                for relative, member in sorted(archive_entries, key=lambda item: item[0].casefold()):
                    digest.update(relative.encode("utf-8"))
                    digest.update(member.file_size.to_bytes(8, "big"))
                    with archive.open(member, "r") as payload:
                        for chunk in iter(lambda: payload.read(1024 * 1024), b""):
                            digest.update(chunk)
                    digest.update(b"\0")
                metadata = None
                if metadata_candidates:
                    _, relative, member = min(metadata_candidates, key=lambda item: (item[0], item[1].casefold()))
                    if member.file_size > MAX_METADATA_BYTES:
                        raise ModLibraryError(f"Package metadata is too large: {relative}")
                    metadata = self._read_metadata(archive.read(member), PurePosixPath(relative).name)
        except (RuntimeError, NotImplementedError) as exc:
            raise ModLibraryError(f"Could not read the ZIP archive: {exc}") from exc
        file_paths.sort(key=str.casefold)
        return self._inspection_result(source, "archive", file_paths, total_size, metadata, digest.hexdigest())

    def _inspect_folder(self, source: Path) -> dict[str, Any]:
        file_paths: list[str] = []
        metadata_candidates: list[Path] = []
        total_size = 0
        actual_total_size = 0
        digest = hashlib.sha256()
        for current, directories, filenames in os.walk(source, followlinks=False):
            current_path = Path(current)
            directories.sort(key=str.casefold)
            filenames.sort(key=str.casefold)
            for directory in list(directories):
                child = current_path / directory
                if child.is_symlink():
                    raise ModLibraryError(f"Symbolic links are not allowed in mod folders: {child}")
            for filename in filenames:
                path = current_path / filename
                if path.is_symlink():
                    raise ModLibraryError(f"Symbolic links are not allowed in mod folders: {path}")
                if not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                _safe_archive_path(relative)
                if len(file_paths) >= MAX_PACKAGE_FILES:
                    raise ModLibraryError(f"The folder exceeds the {MAX_PACKAGE_FILES:,}-file inspection limit.")
                size = path.stat().st_size
                total_size += size
                if total_size > MAX_PACKAGE_BYTES:
                    raise ModLibraryError("The folder exceeds the 2 GiB safety limit.")
                file_paths.append(relative)
                if path.name.casefold() in ("manifest.json", "swofty.json"):
                    metadata_candidates.append(path)
                digest.update(relative.encode("utf-8"))
                digest.update(size.to_bytes(8, "big"))
                bytes_read = 0
                with path.open("rb") as file_obj:
                    for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                        bytes_read += len(chunk)
                        actual_total_size += len(chunk)
                        if actual_total_size > MAX_PACKAGE_BYTES:
                            raise ModLibraryError("The folder changed while being inspected and exceeds the 2 GiB safety limit.")
                        digest.update(chunk)
                if bytes_read != size:
                    raise ModLibraryError(f"Package changed while being inspected: {relative}")
                digest.update(b"\0")
        if not file_paths:
            raise ModLibraryError("The selected folder contains no files.")
        file_paths.sort(key=str.casefold)
        metadata = None
        if metadata_candidates:
            metadata_path = min(metadata_candidates, key=lambda path: len(path.relative_to(source).parts))
            if metadata_path.stat().st_size > MAX_METADATA_BYTES:
                raise ModLibraryError(f"Package metadata is too large: {metadata_path.name}")
            metadata = self._read_metadata(metadata_path.read_bytes(), metadata_path.name)
        return self._inspection_result(
            source,
            "folder",
            file_paths,
            total_size,
            metadata,
            digest.hexdigest(),
        )

    def _inspection_result(
        self,
        source: Path,
        source_type: str,
        file_paths: list[str],
        total_size: int,
        raw_metadata: dict[str, Any] | None,
        fingerprint: str,
    ) -> dict[str, Any]:
        fields = self._metadata_fields(raw_metadata, source, file_paths)
        return {
            "ok": True,
            **fields,
            "source_path": str(source),
            "source_type": source_type,
            "fingerprint": fingerprint,
            "file_count": len(file_paths),
            "total_size": total_size,
            "files": file_paths[:PREVIEW_FILE_LIMIT],
            "file_paths": file_paths,
            "omitted_files": max(0, len(file_paths) - PREVIEW_FILE_LIMIT),
        }

    @staticmethod
    def _stored_mod_record(inspection: dict[str, Any], mod_id: str, saved_source: str) -> dict[str, Any]:
        mod = {
            key: inspection[key]
            for key in (
                "name", "category", "version", "author", "description", "game",
                "season", "dependencies", "compatibility", "source_type",
                "fingerprint", "file_count", "total_size",
            )
        }
        mod.update({
            "id": mod_id,
            "source": saved_source,
            "files": inspection["file_paths"],
            "omitted_files": inspection["omitted_files"],
        })
        return mod

    def list_mods(self, query: str = "", category: str = "") -> list[dict[str, Any]]:
        query = query.strip().casefold()
        category = category.strip().casefold()
        with self._lock:
            mods = [copy.deepcopy(mod) for mod in self._document["mods"].values()]
        if query:
            mods = [
                mod for mod in mods
                if query in " ".join(
                    str(mod.get(key, ""))
                    for key in ("name", "category", "author", "version", "description", "compatibility")
                ).casefold()
            ]
        if category and category != "all":
            mods = [mod for mod in mods if str(mod.get("category", "")).casefold() == category]
        return sorted(mods, key=lambda mod: str(mod.get("name", "")).casefold())

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            profiles = [copy.deepcopy(profile) for profile in self._document["profiles"].values()]
        return sorted(
            profiles,
            key=lambda profile: (
                0 if profile.get("kind") == "season" else 1,
                profile.get("season") or 0,
                str(profile.get("name", "")).casefold(),
            ),
        )

    def selected_profile_id(self) -> str:
        with self._lock:
            return str(self._document["selected_profile_id"])

    def get_profile(self, profile_id: str | None = None) -> dict[str, Any]:
        profile_id = profile_id or self.selected_profile_id()
        with self._lock:
            profile = self._document["profiles"].get(profile_id)
            if profile is None:
                raise ModLibraryError(f"Unknown profile: {profile_id}")
            return copy.deepcopy(profile)

    def get_profile_mods(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        profile = self.get_profile(profile_id)
        with self._lock:
            mods_by_id = copy.deepcopy(self._document["mods"])
        rows = []
        for mod_id, enabled in profile.get("mods", {}).items():
            mod = mods_by_id.get(mod_id)
            if mod:
                row = copy.deepcopy(mod)
                row["enabled"] = bool(enabled)
                row["status"] = {
                    "imported": True,
                    "enabled": bool(enabled),
                    "applied": False,
                    "verified": False,
                }
                rows.append(row)
        return sorted(rows, key=lambda mod: str(mod.get("name", "")).casefold())

    def get_mod_status(self, mod_id: str, profile_id: str | None = None) -> dict[str, bool]:
        profile = self.get_profile(profile_id)
        with self._lock:
            if mod_id not in self._document["mods"]:
                raise ModLibraryError(f"Unknown library mod: {mod_id}")
        return {
            "imported": True,
            "enabled": bool(profile.get("mods", {}).get(mod_id, False)),
            "applied": False,
            "verified": False,
        }

    def import_package(self, source_path: str | Path, progress_cb=None) -> dict[str, Any]:
        if progress_cb:
            progress_cb(3, "Inspecting and verifying the original package...")
        inspection = self.inspect_package(source_path)
        if not inspection["ok"]:
            return inspection
        source = Path(inspection["source_path"])
        if source == self.root or self.root in source.parents:
            return {"ok": False, "error": "A package already inside the mod library cannot be imported again."}
        with self._lock:
            duplicate = next(
                (
                    mod for mod in self._document["mods"].values()
                    if mod.get("fingerprint") == inspection["fingerprint"]
                ),
                None,
            )
            if duplicate:
                return {
                    "ok": False,
                    "duplicate": True,
                    "mod_id": duplicate["id"],
                    "error": f"This package is already imported as '{duplicate['name']}'.",
                }

            mod_id = f"mod-{inspection['fingerprint'][:20]}"
            package_dir = self.packages_dir / mod_id
            staging_dir = self.packages_dir / f".staging-{os.getpid()}-{uuid.uuid4().hex}"
            old_document = copy.deepcopy(self._document)
            try:
                staging_dir.mkdir()
                if inspection["source_type"] == "archive":
                    payload = staging_dir / f"source{source.suffix.casefold()}"
                    total_source_size = max(source.stat().st_size, 1)
                    copied = 0
                    with source.open("rb") as input_file, payload.open("wb") as output_file:
                        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                            output_file.write(chunk)
                            copied += len(chunk)
                            if progress_cb:
                                progress_cb(
                                    40 + int(copied * 45 / total_source_size),
                                    f"Copying archive ({copied:,} of {total_source_size:,} bytes)...",
                                )
                    shutil.copystat(source, payload)
                    saved_source = str(Path("packages") / mod_id / payload.name)
                else:
                    payload = staging_dir / "source"
                    payload.mkdir()
                    self._copy_folder_payload(
                        source,
                        payload,
                        inspection["file_paths"],
                        inspection["file_count"],
                        progress_cb,
                        inspection["total_size"],
                    )
                    saved_source = str(Path("packages") / mod_id / "source")
                copied_inspection = self.inspect_package(payload)
                if not copied_inspection.get("ok") or copied_inspection.get("fingerprint") != inspection["fingerprint"]:
                    raise ModLibraryError("Package contents changed while being copied; the library copy was discarded.")
                mod = self._stored_mod_record(inspection, mod_id, saved_source)
                if progress_cb:
                    progress_cb(92, "Saving the library index...")
                os.replace(staging_dir, package_dir)
                self._document["mods"][mod_id] = mod
                try:
                    self._save()
                except OSError:
                    self._document = old_document
                    shutil.rmtree(package_dir, ignore_errors=True)
                    raise
            except (OSError, ModLibraryError) as exc:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                self.logger.exception(f"Could not import mod package '{source}': {exc}")
                return {"ok": False, "error": f"Could not import package: {exc}"}
        self.logger.info(
            f"Imported mod '{mod['name']}' ({mod_id}) as {mod['compatibility']}; it has not been applied or verified."
        )
        return {"ok": True, "mod": copy.deepcopy(mod)}

    @staticmethod
    def _copy_folder_payload(
        source: Path,
        destination: Path,
        file_paths: list[str],
        file_count: int,
        progress_cb=None,
        total_size: int = 0,
    ):
        if file_count != len(file_paths):
            raise ModLibraryError("Folder is too large to safely copy into the library.")
        source_root = source.resolve(strict=True)
        copied = 0
        for relative in file_paths:
            source_file = source.joinpath(*PurePosixPath(relative).parts)
            if source_file.is_symlink() or not source_file.is_file():
                raise ModLibraryError(f"Package changed during import: {relative}")
            resolved = source_file.resolve(strict=True)
            try:
                resolved.relative_to(source_root)
            except ValueError as exc:
                raise ModLibraryError(f"Package path escaped its source folder: {relative}") from exc
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, target)
            copied += resolved.stat().st_size
            if progress_cb:
                percent = 40 + int(copied * 45 / max(total_size, 1))
                progress_cb(percent, f"Copying mod files ({copied:,} of {total_size:,} bytes)...")

    def add_mod_to_profile(self, profile_id: str, mod_id: str, enabled: bool = True):
        self._set_profile_mod_state(profile_id, mod_id, enabled)

    def set_mod_enabled(self, profile_id: str, mod_id: str, enabled: bool):
        self._set_profile_mod_state(profile_id, mod_id, enabled)

    def _set_profile_mod_state(self, profile_id: str, mod_id: str, enabled: bool):
        with self._lock:
            profile = self._document["profiles"].get(profile_id)
            if profile is None:
                raise ModLibraryError(f"Unknown profile: {profile_id}")
            if mod_id not in self._document["mods"]:
                raise ModLibraryError(f"Unknown library mod: {mod_id}")
            old_document = copy.deepcopy(self._document)
            profile.setdefault("mods", {})[mod_id] = bool(enabled)
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise

    def remove_mod_from_profile(self, profile_id: str, mod_id: str):
        with self._lock:
            profile = self._document["profiles"].get(profile_id)
            if profile is None:
                raise ModLibraryError(f"Unknown profile: {profile_id}")
            if mod_id not in profile.get("mods", {}):
                return
            old_document = copy.deepcopy(self._document)
            del profile["mods"][mod_id]
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise

    def create_profile(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 80:
            raise ModLibraryError("A custom profile name must contain 1 to 80 characters.")
        with self._lock:
            self._ensure_unique_profile_name(name)
            profile_id = f"custom.{uuid.uuid4().hex}"
            profile = {"id": profile_id, "name": name, "kind": "custom", "season": None, "mods": {}}
            old_document = copy.deepcopy(self._document)
            self._document["profiles"][profile_id] = profile
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise
            return copy.deepcopy(profile)

    def rename_profile(self, profile_id: str, name: str):
        name = name.strip()
        if not name or len(name) > 80:
            raise ModLibraryError("A custom profile name must contain 1 to 80 characters.")
        with self._lock:
            profile = self._document["profiles"].get(profile_id)
            if profile is None:
                raise ModLibraryError(f"Unknown profile: {profile_id}")
            if profile.get("kind") != "custom":
                raise ModLibraryError("The built-in 2010 and 2012 profiles cannot be renamed.")
            self._ensure_unique_profile_name(name, exclude_id=profile_id)
            old_document = copy.deepcopy(self._document)
            profile["name"] = name
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise

    def duplicate_profile(self, profile_id: str, name: str) -> dict[str, Any]:
        source = self.get_profile(profile_id)
        name = name.strip()
        if not name or len(name) > 80:
            raise ModLibraryError("A custom profile name must contain 1 to 80 characters.")
        with self._lock:
            self._ensure_unique_profile_name(name)
            new_id = f"custom.{uuid.uuid4().hex}"
            profile = {
                "id": new_id,
                "name": name,
                "kind": "custom",
                "season": None,
                "mods": copy.deepcopy(source.get("mods", {})),
            }
            old_document = copy.deepcopy(self._document)
            self._document["profiles"][new_id] = profile
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise
            return copy.deepcopy(profile)

    def _ensure_unique_profile_name(self, name: str, exclude_id: str | None = None):
        normalized = name.casefold()
        if any(
            profile_id != exclude_id and str(profile.get("name", "")).casefold() == normalized
            for profile_id, profile in self._document["profiles"].items()
        ):
            raise ModLibraryError(f"A profile named '{name}' already exists.")

    def delete_profile(self, profile_id: str):
        with self._lock:
            profile = self._document["profiles"].get(profile_id)
            if profile is None:
                raise ModLibraryError(f"Unknown profile: {profile_id}")
            if profile.get("kind") != "custom":
                raise ModLibraryError("The built-in 2010 and 2012 profiles cannot be deleted.")
            old_document = copy.deepcopy(self._document)
            del self._document["profiles"][profile_id]
            if self._document["selected_profile_id"] == profile_id:
                self._document["selected_profile_id"] = "f1classic_2010"
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise

    def select_profile(self, profile_id: str):
        with self._lock:
            if profile_id not in self._document["profiles"]:
                raise ModLibraryError(f"Unknown profile: {profile_id}")
            old_document = copy.deepcopy(self._document)
            self._document["selected_profile_id"] = profile_id
            try:
                self._save()
            except OSError:
                self._document = old_document
                raise

    def remove_mod(self, mod_id: str) -> dict[str, Any]:
        with self._lock:
            if not re.fullmatch(r"mod-[0-9a-f]{20}", mod_id):
                return {"ok": False, "error": "Invalid library mod identifier."}
            mod = self._document["mods"].get(mod_id)
            if mod is None:
                return {"ok": False, "error": f"Unknown library mod: {mod_id}"}
            profiles = [
                profile["name"]
                for profile in self._document["profiles"].values()
                if mod_id in profile.get("mods", {})
            ]
            if profiles:
                return {
                    "ok": False,
                    "error": f"Remove this mod from these profiles before deleting it: {', '.join(profiles)}.",
                }
            package_dir = self.packages_dir / mod_id
            trash_dir = self.packages_dir / f".removed-{mod_id}-{os.getpid()}-{uuid.uuid4().hex}"
            old_document = copy.deepcopy(self._document)
            try:
                if package_dir.exists():
                    os.replace(package_dir, trash_dir)
                del self._document["mods"][mod_id]
                self._save()
            except OSError as exc:
                self._document = old_document
                if trash_dir.exists() and not package_dir.exists():
                    os.replace(trash_dir, package_dir)
                self.logger.exception(f"Could not remove library mod '{mod_id}': {exc}")
                return {"ok": False, "error": f"Could not remove mod: {exc}"}
            if trash_dir.exists():
                try:
                    shutil.rmtree(trash_dir)
                except OSError as exc:
                    self.logger.exception(f"Removed mod metadata but could not clean package backup '{trash_dir}': {exc}")
                    return {"ok": True, "warning": f"Removed from the library, but temporary package cleanup failed: {exc}"}
        self.logger.info(f"Removed imported mod '{mod_id}' from the library.")
        return {"ok": True}

    def detect_conflicts(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        profile = self.get_profile(profile_id)
        profile_id = profile["id"]
        with self._lock:
            mods_by_id = copy.deepcopy(self._document["mods"])
        enabled_ids = {
            mod_id for mod_id, enabled in profile.get("mods", {}).items()
            if enabled and mod_id in mods_by_id
        }
        conflicts: list[dict[str, Any]] = []
        path_owners: dict[str, list[str]] = {}
        for mod_id in sorted(enabled_ids):
            mod = mods_by_id[mod_id]
            if mod.get("game") and str(mod["game"]).casefold() != "f1 25":
                conflicts.append({
                    "kind": "incompatible-game",
                    "mods": [mod_id],
                    "message": f"{mod['name']} declares support for {mod['game']}, not F1 25.",
                })
            season = profile.get("season")
            mod_season = mod.get("season")
            if season and mod_season and season != mod_season:
                conflicts.append({
                    "kind": "season-mismatch",
                    "mods": [mod_id],
                    "message": f"{mod['name']} is tagged for {mod_season}, but profile {profile['name']} is for {season}.",
                })
            for dependency in mod.get("dependencies", []):
                dependency_id = str(dependency).split()[0]
                if dependency_id not in enabled_ids:
                    conflicts.append({
                        "kind": "missing-dependency",
                        "mods": [mod_id],
                        "dependency": dependency_id,
                        "message": f"{mod['name']} requires {dependency_id}, which is not enabled in this profile.",
                    })
            for path in mod.get("files", []):
                normalized_path = str(path).casefold()
                if PurePosixPath(normalized_path).name in ("manifest.json", "swofty.json", "readme.md"):
                    continue
                path_owners.setdefault(normalized_path, []).append(mod_id)
        for path, owners in sorted(path_owners.items()):
            if len(owners) > 1:
                conflicts.append({
                    "kind": "possible-file-overlap",
                    "mods": owners,
                    "path": path,
                    "message": (
                        f"These packages contain the same relative path '{path}'. This is a potential overlap, "
                        "not proof that either package replaces the same game resource."
                    ),
                })
        return conflicts
