from pathlib import Path

from core.season_manager import SeasonEntry


class ConflictDetector:
    @staticmethod
    def get_package_target_files(entry: SeasonEntry) -> set[str]:
        targets: set[str] = set()
        for op in entry.manifest.operations:
            if not isinstance(op, dict):
                continue
            op_type = op.get("type", "")
            src_rel = op.get("source", "")
            tgt_rel = op.get("target", "")
            if op_type in ("copy_file", "replace_file"):
                targets.add(str(Path(tgt_rel or src_rel)))
            elif op_type in ("copy_dir", "bydanix_merge"):
                src_dir = entry.package_dir / src_rel
                dest_base = Path(tgt_rel) if tgt_rel else Path(".")
                if src_dir.is_dir():
                    for f in src_dir.rglob("*"):
                        if f.is_file():
                            targets.add(str(dest_base / f.relative_to(src_dir)))
        return targets

    @staticmethod
    def detect_conflicts(entries: list[SeasonEntry]) -> list[dict]:
        file_owners: dict[str, list[str]] = {}
        for entry in entries:
            for rel_path in ConflictDetector.get_package_target_files(entry):
                file_owners.setdefault(rel_path, []).append(entry.manifest.id)

        conflicts = []
        for rel_path, owners in sorted(file_owners.items()):
            if len(owners) > 1:
                conflicts.append({"file": rel_path, "packages": owners})
        return conflicts
