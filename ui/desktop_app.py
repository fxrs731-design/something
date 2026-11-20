import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
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


class InstallWorker(QThread):
    progress = Signal(int, str)
    finished_result = Signal(dict)

    def __init__(self, mod_installer, season_id: str):
        super().__init__()
        self.mod_installer = mod_installer
        self.season_id = season_id

    def run(self):
        res = self.mod_installer.install_season(
            self.season_id,
            progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
        )
        self.finished_result.emit(res)


class SwoftySetupWorker(QThread):
    finished_result = Signal(dict)

    def __init__(self, bridge, action: str, game_dir: Path | None = None, other_mods: bool = False):
        super().__init__()
        self.bridge = bridge
        self.action = action
        self.game_dir = game_dir
        self.other_mods = other_mods

    def run(self):
        if self.action == "reset" and self.game_dir is not None:
            result = self.bridge.setup_reset(self.game_dir, self.other_mods)
        else:
            result = self.bridge.setup_done()
        self.finished_result.emit(result)


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


class F1ClassicLauncherWindow(QMainWindow):
    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx
        self.selected_season_id: str | None = None
        self.worker: InstallWorker | None = None
        self.setup_worker: SwoftySetupWorker | None = None

        self.setWindowTitle("F1 Classic Launcher")
        self.resize(1240, 800)
        self.setMinimumSize(960, 640)

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

        launch_btn = QPushButton("Launch Selected Game")
        launch_btn.setObjectName("PrimaryBtn")
        launch_btn.clicked.connect(self._launch_active_game)
        sb_lay.addWidget(launch_btn)

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

        # Build pages: 0=Dashboard, 1=Library, 2=Import, 3=Settings, 4=Logs, 5=Details
        self.page_dashboard = QWidget()
        self.page_library = QWidget()
        self.page_import = QWidget()
        self.page_settings = QWidget()
        self.page_logs = QWidget()
        self.page_details = QWidget()

        self._init_dashboard_page()
        self._init_library_page()
        self._init_import_page()
        self._init_settings_page()
        self._init_logs_page()
        self._init_details_page()

        for p in (
            self.page_dashboard,
            self.page_library,
            self.page_import,
            self.page_settings,
            self.page_logs,
            self.page_details,
        ):
            self.stack.addWidget(p)

        root_lay.addWidget(main_area, 1)
        self.switch_tab(0)
        self.refresh_all()

    def switch_tab(self, idx: int):
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
        }
        self.page_title.setText(titles.get(idx, "F1 Classic Launcher"))
        self.stack.setCurrentIndex(idx)
        self.refresh_all()

    def _on_default_game_changed(self, game_name: str):
        self.ctx["config"].data["default_game"] = game_name
        self.ctx["config"].save()
        self._refresh_dashboard()

    def _launch_active_game(self):
        game = self.ctx["config"].data.get("default_game", "F1 25")
        res = self.ctx["game_detector"].launch_game(game)
        if res.get("ok"):
            QMessageBox.information(
                self,
                "F1 25 Launch Requested",
                f"Started {res.get('profile', 'Swofty-managed')} Swofty process (PID {res['pid']}).\n\n"
                "The profile preflight passed. Watch the Swofty session console for confirmation that "
                "mod files were applied and the game started.",
            )
        else:
            QMessageBox.warning(self, "Launch Failed", res.get("error", "Unknown error"))
        self._refresh_logs()

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

    def _refresh_dashboard(self):
        while self.dash_featured_lay.count():
            w = self.dash_featured_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        while self.dash_grid.count():
            w = self.dash_grid.takeAt(0).widget()
            if w:
                w.deleteLater()

        seasons = self.ctx["season_manager"].query_seasons(sort_by="recent")
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

        for i, entry in enumerate(seasons[:6]):
            self.dash_grid.addWidget(self._make_season_card(entry), i // 3, i % 3)

    # --- Library ---
    def _init_library_page(self):
        lay = QVBoxLayout(self.page_library)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        self.lib_search = QLineEdit()
        self.lib_search.setPlaceholderText("Search seasons, authors, games...")
        self.lib_search.textChanged.connect(self._refresh_library)
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
        all_seasons = self.ctx["season_manager"].scan_seasons()
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
        )

        conflicts = ConflictDetector.detect_conflicts(seasons)
        invalids = self.ctx["season_manager"].invalid_packages
        notices = []
        if conflicts:
            notices.append(f"{len(conflicts)} overlapping file conflict(s) detected across imported packages.")
        if invalids:
            notices.append(f"{len(invalids)} invalid package folder(s) in seasons directory.")
        if notices:
            self.lib_banner.setText(" | ".join(notices))
            self.lib_banner.show()
        else:
            self.lib_banner.hide()

        while self.lib_grid.count():
            w = self.lib_grid.takeAt(0).widget()
            if w:
                w.deleteLater()

        for i, entry in enumerate(seasons):
            self.lib_grid.addWidget(self._make_season_card(entry), i // 3, i % 3)

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
        entry = self.ctx["season_manager"].get_season(season_id)
        if not entry:
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
        active_entry = self.ctx["season_manager"].get_season(active_id) if active_id else None
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
        result = self.ctx["mod_installer"].reorder_season_addons(season_id, addon_ids)
        if result.get("ok"):
            QMessageBox.information(
                self,
                "Load Order Applied",
                result.get("conflicts") or "Swofty rebuilt the profile with the requested addon order.",
            )
        else:
            QMessageBox.warning(self, "Load Order Failed", result.get("error", "Could not apply the requested addon order."))
            self._open_season_details(season_id)

    # --- Import ---
    def _init_import_page(self):
        lay = QVBoxLayout(self.page_import)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        title = QLabel("Import a Swofty Addon or F1 25 Mod")
        title.setObjectName("SectionTitle")
        cl.addWidget(title)
        instructions = QLabel(
            "F1 25 folder mods are imported by the official Simplified Importer and become Swofty addons. "
            "Nested F1 25 game folders are supported; executable and plugin files are excluded by that importer."
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

        lay.addWidget(card)
        lay.addStretch(1)

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
        season_year = self.import_season_combo.currentData()
        res = self.ctx["package_importer"].import_package(
            path_str,
            allow_update=allow_update,
            season_year=season_year,
        )
        if res.get("ok"):
            self.import_status.setText(f"Imported: {res.get('name')} ({res.get('game')})")
            QMessageBox.information(
                self,
                "Import Complete",
                f"Successfully {res.get('action')}: {res.get('name')}\n"
                "Activate it from the season page to convert/apply it through Swofty.",
            )
            self.refresh_all()
        elif res.get("duplicate"):
            reply = QMessageBox.question(
                self,
                "Duplicate Season ID",
                "\n".join(res.get("errors", [])) + "\n\nUpdate existing package?",
            )
            if reply == QMessageBox.Yes:
                self._run_import(path_str, allow_update=True)
        else:
            err_msg = "\n".join(res.get("errors", ["Unknown import error"]))
            self.import_status.setText(f"Error: {err_msg}")
            QMessageBox.warning(self, "Invalid Package", err_msg)

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
            "Before the first modded session, Swofty must restore/record the F1 25 game folder. Verify or repair "
            "the game through Steam or the EA app if Swofty requests it. Modded sessions run offline without "
            "EA AntiCheat; vanilla launches do not use this modded path."
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
        found = self.ctx["game_detector"].detect_installed_games()
        self._refresh_settings()
        QMessageBox.information(self, "Auto-Detect", f"Detected {len(found)} game installation(s).")

    def _start_swofty_setup_reset(self):
        if self.setup_worker and self.setup_worker.isRunning():
            return
        game_dir = self.ctx["config"].get_game_path("F1 25")
        check = self.ctx["game_detector"].validate_game_directory("F1 25", game_dir)
        if not check["valid"]:
            QMessageBox.warning(self, "F1 25 Required", check["reason"])
            return

        status = self.ctx["swofty_bridge"].setup_status()
        if not status["ok"]:
            QMessageBox.warning(self, "Swofty Unavailable", status.get("error", status.get("stderr", "Could not read Swofty setup status.")))
            return
        if status["done"]:
            QMessageBox.information(self, "Setup Complete", "Swofty setup is already complete for modded play.")
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
        self._start_swofty_setup_worker("reset", Path(check["directory"]), other_mods_prompt == QMessageBox.Yes)

    def _finish_swofty_setup(self):
        if self.setup_worker and self.setup_worker.isRunning():
            return
        status = self.ctx["swofty_bridge"].setup_status()
        if not status["ok"]:
            QMessageBox.warning(self, "Swofty Unavailable", status.get("error", status.get("stderr", "Could not read Swofty setup status.")))
            return
        if status["done"]:
            QMessageBox.information(self, "Setup Complete", "Swofty setup is already complete for modded play.")
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
        self.swofty_setup_reset_btn.setEnabled(False)
        self.swofty_setup_done_btn.setEnabled(False)
        self.swofty_setup_status_lbl.setText("Running Swofty setup...")
        self.setup_worker = SwoftySetupWorker(
            self.ctx["swofty_bridge"],
            action,
            game_dir,
            other_mods,
        )
        self.setup_worker.finished_result.connect(self._on_swofty_setup_finished)
        self.setup_worker.start()

    def _on_swofty_setup_finished(self, result: dict):
        self.swofty_setup_reset_btn.setEnabled(True)
        self.swofty_setup_done_btn.setEnabled(True)
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

    # --- Actions ---
    def _install_season(self, season_id: str):
        if self.worker and self.worker.isRunning():
            return
        self.dash_progress.setValue(5)
        self.dash_progress_lbl.setText("Starting installation...")
        self.worker = InstallWorker(self.ctx["mod_installer"], season_id)
        self.worker.progress.connect(self._on_install_progress)
        self.worker.finished_result.connect(self._on_install_finished)
        self.worker.start()

    def _on_install_progress(self, pct: int, msg: str):
        self.dash_progress.setValue(pct)
        self.dash_progress_lbl.setText(msg)

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
        res = self.ctx["mod_installer"].uninstall_season(season_id)
        if res.get("ok"):
            QMessageBox.information(
                self,
                "Season Deactivated",
                f"Disabled {res.get('disabled_addons', 0)} Swofty addon(s). "
                "The imported files remain safely stored in Swofty and can be reactivated.",
            )
        else:
            QMessageBox.warning(self, "Uninstall Failed", res.get("error", "Unknown error"))
        self.refresh_all()

    def _remove_season(self, season_id: str):
        res = self.ctx["season_manager"].remove_season(season_id)
        if res.get("ok"):
            self.selected_season_id = None
            self.switch_tab(1)
        else:
            QMessageBox.warning(self, "Remove Failed", res.get("error", "Unknown error"))

    def _launch_specific_game(self, game: str):
        res = self.ctx["game_detector"].launch_game(game)
        if res.get("ok"):
            QMessageBox.information(
                self,
                "F1 25 Session Started",
                f"Started {res.get('profile', 'Swofty-managed')} session (PID {res['pid']}).",
            )
        else:
            QMessageBox.warning(self, "Launch Failed", res.get("error", "Unknown error"))

    def refresh_all(self):
        def_game = self.ctx["config"].data.get("default_game", "F1 25")
        self.sidebar_game_combo.blockSignals(True)
        self.sidebar_game_combo.setCurrentText(def_game)
        self.sidebar_game_combo.blockSignals(False)

        sw = self.ctx["swofty_bridge"].get_status()
        self.swofty_status_lbl.setText(
            f"Swofty Core: Ready ({len(sw.get('native_dlls', []))} DLLs)"
            if sw.get("available")
            else "Swofty Core: Not bundled"
        )

        self._refresh_dashboard()
        self._refresh_library()
        self._refresh_settings()
        self._refresh_logs()
        if self.selected_season_id:
            self._open_season_details(self.selected_season_id)


def launch_desktop_app(ctx: dict) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(DARK_F1_QSS)
    win = F1ClassicLauncherWindow(ctx)
    win.show()
    return app.exec()
