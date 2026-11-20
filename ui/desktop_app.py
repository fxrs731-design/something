import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.conflict_detector import ConflictDetector
from core.game_adapters import GameAdapter
from core.season_manager import SeasonEntry
from ui.styles import DARK_F1_QSS


def _open_folder_native(path: Path):
    if not path.exists():
        return
    if sys.platform.startswith("win"):
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class OperationWorker(QThread):
    progress = Signal(int, str)

    def __init__(self, operation: Callable, logger):
        super().__init__()
        self.operation = operation
        self.logger = logger
        self.result = None

    def run(self):
        try:
            self.result = self.operation(lambda pct, message: self.progress.emit(pct, message))
        except Exception as exc:
            self.logger.exception(f"Background launcher operation failed: {exc}")
            self.result = {"ok": False, "error": str(exc)}


class DropZoneFrame(QFrame):
    dropped_path = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        title = QLabel("Drag & Drop a Season .zip or Folder Here")
        title.setObjectName("SectionTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Supports manifest.json and swofty.json season packages")
        sub.setObjectName("MutedText")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        lay.addWidget(sub)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            local = urls[0].toLocalFile()
            if local:
                self.dropped_path.emit(local)


class ModPackagePreviewDialog(QDialog):
    def __init__(self, preview: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inspect Mod Package")
        self.resize(620, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for label, key in (
            ("Name", "name"),
            ("Category", "category"),
            ("Version", "version"),
            ("Author", "author"),
            ("Game", "game"),
            ("Season", "season"),
            ("Compatibility", "compatibility"),
            ("Files", "file_count"),
        ):
            value = QLabel(str(preview.get(key) or "Unknown"))
            value.setWordWrap(True)
            form.addRow(f"{label}:", value)
        layout.addLayout(form)

        description = QLabel(preview.get("description") or "No description was provided.")
        description.setWordWrap(True)
        layout.addWidget(description)
        explanation = QLabel(
            "Package structure is inspected, but adding a mod to a profile does not apply or verify it in F1 25."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        contents = QPlainTextEdit()
        contents.setReadOnly(True)
        files = preview.get("files", [])
        contents.setPlainText("\n".join(files) or "No files found.")
        if preview.get("omitted_files"):
            contents.appendPlainText(f"\n... and {preview['omitted_files']} more file(s).")
        layout.addWidget(contents, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Import to Mod Library")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class F1ClassicLauncherWindow(QMainWindow):
    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx
        self.selected_season_id: str | None = None
        self.operation_worker: OperationWorker | None = None
        self.operation_callback: Callable[[object], None] | None = None
        self.operation_name = ""
        self._season_entries: list[SeasonEntry] = []
        self._season_conflicts: list[dict] = []
        self._invalid_packages: list[dict] = []
        self._dashboard_cards: dict[str, QFrame] = {}
        self._library_cards: dict[str, QFrame] = {}
        self.external_game_running: bool | None = None
        self._external_detection_error: str | None = None

        self.setWindowTitle("F1 Classic Launcher")
        self.resize(1240, 800)
        self.setMinimumSize(960, 640)
        status_bar = self.statusBar()
        self.operation_status = QLabel()
        self.operation_progress = QProgressBar()
        self.operation_progress.setMaximumWidth(180)
        self.operation_progress.setRange(0, 100)
        self.operation_status.hide()
        self.operation_progress.hide()
        status_bar.addPermanentWidget(self.operation_status, 1)
        status_bar.addPermanentWidget(self.operation_progress)

        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QHBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(250)
        sb_lay = QVBoxLayout(self.sidebar)
        sb_lay.setContentsMargins(14, 18, 14, 14)
        sb_lay.setSpacing(8)

        brand = QLabel("F1 Classic Launcher")
        brand.setObjectName("SectionTitle")
        brand_sub = QLabel("Native Desktop Season Manager")
        brand_sub.setObjectName("MutedText")
        sb_lay.addWidget(brand)
        sb_lay.addWidget(brand_sub)
        sb_lay.addSpacing(10)

        self.nav_buttons: dict[int, QPushButton] = {}
        nav_items = [
            (0, "Dashboard"),
            (6, "Mod Library"),
            (7, "Profiles"),
            (1, "Season Library"),
            (2, "Import Package"),
            (3, "Settings & Games"),
            (4, "Installation Logs"),
        ]
        for idx, label in nav_items:
            btn = QPushButton(label)
            btn.setObjectName("NavBtn")
            btn.clicked.connect(lambda _=False, i=idx: self.switch_tab(i))
            self.nav_buttons[idx] = btn
            sb_lay.addWidget(btn)

        sb_lay.addStretch(1)

        self.swofty_status_lbl = QLabel()
        self.swofty_status_lbl.setWordWrap(True)
        self.swofty_status_lbl.setObjectName("MutedText")
        sb_lay.addWidget(self.swofty_status_lbl)

        game_lbl = QLabel("ACTIVE GAME")
        game_lbl.setObjectName("MutedText")
        sb_lay.addWidget(game_lbl)

        self.sidebar_game_combo = QComboBox()
        self.sidebar_game_combo.addItems(GameAdapter.list_supported_games())
        self.sidebar_game_combo.currentTextChanged.connect(self._on_default_game_changed)
        sb_lay.addWidget(self.sidebar_game_combo)

        self.launch_button = QPushButton("Launch F1 25")
        self.launch_button.setObjectName("PrimaryBtn")
        self.launch_button.clicked.connect(self._launch_active_game)
        sb_lay.addWidget(self.launch_button)

        root_lay.addWidget(self.sidebar)

        # Main Area
        main_area = QWidget()
        main_lay = QVBoxLayout(main_area)
        main_lay.setContentsMargins(22, 18, 22, 18)
        main_lay.setSpacing(14)

        hdr_lay = QHBoxLayout()
        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("PageTitle")
        hdr_lay.addWidget(self.page_title)
        hdr_lay.addStretch(1)
        quick_import = QPushButton("+ Import Season")
        quick_import.setObjectName("PrimaryBtn")
        quick_import.clicked.connect(lambda: self.switch_tab(2))
        hdr_lay.addWidget(quick_import)
        main_lay.addLayout(hdr_lay)

        self.stack = QStackedWidget()
        main_lay.addWidget(self.stack, 1)

        # Keep details at stack index 5; the library/profile pages are appended.
        self.page_dashboard = QWidget()
        self.page_library = QWidget()
        self.page_import = QWidget()
        self.page_settings = QWidget()
        self.page_logs = QWidget()
        self.page_details = QWidget()
        self.page_mod_library = QWidget()
        self.page_profiles = QWidget()

        self._init_dashboard_page()
        self._init_library_page()
        self._init_import_page()
        self._init_settings_page()
        self._init_logs_page()
        self._init_details_page()
        self._init_mod_library_page()
        self._init_profiles_page()
        self.external_game_timer = QTimer(self)
        self.external_game_timer.setInterval(3000)
        self.external_game_timer.timeout.connect(self._poll_external_game)

        for p in (
            self.page_dashboard,
            self.page_library,
            self.page_import,
            self.page_settings,
            self.page_logs,
            self.page_details,
            self.page_mod_library,
            self.page_profiles,
        ):
            self.stack.addWidget(p)

        root_lay.addWidget(main_area, 1)
        self._refresh_external_launch_profiles()
        enabled = self.ctx["config"].get_external_launch_enabled()
        self.external_launch_enabled_checkbox.blockSignals(True)
        self.external_launch_enabled_checkbox.setChecked(enabled)
        self.external_launch_enabled_checkbox.blockSignals(False)
        self._update_external_launch_status()
        self.switch_tab(0)
        self.refresh_all()
        if enabled:
            self._start_external_monitoring()

    def switch_tab(self, idx: int):
        if idx != 5:
            self.selected_season_id = None
        for i, btn in self.nav_buttons.items():
            btn.setProperty("active", "true" if i == idx else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        titles = {
            0: "Dashboard",
            1: "Season Library",
            2: "Import Package",
            3: "Settings & Games",
            4: "Installation Logs",
            6: "Mod Library",
            7: "Profiles",
        }
        self.page_title.setText(titles.get(idx, "F1 Classic Launcher"))
        self.stack.setCurrentIndex(idx)
        self._refresh_current_tab(idx)

    def _refresh_current_tab(self, idx: int):
        if idx == 0:
            self._refresh_dashboard()
        elif idx == 1:
            self._refresh_library()
        elif idx == 2:
            self._refresh_swofty_mod_manager()
        elif idx == 3:
            self._refresh_settings()
        elif idx == 4:
            self._refresh_logs()
        elif idx == 6:
            self._refresh_mod_library_page()
        elif idx == 7:
            self._refresh_profiles_page()

    def _start_operation(
        self,
        name: str,
        operation: Callable,
        on_complete: Callable[[object], None] | None = None,
    ) -> bool:
        if self.operation_worker is not None:
            message = f"Wait for “{self.operation_name}” to finish before starting another operation."
            self.operation_status.setText(message)
            self.operation_status.show()
            self.ctx["logger"].warning(message)
            return False

        self.operation_name = name
        self.operation_callback = on_complete
        self.operation_status.setText(name)
        self.operation_status.show()
        self.operation_progress.setRange(0, 0)
        self.operation_progress.show()
        self.centralWidget().setEnabled(False)
        self.ctx["logger"].info(f"Started background operation: {name}.")
        worker = OperationWorker(operation, self.ctx["logger"])
        self.operation_worker = worker
        worker.progress.connect(self._on_operation_progress)
        worker.finished.connect(self._on_operation_thread_finished)
        worker.start()
        return True

    def _on_operation_progress(self, percent: int, message: str):
        self.operation_status.setText(message)
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(max(0, min(percent, 100)))
        if "Activating F1 25 season" in self.operation_name or "Swofty" in self.operation_name:
            self.dash_progress.setValue(max(0, min(percent, 100)))
            self.dash_progress_lbl.setText(message)

    def _on_operation_thread_finished(self):
        worker = self.operation_worker
        if worker is None:
            return
        result = worker.result
        callback = self.operation_callback
        name = self.operation_name
        self.operation_worker = None
        self.operation_callback = None
        self.operation_name = ""
        self.centralWidget().setEnabled(True)
        self.operation_progress.hide()
        self.operation_status.hide()
        worker.deleteLater()
        self.ctx["logger"].info(f"Finished background operation: {name}.")
        if callback:
            try:
                callback(result)
            except Exception as exc:
                self.ctx["logger"].exception(f"Could not update the launcher after {name}: {exc}")
                QMessageBox.critical(self, "Launcher Error", str(exc))

    def _show_operation_error(self, title: str, result: object):
        if isinstance(result, dict):
            error = result.get("error") or result.get("stderr") or result.get("stdout") or "The operation failed."
        else:
            error = str(result)
        self.ctx["logger"].error(f"{title}: {error}")
        QMessageBox.warning(self, title, error)

    def _on_default_game_changed(self, game_name: str):
        self.ctx["config"].data["default_game"] = game_name
        self.ctx["config"].save()
        self._refresh_dashboard()

    def _launch_active_game(self):
        self._launch_specific_game("F1 25")

    def _make_season_card(self, entry: SeasonEntry) -> QFrame:
        m = entry.manifest
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        art_lbl = QLabel()
        art_lbl.setFixedHeight(120)
        art_lbl.setAlignment(Qt.AlignCenter)
        art_lbl.setStyleSheet("background-color: #0b0e15; border-radius: 6px;")
        if entry.artwork_path and entry.artwork_path.is_file():
            pix = QPixmap(str(entry.artwork_path))
            if not pix.isNull():
                art_lbl.setPixmap(pix.scaled(260, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                art_lbl.setText(f"F1 {m.season}")
        else:
            art_lbl.setText(f"F1 {m.season}")
        lay.addWidget(art_lbl)

        title = QLabel(m.name)
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        lay.addWidget(title)

        meta = QLabel(f"{m.season} · v{m.version} · {m.game} · by {m.author}")
        meta.setObjectName("MutedText")
        meta.setWordWrap(True)
        lay.addWidget(meta)

        if entry.installed:
            b = QLabel("INSTALLED")
            b.setObjectName("BadgeInstalled")
            lay.addWidget(b, 0, Qt.AlignLeft)
        elif m.requires_swofty_core:
            b = QLabel("REQUIRES SWOFTY CORE")
            b.setObjectName("BadgeWarning")
            lay.addWidget(b, 0, Qt.AlignLeft)

        desc = QLabel(m.description)
        desc.setWordWrap(True)
        desc.setMaximumHeight(54)
        lay.addWidget(desc)

        btn_row = QHBoxLayout()
        det_btn = QPushButton("View Details")
        det_btn.setObjectName("PrimaryBtn")
        det_btn.clicked.connect(lambda _=False, sid=m.id: self._open_season_details(sid))
        btn_row.addWidget(det_btn)

        if entry.installed:
            un_btn = QPushButton("Uninstall")
            un_btn.setObjectName("DangerBtn")
            un_btn.clicked.connect(lambda _=False, sid=m.id: self._uninstall_season(sid))
            btn_row.addWidget(un_btn)
        else:
            in_btn = QPushButton("Install")
            in_btn.setObjectName("SuccessBtn")
            in_btn.clicked.connect(lambda _=False, sid=m.id: self._install_season(sid))
            btn_row.addWidget(in_btn)

        lay.addLayout(btn_row)
        return card

    # --- Dashboard ---
    def _init_dashboard_page(self):
        lay = QVBoxLayout(self.page_dashboard)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        top_row = QHBoxLayout()
        self.dash_featured_card = QFrame()
        self.dash_featured_card.setObjectName("Card")
        self.dash_featured_lay = QVBoxLayout(self.dash_featured_card)
        top_row.addWidget(self.dash_featured_card, 1)

        status_card = QFrame()
        status_card.setObjectName("Card")
        st_lay = QVBoxLayout(status_card)
        st_title = QLabel("Selected Game & Installation Status")
        st_title.setObjectName("SectionTitle")
        self.dash_game_info = QLabel()
        self.dash_game_info.setWordWrap(True)
        self.dash_counts = QLabel()
        self.dash_counts.setObjectName("MutedText")
        self.dash_progress = QProgressBar()
        self.dash_progress.setValue(0)
        self.dash_progress_lbl = QLabel("Status: Idle")
        self.dash_progress_lbl.setObjectName("MutedText")
        st_lay.addWidget(st_title)
        st_lay.addWidget(self.dash_game_info)
        st_lay.addWidget(self.dash_counts)
        st_lay.addWidget(self.dash_progress)
        st_lay.addWidget(self.dash_progress_lbl)
        top_row.addWidget(status_card, 1)
        lay.addLayout(top_row)

        external_card = QFrame()
        external_card.setObjectName("Card")
        external_layout = QVBoxLayout(external_card)
        external_title = QLabel("External Game Launch")
        external_title.setObjectName("SectionTitle")
        external_layout.addWidget(external_title)

        external_row = QHBoxLayout()
        self.external_launch_enabled_checkbox = QCheckBox("Enable External Game Launch")
        self.external_launch_enabled_checkbox.toggled.connect(self._on_external_launch_toggled)
        self.external_launch_status = QLabel()
        self.external_launch_status.setObjectName("MutedText")
        external_row.addWidget(self.external_launch_enabled_checkbox)
        external_row.addStretch(1)
        external_row.addWidget(self.external_launch_status)
        external_layout.addLayout(external_row)

        profile_row = QHBoxLayout()
        self.external_profile_status = QLabel()
        self.external_profile_combo = QComboBox()
        self.external_profile_combo.currentIndexChanged.connect(self._on_external_profile_changed)
        profile_row.addWidget(self.external_profile_status)
        profile_row.addWidget(self.external_profile_combo, 1)
        external_layout.addLayout(profile_row)

        self.external_game_status = QLabel()
        self.external_mod_status = QLabel()
        self.external_game_status.setObjectName("MutedText")
        self.external_mod_status.setObjectName("MutedText")
        external_layout.addWidget(self.external_game_status)
        external_layout.addWidget(self.external_mod_status)
        external_note = QLabel("Keep Simple Toolz open (minimized is fine) to monitor an independently launched game.")
        external_note.setObjectName("MutedText")
        external_note.setWordWrap(True)
        external_layout.addWidget(external_note)
        lay.addWidget(external_card)

        sec_lbl = QLabel("Installed & Recently Added Seasons")
        sec_lbl.setObjectName("SectionTitle")
        lay.addWidget(sec_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.dash_grid_host = QWidget()
        self.dash_grid = QGridLayout(self.dash_grid_host)
        self.dash_grid.setSpacing(12)
        scroll.setWidget(self.dash_grid_host)
        lay.addWidget(scroll, 1)

    def _refresh_external_launch_profiles(self):
        selected_id = self.ctx["config"].get_external_launch_profile()
        profiles: list[tuple[str, str | None, int | None]] = [("None", None, None)]
        found_ids = set()
        for entry in sorted(
            self._season_entries,
            key=lambda item: (item.manifest.season, item.manifest.name.casefold()),
        ):
            manifest = entry.manifest
            if manifest.game.strip().casefold() != "f1 25" or manifest.season not in (2010, 2012):
                continue
            profiles.append((f"{manifest.season} · {manifest.name}", manifest.id, manifest.season))
            found_ids.add(manifest.id)
        for year, profile_id in self.ctx["mod_installer"].SEASON_PROFILES.items():
            profiles.append((f"{year} · Swofty mod profile", profile_id, year))
            found_ids.add(profile_id)
        if selected_id and selected_id not in found_ids:
            year = next((year for year in (2010, 2012) if str(year) in selected_id), None)
            label = f"{year} (unavailable)" if year else "Unavailable profile"
            profiles.append((label, selected_id, year))

        combo = self.external_profile_combo
        combo.blockSignals(True)
        combo.clear()
        selected_index = 0
        self._external_profile_years: dict[str, int | None] = {}
        for label, profile_id, year in profiles:
            combo.addItem(label, profile_id)
            if profile_id:
                self._external_profile_years[profile_id] = year
            if profile_id == selected_id:
                selected_index = combo.count() - 1
        combo.setCurrentIndex(selected_index)
        combo.blockSignals(False)
        self._update_external_launch_status()

    def _on_external_profile_changed(self, _index: int):
        profile_id = self.external_profile_combo.currentData()
        try:
            self.ctx["config"].set_external_launch_profile(profile_id)
        except Exception as exc:
            self.ctx["logger"].exception(f"Could not save the selected external launch profile: {exc}")
            QMessageBox.warning(self, "Profile Selection Failed", str(exc))
            self._refresh_external_launch_profiles()
            return
        self.ctx["logger"].info(f"Selected external launch profile: {profile_id or 'None'}.")
        self._update_external_launch_status()

    def _on_external_launch_toggled(self, enabled: bool):
        try:
            self.ctx["config"].set_external_launch_enabled(enabled)
        except Exception as exc:
            self.ctx["logger"].exception(f"Could not save external launch monitoring setting: {exc}")
            QMessageBox.warning(self, "External Launch Setting Failed", str(exc))
            self.external_launch_enabled_checkbox.blockSignals(True)
            self.external_launch_enabled_checkbox.setChecked(not enabled)
            self.external_launch_enabled_checkbox.blockSignals(False)
            return
        self.ctx["logger"].info(f"External game launch monitoring {'enabled' if enabled else 'disabled'}.")
        if enabled:
            self._start_external_monitoring()
        else:
            self.external_game_timer.stop()
            self.external_game_running = None
            self._update_external_launch_status()

    def _start_external_monitoring(self):
        self.external_game_timer.start()
        self._poll_external_game()

    def _poll_external_game(self):
        if not self.ctx["config"].get_external_launch_enabled():
            return
        try:
            is_running = self.ctx["game_process_monitor"].is_f1_25_running()
        except Exception as exc:
            message = str(exc)
            if message != self._external_detection_error:
                self.ctx["logger"].exception(f"Could not detect the F1 25 process: {exc}")
                self._external_detection_error = message
            self.external_game_running = None
            self._update_external_launch_status()
            return

        self._external_detection_error = None
        if is_running != self.external_game_running:
            previous_state = self.external_game_running
            self.external_game_running = is_running
            state = "started" if is_running else "closed" if previous_state is True else "not running"
            self.ctx["logger"].info(f"External F1 25 process {state}.")
        self._update_external_launch_status()

    def _update_external_launch_status(self):
        enabled = self.ctx["config"].get_external_launch_enabled()
        self.external_launch_status.setText(f"External launch: {'Enabled' if enabled else 'Disabled'}")
        profile_id = self.ctx["config"].get_external_launch_profile()
        if not profile_id:
            profile_text = "None"
        else:
            year = getattr(self, "_external_profile_years", {}).get(profile_id)
            profile_text = str(year) if year else "Unavailable"
        self.external_profile_status.setText(f"Selected profile: {profile_text}")

        if not enabled:
            game_text = "Not monitored"
        elif self.external_game_running is None:
            game_text = "Not verified"
        else:
            game_text = "Running" if self.external_game_running else "Not running"
        self.external_game_status.setText(f"F1 25 status: {game_text}")
        self.external_game_status.setToolTip(
            f"Process detection failed: {self._external_detection_error}"
            if self._external_detection_error
            else ""
        )

        if profile_id in self.ctx["mod_installer"].SEASON_PROFILES.values() and self.external_game_running is True:
            mod_text = "Not verified"
        elif profile_id in self.ctx["mod_installer"].SEASON_PROFILES.values():
            mod_text = "Inactive"
        else:
            active_id = self.ctx["config"].get_active_season("F1 25")
            if profile_id and active_id == profile_id and self.external_game_running is True:
                mod_text = "Not verified"
            elif profile_id and active_id == profile_id:
                mod_text = "Active"
            else:
                mod_text = "Inactive"
        self.external_mod_status.setText(f"Mod status: {mod_text}")
        self.external_mod_status.setToolTip(
            "The selected Swofty profile is a configuration only. This launcher cannot confirm that an independently "
            "launched F1 25 process loaded its mods."
        )

    def _refresh_dashboard(self):
        while self.dash_featured_lay.count():
            w = self.dash_featured_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        seasons = self.ctx["season_manager"].query_seasons(
            sort_by="recent",
            entries=self._season_entries,
        )
        installed = [s for s in seasons if s.installed]
        featured = installed[0] if installed else (seasons[0] if seasons else None)

        f_hdr = QLabel("Featured Season")
        f_hdr.setObjectName("MutedText")
        self.dash_featured_lay.addWidget(f_hdr)
        if featured:
            m = featured.manifest
            name_lbl = QLabel(m.name)
            name_lbl.setObjectName("SectionTitle")
            meta_lbl = QLabel(f"Target: {m.game} · Year: {m.season} · v{m.version} · by {m.author}")
            meta_lbl.setObjectName("MutedText")
            desc_lbl = QLabel(m.description)
            desc_lbl.setWordWrap(True)
            btn_row = QHBoxLayout()
            open_btn = QPushButton("Open Season Page")
            open_btn.setObjectName("PrimaryBtn")
            open_btn.clicked.connect(lambda _=False, sid=m.id: self._open_season_details(sid))
            btn_row.addWidget(open_btn)
            if featured.installed:
                play_btn = QPushButton(f"Play {m.game}")
                play_btn.setObjectName("SuccessBtn")
                play_btn.clicked.connect(self._launch_active_game)
                btn_row.addWidget(play_btn)
            else:
                inst_btn = QPushButton("Install Season")
                inst_btn.setObjectName("SuccessBtn")
                inst_btn.clicked.connect(lambda _=False, sid=m.id: self._install_season(sid))
                btn_row.addWidget(inst_btn)
            btn_row.addStretch(1)
            self.dash_featured_lay.addWidget(name_lbl)
            self.dash_featured_lay.addWidget(meta_lbl)
            self.dash_featured_lay.addWidget(desc_lbl)
            self.dash_featured_lay.addLayout(btn_row)
        else:
            empty_lbl = QLabel("No seasons imported yet. Open Import Package to add a season.")
            empty_lbl.setWordWrap(True)
            self.dash_featured_lay.addWidget(empty_lbl)

        def_game = self.ctx["config"].data.get("default_game", "F1 25")
        def_path = self.ctx["config"].get_game_executable(def_game) or self.ctx["config"].get_game_path(def_game) or "Not configured"
        self.dash_game_info.setText(f"{def_game}\n{def_path}")
        self.dash_counts.setText(f"{len(seasons)} Imported Seasons · {len(installed)} Active Installs")

        visible_ids = {entry.manifest.id for entry in seasons[:6]}
        for season_id, card in self._dashboard_cards.items():
            if season_id not in visible_ids:
                self.dash_grid.removeWidget(card)
                card.hide()
        for i, entry in enumerate(seasons[:6]):
            card = self._dashboard_cards.get(entry.manifest.id)
            if card is None:
                card = self._make_season_card(entry)
                self._dashboard_cards[entry.manifest.id] = card
            self.dash_grid.removeWidget(card)
            self.dash_grid.addWidget(card, i // 3, i % 3)
            card.show()

    # --- Library ---
    def _init_library_page(self):
        lay = QVBoxLayout(self.page_library)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        self.lib_search = QLineEdit()
        self.lib_search.setPlaceholderText("Search seasons, authors, games...")
        self.library_search_timer = QTimer(self)
        self.library_search_timer.setSingleShot(True)
        self.library_search_timer.setInterval(180)
        self.library_search_timer.timeout.connect(self._refresh_library)
        self.lib_search.textChanged.connect(lambda _text: self.library_search_timer.start())
        self.lib_year = QComboBox()
        self.lib_year.currentIndexChanged.connect(self._refresh_library)
        self.lib_game = QComboBox()
        self.lib_game.addItem("All Games", "")
        for g in GameAdapter.list_supported_games():
            self.lib_game.addItem(g, g)
        self.lib_game.currentIndexChanged.connect(self._refresh_library)
        self.lib_sort = QComboBox()
        self.lib_sort.addItem("Year (Newest)", "season_desc")
        self.lib_sort.addItem("Year (Oldest)", "season_asc")
        self.lib_sort.addItem("Name (A-Z)", "name_asc")
        self.lib_sort.addItem("Recently Added", "recent")
        self.lib_sort.currentIndexChanged.connect(self._refresh_library)

        bar.addWidget(self.lib_search, 2)
        bar.addWidget(self.lib_year, 1)
        bar.addWidget(self.lib_game, 1)
        bar.addWidget(self.lib_sort, 1)
        lay.addLayout(bar)

        self.lib_banner = QLabel()
        self.lib_banner.setObjectName("BadgeWarning")
        self.lib_banner.setWordWrap(True)
        self.lib_banner.hide()
        lay.addWidget(self.lib_banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.lib_grid_host = QWidget()
        self.lib_grid = QGridLayout(self.lib_grid_host)
        self.lib_grid.setSpacing(12)
        scroll.setWidget(self.lib_grid_host)
        lay.addWidget(scroll, 1)

    def _refresh_library(self):
        all_seasons = self._season_entries
        years = sorted({s.manifest.season for s in all_seasons}, reverse=True)
        cur_y = self.lib_year.currentData()
        self.lib_year.blockSignals(True)
        self.lib_year.clear()
        self.lib_year.addItem("All Years", None)
        for y in years:
            self.lib_year.addItem(str(y), y)
        if cur_y in years:
            self.lib_year.setCurrentText(str(cur_y))
        self.lib_year.blockSignals(False)

        seasons = self.ctx["season_manager"].query_seasons(
            search=self.lib_search.text(),
            year=self.lib_year.currentData(),
            game=self.lib_game.currentData() or "",
            sort_by=self.lib_sort.currentData() or "season_desc",
            entries=all_seasons,
        )

        notices = []
        if self._season_conflicts:
            notices.append(f"{len(self._season_conflicts)} overlapping file conflict(s) detected across imported packages.")
        if self._invalid_packages:
            notices.append(f"{len(self._invalid_packages)} invalid package folder(s) in seasons directory.")
        if notices:
            self.lib_banner.setText(" | ".join(notices))
            self.lib_banner.show()
        else:
            self.lib_banner.hide()

        visible_ids = {entry.manifest.id for entry in seasons}
        for season_id, card in self._library_cards.items():
            if season_id not in visible_ids:
                self.lib_grid.removeWidget(card)
                card.hide()
        for i, entry in enumerate(seasons):
            card = self._library_cards.get(entry.manifest.id)
            if card is None:
                card = self._make_season_card(entry)
                self._library_cards[entry.manifest.id] = card
            self.lib_grid.removeWidget(card)
            self.lib_grid.addWidget(card, i // 3, i % 3)
            card.show()

    # --- Details ---
    def _init_details_page(self):
        lay = QVBoxLayout(self.page_details)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_host = QWidget()
        self.details_lay = QVBoxLayout(self.details_host)
        self.details_scroll.setWidget(self.details_host)
        lay.addWidget(self.details_scroll)

    def _open_season_details(self, season_id: str):
        self.selected_season_id = season_id
        entry = self.ctx["season_manager"].get_season(season_id, self._season_entries)
        if not entry:
            self.statusBar().showMessage("Season details will be available after the library scan completes.", 8000)
            return
        m = entry.manifest
        self.page_title.setText(m.name)

        while self.details_lay.count():
            w = self.details_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

        back_btn = QPushButton("← Back to Season Library")
        back_btn.clicked.connect(lambda: self.switch_tab(1))
        self.details_lay.addWidget(back_btn, 0, Qt.AlignLeft)

        hdr = QFrame()
        hdr.setObjectName("Card")
        hl = QHBoxLayout(hdr)
        art = QLabel()
        art.setFixedSize(220, 140)
        art.setAlignment(Qt.AlignCenter)
        art.setStyleSheet("background-color: #0b0e15; border-radius: 8px;")
        if entry.artwork_path and entry.artwork_path.is_file():
            pix = QPixmap(str(entry.artwork_path))
            if not pix.isNull():
                art.setPixmap(pix.scaled(220, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            art.setText(f"F1 {m.season}")
        hl.addWidget(art)

        info_col = QVBoxLayout()
        t_lbl = QLabel(f"{m.name} (v{m.version})")
        t_lbl.setObjectName("SectionTitle")
        sub_lbl = QLabel(f"Year: {m.season} · Target Game: {m.game} · Author: {m.author} · Adapter: {m.adapter}")
        sub_lbl.setObjectName("MutedText")
        desc_lbl = QLabel(m.description)
        desc_lbl.setWordWrap(True)
        info_col.addWidget(t_lbl)
        info_col.addWidget(sub_lbl)
        info_col.addWidget(desc_lbl)

        if m.requires_swofty_core:
            warn = QLabel(f"Compatibility Notice: {m.notes}")
            warn.setObjectName("BadgeWarning")
            warn.setWordWrap(True)
            info_col.addWidget(warn)

        act_row = QHBoxLayout()
        if entry.installed:
            play_b = QPushButton(f"Play {m.game}")
            play_b.setObjectName("SuccessBtn")
            play_b.clicked.connect(lambda: self._launch_specific_game(m.game))
            un_b = QPushButton("Deactivate Season")
            un_b.setObjectName("DangerBtn")
            un_b.clicked.connect(lambda: self._uninstall_season(m.id))
            act_row.addWidget(play_b)
            act_row.addWidget(un_b)
        else:
            inst_b = QPushButton("Install Season")
            inst_b.setObjectName("PrimaryBtn")
            inst_b.clicked.connect(lambda: self._install_season(m.id))
            act_row.addWidget(inst_b)

        open_b = QPushButton("Open Package Folder")
        open_b.clicked.connect(lambda: _open_folder_native(entry.package_dir))
        rem_b = QPushButton("Remove Package")
        rem_b.setObjectName("DangerBtn")
        rem_b.clicked.connect(lambda: self._remove_season(m.id))
        act_row.addWidget(open_b)
        act_row.addWidget(rem_b)
        act_row.addStretch(1)
        info_col.addLayout(act_row)
        hl.addLayout(info_col, 1)
        self.details_lay.addWidget(hdr)

        active_id = self.ctx["config"].get_active_season("F1 25")
        active_entry = self.ctx["season_manager"].get_season(active_id, self._season_entries) if active_id else None
        if m.game == "F1 25" and active_entry and active_entry.manifest.season == m.season:
            addon_ids = self.ctx["mod_installer"].get_season_addon_order(m.season)
            if addon_ids:
                order_card = QFrame()
                order_card.setObjectName("Card")
                order_layout = QVBoxLayout(order_card)
                order_title = QLabel(f"{m.season} Swofty Addon Load Order")
                order_title.setObjectName("SectionTitle")
                order_layout.addWidget(order_title)
                order_help = QLabel("Select an addon and move it to change the order Swofty applies the profile.")
                order_help.setObjectName("MutedText")
                order_help.setWordWrap(True)
                order_layout.addWidget(order_help)
                self.season_addon_order = QListWidget()
                self.season_addon_order.addItems(addon_ids)
                self.season_addon_order.setMaximumHeight(min(240, max(72, len(addon_ids) * 34)))
                order_layout.addWidget(self.season_addon_order)
                order_buttons = QHBoxLayout()
                move_up = QPushButton("Move Up")
                move_up.clicked.connect(lambda: self._move_addon_order_item(-1))
                move_down = QPushButton("Move Down")
                move_down.clicked.connect(lambda: self._move_addon_order_item(1))
                save_order = QPushButton("Apply Load Order")
                save_order.setObjectName("PrimaryBtn")
                save_order.clicked.connect(lambda _=False, sid=m.id: self._save_addon_order(sid))
                order_buttons.addWidget(move_up)
                order_buttons.addWidget(move_down)
                order_buttons.addStretch(1)
                order_buttons.addWidget(save_order)
                order_layout.addLayout(order_buttons)
                self.details_lay.addWidget(order_card)

        meta_card = QFrame()
        meta_card.setObjectName("Card")
        ml = QVBoxLayout(meta_card)
        teams_str = ", ".join(str(t.get("name", t) if isinstance(t, dict) else t) for t in m.teams) or "None listed"
        drivers_str = ", ".join(str(d.get("name", d) if isinstance(d, dict) else d) for d in m.drivers) or "None listed"
        deps_str = ", ".join(m.dependencies) or "None"
        ops_str = "\n".join(f"• {op.get('type')}: {op.get('source', '')} -> {op.get('target', '')}" for op in m.operations) or "None"

        for heading, text in (
            (f"Teams ({len(m.teams)})", teams_str),
            (f"Drivers ({len(m.drivers)})", drivers_str),
            ("Dependencies", deps_str),
            (f"Mod Operations ({len(m.operations)})", ops_str),
        ):
            h = QLabel(heading)
            h.setObjectName("SectionTitle")
            v = QLabel(text)
            v.setWordWrap(True)
            v.setObjectName("MutedText")
            ml.addWidget(h)
            ml.addWidget(v)
        self.details_lay.addWidget(meta_card)
        self.details_lay.addStretch(1)
        self.stack.setCurrentIndex(5)

    def _move_addon_order_item(self, offset: int):
        widget = getattr(self, "season_addon_order", None)
        if widget is None:
            return
        row = widget.currentRow()
        destination = row + offset
        if row < 0 or destination < 0 or destination >= widget.count():
            return
        item = widget.takeItem(row)
        widget.insertItem(destination, item)
        widget.setCurrentRow(destination)

    def _save_addon_order(self, season_id: str):
        widget = getattr(self, "season_addon_order", None)
        if widget is None:
            return
        addon_ids = [widget.item(index).text() for index in range(widget.count())]
        def complete(result):
            if result.get("ok"):
                QMessageBox.information(
                    self,
                    "Load Order Applied",
                    result.get("conflicts") or "Swofty rebuilt the profile with the requested addon order.",
                )
                self._open_season_details(season_id)
            else:
                self._show_operation_error("Load Order Failed", result)

        self._start_operation(
            "Applying Swofty addon load order...",
            lambda _progress: self.ctx["mod_installer"].reorder_season_addons(season_id, addon_ids),
            complete,
        )

    # --- Import ---
    def _init_import_page(self):
        lay = QVBoxLayout(self.page_import)
        lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        content = QVBoxLayout(host)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)

        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        title = QLabel("Import a Season Package")
        title.setObjectName("SectionTitle")
        cl.addWidget(title)
        instructions = QLabel(
            "Import a launcher season package with manifest.json or swofty.json. Use the Swofty Profile Mod Manager "
            "below for ordinary F1 25 mod archives and folders."
        )
        instructions.setWordWrap(True)
        instructions.setObjectName("MutedText")
        cl.addWidget(instructions)

        drop = DropZoneFrame()
        drop.dropped_path.connect(self._run_import)
        cl.addWidget(drop)

        row = QHBoxLayout()
        row.addWidget(QLabel("Season for raw mods:"))
        self.import_season_combo = QComboBox()
        self.import_season_combo.addItem("2010", 2010)
        self.import_season_combo.addItem("2012", 2012)
        row.addWidget(self.import_season_combo)
        self.import_path_edit = QLineEdit()
        self.import_path_edit.setPlaceholderText("Select or paste a Swofty addon, ZIP, or F1 25 mod folder...")
        b_zip = QPushButton("Browse .zip")
        b_zip.clicked.connect(self._browse_import_zip)
        b_dir = QPushButton("Browse Folder")
        b_dir.clicked.connect(self._browse_import_dir)
        b_go = QPushButton("Import Path")
        b_go.setObjectName("PrimaryBtn")
        b_go.clicked.connect(lambda: self._run_import(self.import_path_edit.text()))
        row.addWidget(self.import_path_edit, 1)
        row.addWidget(b_zip)
        row.addWidget(b_dir)
        row.addWidget(b_go)
        cl.addLayout(row)

        self.import_status = QLabel("")
        self.import_status.setWordWrap(True)
        cl.addWidget(self.import_status)

        content.addWidget(card)

        manager_card = QFrame()
        manager_card.setObjectName("Card")
        manager_layout = QVBoxLayout(manager_card)
        manager_title = QLabel("Swofty Profile Mod Manager")
        manager_title.setObjectName("SectionTitle")
        manager_layout.addWidget(manager_title)
        manager_note = QLabel(
            "2010 and 2012 are separate F1 25 profiles. Simplified Importer handles ZIP, 7z, RAR, and nested game-folder "
            "layouts, including compatible .erp and mod.serps content. A standalone .erp has no destination, and a "
            "standalone .serps file is metadata, not a mod. Profile selection does not mean mods are loaded in-game; "
            "an EA AntiCheat-compatible modded launch path is not available."
        )
        manager_note.setWordWrap(True)
        manager_note.setObjectName("MutedText")
        manager_layout.addWidget(manager_note)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("F1 25 season profile:"))
        self.swofty_profile_year_combo = QComboBox()
        self.swofty_profile_year_combo.addItem("2010", 2010)
        self.swofty_profile_year_combo.addItem("2012", 2012)
        initial_year = self.ctx["config"].data.get("selected_swofty_profile_year", 2010)
        initial_index = self.swofty_profile_year_combo.findData(initial_year)
        self.swofty_profile_year_combo.setCurrentIndex(initial_index if initial_index >= 0 else 0)
        self.swofty_profile_year_combo.currentIndexChanged.connect(self._on_swofty_profile_year_changed)
        profile_row.addWidget(self.swofty_profile_year_combo)
        self.swofty_profile_label = QLabel()
        self.swofty_profile_label.setObjectName("MutedText")
        profile_row.addWidget(self.swofty_profile_label, 1)
        manager_layout.addLayout(profile_row)

        self.swofty_mod_list = QListWidget()
        self.swofty_mod_list.setMinimumHeight(190)
        self.swofty_mod_list.itemChanged.connect(self._on_swofty_mod_item_changed)
        manager_layout.addWidget(self.swofty_mod_list)

        import_row = QHBoxLayout()
        self.swofty_mod_path_edit = QLineEdit()
        self.swofty_mod_path_edit.setPlaceholderText("Select a Swofty addon, ZIP, 7z, RAR, or F1 25 mod folder...")
        browse_mod = QPushButton("Browse Package")
        browse_mod.clicked.connect(self._browse_swofty_mod_package)
        browse_mod_folder = QPushButton("Browse Folder")
        browse_mod_folder.clicked.connect(self._browse_swofty_mod_folder)
        import_mod = QPushButton("Import Mod")
        import_mod.setObjectName("PrimaryBtn")
        import_mod.clicked.connect(lambda: self._run_swofty_mod_import(self.swofty_mod_path_edit.text()))
        import_row.addWidget(self.swofty_mod_path_edit, 1)
        import_row.addWidget(browse_mod)
        import_row.addWidget(browse_mod_folder)
        import_row.addWidget(import_mod)
        manager_layout.addLayout(import_row)

        action_row = QHBoxLayout()
        refresh_mods = QPushButton("Refresh")
        refresh_mods.clicked.connect(self._refresh_swofty_mod_manager)
        enable_mod = QPushButton("Enable Selected")
        enable_mod.clicked.connect(lambda: self._set_swofty_mod_item_enabled(True))
        disable_mod = QPushButton("Disable Selected")
        disable_mod.clicked.connect(lambda: self._set_swofty_mod_item_enabled(False))
        self.swofty_mod_move_up = QPushButton("Move Up")
        self.swofty_mod_move_up.clicked.connect(lambda: self._move_swofty_mod(-1))
        self.swofty_mod_move_down = QPushButton("Move Down")
        self.swofty_mod_move_down.clicked.connect(lambda: self._move_swofty_mod(1))
        check_conflicts = QPushButton("Check Profile Conflicts")
        check_conflicts.clicked.connect(self._check_swofty_profile_conflicts)
        action_row.addWidget(refresh_mods)
        action_row.addWidget(enable_mod)
        action_row.addWidget(disable_mod)
        action_row.addWidget(self.swofty_mod_move_up)
        action_row.addWidget(self.swofty_mod_move_down)
        action_row.addStretch(1)
        action_row.addWidget(check_conflicts)
        manager_layout.addLayout(action_row)

        self.swofty_mod_status = QLabel("Select Refresh to load the selected Swofty profile.")
        self.swofty_mod_status.setWordWrap(True)
        self.swofty_mod_status.setObjectName("MutedText")
        manager_layout.addWidget(self.swofty_mod_status)

        content.addWidget(manager_card)
        content.addStretch(1)
        scroll.setWidget(host)
        lay.addWidget(scroll)

    def _browse_import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Swofty Addon or Mod (.zip)", "", "Packages (*.zip *.swoftyaddon);;All Files (*)")
        if path:
            self.import_path_edit.setText(path)
            self._run_import(path)

    def _browse_import_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Swofty Addon or F1 25 Mod Folder")
        if path:
            self.import_path_edit.setText(path)
            self._run_import(path)

    def _run_import(self, path_str: str, allow_update: bool = False):
        if not path_str.strip():
            return
        season_year = self.import_season_combo.currentData()
        self.import_status.setText("Importing, validating, and copying package...")

        def complete(result):
            if result.get("ok"):
                self.import_status.setText(f"Imported: {result.get('name')} ({result.get('game')})")
                QMessageBox.information(
                    self,
                    "Import Complete",
                    f"Successfully {result.get('action')}: {result.get('name')}\n"
                    "Activate it from the season page to register its addon with Swofty.",
                )
                self.refresh_all()
            elif result.get("duplicate"):
                reply = QMessageBox.question(
                    self,
                    "Duplicate Season ID",
                    "\n".join(result.get("errors", [])) + "\n\nUpdate existing package?",
                )
                if reply == QMessageBox.Yes:
                    self._run_import(path_str, allow_update=True)
            else:
                errors = result.get("errors", [result.get("error", "Unknown import error")])
                err_msg = "\n".join(errors)
                self.import_status.setText(f"Error: {err_msg}")
                QMessageBox.warning(self, "Invalid Package", err_msg)

        self._start_operation(
            "Importing mod package...",
            lambda _progress: self.ctx["package_importer"].import_package(
                path_str,
                allow_update=allow_update,
                season_year=season_year,
                progress_cb=progress,
            ),
            complete,
        )

    def _selected_swofty_profile_year(self) -> int:
        return int(self.swofty_profile_year_combo.currentData())

    def _on_swofty_profile_year_changed(self, _index: int):
        year = self._selected_swofty_profile_year()
        try:
            self.ctx["config"].data["selected_swofty_profile_year"] = year
            self.ctx["config"].save()
        except Exception as exc:
            self.ctx["logger"].exception(f"Could not save the selected Swofty profile: {exc}")
            QMessageBox.warning(self, "Profile Selection Failed", str(exc))
            return
        self._refresh_swofty_mod_manager()

    def _browse_swofty_mod_package(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a Swofty addon or F1 25 mod package",
            "",
            "Mod packages (*.swoftyaddon *.zip *.7z *.rar *.erp *.serps);;All Files (*)",
        )
        if path:
            self.swofty_mod_path_edit.setText(path)
            self._run_swofty_mod_import(path)

    def _browse_swofty_mod_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select an F1 25 mod folder")
        if path:
            self.swofty_mod_path_edit.setText(path)
            self._run_swofty_mod_import(path)

    def _run_swofty_mod_import(self, path_str: str):
        if not path_str.strip():
            return
        year = self._selected_swofty_profile_year()

        def complete(result):
            if result.get("ok"):
                ids = ", ".join(result.get("addon_ids", [])) or "Swofty will finish conversion on its next profile build."
                self.swofty_mod_status.setText(
                    f"Imported into the {year} Swofty profile. Addon(s): {ids}. "
                    "This confirms profile registration only, not in-game loading."
                )
                self.swofty_mod_path_edit.clear()
                self._refresh_swofty_mod_manager()
            else:
                self.swofty_mod_status.setText(
                    result.get("error") or result.get("stderr") or result.get("stdout") or "Swofty could not import this package."
                )
                self._show_operation_error("Mod Import Failed", result)

        self._start_operation(
            f"Importing mod into the {year} Swofty profile...",
            lambda progress: self.ctx["mod_installer"].import_swofty_mod(path_str, year, progress),
            complete,
        )

    def _refresh_swofty_mod_manager(self):
        if self.operation_worker is not None:
            return
        year = self._selected_swofty_profile_year()

        def complete(result):
            if not result.get("ok"):
                error = result.get("error") or result.get("stderr") or result.get("stdout") or "Could not read the Swofty profile."
                self.swofty_profile_label.setText(f"Swofty profile: {error}")
                self.swofty_mod_status.setText(error)
                self.swofty_mod_list.clear()
                return
            self.swofty_profile_label.setText(f"Swofty profile: {result['profile']}")
            self.swofty_mod_list.blockSignals(True)
            self.swofty_mod_list.clear()
            for mod in result.get("mods", []):
                enabled = mod.get("enabled")
                state_unknown = mod.get("state_unknown", False)
                state = (
                    "enabled (saved preference)" if enabled is True and state_unknown
                    else "disabled (saved preference)" if enabled is False and state_unknown
                    else "enabled" if enabled is True
                    else "disabled" if enabled is False
                    else "state unavailable"
                )
                item = QListWidgetItem(f"{mod['position']}. {mod['name']}  [{state}]  {mod['id']}")
                item.setData(Qt.UserRole, {
                    "kind": "mod",
                    "id": mod["id"],
                    "state_unknown": state_unknown,
                })
                if enabled is not None:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
                self.swofty_mod_list.addItem(item)
            for addon in result.get("direct_addons", []):
                enabled = addon.get("enabled")
                state = "enabled" if enabled else "disabled"
                item = QListWidgetItem(f"{addon['name']}  [{state}]  {addon['id']}")
                item.setData(Qt.UserRole, {"kind": "addon", "id": addon["id"], "state_unknown": False})
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
                self.swofty_mod_list.addItem(item)
            self.swofty_mod_list.blockSignals(False)
            if self.swofty_mod_list.count() == 0:
                self.swofty_mod_status.setText(
                    "No imported mods are registered in this profile. Import a mod folder/archive or Swofty addon."
                )
            else:
                self.swofty_mod_status.setText(
                    "Checked entries are enabled in the Swofty profile. This does not verify that F1 25 loaded them."
                )

        self._start_operation(
            f"Reading the {year} Swofty profile...",
            lambda _progress: self.ctx["mod_installer"].get_swofty_mod_profile(year),
            complete,
        )

    def _on_swofty_mod_item_changed(self, item: QListWidgetItem):
        item_data = item.data(Qt.UserRole) or {}
        kind = item_data.get("kind")
        mod_id = item_data.get("id")
        if kind not in ("mod", "addon") or not mod_id:
            return
        enabled = item.checkState() == Qt.Checked
        self._update_swofty_mod_enabled(item_data, enabled)

    def _set_swofty_mod_item_enabled(self, enabled: bool):
        item = self.swofty_mod_list.currentItem()
        if item is None:
            return
        item_data = item.data(Qt.UserRole) or {}
        if item_data.get("kind") in ("mod", "addon") and item_data.get("id"):
            self._update_swofty_mod_enabled(item_data, enabled)

    def _update_swofty_mod_enabled(self, item_data: dict, enabled: bool):
        kind = item_data.get("kind")
        mod_id = item_data.get("id")
        if kind not in ("mod", "addon") or not mod_id:
            return
        year = self._selected_swofty_profile_year()

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Swofty Profile Update Failed", result)
            self._refresh_swofty_mod_manager()

        self._start_operation(
            f"{'Enabling' if enabled else 'Disabling'} {mod_id} in the {year} profile...",
            lambda _progress: (
                self.ctx["mod_installer"].set_swofty_mod_enabled(
                    year,
                    mod_id,
                    enabled,
                    remember_unknown=item_data.get("state_unknown", False),
                )
                if kind == "mod"
                else self.ctx["mod_installer"].set_swofty_addon_enabled(year, mod_id, enabled)
            ),
            complete,
        )

    def _move_swofty_mod(self, offset: int):
        row = self.swofty_mod_list.currentRow()
        if row < 0:
            return
        item = self.swofty_mod_list.item(row)
        item_data = item.data(Qt.UserRole) or {}
        if item_data.get("kind") != "mod":
            self.swofty_mod_status.setText("Swofty CLI load-order controls apply to Simplified-imported mods.")
            return
        mods = [
            self.swofty_mod_list.item(index).data(Qt.UserRole)
            for index in range(self.swofty_mod_list.count())
            if (self.swofty_mod_list.item(index).data(Qt.UserRole) or {}).get("kind") == "mod"
        ]
        mod_index = next((index for index, entry in enumerate(mods) if entry["id"] == item_data["id"]), -1)
        destination = mod_index + offset
        if mod_index < 0 or destination < 0 or destination >= len(mods):
            return
        year = self._selected_swofty_profile_year()

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Load Order Update Failed", result)
            self._refresh_swofty_mod_manager()

        self._start_operation(
            f"Updating {year} Swofty load order...",
            lambda _progress: self.ctx["mod_installer"].move_swofty_mod(
                year,
                item_data["id"],
                destination + 1,
            ),
            complete,
        )

    def _check_swofty_profile_conflicts(self):
        year = self._selected_swofty_profile_year()

        def complete(result):
            output = (result.get("stdout") or result.get("stderr") or result.get("error") or "").strip()
            self.swofty_mod_status.setText(output or "Swofty conflict check completed without output.")
            if not result.get("ok"):
                self._show_operation_error("Swofty Conflict Check Failed", result)

        self._start_operation(
            f"Checking {year} Swofty profile conflicts...",
            lambda _progress: self.ctx["mod_installer"].check_swofty_profile_conflicts(year),
            complete,
        )

    # --- Settings ---
    def _init_settings_page(self):
        lay = QVBoxLayout(self.page_settings)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        hl = QVBoxLayout(host)

        games_card = QFrame()
        games_card.setObjectName("Card")
        gl = QVBoxLayout(games_card)
        top = QHBoxLayout()
        gt = QLabel("Game Installation Directories")
        gt.setObjectName("SectionTitle")
        detect_btn = QPushButton("Auto-Detect Installed Games")
        detect_btn.setObjectName("PrimaryBtn")
        detect_btn.clicked.connect(self._auto_detect_games)
        top.addWidget(gt)
        top.addStretch(1)
        top.addWidget(detect_btn)
        gl.addLayout(top)

        self.game_inputs: dict[str, QLineEdit] = {}
        for g in GameAdapter.list_supported_games():
            row = QHBoxLayout()
            lbl = QLabel(g)
            lbl.setFixedWidth(90)
            inp = QLineEdit()
            inp.setPlaceholderText(f"Folder or .exe for {g} (e.g. EAAntiCheat.GameServiceLauncher.exe)")
            b_dir = QPushButton("Folder")
            b_dir.clicked.connect(lambda _=False, gm=g: self._browse_game_dir(gm))
            b_exe = QPushButton("Browse .exe")
            b_exe.clicked.connect(lambda _=False, gm=g: self._browse_game_exe(gm))
            b_save = QPushButton("Validate & Save")
            b_save.clicked.connect(lambda _=False, gm=g: self._save_game_path(gm))
            self.game_inputs[g] = inp
            row.addWidget(lbl)
            row.addWidget(inp, 1)
            row.addWidget(b_dir)
            row.addWidget(b_exe)
            row.addWidget(b_save)
            gl.addLayout(row)
        hl.addWidget(games_card)

        setup_card = QFrame()
        setup_card.setObjectName("Card")
        setup_layout = QVBoxLayout(setup_card)
        setup_title = QLabel("Swofty Modded-Play Setup")
        setup_title.setObjectName("SectionTitle")
        setup_text = QLabel(
            "Swofty setup can restore/record the F1 25 game folder for addon profile preparation. "
            "The launcher blocks Swofty's modded run path because it runs without EA AntiCheat. "
            "Vanilla F1 25 launches use the official EA app / EA AntiCheat path."
        )
        setup_text.setWordWrap(True)
        setup_text.setObjectName("MutedText")
        setup_layout.addWidget(setup_title)
        setup_layout.addWidget(setup_text)
        self.swofty_setup_status_lbl = QLabel("Check Swofty setup status before modded play.")
        self.swofty_setup_status_lbl.setWordWrap(True)
        setup_layout.addWidget(self.swofty_setup_status_lbl)
        setup_buttons = QHBoxLayout()
        self.swofty_setup_reset_btn = QPushButton("Start Setup Reset")
        self.swofty_setup_reset_btn.clicked.connect(self._start_swofty_setup_reset)
        self.swofty_setup_done_btn = QPushButton("Finish Setup")
        self.swofty_setup_done_btn.setObjectName("PrimaryBtn")
        self.swofty_setup_done_btn.clicked.connect(self._finish_swofty_setup)
        setup_buttons.addWidget(self.swofty_setup_reset_btn)
        setup_buttons.addWidget(self.swofty_setup_done_btn)
        setup_layout.addLayout(setup_buttons)
        hl.addWidget(setup_card)

        dir_card = QFrame()
        dir_card.setObjectName("Card")
        dl = QVBoxLayout(dir_card)
        dt = QLabel("Launcher Storage Directories")
        dt.setObjectName("SectionTitle")
        dl.addWidget(dt)
        self.dir_seasons = QLineEdit()
        self.dir_backups = QLineEdit()
        self.dir_imports = QLineEdit()
        for label, field in (
            ("Seasons Directory", self.dir_seasons),
            ("Backups Directory", self.dir_backups),
            ("Imports Directory", self.dir_imports),
        ):
            r = QHBoxLayout()
            lb = QLabel(label)
            lb.setFixedWidth(140)
            r.addWidget(lb)
            r.addWidget(field, 1)
            dl.addLayout(r)
        save_dirs_btn = QPushButton("Save Directory Settings")
        save_dirs_btn.setObjectName("SuccessBtn")
        save_dirs_btn.clicked.connect(self._save_storage_dirs)
        dl.addWidget(save_dirs_btn, 0, Qt.AlignRight)
        hl.addWidget(dir_card)

        scroll.setWidget(host)
        lay.addWidget(scroll)

    def _refresh_settings(self):
        cfg = self.ctx["config"].data
        for g, inp in self.game_inputs.items():
            inp.setText(cfg.get("game_executables", {}).get(g) or cfg.get("game_paths", {}).get(g) or "")
        self.dir_seasons.setText(cfg.get("seasons_dir", ""))
        self.dir_backups.setText(cfg.get("backups_dir", ""))
        self.dir_imports.setText(cfg.get("imports_dir", ""))

    def _auto_detect_games(self):
        def complete(found):
            if not isinstance(found, dict):
                self._show_operation_error("Auto-Detect Failed", {"error": "Unexpected detection result."})
                return
            self._refresh_settings()
            QMessageBox.information(self, "Auto-Detect", f"Detected {len(found)} F1 25 installation(s).")

        self._start_operation(
            "Detecting F1 25 installation...",
            lambda _progress: self.ctx["game_detector"].detect_installed_games(),
            complete,
        )

    def _start_swofty_setup_reset(self):
        game_dir = self.ctx["config"].get_game_path("F1 25")
        check = self.ctx["game_detector"].validate_game_directory("F1 25", game_dir)
        if not check["valid"]:
            QMessageBox.warning(self, "F1 25 Required", check["reason"])
            return
        self.swofty_setup_status_lbl.setText("Checking Swofty setup status...")
        self._start_operation(
            "Checking Swofty setup...",
            lambda _progress: self.ctx["swofty_bridge"].setup_status(),
            lambda status: self._continue_swofty_setup_reset(status, Path(check["directory"])),
        )

    def _continue_swofty_setup_reset(self, status: dict, game_dir: Path):
        if not status["ok"]:
            QMessageBox.warning(self, "Swofty Unavailable", status.get("error", status.get("stderr", "Could not read Swofty setup status.")))
            return
        if status["done"]:
            QMessageBox.information(self, "Setup Complete", "Swofty setup is complete for profile preparation.")
            self.swofty_setup_status_lbl.setText(status.get("output", "Swofty setup is complete."))
            return
        if "verify the game files" in status.get("output", "").lower():
            QMessageBox.information(
                self,
                "Verify F1 25 Game Files",
                status["output"] + "\n\nAfter following that instruction, choose “Finish Setup.”",
            )
            return

        reset_prompt = QMessageBox.question(
            self,
            "Reset F1 25 for Swofty",
            "Swofty setup restores the game folder to its vanilla state and records a clean baseline. "
            "The game must be closed. Verify or repair the game files afterward if Swofty requests it. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reset_prompt != QMessageBox.Yes:
            return
        other_mods_prompt = QMessageBox.question(
            self,
            "Other Game-Folder Mods",
            "Does the F1 25 folder contain mods that were not installed by Swofty?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        self._start_swofty_setup_worker("reset", game_dir, other_mods_prompt == QMessageBox.Yes)

    def _finish_swofty_setup(self):
        self.swofty_setup_status_lbl.setText("Checking Swofty setup status...")
        self._start_operation(
            "Checking Swofty setup...",
            lambda _progress: self.ctx["swofty_bridge"].setup_status(),
            self._continue_swofty_setup_finish,
        )

    def _continue_swofty_setup_finish(self, status: dict):
        if not status["ok"]:
            QMessageBox.warning(self, "Swofty Unavailable", status.get("error", status.get("stderr", "Could not read Swofty setup status.")))
            return
        if status["done"]:
            QMessageBox.information(self, "Setup Complete", "Swofty setup is complete for profile preparation.")
            self.swofty_setup_status_lbl.setText(status.get("output", "Swofty setup is complete."))
            return
        if "verify the game files" not in status.get("output", "").lower():
            QMessageBox.warning(
                self,
                "Setup Reset Required",
                "Run the Swofty setup reset first. Swofty has not recorded a reset that is ready to finish.",
            )
            return
        verified = QMessageBox.question(
            self,
            "Confirm Game Verification",
            "Have you completed the Steam verification or EA app repair Swofty requested, or did Swofty say "
            "there was nothing to verify?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if verified == QMessageBox.Yes:
            self._start_swofty_setup_worker("done")

    def _start_swofty_setup_worker(self, action: str, game_dir: Path | None = None, other_mods: bool = False):
        self.swofty_setup_status_lbl.setText("Running Swofty setup...")
        def run(_progress):
            if action == "reset" and game_dir is not None:
                return self.ctx["swofty_bridge"].setup_reset(game_dir, other_mods)
            return self.ctx["swofty_bridge"].setup_done()

        self._start_operation("Running Swofty setup...", run, self._on_swofty_setup_finished)

    def _on_swofty_setup_finished(self, result: dict):
        output = (result.get("stdout") or result.get("stderr") or result.get("error") or "").strip()
        if result.get("ok"):
            self.swofty_setup_status_lbl.setText(output or "Swofty setup command completed.")
            QMessageBox.information(
                self,
                "Swofty Setup",
                output or "Swofty setup command completed.",
            )
        else:
            error = output or "Swofty setup command failed."
            self.swofty_setup_status_lbl.setText(error)
            QMessageBox.warning(self, "Swofty Setup Failed", error)

    def _browse_game_dir(self, game: str):
        path = QFileDialog.getExistingDirectory(self, f"Select {game} Installation Folder")
        if path:
            self.game_inputs[game].setText(path)
            self._save_game_path(game)

    def _browse_game_exe(self, game: str):
        path, _ = QFileDialog.getOpenFileName(self, f"Select {game} Executable", "", "Executables (*.exe);;All Files (*)")
        if path:
            self.game_inputs[game].setText(path)
            self._save_game_path(game)

    def _save_game_path(self, game: str):
        val = self.game_inputs[game].text().strip()
        check = self.ctx["game_detector"].validate_game_directory(game, val)
        if check["valid"]:
            self.ctx["config"].set_game_path(game, check["directory"])
            if check.get("executable"):
                self.ctx["config"].set_game_executable(game, check["executable"])
            QMessageBox.information(self, "Validated", f"{game} verified:\n{check['executable']}")
            self.refresh_all()
        else:
            QMessageBox.warning(self, "Invalid Path", check["reason"])

    def _save_storage_dirs(self):
        cfg = self.ctx["config"]
        cfg.data["seasons_dir"] = self.dir_seasons.text().strip()
        cfg.data["backups_dir"] = self.dir_backups.text().strip()
        cfg.data["imports_dir"] = self.dir_imports.text().strip()
        cfg.save()
        cfg.ensure_directories()
        QMessageBox.information(self, "Settings Saved", "Storage directories updated.")
        self.refresh_all()

    # --- Logs ---
    def _init_logs_page(self):
        lay = QVBoxLayout(self.page_logs)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        lay.addWidget(self.logs_view, 1)
        ref_btn = QPushButton("Refresh Logs")
        ref_btn.clicked.connect(self._refresh_logs)
        lay.addWidget(ref_btn, 0, Qt.AlignRight)

    def _refresh_logs(self):
        entries = self.ctx["logger"].get_recent(250)
        self.logs_view.setPlainText("\n".join(f"[{e['timestamp']}] {e['level']}: {e['message']}" for e in entries))

    # --- Mod library ---
    def _init_mod_library_page(self):
        layout = QVBoxLayout(self.page_mod_library)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self.mod_library_search = QLineEdit()
        self.mod_library_search.setPlaceholderText("Search mods by name, category, author, or description...")
        self.mod_library_search.textChanged.connect(self._refresh_mod_library_page)
        self.mod_library_category = QComboBox()
        self.mod_library_category.addItem("All categories", "")
        self.mod_library_category.currentIndexChanged.connect(self._refresh_mod_library_page)
        import_archive = QPushButton("Inspect ZIP")
        import_archive.clicked.connect(self._browse_mod_library_archive)
        import_folder = QPushButton("Inspect Folder")
        import_folder.clicked.connect(self._browse_mod_library_folder)
        toolbar.addWidget(self.mod_library_search, 1)
        toolbar.addWidget(self.mod_library_category)
        toolbar.addWidget(import_archive)
        toolbar.addWidget(import_folder)
        layout.addLayout(toolbar)

        note = QLabel(
            "Imported files are copied into the local library. Compatibility is based on package metadata or visible "
            "structure only; profile selection does not apply mods to F1 25."
        )
        note.setWordWrap(True)
        note.setObjectName("MutedText")
        layout.addWidget(note)

        body = QHBoxLayout()
        self.mod_library_list = QListWidget()
        self.mod_library_list.currentItemChanged.connect(self._show_selected_library_mod)
        body.addWidget(self.mod_library_list, 1)

        details = QFrame()
        details.setObjectName("Card")
        details_layout = QVBoxLayout(details)
        self.mod_library_title = QLabel("Select a mod")
        self.mod_library_title.setObjectName("SectionTitle")
        self.mod_library_title.setWordWrap(True)
        details_layout.addWidget(self.mod_library_title)
        self.mod_library_metadata = QLabel("")
        self.mod_library_metadata.setWordWrap(True)
        details_layout.addWidget(self.mod_library_metadata)
        self.mod_library_description = QPlainTextEdit()
        self.mod_library_description.setReadOnly(True)
        details_layout.addWidget(self.mod_library_description, 1)
        self.mod_library_status = QLabel("")
        self.mod_library_status.setWordWrap(True)
        self.mod_library_status.setObjectName("MutedText")
        details_layout.addWidget(self.mod_library_status)
        self.remove_library_mod_button = QPushButton("Remove from Library")
        self.remove_library_mod_button.clicked.connect(self._remove_library_mod)
        details_layout.addWidget(self.remove_library_mod_button)
        body.addWidget(details, 1)
        layout.addLayout(body, 1)
        self._selected_library_mod_id: str | None = None

    def _refresh_mod_library_page(self, *_args):
        manager = self.ctx["mod_library"]
        selected_category = self.mod_library_category.currentData()
        mods = manager.list_mods(self.mod_library_search.text(), selected_category or "")
        categories = sorted({mod["category"] for mod in manager.list_mods()}, key=str.casefold)
        current_category = selected_category
        self.mod_library_category.blockSignals(True)
        self.mod_library_category.clear()
        self.mod_library_category.addItem("All categories", "")
        for category in categories:
            self.mod_library_category.addItem(category, category)
        category_index = self.mod_library_category.findData(current_category)
        self.mod_library_category.setCurrentIndex(max(0, category_index))
        self.mod_library_category.blockSignals(False)

        selected_id = self._selected_library_mod_id
        self.mod_library_list.blockSignals(True)
        self.mod_library_list.clear()
        selected_item = None
        for mod in mods:
            item = QListWidgetItem(
                f"{mod['name']}  ·  {mod['category']}  ·  {mod['compatibility']}"
            )
            item.setData(Qt.UserRole, mod["id"])
            self.mod_library_list.addItem(item)
            if mod["id"] == selected_id:
                selected_item = item
        self.mod_library_list.blockSignals(False)
        if selected_item:
            self.mod_library_list.setCurrentItem(selected_item)
        elif self.mod_library_list.count():
            self.mod_library_list.setCurrentRow(0)
        else:
            self._selected_library_mod_id = None
            self._show_selected_library_mod(None)

    def _show_selected_library_mod(self, current: QListWidgetItem | None, _previous=None):
        mod_id = current.data(Qt.UserRole) if current else None
        self._selected_library_mod_id = mod_id
        mod = next(
            (item for item in self.ctx["mod_library"].list_mods() if item["id"] == mod_id),
            None,
        )
        self.remove_library_mod_button.setEnabled(mod is not None)
        if mod is None:
            self.mod_library_title.setText("Select a mod")
            self.mod_library_metadata.clear()
            self.mod_library_description.clear()
            self.mod_library_status.clear()
            return
        profile_id = self.ctx["mod_library"].selected_profile_id()
        status = self.ctx["mod_library"].get_mod_status(mod_id, profile_id)
        self.mod_library_title.setText(mod["name"])
        self.mod_library_metadata.setText(
            f"Category: {mod['category']}\nVersion: {mod['version']}\nAuthor: {mod['author']}\n"
            f"Game: {mod['game'] or 'Not declared'}\nSeason: {mod['season'] or 'Not declared'}\n"
            f"Package: {mod['file_count']} files · {mod['source_type']}\n"
            f"Compatibility: {mod['compatibility']}"
        )
        self.mod_library_description.setPlainText(mod["description"] or "No description was provided.")
        self.mod_library_status.setText(
            f"Imported: Yes  |  Enabled in selected profile: {'Yes' if status['enabled'] else 'No'}  |  "
            "Applied: No  |  Verified: No"
        )

    def _browse_mod_library_archive(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Inspect F1 25 mod package",
            "",
            "ZIP packages (*.zip *.swoftyaddon);;All files (*)",
        )
        if path:
            self._inspect_mod_package(path)

    def _browse_mod_library_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Inspect F1 25 mod folder")
        if path:
            self._inspect_mod_package(path)

    def _inspect_mod_package(self, path: str):
        def complete(preview):
            if not isinstance(preview, dict) or not preview.get("ok"):
                self._show_operation_error(
                    "Package Inspection Failed",
                    preview if isinstance(preview, dict) else {"error": "Unexpected inspection result."},
                )
                return
            dialog = ModPackagePreviewDialog(preview, self)
            if dialog.exec() == QDialog.Accepted:
                self._import_mod_library_package(preview["source_path"])

        self._start_operation(
            "Inspecting mod package contents...",
            lambda _progress: self.ctx["mod_library"].inspect_package(path),
            complete,
        )

    def _import_mod_library_package(self, source_path: str):
        def complete(result):
            if result.get("ok"):
                mod = result["mod"]
                self.ctx["logger"].info(f"Added '{mod['name']}' to the mod library.")
                self._selected_library_mod_id = mod["id"]
                self._refresh_mod_library_page()
                QMessageBox.information(
                    self,
                    "Mod Imported",
                    f"{mod['name']} was copied to the library as {mod['compatibility']}.\n"
                    "It has not been applied or verified.",
                )
            else:
                self._show_operation_error("Mod Import Failed", result)

        self._start_operation(
            "Copying mod into the library...",
            lambda progress: self.ctx["mod_library"].import_package(source_path, progress_cb=progress),
            complete,
        )

    def _remove_library_mod(self):
        mod_id = self._selected_library_mod_id
        if not mod_id:
            return
        answer = QMessageBox.question(
            self,
            "Remove Mod",
            "Remove this imported copy from the library? The original source will not be changed.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Mod Removal Failed", result)
                return
            self._selected_library_mod_id = None
            self._refresh_mod_library_page()
            if result.get("warning"):
                self._show_operation_error("Mod Removed with Cleanup Warning", result)

        self._start_operation(
            "Removing mod from the library...",
            lambda _progress: self.ctx["mod_library"].remove_mod(mod_id),
            complete,
        )

    # --- Local mod profiles (configuration only; not applied to game files) ---
    def _init_profiles_page(self):
        layout = QVBoxLayout(self.page_profiles)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        note = QLabel(
            "These local library profiles are separate from Swofty's existing profiles and never change their contents. "
            "The 2010 and 2012 profiles are separate F1 25 season configurations. Custom profiles can be created, "
            "renamed, duplicated, and deleted. Enabled means selected in this profile only; Applied and Verified "
            "remain No until a supported workflow and actual in-game test establish them."
        )
        note.setWordWrap(True)
        note.setObjectName("MutedText")
        layout.addWidget(note)

        profile_row = QHBoxLayout()
        self.managed_profile_combo = QComboBox()
        self.managed_profile_combo.currentIndexChanged.connect(self._on_managed_profile_changed)
        profile_row.addWidget(QLabel("Profile:"))
        profile_row.addWidget(self.managed_profile_combo, 1)
        self.create_profile_button = QPushButton("Create")
        self.create_profile_button.clicked.connect(self._create_custom_profile)
        self.rename_profile_button = QPushButton("Rename")
        self.rename_profile_button.clicked.connect(self._rename_custom_profile)
        self.duplicate_profile_button = QPushButton("Duplicate")
        self.duplicate_profile_button.clicked.connect(self._duplicate_profile)
        self.delete_profile_button = QPushButton("Delete")
        self.delete_profile_button.clicked.connect(self._delete_custom_profile)
        for button in (
            self.create_profile_button,
            self.rename_profile_button,
            self.duplicate_profile_button,
            self.delete_profile_button,
        ):
            profile_row.addWidget(button)
        layout.addLayout(profile_row)

        self.profile_contents_list = QListWidget()
        self.profile_contents_list.currentItemChanged.connect(self._update_profile_action_buttons)
        layout.addWidget(self.profile_contents_list, 1)

        actions = QHBoxLayout()
        self.add_profile_mod_button = QPushButton("Add to Profile")
        self.add_profile_mod_button.clicked.connect(self._add_selected_mod_to_profile)
        self.remove_profile_mod_button = QPushButton("Remove from Profile")
        self.remove_profile_mod_button.clicked.connect(self._remove_selected_mod_from_profile)
        self.enable_profile_mod_button = QPushButton("Enable")
        self.enable_profile_mod_button.clicked.connect(lambda: self._set_selected_profile_mod_enabled(True))
        self.disable_profile_mod_button = QPushButton("Disable")
        self.disable_profile_mod_button.clicked.connect(lambda: self._set_selected_profile_mod_enabled(False))
        self.check_profile_conflicts_button = QPushButton("Check Conflicts")
        self.check_profile_conflicts_button.clicked.connect(self._check_local_profile_conflicts)
        for button in (
            self.add_profile_mod_button,
            self.remove_profile_mod_button,
            self.enable_profile_mod_button,
            self.disable_profile_mod_button,
            self.check_profile_conflicts_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.profile_conflict_status = QLabel("")
        self.profile_conflict_status.setWordWrap(True)
        self.profile_conflict_status.setObjectName("MutedText")
        layout.addWidget(self.profile_conflict_status)
        self._refresh_profiles_page()

    def _current_managed_profile_id(self) -> str:
        return str(self.managed_profile_combo.currentData() or "")

    def _refresh_profiles_page(self, *_args):
        manager = self.ctx["mod_library"]
        selected = manager.selected_profile_id()
        self.managed_profile_combo.blockSignals(True)
        self.managed_profile_combo.clear()
        for profile in manager.list_profiles():
            label = profile["name"] if profile["kind"] == "custom" else f"{profile['name']} (Library only)"
            self.managed_profile_combo.addItem(label, profile["id"])
        index = self.managed_profile_combo.findData(selected)
        self.managed_profile_combo.setCurrentIndex(max(0, index))
        self.managed_profile_combo.blockSignals(False)
        profile_id = self._current_managed_profile_id()
        profile = manager.get_profile(profile_id)
        is_custom = profile.get("kind") == "custom"
        self.rename_profile_button.setEnabled(is_custom)
        self.delete_profile_button.setEnabled(is_custom)

        memberships = profile.get("mods", {})
        self.profile_contents_list.blockSignals(True)
        self.profile_contents_list.clear()
        selected_item = None
        for mod in manager.list_mods():
            if mod["id"] not in memberships:
                state = "Imported · Not in profile · Applied: No · Verified: No"
                enabled = None
            else:
                enabled = bool(memberships[mod["id"]])
                state = f"Imported · {'Enabled' if enabled else 'Disabled'} · Applied: No · Verified: No"
            item = QListWidgetItem(f"{mod['name']}  ·  {mod['category']}  ·  {state}")
            item.setData(Qt.UserRole, {"id": mod["id"], "member": enabled is not None, "enabled": enabled})
            self.profile_contents_list.addItem(item)
            if mod["id"] == getattr(self, "_selected_profile_mod_id", None):
                selected_item = item
        self.profile_contents_list.blockSignals(False)
        if selected_item:
            self.profile_contents_list.setCurrentItem(selected_item)
        elif self.profile_contents_list.count():
            self.profile_contents_list.setCurrentRow(0)
        else:
            self._update_profile_action_buttons(None)
        self._update_profile_action_buttons(self.profile_contents_list.currentItem())

    def _update_profile_action_buttons(self, item, _previous=None):
        data = item.data(Qt.UserRole) if item else None
        member = bool(data and data.get("member"))
        enabled = data.get("enabled") if data else None
        self.add_profile_mod_button.setEnabled(bool(data) and not member)
        self.remove_profile_mod_button.setEnabled(member)
        self.enable_profile_mod_button.setEnabled(member and enabled is False)
        self.disable_profile_mod_button.setEnabled(member and enabled is True)
        self._selected_profile_mod_id = data.get("id") if data else None

    def _on_managed_profile_changed(self, _index: int):
        profile_id = self._current_managed_profile_id()
        if not profile_id or profile_id == self.ctx["mod_library"].selected_profile_id():
            return

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Profile Switch Failed", result)
            self._refresh_profiles_page()
            self._show_selected_library_mod(self.mod_library_list.currentItem())

        self._start_operation(
            "Switching mod profile...",
            lambda _progress: self._select_profile(profile_id),
            complete,
        )

    def _select_profile(self, profile_id: str) -> dict:
        self.ctx["mod_library"].select_profile(profile_id)
        return {"ok": True}

    def _create_custom_profile(self):
        name, accepted = QInputDialog.getText(self, "Create Profile", "Profile name:")
        if not accepted:
            return

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Profile Creation Failed", result)
                return
            self._refresh_profiles_page()

        self._start_operation(
            "Creating custom profile...",
            lambda _progress: self._create_and_select_profile(name),
            complete,
        )

    def _create_and_select_profile(self, name: str) -> dict:
        profile = self.ctx["mod_library"].create_profile(name)
        self.ctx["mod_library"].select_profile(profile["id"])
        return {"ok": True, "profile": profile}

    def _rename_custom_profile(self):
        profile_id = self._current_managed_profile_id()
        profile = self.ctx["mod_library"].get_profile(profile_id)
        name, accepted = QInputDialog.getText(
            self,
            "Rename Profile",
            "Profile name:",
            text=profile["name"],
        )
        if not accepted:
            return

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Profile Rename Failed", result)
            self._refresh_profiles_page()

        self._start_operation(
            "Renaming custom profile...",
            lambda _progress: self._rename_profile(profile_id, name),
            complete,
        )

    def _rename_profile(self, profile_id: str, name: str) -> dict:
        self.ctx["mod_library"].rename_profile(profile_id, name)
        return {"ok": True}

    def _duplicate_profile(self):
        profile_id = self._current_managed_profile_id()
        profile = self.ctx["mod_library"].get_profile(profile_id)
        name, accepted = QInputDialog.getText(
            self,
            "Duplicate Profile",
            "New profile name:",
            text=f"{profile['name']} Copy",
        )
        if not accepted:
            return

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Profile Duplication Failed", result)
                return
            self._refresh_profiles_page()

        self._start_operation(
            "Duplicating profile...",
            lambda _progress: self._duplicate_and_select_profile(profile_id, name),
            complete,
        )

    def _duplicate_and_select_profile(self, profile_id: str, name: str) -> dict:
        profile = self.ctx["mod_library"].duplicate_profile(profile_id, name)
        self.ctx["mod_library"].select_profile(profile["id"])
        return {"ok": True, "profile": profile}

    def _delete_custom_profile(self):
        profile_id = self._current_managed_profile_id()
        profile = self.ctx["mod_library"].get_profile(profile_id)
        if profile["kind"] != "custom":
            return
        answer = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete custom profile '{profile['name']}'? Imported library packages will not be deleted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Profile Deletion Failed", result)
            self._refresh_profiles_page()

        self._start_operation(
            "Deleting custom profile...",
            lambda _progress: self._delete_profile(profile_id),
            complete,
        )

    def _delete_profile(self, profile_id: str) -> dict:
        self.ctx["mod_library"].delete_profile(profile_id)
        return {"ok": True}

    def _run_profile_mod_operation(self, name: str, operation: Callable):
        def complete(result):
            if not result.get("ok"):
                self._show_operation_error("Profile Mod Update Failed", result)
            else:
                self.profile_conflict_status.clear()
            self._refresh_profiles_page()
            self._show_selected_library_mod(self.mod_library_list.currentItem())

        self._start_operation(name, lambda _progress: self._perform_profile_update(operation), complete)

    @staticmethod
    def _perform_profile_update(operation: Callable) -> dict:
        operation()
        return {"ok": True}

    def _add_selected_mod_to_profile(self):
        mod_id = getattr(self, "_selected_profile_mod_id", None)
        if mod_id:
            profile_id = self._current_managed_profile_id()
            self._run_profile_mod_operation(
                "Adding mod to profile...",
                lambda: self.ctx["mod_library"].add_mod_to_profile(profile_id, mod_id, True),
            )

    def _remove_selected_mod_from_profile(self):
        mod_id = getattr(self, "_selected_profile_mod_id", None)
        if mod_id:
            profile_id = self._current_managed_profile_id()
            self._run_profile_mod_operation(
                "Removing mod from profile...",
                lambda: self.ctx["mod_library"].remove_mod_from_profile(profile_id, mod_id),
            )

    def _set_selected_profile_mod_enabled(self, enabled: bool):
        mod_id = getattr(self, "_selected_profile_mod_id", None)
        if mod_id:
            profile_id = self._current_managed_profile_id()
            self._run_profile_mod_operation(
                f"{'Enabling' if enabled else 'Disabling'} mod in profile...",
                lambda: self.ctx["mod_library"].set_mod_enabled(profile_id, mod_id, enabled),
            )

    def _check_local_profile_conflicts(self):
        profile_id = self._current_managed_profile_id()

        def complete(conflicts):
            if not isinstance(conflicts, list):
                self._show_operation_error("Conflict Check Failed", conflicts)
                return
            self.profile_conflict_status.setText(
                "\n".join(conflict["message"] for conflict in conflicts)
                if conflicts
                else "No package-metadata or package-relative path conflicts detected. This does not verify game-level compatibility."
            )

        self._start_operation(
            "Checking profile conflicts...",
            lambda _progress: self.ctx["mod_library"].detect_conflicts(profile_id),
            complete,
        )

    # --- Actions ---
    def _install_season(self, season_id: str):
        self.dash_progress.setValue(0)
        self.dash_progress_lbl.setText("Starting season activation...")
        self._start_operation(
            "Activating F1 25 season...",
            lambda progress: self.ctx["mod_installer"].install_season(season_id, progress_cb=progress),
            self._on_install_finished,
        )

    def _on_install_finished(self, res: dict):
        if res.get("ok"):
            self.dash_progress.setValue(100)
            self.dash_progress_lbl.setText(f"Swofty profile ready ({res.get('addon_count', 0)} addons)")
            conflict_text = res.get("conflicts") or "No conflicts reported."
            QMessageBox.information(
                self,
                "Swofty Profile Ready",
                f"Season {res.get('season')} is active with {res.get('addon_count', 0)} addon(s).\n\n"
                f"Swofty conflict check:\n{conflict_text}",
            )
        else:
            self.dash_progress.setValue(0)
            self.dash_progress_lbl.setText(res.get("error", "Install failed"))
            QMessageBox.warning(self, "Install Failed", res.get("error", "Unknown error"))
        self.refresh_all()

    def _uninstall_season(self, season_id: str):
        def complete(result):
            if result.get("ok"):
                QMessageBox.information(
                    self,
                    "Season Deactivated",
                    f"Disabled {result.get('disabled_addons', 0)} Swofty addon(s). "
                    "The imported files remain safely stored in Swofty.",
                )
                self.refresh_all()
            else:
                self._show_operation_error("Deactivate Failed", result)

        self._start_operation(
            "Deactivating F1 25 season...",
            lambda _progress: self.ctx["mod_installer"].uninstall_season(season_id),
            complete,
        )

    def _remove_season(self, season_id: str):
        def complete(result):
            if result.get("ok"):
                self.selected_season_id = None
                self.refresh_all()
                self.switch_tab(1)
            else:
                self._show_operation_error("Remove Failed", result)

        self._start_operation(
            "Removing imported package...",
            lambda _progress: self.ctx["season_manager"].remove_season(season_id),
            complete,
        )

    def _launch_specific_game(self, game: str):
        def complete(result):
            if result.get("ok"):
                self.statusBar().showMessage(
                    "Swofty confirmed F1 25 started through the official EA app / EA AntiCheat path.",
                    12000,
                )
                self._refresh_logs()
            else:
                self._show_operation_error("F1 25 Launch Failed", result)

        self._start_operation(
            "Starting F1 25 through EA AntiCheat...",
            lambda _progress: self.ctx["game_detector"].launch_game(game),
            complete,
        )

    def refresh_all(self):
        def_game = self.ctx["config"].data.get("default_game", "F1 25")
        self.sidebar_game_combo.blockSignals(True)
        self.sidebar_game_combo.setCurrentText(def_game)
        self.sidebar_game_combo.blockSignals(False)

        self._refresh_dashboard()
        self._refresh_library()
        self._refresh_settings()
        self._refresh_logs()
        if self.selected_season_id:
            self._open_season_details(self.selected_season_id)

        def load_packages(progress):
            progress(2, "Detecting F1 25 installation...")
            detected = self.ctx["game_detector"].detect_installed_games()
            progress(5, "Scanning imported F1 25 packages...")
            season_manager = self.ctx["season_manager"]
            entries = season_manager.scan_seasons()
            progress(65, "Checking package file conflicts...")
            conflicts = ConflictDetector.detect_conflicts(entries)
            progress(90, "Checking Swofty installation...")
            swofty_status = self.ctx["swofty_bridge"].get_status()
            return {
                "ok": True,
                "entries": entries,
                "conflicts": conflicts,
                "invalid": season_manager.invalid_packages,
                "swofty": swofty_status,
                "detected": detected,
            }

        def complete(result):
            if not isinstance(result, dict) or not result.get("ok"):
                self._show_operation_error("Library Refresh Failed", result)
                return
            for cards, layout in (
                (self._dashboard_cards, self.dash_grid),
                (self._library_cards, self.lib_grid),
            ):
                for card in cards.values():
                    layout.removeWidget(card)
                    card.deleteLater()
                cards.clear()
            self._season_entries = result["entries"]
            self._season_conflicts = result["conflicts"]
            self._invalid_packages = result["invalid"]
            self._refresh_external_launch_profiles()
            swofty = result["swofty"]
            self.swofty_status_lbl.setText(
                f"Swofty Core: Ready ({len(swofty.get('native_dlls', []))} DLLs)"
                if swofty.get("available")
                else "Swofty Core: Not bundled"
            )
            self._refresh_dashboard()
            self._refresh_library()
            self._refresh_settings()
            if self.selected_season_id:
                self._open_season_details(self.selected_season_id)

        self._start_operation("Refreshing mod library...", load_packages, complete)

    def closeEvent(self, event):
        if self.operation_worker and self.operation_worker.isRunning():
            message = (
                f"{self.operation_name} is still running. Wait for it to finish before closing "
                "so the package or game files are not left in a partial state."
            )
            self.statusBar().showMessage(message)
            self.ctx["logger"].warning(message)
            event.ignore()
            return
        self.external_game_timer.stop()
        event.accept()


def launch_desktop_app(ctx: dict) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(DARK_F1_QSS)
    win = F1ClassicLauncherWindow(ctx)
    win.show()
    return app.exec()
