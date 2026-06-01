"""Qt main window and interaction workflow for MineMods Updater.

The UI coordinates scan/check/apply actions, keeps settings in sync with forms,
and exposes diagnostics/reporting features for safe updates.
"""

from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from .app_meta import APP_NAME
from .mod_scanner import autodetect_loader_and_minecraft, scan_mods
from .models import LocalMod, UpdateInfo
from .providers import ModrinthProvider
from .reporting import (
    build_after_snapshot,
    build_before_snapshot,
    build_full_report,
    export_report_csv,
    export_report_json,
    make_report_basename,
)
from .settings_store import (
    load_settings,
    save_settings,
)
from .update_service import CHANGELOG_CATEGORY_ORDER, apply_updates, build_changelog_text, check_updates, get_last_check_stats


class TaskThread(QtCore.QThread):
    """Run blocking operations in a worker thread and emit typed callbacks."""
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, fn: Callable[[], object], parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # pragma: no cover - defensive in thread context
            stack = traceback.format_exc(limit=4)
            self.failed.emit(f"{exc}\n{stack}")
            return
        self.completed.emit(result)


class MainWindow(QtWidgets.QMainWindow):
    """Primary desktop window that orchestrates the end-to-end update flow."""
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)

        self.settings = load_settings()
        self.modrinth = ModrinthProvider()

        self.local_mods: list[LocalMod] = []
        self.local_mods_by_path: dict[str, LocalMod] = {}
        self.update_infos: list[UpdateInfo] = []
        self.before_snapshot: dict | None = None
        self.last_report: dict | None = None
        self._selected_mod_page_url = ""

        self._threads: set[TaskThread] = set()
        self._busy = False
        self._loading_form = False

        self._build_ui()
        self._apply_theme()
        self._apply_startup_min_width()
        self._load_settings_to_form()
        self._refresh_minecraft_versions(background=True)

    def _apply_startup_min_width(self) -> None:
        """Start at the effective minimal width to avoid unnecessary horizontal slack."""
        min_hint = self.minimumSizeHint()
        min_width = int(min_hint.width())
        min_height = int(min_hint.height())

        if min_width > 0:
            self.setMinimumWidth(min_width)

        if min_height > 0:
            self.setMinimumHeight(min_height)

        target_width = max(min_width, self.width()) if min_width <= 0 else min_width
        target_height = max(self.height(), min_height)
        self.resize(target_width, target_height)

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        hero = QtWidgets.QFrame()
        hero.setObjectName("hero")
        hero_layout = QtWidgets.QHBoxLayout(hero)
        hero_layout.setContentsMargins(14, 10, 14, 10)
        hero_layout.setSpacing(14)

        title_block = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(APP_NAME)
        title.setObjectName("heroTitle")
        subtitle = QtWidgets.QLabel(
            "Scan rapide des mods, vérification provider, dry-run et rapport JSON/CSV"
        )
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        self.busy_label = QtWidgets.QLabel("Prêt")
        self.busy_label.setObjectName("busyBadge")
        self.busy_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        hero_layout.addLayout(title_block, 1)
        hero_layout.addWidget(self.busy_label)
        root_layout.addWidget(hero)

        controls = QtWidgets.QFrame()
        controls.setObjectName("controlsCard")
        controls_layout = QtWidgets.QGridLayout(controls)
        controls_layout.setContentsMargins(12, 10, 12, 10)
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setVerticalSpacing(8)

        self.mods_dir_input = QtWidgets.QLineEdit()
        self.mods_dir_input.setPlaceholderText("Dossier mods Minecraft")
        browse_button = QtWidgets.QPushButton("Parcourir")
        browse_button.clicked.connect(self._choose_mods_directory)

        self.mc_version_combo = QtWidgets.QComboBox()
        self.mc_version_combo.setEditable(True)
        self.mc_version_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        refresh_versions_button = QtWidgets.QPushButton("Versions MC")
        refresh_versions_button.clicked.connect(lambda: self._refresh_minecraft_versions(background=True))

        self.loader_combo = QtWidgets.QComboBox()
        self.loader_combo.addItem("Auto", "auto")
        self.loader_combo.addItem("Fabric", "fabric")
        self.loader_combo.addItem("Forge", "forge")
        self.loader_combo.addItem("Quilt", "quilt")
        self.loader_combo.addItem("NeoForge", "neoforge")

        self.modrinth_checkbox = QtWidgets.QCheckBox("Modrinth")
        self.modrinth_checkbox.setChecked(True)
        self.curseforge_checkbox = QtWidgets.QCheckBox("CurseForge")
        self.curseforge_checkbox.stateChanged.connect(self._on_curseforge_toggle)

        self.curseforge_api_key_input = QtWidgets.QLineEdit()
        self.curseforge_api_key_input.setPlaceholderText("API key CurseForge (optionnel)")
        self.curseforge_api_key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.strict_matching_checkbox = QtWidgets.QCheckBox("Matching strict (anti faux-positifs)")
        self.strict_matching_checkbox.setChecked(True)
        self.dry_run_checkbox = QtWidgets.QCheckBox("Dry-run (sans écriture)")
        self.dry_run_checkbox.setChecked(False)

        self.auto_detect_button = QtWidgets.QPushButton("Auto-détecter")
        self.auto_detect_button.clicked.connect(self._autodetect_context)
        self.scan_button = QtWidgets.QPushButton("Scanner")
        self.scan_button.clicked.connect(self._scan_mods)
        self.check_updates_button = QtWidgets.QPushButton("Vérifier")
        self.check_updates_button.clicked.connect(self._check_updates)

        self.update_selected_button = QtWidgets.QPushButton("Mettre à jour la sélection")
        self.update_selected_button.clicked.connect(self._update_selected_mods)
        self.update_all_button = QtWidgets.QPushButton("Tout mettre à jour (disponibles)")
        self.update_all_button.clicked.connect(self._update_all_mods)
        self.export_report_button = QtWidgets.QPushButton("Exporter rapport")
        self.export_report_button.clicked.connect(self._export_report)

        self.scan_button.setToolTip("Scanner les mods présents dans le dossier (auto-scan aussi après choix du dossier).")
        self.check_updates_button.setToolTip("Vérifier les mises à jour disponibles (check parallèle + cache court).")
        self.dry_run_checkbox.setToolTip("Simuler les actions sans modifier les fichiers .jar.")
        self.update_selected_button.setToolTip("Appliquer uniquement les mods cochés.")
        self.update_all_button.setToolTip(
            "Appliquer uniquement les mods marqués 'Mise à jour disponible'. "
            "Les mods en erreur/introuvables sont ignorés et les échecs n'arrêtent pas le lot."
        )
        self.export_report_button.setToolTip("Exporter un rapport JSON/CSV (avant ou après opération).")

        controls_layout.addWidget(QtWidgets.QLabel("Dossier mods"), 1, 0)
        controls_layout.addWidget(self.mods_dir_input, 1, 1, 1, 3)
        controls_layout.addWidget(browse_button, 1, 4)

        controls_layout.addWidget(QtWidgets.QLabel("Minecraft"), 2, 0)
        controls_layout.addWidget(self.mc_version_combo, 2, 1)
        controls_layout.addWidget(refresh_versions_button, 2, 2)
        controls_layout.addWidget(QtWidgets.QLabel("Loader"), 2, 3)
        controls_layout.addWidget(self.loader_combo, 2, 4)

        controls_layout.addWidget(QtWidgets.QLabel("Sources"), 3, 0)
        controls_layout.addWidget(self.modrinth_checkbox, 3, 1)
        controls_layout.addWidget(self.curseforge_checkbox, 3, 2)
        controls_layout.addWidget(self.curseforge_api_key_input, 3, 3, 1, 2)

        controls_layout.addWidget(self.strict_matching_checkbox, 4, 1, 1, 2)
        controls_layout.addWidget(self.dry_run_checkbox, 4, 3)
        controls_layout.addWidget(self.auto_detect_button, 4, 4)

        controls_layout.addWidget(self.scan_button, 5, 1)
        controls_layout.addWidget(self.check_updates_button, 5, 2)
        controls_layout.addWidget(self.update_selected_button, 5, 3)
        controls_layout.addWidget(self.update_all_button, 5, 4)

        self.show_logs_checkbox = QtWidgets.QCheckBox("Logs")
        self.show_logs_checkbox.setChecked(False)
        self.show_logs_checkbox.stateChanged.connect(self._on_show_logs_changed)

        controls_layout.addWidget(self.show_logs_checkbox, 6, 2)
        controls_layout.addWidget(self.export_report_button, 6, 4)

        root_layout.addWidget(controls)

        content_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        table_card = QtWidgets.QFrame()
        table_card.setObjectName("tableCard")
        table_layout = QtWidgets.QVBoxLayout(table_card)
        table_layout.setContentsMargins(10, 10, 10, 10)
        table_layout.setSpacing(8)

        table_filters = QtWidgets.QHBoxLayout()
        table_filters.setSpacing(6)

        self.mods_search_input = QtWidgets.QLineEdit()
        self.mods_search_input.setPlaceholderText("Rechercher un mod (nom, ID...)")
        self.mods_search_input.setMinimumWidth(200)
        self.mods_search_input.setClearButtonEnabled(True)
        self.mods_search_input.textChanged.connect(self._on_table_filter_changed)

        self.status_filter_combo = QtWidgets.QComboBox()
        self.status_filter_combo.addItem("Tous les états", "")
        self.status_filter_combo.addItem("Mises à jour", "update_available")
        self.status_filter_combo.addItem("À jour", "up_to_date")
        self.status_filter_combo.addItem("Introuvables", "not_found")
        self.status_filter_combo.addItem("Erreurs", "error")
        self.status_filter_combo.addItem("Scannés", "scanned")
        self.status_filter_combo.setMinimumWidth(132)
        self.status_filter_combo.currentIndexChanged.connect(self._on_table_filter_changed)

        self.source_filter_combo = QtWidgets.QComboBox()
        self.source_filter_combo.addItem("Toutes les sources", "")
        self.source_filter_combo.addItem("Modrinth", "modrinth")
        self.source_filter_combo.addItem("CurseForge", "curseforge")
        self.source_filter_combo.addItem("Autre", "other")
        self.source_filter_combo.setMinimumWidth(132)
        self.source_filter_combo.currentIndexChanged.connect(self._on_table_filter_changed)

        self.select_visible_button = QtWidgets.QPushButton("Cocher visibles")
        self.select_visible_button.clicked.connect(lambda: self._set_check_state_for_visible_rows(QtCore.Qt.CheckState.Checked))
        self.clear_visible_button = QtWidgets.QPushButton("Décocher visibles")
        self.clear_visible_button.clicked.connect(lambda: self._set_check_state_for_visible_rows(QtCore.Qt.CheckState.Unchecked))
        self.select_visible_button.setMaximumWidth(145)
        self.clear_visible_button.setMaximumWidth(155)

        self.visible_count_label = QtWidgets.QLabel("Visibles: 0/0")
        self.visible_count_label.setObjectName("visibleCount")

        table_filters.addWidget(QtWidgets.QLabel("Recherche:"))
        table_filters.addWidget(self.mods_search_input, 3)
        table_filters.addWidget(QtWidgets.QLabel("État:"))
        table_filters.addWidget(self.status_filter_combo)
        table_filters.addWidget(QtWidgets.QLabel("Source:"))
        table_filters.addWidget(self.source_filter_combo)
        table_filters.addWidget(self.select_visible_button)
        table_filters.addWidget(self.clear_visible_button)
        table_filters.addWidget(self.visible_count_label)
        table_filters.addStretch(1)

        table_layout.addLayout(table_filters)

        self.mods_table = QtWidgets.QTableWidget(0, 9)
        self.mods_table.setHorizontalHeaderLabels(
            [
                "MàJ",
                "Mod",
                "ID",
                "Version locale",
                "Dernière",
                "État",
                "Source",
                "Loader",
                "Fichier",
            ]
        )
        self.mods_table.verticalHeader().setVisible(False)
        self.mods_table.setAlternatingRowColors(True)
        self.mods_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.mods_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.mods_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mods_table.setSortingEnabled(True)
        self.mods_table.verticalHeader().setDefaultSectionSize(25)
        header = self.mods_table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(56)
        header.setStretchLastSection(False)
        self.mods_table.setColumnWidth(0, 56)
        self.mods_table.setColumnWidth(1, 260)
        self.mods_table.setColumnWidth(2, 170)
        self.mods_table.setColumnWidth(3, 120)
        self.mods_table.setColumnWidth(4, 120)
        self.mods_table.setColumnWidth(5, 148)
        self.mods_table.setColumnWidth(6, 108)
        self.mods_table.setColumnWidth(7, 82)
        self.mods_table.setColumnWidth(8, 220)
        self.mods_table.itemSelectionChanged.connect(self._on_table_selection_changed)

        table_layout.addWidget(self.mods_table)

        detail_card = QtWidgets.QFrame()
        detail_card.setObjectName("detailCard")
        detail_layout = QtWidgets.QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(10, 10, 10, 10)
        detail_layout.setSpacing(6)

        detail_title = QtWidgets.QLabel("Détails des versions")
        detail_title.setObjectName("panelTitle")
        self.open_mod_page_button = QtWidgets.QPushButton("Ouvrir page mod")
        self.open_mod_page_button.setEnabled(False)
        self.open_mod_page_button.clicked.connect(self._open_selected_mod_page)

        detail_header = QtWidgets.QHBoxLayout()
        detail_header.setSpacing(8)
        detail_header.addWidget(detail_title)
        detail_header.addStretch(1)
        detail_header.addWidget(self.open_mod_page_button)

        filters_row = QtWidgets.QHBoxLayout()
        filters_row.setSpacing(6)
        filters_row.addWidget(QtWidgets.QLabel("Filtres changelog:"))

        self.filter_breaking = QtWidgets.QCheckBox("breaking")
        self.filter_fix = QtWidgets.QCheckBox("fix")
        self.filter_performance = QtWidgets.QCheckBox("performance")
        self.filter_other = QtWidgets.QCheckBox("other")

        for checkbox in [self.filter_breaking, self.filter_fix, self.filter_performance, self.filter_other]:
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._on_changelog_filter_changed)
            filters_row.addWidget(checkbox)

        filters_row.addStretch(1)

        self.details_text = QtWidgets.QPlainTextEdit()
        self.details_text.setReadOnly(True)

        detail_layout.addLayout(detail_header)
        detail_layout.addLayout(filters_row)
        detail_layout.addWidget(self.details_text, 1)

        content_splitter.addWidget(table_card)
        content_splitter.addWidget(detail_card)
        content_splitter.setStretchFactor(0, 4)
        content_splitter.setStretchFactor(1, 2)

        root_layout.addWidget(content_splitter, 1)

        self.logs_card = QtWidgets.QFrame()
        self.logs_card.setObjectName("logsCard")
        logs_layout = QtWidgets.QVBoxLayout(self.logs_card)
        logs_layout.setContentsMargins(12, 12, 12, 12)

        logs_title = QtWidgets.QLabel("Logs")
        logs_title.setObjectName("panelTitle")
        self.logs_text = QtWidgets.QPlainTextEdit()
        self.logs_text.setReadOnly(True)
        self.logs_text.setMaximumHeight(110)

        logs_layout.addWidget(logs_title)
        logs_layout.addWidget(self.logs_text)

        root_layout.addWidget(self.logs_card)

        credit = QtWidgets.QLabel(
            'Développé par <a href="https://github.com/Zokkyen/minemods-updater">Zokkyen</a> © 2026'
        )
        credit.setObjectName("creditLabel")
        credit.setOpenExternalLinks(True)
        credit.setTextFormat(QtCore.Qt.TextFormat.RichText)
        credit.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        root_layout.addWidget(credit)

        self.logs_card.setVisible(False)

        self.setCentralWidget(root)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0d1320,
                    stop:0.5 #101f2f,
                    stop:1 #142639);
                color: #eef5ff;
                font-family: 'Bahnschrift', 'Segoe UI Variable', 'Segoe UI';
                font-size: 10.2pt;
            }
            QFrame#hero, QFrame#controlsCard, QFrame#tableCard, QFrame#detailCard, QFrame#logsCard {
                background: rgba(7, 15, 24, 0.82);
                border: 1px solid rgba(102, 213, 255, 0.22);
                border-radius: 12px;
            }
            QLabel#heroTitle {
                font-size: 21pt;
                font-weight: 700;
                color: #f4fbff;
            }
            QLabel#heroSubtitle {
                color: #9ec6db;
                font-size: 9.8pt;
            }
            QLabel#busyBadge {
                min-width: 138px;
                padding: 6px 12px;
                border-radius: 999px;
                background: #1d3144;
                color: #7be7ff;
                border: 1px solid #2d6d87;
                font-weight: 600;
            }
            QLabel#panelTitle {
                color: #90dcff;
                font-weight: 600;
                font-size: 10.5pt;
            }
            QLabel#creditLabel {
                color: #7fa6be;
                font-size: 9pt;
                padding-right: 4px;
            }
            QLabel#creditLabel a {
                color: #9fdfff;
                text-decoration: none;
            }
            QLabel {
                color: #dceffe;
            }
            QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
                background: rgba(11, 20, 32, 0.9);
                border: 1px solid rgba(111, 198, 233, 0.25);
                border-radius: 8px;
                color: #eff8ff;
                padding: 5px 7px;
                selection-background-color: #1b8fb9;
                selection-color: #f2fbff;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QPushButton {
                background: #2199ba;
                border: 1px solid #39c0de;
                border-radius: 8px;
                padding: 6px 10px;
                color: #f5fdff;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #26a8c9;
            }
            QPushButton:disabled {
                background: #224358;
                color: #89a7bb;
                border-color: #2f5871;
            }
            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #79b5cf;
                background: #0e2437;
            }
            QCheckBox::indicator:checked {
                background: #1fbad2;
            }
            QHeaderView::section {
                background: #17324a;
                color: #d8f4ff;
                border: none;
                padding: 6px;
                font-weight: 600;
            }
            QTableWidget {
                gridline-color: rgba(136, 195, 224, 0.13);
                alternate-background-color: rgba(22, 36, 52, 0.62);
            }
            """
        )

    def _set_changelog_filters(self, filters: list[str]) -> None:
        selected = set(filters)

        for checkbox in [self.filter_breaking, self.filter_fix, self.filter_performance, self.filter_other]:
            checkbox.blockSignals(True)
        try:
            self.filter_breaking.setChecked("breaking" in selected)
            self.filter_fix.setChecked("fix" in selected)
            self.filter_performance.setChecked("performance" in selected)
            self.filter_other.setChecked("other" in selected)
        finally:
            for checkbox in [self.filter_breaking, self.filter_fix, self.filter_performance, self.filter_other]:
                checkbox.blockSignals(False)

    def _selected_changelog_filters(self) -> list[str]:
        selected: list[str] = []
        if self.filter_breaking.isChecked():
            selected.append("breaking")
        if self.filter_fix.isChecked():
            selected.append("fix")
        if self.filter_performance.isChecked():
            selected.append("performance")
        if self.filter_other.isChecked():
            selected.append("other")
        return selected

    def _load_form_fields(self) -> None:
        self.mods_dir_input.setText(self.settings.mods_directory)
        self._set_combo_value(self.mc_version_combo, self.settings.minecraft_version)
        self._set_loader_combo_value(self.settings.loader)

        self.modrinth_checkbox.setChecked(self.settings.use_modrinth)
        self.curseforge_checkbox.setChecked(self.settings.use_curseforge)
        self.curseforge_api_key_input.setText(self.settings.curseforge_api_key)
        self.strict_matching_checkbox.setChecked(self.settings.strict_matching)
        self.dry_run_checkbox.setChecked(self.settings.dry_run)

        self._set_changelog_filters(self.settings.changelog_filters)
        self._on_curseforge_toggle()

    def _load_settings_to_form(self) -> None:
        self._loading_form = True
        try:
            self._load_form_fields()
        finally:
            self._loading_form = False

    def _write_form_to_settings_object(self) -> None:
        self.settings.mods_directory = self.mods_dir_input.text().strip()
        self.settings.minecraft_version = self.mc_version_combo.currentText().strip()
        self.settings.loader = self.current_loader()
        self.settings.use_modrinth = self.modrinth_checkbox.isChecked()
        self.settings.use_curseforge = self.curseforge_checkbox.isChecked()
        self.settings.curseforge_api_key = self.curseforge_api_key_input.text().strip()
        self.settings.strict_matching = self.strict_matching_checkbox.isChecked()
        self.settings.dry_run = self.dry_run_checkbox.isChecked()

        selected_filters = self._selected_changelog_filters()
        if not selected_filters:
            selected_filters = list(CHANGELOG_CATEGORY_ORDER)
        self.settings.changelog_filters = selected_filters

    def _save_form_settings(self):
        if self._loading_form:
            return self.settings

        self._write_form_to_settings_object()
        save_settings(self.settings)
        return self.settings

    def current_loader(self) -> str:
        value = self.loader_combo.currentData()
        return str(value or "auto")

    def _set_loader_combo_value(self, value: str) -> None:
        idx = self.loader_combo.findData(value)
        if idx >= 0:
            self.loader_combo.setCurrentIndex(idx)

    def _set_combo_value(self, combo: QtWidgets.QComboBox, value: str) -> None:
        if not value:
            return
        idx = combo.findText(value)
        if idx == -1:
            combo.insertItem(0, value)
            idx = combo.findText(value)
        combo.setCurrentIndex(idx)

    def _choose_mods_directory(self) -> None:
        current = self.mods_dir_input.text().strip() or str(Path.home())
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choisir un dossier mods", current)
        if folder:
            self.mods_dir_input.setText(folder)
            self._save_form_settings()
            # Automatically trigger a scan after selecting a mods folder.
            self._scan_mods()

    def _on_curseforge_toggle(self) -> None:
        enabled = self.curseforge_checkbox.isChecked()
        self.curseforge_api_key_input.setEnabled(enabled)

    def _on_changelog_filter_changed(self) -> None:
        if self._loading_form:
            return

        selected = self._selected_changelog_filters()
        if not selected:
            checkbox = self.sender()
            if isinstance(checkbox, QtWidgets.QCheckBox):
                checkbox.blockSignals(True)
                checkbox.setChecked(True)
                checkbox.blockSignals(False)

        self._save_form_settings()
        self._refresh_details_from_selection()

    def _on_table_filter_changed(self) -> None:
        self._apply_table_filters()

    def _apply_table_filters(self) -> None:
        query = self.mods_search_input.text().strip().lower()
        status_filter = str(self.status_filter_combo.currentData() or "")
        source_filter = str(self.source_filter_combo.currentData() or "")

        visible_row = -1
        visible_count = 0
        for row in range(self.mods_table.rowCount()):
            name_item = self.mods_table.item(row, 1)
            id_item = self.mods_table.item(row, 2)
            status_item = self.mods_table.item(row, 5)
            source_item = self.mods_table.item(row, 6)

            if not name_item or not id_item or not status_item or not source_item:
                self.mods_table.setRowHidden(row, True)
                continue

            status_key = str(status_item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            source_key = str(source_item.data(QtCore.Qt.ItemDataRole.UserRole) or "other")

            haystack = " ".join(
                [
                    name_item.text().lower(),
                    id_item.text().lower(),
                    str(name_item.data(QtCore.Qt.ItemDataRole.UserRole + 1) or "").lower(),
                ]
            )

            matches_query = not query or query in haystack
            matches_status = not status_filter or status_key == status_filter
            matches_source = not source_filter or source_key == source_filter

            hidden = not (matches_query and matches_status and matches_source)
            self.mods_table.setRowHidden(row, hidden)

            if not hidden and visible_row == -1:
                visible_row = row
            if not hidden:
                visible_count += 1

        if visible_row >= 0:
            selected_rows = self.mods_table.selectionModel().selectedRows()
            if not selected_rows or self.mods_table.isRowHidden(selected_rows[0].row()):
                self.mods_table.selectRow(visible_row)
        else:
            self.details_text.clear()
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)

        self.visible_count_label.setText(f"Visibles: {visible_count}/{self.mods_table.rowCount()}")

    def _on_show_logs_changed(self) -> None:
        self.logs_card.setVisible(self.show_logs_checkbox.isChecked())

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        self.check_updates_button.setEnabled(not busy)
        self.update_selected_button.setEnabled(not busy)
        self.update_all_button.setEnabled(not busy)
        self.auto_detect_button.setEnabled(not busy)
        self.export_report_button.setEnabled(not busy)
        self.select_visible_button.setEnabled(not busy)
        self.clear_visible_button.setEnabled(not busy)
        self.open_mod_page_button.setEnabled((not busy) and bool(self._selected_mod_page_url))

        self.dry_run_checkbox.setEnabled(not busy)
        self.show_logs_checkbox.setEnabled(not busy)

        self.busy_label.setText(label if busy and label else "Prêt")
        if busy:
            self.busy_label.setStyleSheet("background:#243645; color:#ffd27f; border:1px solid #9d6f24; border-radius:999px;")
        else:
            self.busy_label.setStyleSheet("")

    def _run_task(
        self,
        fn: Callable[[], object],
        on_success: Callable[[object], None],
        busy_label: str,
    ) -> None:
        """Execute long-running work in a thread while keeping the UI responsive."""
        if self._busy:
            self._log("Une opération est déjà en cours.")
            return

        self._set_busy(True, busy_label)

        thread = TaskThread(fn, self)
        self._threads.add(thread)

        def _finish_cleanup() -> None:
            # Always reset busy state and release worker reference, success or error.
            self._threads.discard(thread)
            self._set_busy(False)

        def _success(result: object) -> None:
            try:
                on_success(result)
            finally:
                _finish_cleanup()

        def _error(message: str) -> None:
            self._log(f"Erreur: {message}")
            QtWidgets.QMessageBox.critical(self, "Erreur", message.splitlines()[0])
            _finish_cleanup()

        thread.completed.connect(_success)
        thread.failed.connect(_error)
        thread.start()

    def _refresh_minecraft_versions(self, background: bool) -> None:
        def work() -> list[str]:
            return self.modrinth.fetch_minecraft_versions()

        def done(versions: object) -> None:
            if not isinstance(versions, list):
                return

            current = self.mc_version_combo.currentText().strip()
            self.mc_version_combo.blockSignals(True)
            self.mc_version_combo.clear()
            self.mc_version_combo.addItems([str(v) for v in versions])
            self.mc_version_combo.blockSignals(False)

            if current:
                self._set_combo_value(self.mc_version_combo, current)
            elif versions:
                self._set_combo_value(self.mc_version_combo, str(versions[0]))

            self._log(f"Liste des versions Minecraft chargée ({len(versions)} entrée(s)).")

        if background:
            self._run_task(work, done, "Chargement des versions Minecraft...")
        else:
            done(work())

    def _scan_mods(self) -> None:
        settings = self._save_form_settings()
        mods_dir = settings.mods_directory

        if not mods_dir:
            QtWidgets.QMessageBox.warning(self, "Dossier manquant", "Choisis un dossier mods avant le scan.")
            return

        def work() -> list[LocalMod]:
            return scan_mods(mods_dir)

        def done(mods: object) -> None:
            self.local_mods = list(mods) if isinstance(mods, list) else []
            self.update_infos = []
            self.before_snapshot = None
            self.last_report = None
            self._populate_table()
            self._log(f"Scan terminé: {len(self.local_mods)} mod(s) détecté(s).")

        self._run_task(work, done, "Scan des mods...")

    def _autodetect_context(self) -> None:
        if not self.local_mods:
            QtWidgets.QMessageBox.information(self, "Auto-détection", "Scanne d'abord le dossier mods.")
            return

        loader, minecraft = autodetect_loader_and_minecraft(self.local_mods)
        if loader and loader != "unknown":
            self._set_loader_combo_value(loader)
        if minecraft:
            self._set_combo_value(self.mc_version_combo, minecraft)

        self._save_form_settings()
        self._log(f"Auto-détection: loader={loader}, minecraft={minecraft or '-'}")

    def _check_updates(self) -> None:
        """Query enabled providers and build the pre-apply report snapshot."""
        if not self.local_mods:
            QtWidgets.QMessageBox.information(self, "Vérification", "Scanne les mods avant de vérifier les mises à jour.")
            return

        settings = self._save_form_settings()
        started_at = datetime.now()

        if not settings.use_modrinth and not settings.use_curseforge:
            QtWidgets.QMessageBox.warning(self, "Provider", "Active au moins un provider (Modrinth ou CurseForge).")
            return

        def work() -> list[UpdateInfo]:
            return check_updates(self.local_mods, settings)

        def done(result: object) -> None:
            self.update_infos = list(result) if isinstance(result, list) else []
            self._populate_table()
            self._save_form_settings()
            # Keep a full pre-operation snapshot for reproducible exports.
            self.before_snapshot = build_before_snapshot(self.local_mods, self.update_infos)
            self.last_report = build_full_report(self.settings, self.before_snapshot, None)

            available = len([u for u in self.update_infos if u.status == "update_available"])
            uptodate = len([u for u in self.update_infos if u.status == "up_to_date"])
            missing = len([u for u in self.update_infos if u.status == "not_found"])
            errors = len([u for u in self.update_infos if u.status == "error"])
            strict_state = "on" if self.settings.strict_matching else "off"
            elapsed = (datetime.now() - started_at).total_seconds()
            stats = get_last_check_stats()
            cache_hits = int(stats.get("cache_hits", 0))
            cache_misses = int(stats.get("cache_misses", 0))
            workers = int(stats.get("workers", 0))
            worker_label = "cache-only" if workers <= 0 else str(workers)
            self._log(
                f"Vérification terminée en {elapsed:.1f}s: {available} mise(s) à jour, {uptodate} à jour, {missing} introuvable(s), {errors} erreur(s), strict={strict_state}, cache hits={cache_hits}, misses={cache_misses}, workers={worker_label}."
            )

        self._run_task(work, done, "Vérification des mises à jour...")

    def _update_selected_mods(self) -> None:
        selected = self._collect_target_updates(only_checked=True)
        if not selected:
            QtWidgets.QMessageBox.information(self, "Sélection", "Aucun mod coché avec mise à jour disponible.")
            return
        self._run_updates(selected)

    def _update_all_mods(self) -> None:
        selected = self._collect_target_updates(only_checked=False)
        if not selected:
            QtWidgets.QMessageBox.information(self, "Mise à jour", "Aucune mise à jour disponible.")
            return

        ignored_up_to_date = len([info for info in self.update_infos if info.status == "up_to_date"])
        ignored_not_found = len([info for info in self.update_infos if info.status == "not_found"])
        ignored_errors = len([info for info in self.update_infos if info.status == "error"])
        self._log(
            "Tout mettre à jour: "
            f"{len(selected)} éligible(s), ignorés -> "
            f"{ignored_up_to_date} à jour, {ignored_not_found} introuvable(s), {ignored_errors} erreur(s)."
        )
        self._run_updates(selected)

    def _collect_target_updates(self, only_checked: bool) -> list[UpdateInfo]:
        if not only_checked:
            return [
                info
                for info in self.update_infos
                if info.status == "update_available" and info.latest is not None
            ]

        targets: list[UpdateInfo] = []
        by_path = {str(info.local_mod.path): info for info in self.update_infos}
        seen: set[str] = set()

        for row in range(self.mods_table.rowCount()):
            check_item = self.mods_table.item(row, 0)
            mod_item = self.mods_table.item(row, 1)
            if not check_item or not mod_item:
                continue

            if check_item.checkState() != QtCore.Qt.CheckState.Checked:
                continue

            mod_path = str(mod_item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            if not mod_path or mod_path in seen:
                continue

            info = by_path.get(mod_path)
            if not info or info.status != "update_available" or info.latest is None:
                continue

            seen.add(mod_path)
            targets.append(info)

        return targets

    def _set_check_state_for_visible_rows(self, state: QtCore.Qt.CheckState) -> None:
        changed = 0
        eligible = 0
        for row in range(self.mods_table.rowCount()):
            if self.mods_table.isRowHidden(row):
                continue

            check_item = self.mods_table.item(row, 0)
            if check_item is None:
                continue

            if not bool(check_item.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable):
                continue

            eligible += 1
            if check_item.checkState() != state:
                check_item.setCheckState(state)
                changed += 1

        action_label = "cochés" if state == QtCore.Qt.CheckState.Checked else "décochés"
        self._log(f"Sélection visible: {changed}/{eligible} mod(s) {action_label}.")

    def _run_updates(self, targets: list[UpdateInfo]) -> None:
        """Apply updates (or dry-run simulation) and persist a post-operation report."""
        names = "\n".join(f"- {item.local_mod.name}" for item in targets[:10])
        more = "" if len(targets) <= 10 else f"\n... +{len(targets) - 10} autre(s)"
        settings = self._save_form_settings()
        mode_text = "SIMULATION dry-run" if settings.dry_run else "application réelle"
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirmation",
            f"Lancer {len(targets)} mise(s) à jour en mode {mode_text} ?\n\n{names}{more}\n\n"
            "Les anciens .jar seront renommés en .old hors dry-run.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        def work() -> tuple[list, list, list[LocalMod]]:
            # Apply/simulate updates first, then re-scan to reflect resulting local state.
            applied, errors = apply_updates(targets, settings, dry_run=settings.dry_run)
            post_mods = scan_mods(settings.mods_directory) if settings.mods_directory else list(self.local_mods)
            return applied, errors, post_mods

        def done(result: object) -> None:
            if not isinstance(result, tuple) or len(result) != 3:
                self._log("Format de résultat inattendu pour update.")
                return

            applied, errors, post_mods = result
            applied_count = len(applied)
            error_count = len(errors)

            for item in applied:
                prefix = "SIMULATION" if item.simulated else "OK"
                self._log(f"{prefix}: {item.mod_name} {item.old_version} -> {item.new_version} via {item.provider}")
            for err in errors:
                self._log(f"ECHEC: {err}")

            self.local_mods = list(post_mods) if isinstance(post_mods, list) else []
            self.update_infos = []
            self._populate_table()

            before = self.before_snapshot or build_before_snapshot(self.local_mods, [])
            after = build_after_snapshot(self.local_mods, applied, errors, settings.dry_run)
            self.last_report = build_full_report(settings, before, after)

            QtWidgets.QMessageBox.information(
                self,
                "Opération terminée",
                f"Mode: {'dry-run' if settings.dry_run else 'normal'}\n"
                f"Mises à jour traitées: {applied_count}\n"
                f"Erreurs: {error_count}",
            )

        busy_text = "Simulation des mises à jour..." if settings.dry_run else "Téléchargement et remplacement des mods..."
        self._run_task(work, done, busy_text)

    def _populate_table(self) -> None:
        self.local_mods_by_path = {str(mod.path): mod for mod in self.local_mods}
        self._selected_mod_page_url = ""
        self.open_mod_page_button.setEnabled(False)
        self.mods_table.setSortingEnabled(False)
        self.mods_table.setRowCount(0)
        by_path = {info.local_mod.path: info for info in self.update_infos}
        status_labels = {
            "scanned": "Scanné",
            "update_available": "Mise à jour disponible",
            "up_to_date": "À jour",
            "not_found": "Introuvable",
            "error": "Erreur",
        }

        for row, local in enumerate(self.local_mods):
            info = by_path.get(local.path)

            status_key = "scanned"
            latest_text = "-"
            provider = "-"
            if info:
                status_key = info.status
                latest_text = info.latest.version_number if info.latest else "-"
                provider = info.provider or "-"

            source_key = provider.lower() if provider in {"Modrinth", "CurseForge"} else "other"

            status_text = status_labels.get(status_key, status_key)

            self.mods_table.insertRow(row)

            check_item = QtWidgets.QTableWidgetItem("")
            can_update = bool(info and info.status == "update_available" and info.latest)
            flags = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            if can_update:
                flags |= QtCore.Qt.ItemFlag.ItemIsUserCheckable
                check_item.setCheckState(QtCore.Qt.CheckState.Checked)
            else:
                check_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            check_item.setFlags(flags)

            check_item.setData(QtCore.Qt.ItemDataRole.UserRole, str(local.path))
            self.mods_table.setItem(row, 0, check_item)

            mod_item = QtWidgets.QTableWidgetItem(local.name)
            mod_item.setData(QtCore.Qt.ItemDataRole.UserRole, str(local.path))
            mod_item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, local.path.name)
            self.mods_table.setItem(row, 1, mod_item)

            self.mods_table.setItem(row, 2, QtWidgets.QTableWidgetItem(local.mod_id))
            self.mods_table.setItem(row, 3, QtWidgets.QTableWidgetItem(local.version))
            self.mods_table.setItem(row, 4, QtWidgets.QTableWidgetItem(latest_text))

            status_item = QtWidgets.QTableWidgetItem(status_text)
            status_item.setData(QtCore.Qt.ItemDataRole.UserRole, status_key)
            self.mods_table.setItem(row, 5, status_item)

            source_item = QtWidgets.QTableWidgetItem(provider)
            source_item.setData(QtCore.Qt.ItemDataRole.UserRole, source_key)
            self.mods_table.setItem(row, 6, source_item)

            self.mods_table.setItem(row, 7, QtWidgets.QTableWidgetItem(local.loader_hint))
            self.mods_table.setItem(row, 8, QtWidgets.QTableWidgetItem(local.path.name))

            self._colorize_status_row(row, status_key)

        if self.local_mods:
            self.mods_table.selectRow(0)
        else:
            self.details_text.setPlainText("Aucun mod détecté.")
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)

        self.mods_table.setSortingEnabled(True)
        self.mods_table.sortItems(1, QtCore.Qt.SortOrder.AscendingOrder)
        self._apply_table_filters()

    def _colorize_status_row(self, row: int, status: str) -> None:
        status_colors = {
            "update_available": QtGui.QColor("#1c6a4a"),
            "up_to_date": QtGui.QColor("#1f4160"),
            "not_found": QtGui.QColor("#5a4b21"),
            "error": QtGui.QColor("#6a2626"),
        }
        color = status_colors.get(status)
        if not color:
            return

        for col in range(self.mods_table.columnCount()):
            item = self.mods_table.item(row, col)
            if item:
                item.setBackground(color)

    def _refresh_details_from_selection(self) -> None:
        selected_rows = self.mods_table.selectionModel().selectedRows()
        if not selected_rows:
            self.details_text.clear()
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)
            return

        row = selected_rows[0].row()
        if row < 0:
            self.details_text.clear()
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)
            return

        mod_item = self.mods_table.item(row, 1)
        if mod_item is None:
            self.details_text.clear()
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)
            return

        mod_path = str(mod_item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        local = self.local_mods_by_path.get(mod_path)
        if local is None:
            self.details_text.clear()
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)
            return

        info = next((item for item in self.update_infos if item.local_mod.path == local.path), None)

        if not info:
            lines = [
                f"Mod: {local.name}",
                f"ID: {local.mod_id}",
                f"Version locale: {local.version}",
                f"Loader détecté: {local.loader_hint}",
                f"Fichier: {local.path.name}",
                "",
                "Lance une vérification des mises à jour pour voir les détails de versions.",
            ]
            self.details_text.setPlainText("\n".join(lines))
            self._selected_mod_page_url = ""
            self.open_mod_page_button.setEnabled(False)
            return

        selected_filters = set(self._selected_changelog_filters())
        self.details_text.setPlainText(build_changelog_text(info, enabled_filters=selected_filters))
        self._selected_mod_page_url = self._resolve_update_page_url(info)
        self.open_mod_page_button.setEnabled(bool(self._selected_mod_page_url) and not self._busy)

    def _resolve_update_page_url(self, info: UpdateInfo) -> str:
        if info.matched_project_url:
            return info.matched_project_url

        if info.provider == "Modrinth" and info.matched_project_id:
            return f"https://modrinth.com/mod/{info.matched_project_id}"

        if info.provider == "CurseForge" and info.matched_project_slug:
            return f"https://www.curseforge.com/minecraft/mc-mods/{info.matched_project_slug}"

        return ""

    def _open_selected_mod_page(self) -> None:
        url = self._selected_mod_page_url.strip()
        if not url:
            QtWidgets.QMessageBox.information(
                self,
                "Page du mod",
                "Aucune page provider associée au mod sélectionné.",
            )
            return

        opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
        if not opened:
            QtWidgets.QMessageBox.warning(
                self,
                "Page du mod",
                f"Impossible d'ouvrir: {url}",
            )

    def _on_table_selection_changed(self) -> None:
        self._refresh_details_from_selection()

    def _export_report(self) -> None:
        """Export the latest in-memory report to JSON and CSV files."""
        settings = self._save_form_settings()

        if self.last_report is None:
            if self.update_infos:
                before = build_before_snapshot(self.local_mods, self.update_infos)
                self.last_report = build_full_report(settings, before, None)
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    "Exporter le rapport",
                    "Aucun rapport à exporter. Lance une vérification (et optionnellement une mise à jour) d'abord.",
                )
                return

        default_dir = settings.mods_directory.strip() or str(Path.home())
        output_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Choisir le dossier d'export", default_dir)
        if not output_dir:
            return

        basename = make_report_basename(settings)
        output_path = Path(output_dir)
        json_path = export_report_json(self.last_report, output_path, basename)
        csv_path = export_report_csv(self.last_report, output_path, basename)

        self._log(f"Rapport JSON exporté: {json_path}")
        self._log(f"Rapport CSV exporté: {csv_path}")
        QtWidgets.QMessageBox.information(
            self,
            "Export terminé",
            f"JSON: {json_path}\nCSV: {csv_path}",
        )

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs_text.appendPlainText(f"[{timestamp}] {message}")
        cursor = self.logs_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.logs_text.setTextCursor(cursor)
