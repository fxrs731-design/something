import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from core.logger import LauncherLogger
from core.process_utils import hidden_subprocess_options


SIMPLIFIED_IMPORTER_REPO = "TeamSimplified/simplified-swofty-addon"
SIMPLIFIED_IMPORTER_ASSET = "teamsimplified.mods.swoftyaddon"
SIMPLIFIED_IMPORTER_ID = "teamsimplified.mods"
WATERMARK_ADDON_ID = "f1classic.launcher"


class SwoftyBridge:
    """Runs the bundled Swofty CLI and delegates mod lifecycle to its supported APIs."""

    def __init__(self, base_dir: Path, logger: LauncherLogger, data_dir: Path | None = None):
        self.base_dir = Path(base_dir)
        self.logger = logger
        self.core_dir = self.base_dir / "swofty_core"
        local_app_data = os.environ.get("LOCALAPPDATA")
        default_data_dir = (
            Path(local_app_data) / "F1ClassicLauncher" / "Swofty"
            if local_app_data
            else self.base_dir / "swofty_data"
        )
        self.data_dir = Path(data_dir) if data_dir else default_data_dir

    def find_swofty_cli(self) -> Path | None:
        candidates = [
            self.core_dir / "swofty.exe",
            self.core_dir / "swofty",
            self.base_dir / "swofty.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        which_name = "swofty.exe" if sys.platform.startswith("win") else "swofty"
        found = shutil.which(which_name)
        return Path(found) if found else None

    def get_native_dist_dir(self) -> Path | None:
        candidate = self.core_dir / "native" / "dist"
        return candidate if candidate.is_dir() else None

    def get_watermark_addon_dir(self) -> Path:
        return self.core_dir / "addons" / WATERMARK_ADDON_ID

    def _installed_addon_dir(self, addon_id: str) -> Path:
        return self.data_dir / "addons" / addon_id

    @staticmethod
    def content_fingerprint(directory: Path) -> str:
        root = Path(directory)
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"Addon content directory is missing or invalid: {root}")

        digest = hashlib.sha256()
        paths = list(root.rglob("*"))
        if any(path.is_symlink() for path in paths):
            link = next(path for path in paths if path.is_symlink())
            raise ValueError(f"Addon content contains a symbolic link: {link}")
        files = sorted(
            (path for path in paths if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix().casefold(),
        )
        for path in files:
            relative = path.relative_to(root).as_posix()
            if relative == ".swofty-files.json":
                continue
            encoded_path = relative.encode("utf-8")
            size = path.stat().st_size
            digest.update(len(encoded_path).to_bytes(8, "big"))
            digest.update(encoded_path)
            digest.update(size.to_bytes(8, "big"))
            with path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        return digest.hexdigest()

    def _addon_ids(self) -> set[str]:
        addons_dir = self.data_dir / "addons"
        if not addons_dir.is_dir():
            return set()
        return {
            entry.name
            for entry in addons_dir.iterdir()
            if entry.is_dir() and (entry / "swofty.json").is_file()
        }

    def has_addons(self, addon_ids: list[str]) -> bool:
        installed = self._addon_ids()
        return bool(addon_ids) and all(addon_id in installed for addon_id in addon_ids)

    def get_status(self) -> dict:
        cli = self.find_swofty_cli()
        native_dir = self.get_native_dist_dir()
        native_dlls = sorted(
            str(path.relative_to(native_dir)) for path in native_dir.rglob("*.dll")
        ) if native_dir else []
        if not cli:
            return {
                "available": False,
                "cli_path": None,
                "native_dist_dir": str(native_dir) if native_dir else None,
                "native_dlls": native_dlls,
                "message": (
                    "Swofty Core binary (swofty.exe) not found. Run "
                    "build_windows/build_swofty_and_launcher.ps1 on Windows or the GitHub Actions workflow."
                ),
            }
        return {
            "available": True,
            "cli_path": str(cli),
            "native_dist_dir": str(native_dir) if native_dir else None,
            "native_dlls": native_dlls,
            "message": f"Swofty Core CLI ready at {cli} ({len(native_dlls)} native DLLs in native/dist)",
        }

    def run_cli(
        self,
        args: list[str],
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout_seconds: int = 120,
    ) -> dict:
        cli = self.find_swofty_cli()
        if not cli:
            return {"ok": False, "error": self.get_status()["message"]}

        cmd = [str(cli), *args]
        env = os.environ.copy()
        env["SWOFTY_DATA_DIR"] = str(self.data_dir)
        native_dir = self.get_native_dist_dir()
        if native_dir:
            env["SWOFTY_NATIVE_DIST"] = str(native_dir)
        if env_overrides:
            env.update(env_overrides)
        try:
            self.logger.info(f"Starting Swofty CLI subprocess: {cli.name} {' '.join(args)}")
            proc = subprocess.run(
                cmd,
                cwd=str(cwd or (self.core_dir if self.core_dir.is_dir() else self.base_dir)),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
                **hidden_subprocess_options(),
            )
            result = {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
            self.logger.info(f"Swofty CLI subprocess exited with code {proc.returncode}.")
            if proc.returncode != 0:
                self.logger.error(
                    f"Swofty CLI failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}"
                )
            else:
                self.logger.info(f"Swofty CLI succeeded: {' '.join(cmd)}")
            return result
        except subprocess.TimeoutExpired as exc:
            self.logger.error(f"Swofty CLI timed out after {timeout_seconds}s: {' '.join(cmd)}")
            return {
                "ok": False,
                "error": f"Swofty command timed out after {timeout_seconds} seconds.",
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
            }
        except OSError as exc:
            self.logger.error(f"Could not run Swofty CLI: {exc}")
            return {"ok": False, "error": str(exc)}

    def _install_local_addon(
        self,
        addon_id: str,
        package_dir: Path,
        profile: str,
        enable: bool = True,
    ) -> dict:
        manifest_path = package_dir / "swofty.json"
        if not manifest_path.is_file():
            return {"ok": False, "error": f"Required Swofty addon manifest is missing: {manifest_path}"}
        try:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"Cannot read addon manifest {manifest_path}: {exc}"}
        if source_manifest.get("id") != addon_id:
            return {"ok": False, "error": f"Addon manifest ID must be '{addon_id}'."}

        install = True
        installed_dir = self._installed_addon_dir(addon_id)
        installed_manifest = installed_dir / "swofty.json"
        if installed_manifest.is_file():
            try:
                install = self.content_fingerprint(package_dir) != self.content_fingerprint(installed_dir)
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": f"Cannot compare addon content for '{addon_id}': {exc}"}
        if install:
            result = self.run_cli(["addons", "install", str(package_dir)], timeout_seconds=300)
            if not result["ok"]:
                return result

        if not enable:
            return {"ok": True}
        enabled = self.run_cli(["--profile", profile, "addons", "enable", addon_id])
        return enabled

    def ensure_watermark_addon(self, profile: str = "default") -> dict:
        package_dir = self.get_watermark_addon_dir()
        dll = package_dir / "native" / "f1classic_watermark.dll"
        if not dll.is_file():
            return {
                "ok": False,
                "error": (
                    "The F1 Classic in-game watermark addon is not packaged. "
                    "Rebuild with build_windows/build_swofty_and_launcher.ps1."
                ),
            }
        result = self._install_local_addon(WATERMARK_ADDON_ID, package_dir, profile)
        if result["ok"]:
            self.logger.info("F1 Classic in-game watermark addon is enabled for the Swofty profile.")
        return result

    def install_addon_package(self, package_dir: Path, addon_id: str, profile: str = "default") -> dict:
        return self._install_local_addon(addon_id, Path(package_dir), profile, enable=False)

    @staticmethod
    def _request(url: str, accept: str = "application/octet-stream"):
        request = urllib.request.Request(
            url,
            headers={"Accept": accept, "User-Agent": "F1ClassicLauncher/1.0"},
        )
        return urllib.request.urlopen(request, timeout=30)

    def _download_simplified_importer(self) -> dict:
        metadata_url = f"https://api.github.com/repos/{SIMPLIFIED_IMPORTER_REPO}/releases/latest"
        try:
            with self._request(metadata_url, "application/vnd.github+json") as response:
                release = json.loads(response.read().decode("utf-8"))
            assets = {asset["name"]: asset for asset in release.get("assets", [])}
            addon_asset = assets.get(SIMPLIFIED_IMPORTER_ASSET)
            sums_asset = assets.get("SHA256SUMS")
            if not addon_asset or not sums_asset:
                return {"ok": False, "error": "The official Simplified Importer release is missing its addon or checksum."}
            if addon_asset.get("size", 0) <= 0 or addon_asset["size"] > 250 * 1024 * 1024:
                return {"ok": False, "error": "The official Simplified Importer release has an unexpected file size."}
            if not str(addon_asset.get("digest", "")).startswith("sha256:"):
                return {"ok": False, "error": "GitHub did not provide a SHA-256 digest for the official importer."}
            if not str(sums_asset.get("digest", "")).startswith("sha256:"):
                return {"ok": False, "error": "GitHub did not provide a SHA-256 digest for the importer checksums."}

            with self._request(sums_asset["browser_download_url"]) as response:
                sums_bytes = response.read()
            sums_hash = hashlib.sha256(sums_bytes).hexdigest()
            if sums_hash != sums_asset["digest"].removeprefix("sha256:"):
                return {"ok": False, "error": "The official importer checksum file failed SHA-256 verification."}
            sums_text = sums_bytes.decode("utf-8")
            match = re.search(
                rf"(?m)^([0-9a-fA-F]{{64}})\s+\*?{re.escape(SIMPLIFIED_IMPORTER_ASSET)}\s*$",
                sums_text,
            )
            if not match:
                return {"ok": False, "error": "The official importer checksum file does not list its addon package."}
            expected_hash = match.group(1).lower()
            if expected_hash != addon_asset["digest"].removeprefix("sha256:").lower():
                return {"ok": False, "error": "GitHub's importer digest does not match the release checksum file."}

            download_dir = self.data_dir / "cache" / "downloads"
            download_dir.mkdir(parents=True, exist_ok=True)
            package_path = download_dir / SIMPLIFIED_IMPORTER_ASSET
            with self._request(addon_asset["browser_download_url"]) as response, tempfile.NamedTemporaryFile(
                dir=download_dir, prefix="simplified-importer-", suffix=".tmp", delete=False
            ) as output:
                temp_path = Path(output.name)
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > addon_asset["size"]:
                        raise ValueError("The official importer download exceeded its declared size.")
                    digest.update(chunk)
                    output.write(chunk)
            if size != addon_asset["size"] or digest.hexdigest() != expected_hash:
                temp_path.unlink(missing_ok=True)
                return {"ok": False, "error": "The official Simplified Importer download failed size or SHA-256 verification."}
            os.replace(temp_path, package_path)
            self.logger.info(f"Downloaded verified Simplified Importer {release.get('tag_name', 'latest')}.")
            return {"ok": True, "path": str(package_path)}
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self.logger.error(f"Failed to download the official Simplified Importer: {exc}")
            return {"ok": False, "error": f"Could not download the official Simplified Importer: {exc}"}

    def ensure_simplified_importer(self, profile: str = "default") -> dict:
        if not (self._installed_addon_dir(SIMPLIFIED_IMPORTER_ID) / "swofty.json").is_file():
            download = self._download_simplified_importer()
            if not download["ok"]:
                return download
            installed = self.run_cli(["addons", "install", download["path"]], timeout_seconds=600)
            if not installed["ok"]:
                return installed
        return self.run_cli(
            ["--profile", profile, "addons", "enable", SIMPLIFIED_IMPORTER_ID],
            timeout_seconds=300,
        )

    def register_game(self, game_dir: Path) -> dict:
        game_dir = Path(game_dir).resolve()
        listed = self.run_cli(["game", "list"])
        if not listed["ok"]:
            return listed
        normalized_target = str(game_dir).replace("/", "\\").casefold()
        normalized_output = (listed.get("stdout") or "").replace("/", "\\").casefold()
        if normalized_target in normalized_output:
            return {"ok": True, "stdout": listed.get("stdout", "")}
        return self.run_cli(["game", "add", str(game_dir)], timeout_seconds=300)

    def import_mods(
        self,
        source: Path,
        game_dir: Path,
        profile: str = "default",
        known_addon_ids: list[str] | None = None,
    ) -> dict:
        importer = self.ensure_simplified_importer(profile)
        if not importer["ok"]:
            return importer
        registered = self.register_game(game_dir)
        if not registered["ok"]:
            return registered

        before = self._addon_ids()
        result = self.run_cli(
            ["--profile", profile, "mods", "import", str(Path(source).resolve())],
            timeout_seconds=1800,
        )
        if not result["ok"]:
            return result
        new_ids = self._addon_ids() - before
        imported_ids = [
            path.name
            for path in sorted(
                (self.data_dir / "addons" / addon_id for addon_id in new_ids),
                key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name.casefold()),
            )
        ]
        if not imported_ids:
            known = list(dict.fromkeys(known_addon_ids or []))
            if known and all(
                (self._installed_addon_dir(addon_id) / "swofty.json").is_file()
                for addon_id in known
            ):
                self.logger.info(
                    "Simplified Importer refreshed existing Swofty addon(s): "
                    f"{', '.join(known)}"
                )
                return {"ok": True, "addon_ids": known, "stdout": result.get("stdout", "")}
            return {
                "ok": False,
                "error": (
                    "Simplified Importer exited successfully but created no new Swofty addons. "
                    "The package may already be imported or may not contain supported F1 25 files."
                ),
                "stdout": result.get("stdout", ""),
            }
        self.logger.info(f"Simplified Importer created Swofty addon(s): {', '.join(imported_ids)}")
        return {"ok": True, "addon_ids": imported_ids, "stdout": result.get("stdout", "")}

    def install_addon_from_path(self, source: Path, profile: str) -> dict:
        result = self.run_cli(
            ["addons", "install", str(Path(source).resolve())],
            timeout_seconds=600,
        )
        if not result["ok"]:
            return result
        addon_ids = list(dict.fromkeys(re.findall(
            r"(?m)^installed\s+([A-Za-z0-9._-]+)(?:\s+|$)",
            result.get("stdout", ""),
        )))
        if not addon_ids:
            return {
                "ok": False,
                "error": "Swofty installed the package but did not report its addon ID.",
                "stdout": result.get("stdout", ""),
            }
        for addon_id in addon_ids:
            enabled = self.set_addon_enabled(addon_id, True, profile)
            if not enabled["ok"]:
                return {
                    **enabled,
                    "addon_ids": addon_ids,
                    "error": enabled.get("error") or enabled.get("stderr") or f"Installed {addon_id}, but could not enable it in profile {profile}.",
                }
        return {"ok": True, "addon_ids": addon_ids, "stdout": result.get("stdout", "")}

    def list_profile_addons(self, profile: str) -> dict:
        result = self.run_cli(["--profile", profile, "addons", "list"])
        if not result["ok"]:
            return result
        addons = {}
        for line in (result.get("stdout") or "").splitlines():
            parts = line.split(maxsplit=3)
            if len(parts) < 3 or parts[2] not in ("enabled", "disabled"):
                continue
            addons[parts[0]] = {
                "id": parts[0],
                "enabled": parts[2] == "enabled",
                "name": parts[3] if len(parts) > 3 else parts[0],
            }
        return {"ok": True, "addons": addons}

    def list_simplified_mods(self, profile: str) -> dict:
        result = self.run_cli(["--profile", profile, "mods", "list"])
        if not result["ok"]:
            return result
        addons_result = self.list_profile_addons(profile)
        if not addons_result["ok"]:
            return addons_result
        addons = addons_result["addons"]
        mods = []
        for line in (result.get("stdout") or "").splitlines():
            row = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
            if not row:
                continue
            position, remainder = row.groups()
            addon_id = None
            enabled = None
            if remainder.startswith("-> "):
                converted = re.match(r"^->\s+(\S+)\s+(.+)$", remainder)
                if not converted:
                    continue
                addon_id, remainder = converted.groups()
                enabled = addons.get(addon_id, {}).get("enabled")
                state = f"-> {addon_id}"
            elif remainder.startswith("["):
                end = remainder.find("]")
                if end < 0:
                    continue
                state = remainder[:end + 1]
                enabled = state.startswith("[on")
                remainder = remainder[end + 1:].strip()
                if remainder.startswith("not converted:"):
                    boundary = re.search(r"\s(\S+)\s{2}(.+)$", remainder)
                    if not boundary:
                        continue
                    remainder = f"{boundary.group(1)}  {boundary.group(2)}"
            elif remainder.startswith("addon "):
                state = "addon"
                remainder = remainder[len("addon "):]
            else:
                continue
            package = re.match(r"^(\S+)\s{2}(.+)$", remainder)
            if not package:
                continue
            mod_id, name = package.groups()
            mods.append({
                "position": int(position),
                "id": mod_id,
                "name": name,
                "enabled": enabled,
                "addon_id": addon_id,
                "state": state,
            })
        return {"ok": True, "mods": mods, "addons": addons, "stdout": result.get("stdout", "")}

    def set_simplified_mod_enabled(self, mod_id: str, enabled: bool, profile: str) -> dict:
        action = "enable" if enabled else "disable"
        result = self.run_cli(["--profile", profile, "mods", action, mod_id])
        if not result["ok"]:
            return result
        listed = self.list_simplified_mods(profile)
        if not listed["ok"]:
            return listed
        mod = next((entry for entry in listed["mods"] if entry["id"] == mod_id), None)
        if mod and mod.get("addon_id"):
            addon = self.set_addon_enabled(mod["addon_id"], enabled, profile)
            if not addon["ok"]:
                return addon
        return result

    def move_simplified_mod(self, mod_id: str, position: int, profile: str) -> dict:
        if position < 1:
            return {"ok": False, "error": "Load-order positions start at 1."}
        return self.run_cli(["--profile", profile, "mods", "move", mod_id, str(position)])

    def set_addon_enabled(self, addon_id: str, enabled: bool, profile: str = "default") -> dict:
        action = "enable" if enabled else "disable"
        args = ["--profile", profile, "addons", action, addon_id]
        if not enabled:
            args.append("--yes")
        return self.run_cli(args)

    def reorder_addons(
        self,
        addon_ids: list[str],
        previous_order: list[str] | None = None,
        profile: str = "default",
    ) -> dict:
        ordered = list(dict.fromkeys(addon_ids))
        previous = list(dict.fromkeys(ordered if previous_order is None else previous_order))
        for addon_id in ordered:
            result = self.set_addon_enabled(addon_id, False, profile)
            if not result["ok"]:
                for restore_id in previous:
                    self.set_addon_enabled(restore_id, True, profile)
                return result
        for addon_id in ordered:
            result = self.set_addon_enabled(addon_id, True, profile)
            if not result["ok"]:
                for rollback_id in ordered:
                    self.set_addon_enabled(rollback_id, False, profile)
                for restore_id in previous:
                    self.set_addon_enabled(restore_id, True, profile)
                return result
        return {"ok": True, "addon_ids": ordered}

    def check_conflicts(self, game_dir: Path, profile: str = "default") -> dict:
        return self.run_cli(
            ["--profile", profile, "conflicts", "--check", "--game", str(Path(game_dir).resolve())],
            timeout_seconds=1800,
        )

    def setup_status(self) -> dict:
        result = self.run_cli(["setup", "status"])
        if not result["ok"]:
            return result
        output = (result.get("stdout") or "").strip()
        return {
            **result,
            "done": any(line.strip().lower().startswith("setup: done") for line in output.splitlines()),
            "output": output,
        }

    def setup_reset(self, game_dir: Path, other_mods: bool) -> dict:
        args = ["setup", "reset", "--game", str(Path(game_dir).resolve())]
        if other_mods:
            args.append("--other-mods")
        return self.run_cli(args, timeout_seconds=1800)

    def setup_done(self) -> dict:
        return self.run_cli(["setup", "done"], timeout_seconds=300)

    def launch_game(self, game_dir: Path, modded: bool) -> dict:
        game_dir = Path(game_dir).resolve()
        if modded:
            return {
                "ok": False,
                "error": (
                    "Modded F1 25 launch is unavailable: the pinned Swofty `run --apply` command runs "
                    "without EA AntiCheat. No anti-cheat bypass or direct game-executable launch was attempted."
                ),
            }
        registered = self.register_game(game_dir)
        if not registered["ok"]:
            error = registered.get("stderr") or registered.get("error")
            return {"ok": False, "error": f"Could not register the F1 25 game folder with Swofty: {error}"}

        result = self.run_cli(
            ["game", "launch", "--game", str(game_dir)],
            timeout_seconds=360,
        )
        if not result["ok"]:
            return {
                **result,
                "error": result.get("stderr") or result.get("error") or "Swofty could not start F1 25 through the EA app.",
            }
        self.logger.info(
            "Swofty confirmed F1 25 started through its official EA app path (EA AntiCheat enabled)."
        )
        return {
            "ok": True,
            "profile": "official EA app launch (EA AntiCheat)",
            "cli_path": str(self.find_swofty_cli()),
            "stdout": result.get("stdout", ""),
        }
