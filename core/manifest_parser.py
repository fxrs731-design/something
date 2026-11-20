import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


REQUIRED_FIELDS = [
    "schema_version",
    "id",
    "name",
    "version",
    "author",
    "description",
    "game",
    "season",
]

VALID_OPERATIONS = {
    "copy_file",
    "replace_file",
    "copy_dir",
    "extract_zip",
    "mkdir",
    "bydanix_merge",
}


@dataclass
class SeasonManifest:
    schema_version: int
    id: str
    name: str
    version: str
    author: str
    description: str
    game: str
    season: int
    artwork: str = "cover.png"
    teams: list[Any] = field(default_factory=list)
    drivers: list[Any] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    adapter: str = "codemasters_standard"
    notes: str = ""
    requires_swofty_core: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class ManifestValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _normalize_str_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, dict):
        return [str(k) for k in val.keys()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def _relative_package_path(raw: Any, allow_dot: bool = False) -> Path | None:
    value = str(raw).replace("\\", "/")
    if allow_dot and value in ("", "."):
        return Path(".")
    pure = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or pure.is_absolute()
        or any(part in ("", ".", "..") for part in pure.parts)
        or (len(value) > 1 and value[1] == ":")
    ):
        return None
    return Path(*pure.parts)


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _normalize_game_name(value: Any) -> str:
    game = str(value).strip()
    compact = re.sub(r"[\s_-]+", "", game).lower()
    if compact in ("f125",):
        return "F1 25"
    return game


