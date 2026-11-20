import os
import shutil
import subprocess
import sys
from pathlib import Path

from core.logger import LauncherLogger


class SwoftyBridge:
    """
    Integrates with the real Swofty Core CLI/binary and native/dist/ DLLs
    compiled from https://github.com/Swofty-Developments/SwoftyLauncher.
    Never fakes execution when the binary is absent.
    """

    def __init__(self, base_dir: Path, logger: LauncherLogger):
        self.base_dir = Path(base_dir)
        self.logger = logger
        self.core_dir = self.base_dir / "swofty_core"

    def find_swofty_cli(self) -> Path | None:
        candidates = [
            self.core_dir / "swofty.exe",
            self.core_dir / "swofty",
            self.base_dir / "swofty.exe",
        ]
        for c in candidates:
            if c.is_file():
                return c
        which_name = "swofty.exe" if sys.platform.startswith("win") else "swofty"
        found = shutil.which(which_name)
        return Path(found) if found else None

    def get_native_dist_dir(self) -> Path | None:
        cand = self.core_dir / "native" / "dist"
        if cand.is_dir():
            return cand
        return None

    def get_status(self) -> dict:
        cli = self.find_swofty_cli()
        native_dir = self.get_native_dist_dir()
        native_dlls = sorted(str(p.relative_to(native_dir)) for p in native_dir.rglob("*.dll")) if native_dir else []
        if not cli:
            return {
                "available": False,
                "cli_path": None,
                "native_dist_dir": str(native_dir) if native_dir else None,
                "native_dlls": native_dlls,
                "message": (
                    "Swofty Core binary (swofty.exe) not found. "
                    "Run build_windows/build_swofty_and_launcher.ps1 on Windows (or the GitHub Actions CI workflow) "
                    "to compile swofty-core and native/dist/ from Swofty-Developments/SwoftyLauncher."
                ),
            }
        return {
            "available": True,
            "cli_path": str(cli),
            "native_dist_dir": str(native_dir) if native_dir else None,
            "native_dlls": native_dlls,
            "message": f"Swofty Core CLI ready at {cli} ({len(native_dlls)} native DLLs in native/dist)",
        }

    def run_cli(self, args: list[str], cwd: Path | None = None, env_overrides: dict[str, str] | None = None) -> dict:
        cli = self.find_swofty_cli()
        if not cli:
            return {"ok": False, "error": self.get_status()["message"]}
        cmd = [str(cli), *args]
        env = os.environ.copy()
        native_dir = self.get_native_dist_dir()
        if native_dir:
            env["SWOFTY_NATIVE_DIST"] = str(native_dir)
        if env_overrides:
            env.update(env_overrides)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd or self.core_dir if self.core_dir.is_dir() else self.base_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                self.logger.error(f"Swofty CLI failed ({proc.returncode}): {proc.stderr.strip()}")
                return {"ok": False, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
            self.logger.info(f"Swofty CLI succeeded: {' '.join(cmd)}")
            return {"ok": True, "returncode": 0, "stdout": proc.stdout, "stderr": proc.stderr}
        except Exception as exc:
            self.logger.error(f"Swofty CLI error: {exc}")
            return {"ok": False, "error": str(exc)}
