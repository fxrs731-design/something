import os
import sys
from pathlib import Path


def safe_fs_path(path: Path | str) -> Path:
    p = Path(path)
    if not sys.platform.startswith("win"):
        return p
    resolved = os.path.abspath(str(p))
    if resolved.startswith("\\\\?\\"):
        return Path(resolved)
    if resolved.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved.lstrip("\\"))
    if len(resolved) >= 240:
        return Path("\\\\?\\" + resolved)
    return Path(resolved)