class ManifestParser:
    @staticmethod
    def parse_package(package_root: Path, default_season: int | None = None) -> SeasonManifest:
        manifest_path = package_root / "manifest.json"
        swofty_path = package_root / "swofty.json"
        if manifest_path.is_file():
            return ManifestParser.parse_file(manifest_path, package_root=package_root)
        if swofty_path.is_file():
            return ManifestParser.parse_swofty_file(
                swofty_path,
                package_root=package_root,
                default_season=default_season,
            )
        raise ManifestValidationError(["Missing required manifest.json or swofty.json in package."])

    @staticmethod
    def parse_swofty_file(
        swofty_path: Path,
        package_root: Path | None = None,
        default_season: int | None = None,
    ) -> SeasonManifest:
        try:
            raw = json.loads(swofty_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ManifestValidationError([f"Invalid JSON in swofty.json: {exc}"])

        if not isinstance(raw, dict):
            raise ManifestValidationError(["swofty.json root must be a JSON object."])

        pkg_id = str(raw.get("id") or (package_root.name if package_root else "swofty.addon")).strip()
        name = str(raw.get("name") or pkg_id).strip()
        version = str(raw.get("version") or "1.0.0").strip()

        authors = raw.get("author") or raw.get("authors") or "Unknown Author"
        if isinstance(authors, list):
            author = ", ".join(str(a.get("name", a) if isinstance(a, dict) else a) for a in authors)
        elif isinstance(authors, dict):
            author = str(authors.get("name") or authors)
        else:
            author = str(authors)

        description = str(raw.get("description") or f"Swofty addon package: {name}").strip()

        year_match = re.search(r"(19\d{2}|20\d{2})", f"{name} {pkg_id}")
        raw_season = raw.get("season")
        try:
            if raw_season is not None:
                season_year = int(raw_season)
            else:
                inferred_year = int(year_match.group(1)) if year_match else None
                season_year = (
                    inferred_year
                    if inferred_year in (2010, 2012)
                    else default_season or inferred_year or 2025
                )
        except (ValueError, TypeError):
            season_year = default_season or (int(year_match.group(1)) if year_match else 2025)

        compat = raw.get("compat", {})
        compat_game = compat.get("game") if isinstance(compat, dict) else None
        game = _normalize_game_name(raw.get("game") or compat_game or "F1 25")
        if game.lower() != "f1 25":
            raise ManifestValidationError([f"Only F1 25 addons are supported; this package targets '{game}'."])

        artwork = ""
        if package_root is not None:
            for cand in (raw.get("icon"), "icon.png", "cover.png"):
                if cand and (package_root / str(cand)).is_file():
                    artwork = str(cand)
                    break

        operations: list[dict[str, Any]] = []
        if package_root is not None:
            for folder in ("files", "assets", "db", "lang", "native", "parts", "registry", "registry_files"):
                if (package_root / folder).is_dir():
                    operations.append({
                        "type": "bydanix_merge",
                        "source": folder,
                        "target": folder if folder != "files" else ".",
                    })

        teams = raw.get("teams", [])
        if not isinstance(teams, list):
            teams = []
        if not teams and package_root is not None:
            reg_teams = package_root / "registry" / "teams.json"
            if reg_teams.is_file():
                try:
                    tjson = json.loads(reg_teams.read_text(encoding="utf-8-sig"))
                    ident = tjson.get("team.identity", {})
                    if isinstance(ident, dict):
                        for tid, info in ident.items():
                            code = info.get("names", {}).get("code", "") if isinstance(info, dict) else ""
                            teams.append(f"{code} ({tid})" if code else tid)
                except Exception:
                    pass

        drivers = raw.get("drivers", [])
        if not isinstance(drivers, list):
            drivers = []

        deps = _normalize_str_list(raw.get("dependencies", []))
        reqs = raw.get("requires", [])
        requires_swofty = False
        if isinstance(reqs, list):
            for r in reqs:
                if isinstance(r, dict) and r.get("id"):
                    rid = str(r["id"])
                    ver = str(r.get("version", "")).strip()
                    deps.append(f"{rid} {ver}".strip())
                    if "swofty" in rid.lower():
                        requires_swofty = True
        raw_ops = raw.get("operations", {})
        if isinstance(raw_ops, dict) and raw_ops.get("native"):
            requires_swofty = True

        note_msg = (
            "Requires Swofty Core runtime (native DLL hooks cannot run standalone)."
            if requires_swofty
            else "Imported from swofty.json package format."
        )

        return SeasonManifest(
            schema_version=1,
            id=pkg_id,
            name=name,
            version=version,
            author=author,
            description=description,
            game=game,
            season=season_year,
            artwork=artwork,
            teams=teams,
            drivers=drivers,
            operations=operations,
            dependencies=deps,
            adapter="swofty_addon_compat",
            notes=note_msg,
            requires_swofty_core=requires_swofty,
            raw=raw,
        )

    @staticmethod
    def parse_file(manifest_path: Path, package_root: Path | None = None) -> SeasonManifest:
        if not manifest_path.exists():
            raise ManifestValidationError([f"Missing required file: {manifest_path.name}"])

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ManifestValidationError([f"Invalid JSON in manifest.json: {exc}"])

        if not isinstance(raw, dict):
            raise ManifestValidationError(["manifest.json root must be a JSON object."])

        errors: list[str] = []
        for field_name in REQUIRED_FIELDS:
            if field_name not in raw or raw[field_name] in (None, ""):
                errors.append(f"Missing required field '{field_name}' in manifest.json.")

        if "schema_version" in raw and not isinstance(raw["schema_version"], int):
            errors.append("'schema_version' must be an integer (e.g. 1).")

        if "season" in raw and not isinstance(raw["season"], int):
            errors.append("'season' must be an integer year (e.g. 2018).")
        game = _normalize_game_name(raw.get("game", ""))
        if game.lower() != "f1 25":
            errors.append(f"Only F1 25 mods are supported; this package targets '{raw.get('game', '')}'.")

        operations = raw.get("operations", [])
        if not isinstance(operations, list):
            errors.append("'operations' must be a list of modification steps.")
        else:
            for idx, op in enumerate(operations):
                if not isinstance(op, dict):
                    errors.append(f"Operation #{idx + 1} must be an object.")
                    continue
                op_type = op.get("type")
                if op_type not in VALID_OPERATIONS:
                    errors.append(
                        f"Operation #{idx + 1} has unsupported type '{op_type}'. "
                        f"Supported: {', '.join(sorted(VALID_OPERATIONS))}."
                    )
                if op_type in ("copy_file", "replace_file", "copy_dir", "extract_zip", "bydanix_merge"):
                    if not op.get("source"):
                        errors.append(f"Operation #{idx + 1} ({op_type}) is missing 'source'.")
                    elif package_root is not None:
                        source_rel = _relative_package_path(op["source"])
                        src_path = package_root / source_rel if source_rel else package_root
                        if not source_rel or not _is_within(package_root, src_path) or not src_path.exists():
                            errors.append(
                                f"Operation #{idx + 1} source '{op['source']}' is missing or unsafe."
                            )
                if op_type in ("copy_file", "replace_file", "copy_dir", "extract_zip", "bydanix_merge"):
                    target_rel = _relative_package_path(op.get("target", "."), allow_dot=True)
                    if not target_rel:
                        errors.append(f"Operation #{idx + 1} has an unsafe target path.")
                if op_type == "mkdir" and not op.get("target"):
                    errors.append(f"Operation #{idx + 1} (mkdir) is missing 'target'.")

        artwork_rel = raw.get("artwork", "cover.png")
        if artwork_rel:
            artwork_path = _relative_package_path(artwork_rel)
            if not artwork_path:
                errors.append(f"Artwork path '{artwork_rel}' is unsafe.")
            elif package_root is not None:
                art_path = package_root / artwork_path
                if not _is_within(package_root, art_path) or not art_path.exists():
                    errors.append(f"Declared artwork file '{artwork_rel}' not found in package.")

        if errors:
            raise ManifestValidationError(errors)

        teams = raw.get("teams", [])
        drivers = raw.get("drivers", [])

        return SeasonManifest(
            schema_version=int(raw["schema_version"]),
            id=str(raw["id"]).strip(),
            name=str(raw["name"]).strip(),
            version=str(raw["version"]).strip(),
            author=str(raw["author"]).strip(),
            description=str(raw["description"]).strip(),
            game=game,
            season=int(raw["season"]),
            artwork=str(artwork_rel),
            teams=teams if isinstance(teams, list) else [],
            drivers=drivers if isinstance(drivers, list) else [],
            operations=operations,
            dependencies=_normalize_str_list(raw.get("dependencies", [])),
            adapter=str(raw.get("adapter", "codemasters_standard")),
            notes=str(raw.get("notes", "")),
            requires_swofty_core=bool(raw.get("requires_swofty_core", False)),
            raw=raw,
        )
