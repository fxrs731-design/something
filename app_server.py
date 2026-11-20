import base64
import http.server
import json
import mimetypes
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.game_adapters import GameAdapter


install_progress = {"active": False, "percent": 0, "message": "Idle", "season_id": None}
progress_lock = threading.Lock()


def _browse_file_or_dir(mode: str = "zip") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if mode == "dir":
            path = filedialog.askdirectory(title="Select Directory")
        elif mode == "exe":
            path = filedialog.askopenfilename(title="Select Game Executable (.exe)", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        else:
            path = filedialog.askopenfilename(
                title="Select Season Package (.zip)",
                filetypes=[("Season ZIP Package", "*.zip"), ("All files", "*.*")],
            )
        root.destroy()
        return path or ""
    except Exception:
        return ""


def _open_folder_in_os(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


def make_handler(ctx: dict):
    frontend_path = Path(__file__).resolve().parent / "frontend.html"

    class AppHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send_json(self, payload: dict, status: int = 200):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/" or path == "/index.html":
                html = frontend_path.read_text(encoding="utf-8").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return

            if path == "/api/state":
                sm = ctx["season_manager"]
                cfg = ctx["config"]
                seasons = sm.query_seasons(
                    search=qs.get("search", [""])[0],
                    year=int(qs["year"][0]) if qs.get("year", [""])[0].isdigit() else None,
                    game=qs.get("game", [""])[0],
                    sort_by=qs.get("sort", ["season_desc"])[0],
                )
                serialized = []
                for s in seasons:
                    m = s.manifest
                    serialized.append({
                        "id": m.id,
                        "name": m.name,
                        "version": m.version,
                        "author": m.author,
                        "description": m.description,
                        "game": m.game,
                        "season": m.season,
                        "artwork_url": f"/api/artwork/{m.id}" if s.artwork_path else None,
                        "teams": m.teams,
                        "drivers": m.drivers,
                        "operations": m.operations,
                        "dependencies": m.dependencies,
                        "adapter": m.adapter,
                        "notes": m.notes,
                        "requires_swofty_core": m.requires_swofty_core,
                        "installed": s.installed,
                        "imported_at": s.imported_at,
                        "package_dir": str(s.package_dir),
                    })
                with progress_lock:
                    prog = dict(install_progress)

                self._send_json({
                    "seasons": serialized,
                    "invalid_packages": sm.invalid_packages,
                    "supported_games": GameAdapter.list_supported_games(),
                    "config": cfg.data,
                    "progress": prog,
                    "logs": ctx["logger"].get_recent(120),
                })
                return

            if path.startswith("/api/artwork/"):
                season_id = path.split("/api/artwork/", 1)[1]
                entry = ctx["season_manager"].get_season(season_id)
                if not entry or not entry.artwork_path or not entry.artwork_path.is_file():
                    self.send_error(404)
                    return
                mime = mimetypes.guess_type(str(entry.artwork_path))[0] or "image/png"
                data = entry.artwork_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}

            if self.path == "/api/import/path":
                res = ctx["package_importer"].import_package(
                    body.get("path", ""),
                    allow_update=bool(body.get("allow_update", False)),
                )
                self._send_json(res, 200 if res.get("ok") or res.get("duplicate") else 400)
                return

            if self.path == "/api/import/upload":
                filename = body.get("filename", "package.zip")
                b64 = body.get("data_base64", "")
                if not b64:
                    self._send_json({"ok": False, "errors": ["Empty upload payload."]}, 400)
                    return
                tmp_dir = Path(tempfile.mkdtemp(prefix="f1classic_up_"))
                tmp_zip = tmp_dir / Path(filename).name
                try:
                    tmp_zip.write_bytes(base64.b64decode(b64))
                    res = ctx["package_importer"].import_package(
                        str(tmp_zip),
                        allow_update=bool(body.get("allow_update", False)),
                    )
                    self._send_json(res, 200 if res.get("ok") or res.get("duplicate") else 400)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            if self.path == "/api/season/install":
                season_id = body.get("season_id", "")
                def cb(pct: int, msg: str):
                    with progress_lock:
                        install_progress.update({"active": pct < 100, "percent": pct, "message": msg, "season_id": season_id})
                with progress_lock:
                    install_progress.update({"active": True, "percent": 5, "message": "Validating package & game path...", "season_id": season_id})
                res = ctx["mod_installer"].install_season(season_id, progress_cb=cb)
                with progress_lock:
                    install_progress.update({"active": False, "percent": 100 if res.get("ok") else 0, "message": res.get("error", "Complete"), "season_id": season_id})
                self._send_json(res, 200 if res.get("ok") else 400)
                return

            if self.path == "/api/season/uninstall":
                res = ctx["mod_installer"].uninstall_season(body.get("season_id", ""))
                self._send_json(res, 200 if res.get("ok") else 400)
                return

            if self.path == "/api/season/remove":
                res = ctx["season_manager"].remove_season(body.get("season_id", ""))
                self._send_json(res, 200 if res.get("ok") else 400)
                return

            if self.path == "/api/game/detect":
                detected = ctx["game_detector"].detect_installed_games()
                self._send_json({"ok": True, "detected": detected})
                return

            if self.path == "/api/game/set_path":
                game = body.get("game", "")
                path = body.get("path", "")
                pref_exe = body.get("exe", "")
                check = ctx["game_detector"].validate_game_directory(game, path, preferred_exe=pref_exe)
                if check["valid"]:
                    ctx["config"].set_game_path(game, check["directory"])
                    if Path(path).is_file() and Path(path).suffix.lower() == ".exe":
                        ctx["config"].set_game_executable(game, str(Path(path)))
                    elif pref_exe:
                        ctx["config"].set_game_executable(game, pref_exe)
                    elif check.get("executable"):
                        ctx["config"].set_game_executable(game, check["executable"])
                self._send_json({"ok": check["valid"], **check})
                return

            if self.path == "/api/game/launch":
                game = body.get("game", "")
                res = ctx["game_detector"].launch_game(game)
                self._send_json(res, 200 if res.get("ok") else 400)
                return

            if self.path == "/api/open_folder":
                target = Path(body.get("path", ""))
                ok = _open_folder_in_os(target)
                self._send_json({"ok": ok})
                return

            if self.path == "/api/browse":
                mode = body.get("mode", "zip")
                self._send_json({"path": _browse_file_or_dir(mode)})
                return

            if self.path == "/api/settings/save":
                cfg = ctx["config"]
                for key in ("default_game", "seasons_dir", "backups_dir", "imports_dir", "appearance", "updates"):
                    if key in body:
                        cfg.data[key] = body[key]
                cfg.save()
                cfg.ensure_directories()
                self._send_json({"ok": True})
                return

            self.send_error(404)

    return AppHandler


def run_server(ctx: dict, port: int = 0, open_browser: bool = True):
    handler = make_handler(ctx)
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        actual_port = httpd.server_address[1]
        url = f"http://127.0.0.1:{actual_port}/"
        ctx["logger"].info(f"F1 Classic Launcher UI listening at {url}")
        print(f"F1 Classic Launcher running at {url}")
        if open_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            ctx["logger"].info("Launcher shut down by user.")
