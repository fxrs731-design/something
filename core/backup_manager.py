import json
import shutil
from datetime import datetime
from pathlib import Path

from core.logger import LauncherLogger
from core.path_utils import safe_fs_path


class BackupManager:
    def __init__(self, backups_dir: Path, logger: LauncherLogger):
        self.backups_dir = Path(backups_dir)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger

    def _safe_game_key(self, game_name: str) -> str:
        return game_name.replace(" ", "_").replace("/", "_")

    def get_season_backup_dir(self, game_name: str, season_id: str) -> Path:
        return self.backups_dir / self._safe_game_key(game_name) / season_id

    def begin_session_backup(self, game_name: str, season_id: str) -> dict:
        bdir = self.get_season_backup_dir(game_name, season_id)
        files_dir = bdir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Started backup for {game_name} season {season_id} at {bdir}.")
        return {
            "game": game_name,
            "season_id": season_id,
            "created_at": datetime.now().isoformat(),
            "entries": {},
            "created_dirs": [],
            "_files_dir": files_dir,
            "_meta_file": bdir / "backup_manifest.json",
        }

    def backup_target_file(self, record: dict, game_dir: Path, rel_target: str):
        rel_norm = str(Path(rel_target))
        if rel_norm in record["entries"]:
            return
        target_path = safe_fs_path(game_dir / rel_norm)
        if target_path.exists() and target_path.is_file():
            backup_file = safe_fs_path(record["_files_dir"] / rel_norm)
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_path, backup_file)
            record["entries"][rel_norm] = {"existed": True}
        else:
            record["entries"][rel_norm] = {"existed": False}

    def record_created_dir(self, record: dict, rel_dir: str):
        rel_norm = str(Path(rel_dir))
        if rel_norm not in record["created_dirs"]:
            record["created_dirs"].append(rel_norm)

    def commit_backup_record(self, record: dict):
        meta_file: Path = record["_meta_file"]
        serializable = {
            "game": record["game"],
            "season_id": record["season_id"],
            "created_at": record["created_at"],
            "entries": record["entries"],
            "created_dirs": record["created_dirs"],
        }
        meta_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        self.logger.info(
            f"Saved backup manifest for {record['season_id']} ({len(record['entries'])} tracked files)."
        )

    def has_backup(self, game_name: str, season_id: str) -> bool:
        bdir = self.get_season_backup_dir(game_name, season_id)
        return (bdir / "backup_manifest.json").is_file()

    def restore_backup(self, game_name: str, season_id: str, game_dir: Path) -> dict:
        bdir = self.get_season_backup_dir(game_name, season_id)
        meta_file = bdir / "backup_manifest.json"
        files_dir = bdir / "files"

        if not meta_file.is_file():
            return {"ok": False, "restored": 0, "removed": 0, "error": "Backup manifest not found."}

        data = json.loads(meta_file.read_text(encoding="utf-8"))
        restored = 0
        removed = 0

        for rel_str, info in data.get("entries", {}).items():
            dest = safe_fs_path(game_dir / rel_str)
            if info.get("existed"):
                src = safe_fs_path(files_dir / rel_str)
                if src.is_file():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    restored += 1
            else:
                if dest.is_file():
                    dest.unlink()
                    removed += 1

        # Clean up empty directories that were created specifically by this season
        for rel_dir in sorted(data.get("created_dirs", []), key=len, reverse=True):
            d = game_dir / rel_dir
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except Exception:
                    pass

        shutil.rmtree(bdir, ignore_errors=True)
        self.logger.info(
            f"Restored backup for {season_id} in {game_name}: {restored} restored, {removed} removed."
        )
        return {"ok": True, "restored": restored, "removed": removed}
