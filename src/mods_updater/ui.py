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
    add_profile,
    load_settings,
    remove_profile,
    save_settings,
    set_active_profile,
    write_back_active_profile,
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
        self.resize(1480, 940)

        self.settings = load_settings()
        self.modrinth = ModrinthProvider()

        self.local_mods: list[LocalMod] = []
        self.local_mods_by_path: dict[str, LocalMod] = {}
        self.update_infos: list[UpdateInfo] = []
        self.before_snapshot: dict | None = None
        self.last_report: dict | None = None

        self._threads: set[TaskThread] = set()
        self._busy = False
        self._loading_form = False

        self._build_ui()
        self._apply_theme()
        self._load_settings_to_form()
        self._refresh_minecraft_versions(background=True)

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(20, 18, 20, 18)
        root_layout.setSpacing(14)

        hero = QtWidgets.QFrame()
        hero.setObjectName("hero")
        hero_layout = QtWidgets.QHBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(20)

        title_block = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(APP_NAME)
        title.setObjectName("heroTitle")
        subtitle = QtWidgets.QLabel(
            "Scan des .jar, score de matching providers, simulation dry-run, export de rapports et backup .old"
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
        controls_layout.setContentsMargins(18, 18, 18, 18)
        controls_layout.setHorizontalSpacing(12)
        controls_layout.setVerticalSpacing(10)

        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self.profile_new_button = QtWidgets.QPushButton("Nouveau profil")
        self.profile_new_button.clicked.connect(self._create_profile)
        self.profile_delete_button = QtWidgets.QPushButton("Supprimer profil")
        self.profile_delete_button.clicked.connect(self._delete_profile)

        self.mods_dir_input = QtWidgets.QLineEdit()
        self.mods_dir_input.setPlaceholderText("Dossier mods Minecraft")
        browse_button = QtWidgets.QPushButton("Parcourir")
        browse_button.clicked.connect(self._choose_mods_directory)

        self.mc_version_combo = QtWidgets.QComboBox()
        self.mc_version_combo.setEditable(True)
        self.mc_version_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        refresh_versions_button = QtWidgets.QPushButton("Actualiser versions MC")
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

        self.strict_matching_checkbox = QtWidgets.QCheckBox("Matching strict anti faux-positifs")
        self.strict_matching_checkbox.setChecked(True)
        self.dry_run_checkbox = QtWidgets.QCheckBox("Dry-run (simulation sans écriture)")
        self.dry_run_checkbox.setChecked(False)

        self.auto_detect_button = QtWidgets.QPushButton("Auto-détecter")
        self.auto_detect_button.clicked.connect(self._autodetect_context)
        self.scan_button = QtWidgets.QPushButton("Scanner")
        self.scan_button.clicked.connect(self._scan_mods)
        self.check_updates_button = QtWidgets.QPushButton("Vérifier les mises à jour")
        self.check_updates_button.clicked.connect(self._check_updates)

        self.update_selected_button = QtWidgets.QPushButton("Mettre à jour la sélection")
        self.update_selected_button.clicked.connect(self._update_selected_mods)
        self.update_all_button = QtWidgets.QPushButton("Tout mettre à jour")
        self.update_all_button.clicked.connect(self._update_all_mods)
        self.matching_button = QtWidgets.QPushButton("Confiance du matching")
        self.matching_button.clicked.connect(self._show_matching_confidence_dialog)
        self.export_report_button = QtWidgets.QPushButton("Exporter le rapport JSON/CSV")
        self.export_report_button.clicked.connect(self._export_report)

        self.scan_button.setToolTip("Scanner les mods présents dans le dossier (auto-scan aussi après sélection du dossier).")
        self.check_updates_button.setToolTip("Vérifier les mises à jour disponibles sur les providers (check parallèle + cache court).")
        self.matching_button.setToolTip("Étape 3: contrôler les scores de matching avant validation.")
        self.dry_run_checkbox.setToolTip("Option: simuler les actions sans modifier les fichiers .jar.")
        self.update_selected_button.setToolTip("Étape 4: appliquer uniquement les mods cochés.")
        self.update_all_button.setToolTip("Étape 4: appliquer toutes les mises à jour disponibles.")
        self.export_report_button.setToolTip("Étape 5: exporter un rapport JSON/CSV avant ou après opération.")

        controls_layout.addWidget(QtWidgets.QLabel("Profil d'instance"), 0, 0)
        controls_layout.addWidget(self.profile_combo, 0, 1, 1, 2)
        controls_layout.addWidget(self.profile_new_button, 0, 3)
        controls_layout.addWidget(self.profile_delete_button, 0, 4)

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

        self.show_logs_checkbox = QtWidgets.QCheckBox("Afficher les logs")
        self.show_logs_checkbox.setChecked(False)
        self.show_logs_checkbox.stateChanged.connect(self._on_show_logs_changed)

        controls_layout.addWidget(self.show_logs_checkbox, 6, 2)
        controls_layout.addWidget(self.matching_button, 6, 3)
        controls_layout.addWidget(self.export_report_button, 6, 4)

        root_layout.addWidget(controls)

        content_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        table_card = QtWidgets.QFrame()
        table_card.setObjectName("tableCard")
        table_layout = QtWidgets.QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 12, 12, 12)

        table_filters = QtWidgets.QHBoxLayout()
        table_filters.setSpacing(8)

        self.mods_search_input = QtWidgets.QLineEdit()
        self.mods_search_input.setPlaceholderText("Rechercher un mod (nom, ID, fichier)")
        self.mods_search_input.textChanged.connect(self._on_table_filter_changed)

        self.status_filter_available = QtWidgets.QCheckBox("Dispo")
        self.status_filter_uptodate = QtWidgets.QCheckBox("À jour")
        self.status_filter_notfound = QtWidgets.QCheckBox("Introuv.")
        self.status_filter_error = QtWidgets.QCheckBox("Erreur")
        self.status_filter_scanned = QtWidgets.QCheckBox("Scanné")

        for checkbox in [
            self.status_filter_available,
            self.status_filter_uptodate,
            self.status_filter_notfound,
            self.status_filter_error,
            self.status_filter_scanned,
        ]:
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._on_table_filter_changed)

        self.source_filter_modrinth = QtWidgets.QCheckBox("Modrinth")
        self.source_filter_curseforge = QtWidgets.QCheckBox("CurseForge")
        self.source_filter_other = QtWidgets.QCheckBox("Autre")

        for checkbox in [self.source_filter_modrinth, self.source_filter_curseforge, self.source_filter_other]:
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._on_table_filter_changed)

        table_filters.addWidget(QtWidgets.QLabel("Recherche:"))
        table_filters.addWidget(self.mods_search_input, 2)
        table_filters.addWidget(QtWidgets.QLabel("État:"))
        table_filters.addWidget(self.status_filter_available)
        table_filters.addWidget(self.status_filter_uptodate)
        table_filters.addWidget(self.status_filter_notfound)
        table_filters.addWidget(self.status_filter_error)
        table_filters.addWidget(self.status_filter_scanned)
        table_filters.addWidget(QtWidgets.QLabel("Source:"))
        table_filters.addWidget(self.source_filter_modrinth)
        table_filters.addWidget(self.source_filter_curseforge)
        table_filters.addWidget(self.source_filter_other)
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
        self.mods_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.mods_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.mods_table.itemSelectionChanged.connect(self._on_table_selection_changed)

        table_layout.addWidget(self.mods_table)

        detail_card = QtWidgets.QFrame()
        detail_card.setObjectName("detailCard")
        detail_layout = QtWidgets.QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.setSpacing(8)

        detail_title = QtWidgets.QLabel("Détails des versions")
        detail_title.setObjectName("panelTitle")

        filters_row = QtWidgets.QHBoxLayout()
        filters_row.setSpacing(8)
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

        detail_layout.addWidget(detail_title)
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
        self.logs_text.setMaximumHeight(130)

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
                font-size: 10.5pt;
            }
            QFrame#hero, QFrame#controlsCard, QFrame#tableCard, QFrame#detailCard, QFrame#logsCard {
                background: rgba(7, 15, 24, 0.82);
                border: 1px solid rgba(102, 213, 255, 0.22);
                border-radius: 14px;
            }
            QLabel#heroTitle {
                font-size: 24pt;
                font-weight: 700;
                color: #f4fbff;
            }
            QLabel#heroSubtitle {
                color: #9ec6db;
                font-size: 10.5pt;
            }
            QLabel#busyBadge {
                min-width: 150px;
                padding: 7px 14px;
                border-radius: 999px;
                background: #1d3144;
                color: #7be7ff;
                border: 1px solid #2d6d87;
                font-weight: 600;
            }
            QLabel#panelTitle {
                color: #90dcff;
                font-weight: 600;
                font-size: 10.8pt;
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
                padding: 6px 8px;
                selection-background-color: #1b8fb9;
                selection-color: #f2fbff;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1f7a9f,
                    stop:1 #20a3bd);
                border: 1px solid #39c0de;
                border-radius: 8px;
                padding: 7px 12px;
                color: #f5fdff;
                font-weight: 600;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #258cb5,
                    stop:1 #28b8d4);
            }
            QPushButton:disabled {
                background: #224358;
                color: #89a7bb;
                border-color: #2f5871;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
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
                padding: 8px;
                font-weight: 600;
            }
            QTableWidget {
                gridline-color: rgba(136, 195, 224, 0.13);
                alternate-background-color: rgba(22, 36, 52, 0.62);
            }
            """
        )

    def _populate_profiles_combo(self) -> None:
        names = list(self.settings.profiles.keys())
        if not names:
            names = ["Default"]

        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)

        idx = self.profile_combo.findText(self.settings.active_profile)
        if idx == -1:
            idx = 0
        self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

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

    def _load_active_profile_fields(self) -> None:
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
            self._populate_profiles_combo()
            self._load_active_profile_fields()
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

        write_back_active_profile(self.settings)

    def _save_form_settings(self):
        if self._loading_form:
            return self.settings

        selected_profile = self.profile_combo.currentText().strip()
        if selected_profile and selected_profile != self.settings.active_profile and selected_profile in self.settings.profiles:
            set_active_profile(self.settings, selected_profile)

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

    def _selected_status_filters(self) -> set[str]:
        selected: set[str] = set()
        if self.status_filter_available.isChecked():
            selected.add("update_available")
        if self.status_filter_uptodate.isChecked():
            selected.add("up_to_date")
        if self.status_filter_notfound.isChecked():
            selected.add("not_found")
        if self.status_filter_error.isChecked():
            selected.add("error")
        if self.status_filter_scanned.isChecked():
            selected.add("scanned")
        return selected

    def _selected_source_filters(self) -> set[str]:
        selected: set[str] = set()
        if self.source_filter_modrinth.isChecked():
            selected.add("modrinth")
        if self.source_filter_curseforge.isChecked():
            selected.add("curseforge")
        if self.source_filter_other.isChecked():
            selected.add("other")
        return selected

    def _apply_table_filters(self) -> None:
        query = self.mods_search_input.text().strip().lower()
        status_filters = self._selected_status_filters()
        source_filters = self._selected_source_filters()

        visible_row = -1
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
            matches_status = not status_filters or status_key in status_filters
            matches_source = not source_filters or source_key in source_filters

            hidden = not (matches_query and matches_status and matches_source)
            self.mods_table.setRowHidden(row, hidden)

            if not hidden and visible_row == -1:
                visible_row = row

        if visible_row >= 0:
            selected_rows = self.mods_table.selectionModel().selectedRows()
            if not selected_rows or self.mods_table.isRowHidden(selected_rows[0].row()):
                self.mods_table.selectRow(visible_row)
        else:
            self.details_text.clear()

    def _on_show_logs_changed(self) -> None:
        self.logs_card.setVisible(self.show_logs_checkbox.isChecked())

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        self.check_updates_button.setEnabled(not busy)
        self.update_selected_button.setEnabled(not busy)
        self.update_all_button.setEnabled(not busy)
        self.auto_detect_button.setEnabled(not busy)
        self.matching_button.setEnabled(not busy)
        self.export_report_button.setEnabled(not busy)

        self.profile_combo.setEnabled(not busy)
        self.profile_new_button.setEnabled(not busy)
        self.profile_delete_button.setEnabled(not busy)
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
            return

        row = selected_rows[0].row()
        if row < 0:
            self.details_text.clear()
            return

        mod_item = self.mods_table.item(row, 1)
        if mod_item is None:
            self.details_text.clear()
            return

        mod_path = str(mod_item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        local = self.local_mods_by_path.get(mod_path)
        if local is None:
            self.details_text.clear()
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
            return

        selected_filters = set(self._selected_changelog_filters())
        self.details_text.setPlainText(build_changelog_text(info, enabled_filters=selected_filters))

    def _on_table_selection_changed(self) -> None:
        self._refresh_details_from_selection()

    def _show_matching_confidence_dialog(self) -> None:
        """Display provider candidates, confidence, and selected matches per mod."""
        if not self.update_infos:
            QtWidgets.QMessageBox.information(
                self,
                "Confiance du matching",
                "Lance d'abord une vérification des mises à jour pour voir les scores.",
            )
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Confiance du matching (pré-validation)")
        dialog.resize(1220, 680)

        layout = QtWidgets.QVBoxLayout(dialog)
        info_label = QtWidgets.QLabel(
            "Scores remontés par provider avant validation finale.\n"
            "Un candidat marqué 'sélectionné' est celui retenu pour le mod."
        )
        info_label.setWordWrap(True)

        table = QtWidgets.QTableWidget(0, 10)
        table.setHorizontalHeaderLabels(
            [
                "Mod",
                "Provider",
                "Rang",
                "Confiance",
                "Score",
                "Projet",
                "Slug",
                "Downloads",
                "Sélection",
                "Note",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(9, QtWidgets.QHeaderView.ResizeMode.Stretch)

        def append_row(values: list[str], accepted: bool) -> None:
            row = table.rowCount()
            table.insertRow(row)
            for col, value in enumerate(values):
                table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
            if accepted:
                for col in range(table.columnCount()):
                    item = table.item(row, col)
                    if item is not None:
                        item.setBackground(QtGui.QColor("#1f5f4a"))

        for info in self.update_infos:
            if info.match_candidates:
                sorted_candidates = sorted(
                    info.match_candidates,
                    key=lambda candidate: (1 if candidate.accepted else 0, candidate.score, candidate.confidence),
                    reverse=True,
                )
                for candidate in sorted_candidates:
                    append_row(
                        [
                            info.local_mod.name,
                            candidate.provider,
                            str(candidate.rank),
                            f"{candidate.confidence:.4f}",
                            f"{candidate.score:.3f}",
                            candidate.project_id,
                            candidate.slug,
                            str(candidate.downloads),
                            "oui" if candidate.accepted else "non",
                            candidate.note or info.match_note,
                        ],
                        candidate.accepted,
                    )
            else:
                append_row(
                    [
                        info.local_mod.name,
                        info.provider or "-",
                        "-",
                        f"{info.match_confidence:.4f}",
                        f"{info.match_score:.3f}",
                        info.matched_project_id or "-",
                        "-",
                        "-",
                        "-",
                        info.match_note or info.message,
                    ],
                    False,
                )

        close_button = QtWidgets.QPushButton("Fermer")
        close_button.clicked.connect(dialog.accept)

        layout.addWidget(info_label)
        layout.addWidget(table, 1)
        layout.addWidget(close_button, 0, QtCore.Qt.AlignmentFlag.AlignRight)

        dialog.exec()

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

    def _clear_results(self) -> None:
        self.local_mods = []
        self.update_infos = []
        self.before_snapshot = None
        self.last_report = None
        self._populate_table()

    def _on_profile_changed(self) -> None:
        if self._loading_form:
            return

        profile_name = self.profile_combo.currentText().strip()
        if not profile_name or profile_name == self.settings.active_profile:
            return

        try:
            self._write_form_to_settings_object()
            set_active_profile(self.settings, profile_name)
            save_settings(self.settings)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Profil", f"Impossible de changer de profil: {exc}")
            self._loading_form = True
            try:
                self._populate_profiles_combo()
            finally:
                self._loading_form = False
            return

        self._loading_form = True
        try:
            self._load_active_profile_fields()
        finally:
            self._loading_form = False

        self._clear_results()
        self._log(f"Profil actif: {self.settings.active_profile}")

    def _create_profile(self) -> None:
        self._save_form_settings()

        name, ok = QtWidgets.QInputDialog.getText(self, "Nouveau profil", "Nom du profil:")
        if not ok:
            return

        profile_name = name.strip()
        if not profile_name:
            QtWidgets.QMessageBox.warning(self, "Profil", "Le nom du profil est vide.")
            return

        try:
            add_profile(self.settings, profile_name, clone_current=True)
            save_settings(self.settings)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Profil", str(exc))
            return

        self._loading_form = True
        try:
            self._populate_profiles_combo()
            self._load_active_profile_fields()
        finally:
            self._loading_form = False

        self._clear_results()
        self._log(f"Profil créé: {profile_name}")

    def _delete_profile(self) -> None:
        self._save_form_settings()

        profile_name = self.profile_combo.currentText().strip()
        if not profile_name:
            return

        answer = QtWidgets.QMessageBox.question(
            self,
            "Supprimer profil",
            f"Supprimer le profil '{profile_name}' ?",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            new_active = remove_profile(self.settings, profile_name)
            save_settings(self.settings)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Profil", str(exc))
            return

        self._loading_form = True
        try:
            self._populate_profiles_combo()
            self._load_active_profile_fields()
        finally:
            self._loading_form = False

        self._clear_results()
        self._log(f"Profil supprimé: {profile_name} (actif: {new_active})")

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs_text.appendPlainText(f"[{timestamp}] {message}")
        cursor = self.logs_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.logs_text.setTextCursor(cursor)
