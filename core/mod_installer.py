import shutil
import zipfile
from pathlib import Path
from typing import Callable

from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from core.game_adapters import GameAdapter
from core.game_detector import GameDetector
from core.logger import LauncherLogger
from core.path_utils import safe_fs_path
from core.season_manager import SeasonEntry, SeasonManager


class ModInstaller:
    def __init__(
        self,
        config: ConfigManager,
        season_manager: SeasonManager,
        backup_manager: BackupManager,
        game_detector: GameDetector,
        logger: LauncherLogger,
    ):
        self.config = config
        self.season_manager = season_manager
        self.backup_manager = backup_manager
        self.game_detector = game_detector
        self.logger = logger

    def install_season(
        self,
        season_id: str,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> dict:
        def report(pct: int, msg: str):
            if progress_cb:
                progress_cb(pct, msg)

        entry = self.season_manager.get_season(season_id)
        if not entry:
            return {"ok": False, "error": f"Season '{season_id}' not found."}

        manifest = entry.manifest
        target_game = manifest.game

        # 1. Validate target game directory
        game_dir_str = self.config.get_game_path(target_game)
        game_check = self.game_detector.validate_game_directory(target_game, game_dir_str)
        if not game_check["valid"]:
            return {
                "ok": False,
                "error": f"Cannot install '{manifest.name}': {target_game} directory is invalid or not configured ({game_check['reason']}).",
            }
        game_dir = Path(game_dir_str)

        # 2. Check dependencies
        available_ids = {s.manifest.id for s in self.season_manager.scan_seasons()}
        missing_deps = [d for d in manifest.dependencies if not d.lower().startswith("swofty.") and d not in available_ids]
        if missing_deps:
            return {
                "ok": False,
                "error": f"Missing required dependencies: {', '.join(missing_deps)}",
            }

        # 3. Season switching: if another season is currently installed on this game, uninstall it first
        current_active = self.config.get_active_season(target_game)
        if current_active and current_active != season_id:
            report(10, f"Switching from '{current_active}' — restoring vanilla files...")
            unres = self.uninstall_season(current_active)
            if not unres["ok"]:
                return {
                    "ok": False,
                    "error": f"Failed to restore previous season '{current_active}': {unres.get('error')}",
                }

        if current_active == season_id:
            return {"ok": True, "message": f"'{manifest.name}' is already installed for {target_game}.", "files_modified": 0}

        # 4. Execute operations with per-file backup & rollback on failure
        report(20, "Creating backup of original game files...")
        backup_record = self.backup_manager.begin_session_backup(target_game, season_id)
        ops = manifest.operations
        total_ops = max(len(ops), 1)
        files_modified = 0

        try:
            for idx, op in enumerate(ops):
                pct = 25 + int(((idx + 1) / total_ops) * 70)
                op_type = op["type"]
                src_rel = op.get("source", "")
                tgt_rel = op.get("target", "")
                report(pct, f"Applying {op_type}: {tgt_rel or src_rel}")

                if op_type in ("copy_file", "replace_file"):
                    src_file = safe_fs_path(entry.package_dir / src_rel)
                    dest_rel = tgt_rel or src_rel
                    self.backup_manager.backup_target_file(backup_record, game_dir, dest_rel)
                    dest_file = safe_fs_path(game_dir / dest_rel)
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(safe_fs_path(src_file), safe_fs_path(dest_file))
                    files_modified += 1

                elif op_type in ("copy_dir", "bydanix_merge"):
                    src_dir = safe_fs_path(entry.package_dir / src_rel)
                    dest_base = Path(tgt_rel) if tgt_rel else Path(".")
                    for src_file in src_dir.rglob("*"):
                        if not src_file.is_file():
                            continue
                        rel_inside = src_file.relative_to(src_dir)
                        rel_target = str(dest_base / rel_inside)
                        self.backup_manager.backup_target_file(backup_record, game_dir, rel_target)
                        dest_file = safe_fs_path(game_dir / rel_target)
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(safe_fs_path(src_file), safe_fs_path(dest_file))
                        files_modified += 1

                elif op_type == "extract_zip":
                    zip_file = entry.package_dir / src_rel
                    dest_base = Path(tgt_rel) if tgt_rel else Path(".")
                    with zipfile.ZipFile(zip_file, "r") as zf:
                        for member in zf.infolist():
                            if member.is_dir():
                                continue
                            rel_target = str(dest_base / member.filename)
                            self.backup_manager.backup_target_file(backup_record, game_dir, rel_target)
                            dest_file = safe_fs_path(game_dir / rel_target)
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as zsrc, open(dest_file, "wb") as zout:
                                shutil.copyfileobj(zsrc, zout)
                            files_modified += 1

                elif op_type == "mkdir":
                    target_dir = game_dir / tgt_rel
                    if not target_dir.exists():
                        target_dir.mkdir(parents=True, exist_ok=True)
                        self.backup_manager.record_created_dir(backup_record, tgt_rel)

            self.backup_manager.commit_backup_record(backup_record)
            self.config.set_active_season(target_game, season_id)
            report(100, f"Installed '{manifest.name}' ({files_modified} files).")
            self.logger.info(f"Installed season '{manifest.name}' ({season_id}) into {game_dir} ({files_modified} files).")
            return {"ok": True, "files_modified": files_modified, "game": target_game}

        except Exception as exc:
            self.logger.error(f"Installation failed for '{season_id}': {exc}. Rolling back...")
            self.backup_manager.commit_backup_record(backup_record)
            self.backup_manager.restore_backup(target_game, season_id, game_dir)
            return {"ok": False, "error": f"Installation failed and rolled back: {exc}"}

    def uninstall_season(self, season_id: str) -> dict:
        entry: SeasonEntry | None = self.season_manager.get_season(season_id)
        if not entry:
            return {"ok": False, "error": f"Season '{season_id}' not found."}

        target_game = entry.manifest.game
        game_dir_str = self.config.get_game_path(target_game)
        if not game_dir_str or not Path(game_dir_str).is_dir():
            return {"ok": False, "error": f"Game directory for {target_game} is not configured."}

        res = self.backup_manager.restore_backup(target_game, season_id, Path(game_dir_str))
        if not res["ok"]:
            return res

        if self.config.get_active_season(target_game) == season_id:
            self.config.set_active_season(target_game, None)

        self.logger.info(f"Uninstalled season '{entry.manifest.name}' ({season_id}) from {target_game}.")
        return res
