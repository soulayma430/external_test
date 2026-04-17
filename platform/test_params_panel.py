"""
test_params_panel.py  —  Éditeur de paramètres des tests WipeWash
==================================================================
Permet de visualiser et modifier les limites hard-codées dans test_cases.py
directement depuis l'interface, sans toucher au code.

Fonctionnalités :
  • Tableau éditable : LIMIT_MS, TOL_MS, TEST_TIMEOUT_S par test
  • N_SAMPLES global (nombre d'intervalles pour les tests de cycle)
  • Filtre par catégorie et recherche par ID/nom
  • Bouton « Appliquer » : écrit les valeurs en mémoire sur les classes
  • Bouton « Réinitialiser » : restaure les valeurs d'origine
  • Bouton « Exporter JSON » : sauvegarde les paramètres dans un fichier
  • Bouton « Importer JSON » : charge des paramètres depuis un fichier
  • Indicateur visuel si une valeur a été modifiée (fond ambré)
"""

from __future__ import annotations

import copy
import json
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QFileDialog, QMessageBox, QFrame,
    QSizePolicy, QScrollArea, QGroupBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush

import test_cases as _tc
from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_TOOLBAR, W_TITLEBAR,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_AMBER, KPIT_GREEN,
)

# ─── Colonnes du tableau ─────────────────────────────────────────────────
_COL_ID        = 0
_COL_NAME      = 1
_COL_CAT       = 2
_COL_LIMIT_MS  = 3
_COL_TOL_MS    = 4
_COL_MIN_MS    = 5
_COL_TIMEOUT_S = 6
_HEADERS = ["ID", "Nom", "Catégorie", "LIMIT_MS", "TOL_MS", "MIN_MS", "TIMEOUT_S"]

# Colonnes numériques éditables
_EDITABLE_COLS = {_COL_LIMIT_MS, _COL_TOL_MS, _COL_MIN_MS, _COL_TIMEOUT_S}

# Couleurs
_BG_MODIFIED = "#FFF8E1"   # jaune ambré si modifié
_BG_DEFAULT  = W_PANEL
_BG_NONE     = "#F5F5F5"   # gris clair pour valeur N/A


# ─────────────────────────────────────────────────────────────────────────
#  Collecte des paramètres d'origine (avant toute modification)
# ─────────────────────────────────────────────────────────────────────────

def _collect_defaults() -> dict:
    """
    Lit LIMIT_MS, TOL_MS, MIN_MS, TEST_TIMEOUT_S sur chaque classe ALL_TESTS.
    Retourne { cls.__name__ : { "LIMIT_MS": int|None, ... } }
    """
    defaults = {}
    for cls in _tc.ALL_TESTS:
        defaults[cls.__name__] = {
            "ID"           : getattr(cls, "ID", ""),
            "NAME"         : getattr(cls, "NAME", ""),
            "CATEGORY"     : getattr(cls, "CATEGORY", ""),
            "LIMIT_MS"     : getattr(cls, "LIMIT_MS", None),
            "TOL_MS"       : getattr(cls, "TOL_MS", None),
            "MIN_MS"       : getattr(cls, "MIN_MS", None),
            "TEST_TIMEOUT_S": getattr(cls, "TEST_TIMEOUT_S", None),
        }
    return defaults


# ─────────────────────────────────────────────────────────────────────────
#  Panel principal
# ─────────────────────────────────────────────────────────────────────────

class TestParamsPanel(QWidget):
    """Éditeur visuel des paramètres de test_cases.py."""

    # Emis quand les paramètres sont appliqués (pour notifier d'autres panels)
    params_applied = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Sauvegarde des valeurs d'origine au démarrage (une seule fois)
        self._origin: dict = _collect_defaults()
        # Copie de travail (sera modifiée par l'utilisateur)
        self._current: dict = copy.deepcopy(self._origin)

        self._build_ui()
        self._populate_table()

    # ──────────────────────────────────────────────────────────────────
    #  Construction de l'UI
    # ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── En-tête ───────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setStyleSheet(f"""
            QFrame {{
                background: {W_TITLEBAR};
                border-radius: 6px;
                padding: 4px 12px;
            }}
        """)
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 8, 12, 8)

        title = QLabel("  Paramétrage des Tests")
        title.setStyleSheet(f"color: {KPIT_GREEN}; font-size: 13pt; font-weight: bold; font-family: '{FONT_UI}';")
        hdr_lay.addWidget(title)

        hdr_lay.addStretch()

        sub = QLabel("Modifiez les limites sans toucher au code source")
        sub.setStyleSheet(f"color: {W_TEXT_DIM}; font-size: 9pt; font-family: '{FONT_UI}';")
        hdr_lay.addWidget(sub)

        root.addWidget(hdr)

        # ── Barre globale N_SAMPLES ───────────────────────────────────
        global_box = QGroupBox("Paramètre global")
        global_box.setStyleSheet(f"""
            QGroupBox {{
                font-family: '{FONT_UI}'; font-size: 10pt; font-weight: bold;
                color: {W_TEXT}; border: 1px solid {W_BORDER};
                border-radius: 5px; margin-top: 8px; background: {W_PANEL2};
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; }}
        """)
        g_lay = QHBoxLayout(global_box)
        g_lay.setSpacing(16)

        g_lay.addWidget(QLabel("N_SAMPLES  (intervalles cycle) :"))
        self._nspin = QSpinBox()
        self._nspin.setRange(5, 200)
        self._nspin.setValue(_tc.N_SAMPLES)
        self._nspin.setSuffix("  trames")
        self._nspin.setToolTip(
            "Nombre d'intervalles collectés avant qu'un test de cycle renvoie PASS/FAIL.\n"
            "Valeur d'origine : 20"
        )
        self._nspin.setStyleSheet(self._spin_style())
        self._nspin.valueChanged.connect(self._on_nspin_changed)
        g_lay.addWidget(self._nspin)

        self._nspin_reset_btn = QPushButton("↺")
        self._nspin_reset_btn.setFixedWidth(32)
        self._nspin_reset_btn.setToolTip("Restaurer N_SAMPLES = 20")
        self._nspin_reset_btn.clicked.connect(lambda: self._nspin.setValue(20))
        self._nspin_reset_btn.setStyleSheet(self._small_btn_style())
        g_lay.addWidget(self._nspin_reset_btn)

        self._nspin_origin = _tc.N_SAMPLES   # valeur d'origine

        g_lay.addStretch()
        root.addWidget(global_box)

        # ── Barre de filtre / recherche ───────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("Catégorie :"))
        self._cat_cb = QComboBox()
        self._cat_cb.addItem("Toutes")
        cats = sorted({d["CATEGORY"] for d in self._origin.values() if d["CATEGORY"]})
        for c in cats:
            self._cat_cb.addItem(c)
        self._cat_cb.setStyleSheet(self._combo_style())
        self._cat_cb.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(self._cat_cb)

        bar.addSpacing(12)
        bar.addWidget(QLabel("Recherche :"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("ID ou nom…")
        self._search.setStyleSheet(self._input_style())
        self._search.textChanged.connect(self._apply_filter)
        bar.addWidget(self._search)
        bar.addStretch()

        root.addLayout(bar)

        # ── Tableau ───────────────────────────────────────────────────
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked |
                                    QTableWidget.EditTrigger.SelectedClicked)
        self._table.setStyleSheet(self._table_style())
        self._table.itemChanged.connect(self._on_item_changed)

        # Largeurs initiales
        self._table.setColumnWidth(_COL_ID,        60)
        self._table.setColumnWidth(_COL_NAME,      260)
        self._table.setColumnWidth(_COL_CAT,       130)
        self._table.setColumnWidth(_COL_LIMIT_MS,  90)
        self._table.setColumnWidth(_COL_TOL_MS,    80)
        self._table.setColumnWidth(_COL_MIN_MS,    80)
        self._table.setColumnWidth(_COL_TIMEOUT_S, 90)

        root.addWidget(self._table, 1)

        # ── Légende ───────────────────────────────────────────────────
        leg_lay = QHBoxLayout()
        leg_lay.setSpacing(16)
        for color, text in [(_BG_MODIFIED, "Valeur modifiée"),
                             (_BG_NONE,     "Paramètre non applicable"),
                             (_BG_DEFAULT,  "Valeur d'origine")]:
            dot = QLabel("■")
            dot.setStyleSheet(f"color: {color}; font-size: 14pt;")
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 8pt; color: {W_TEXT_DIM}; font-family: '{FONT_UI}';")
            leg_lay.addWidget(dot)
            leg_lay.addWidget(lbl)
        leg_lay.addStretch()
        root.addLayout(leg_lay)

        # ── Boutons d'action ─────────────────────────────────────────
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self._apply_btn = QPushButton("Appliquer")
        self._apply_btn.setToolTip("Écrit les nouvelles valeurs sur les classes Python en mémoire")
        self._apply_btn.clicked.connect(self._apply_params)
        self._apply_btn.setStyleSheet(self._action_btn_style(KPIT_GREEN, "#FFFFFF"))

        self._reset_btn = QPushButton("Réinitialiser")
        self._reset_btn.setToolTip("Restaure toutes les valeurs d'origine")
        self._reset_btn.clicked.connect(self._reset_params)
        self._reset_btn.setStyleSheet(self._action_btn_style("#607D8B", "#FFFFFF"))

        self._export_btn = QPushButton("Exporter JSON")
        self._export_btn.setToolTip("Sauvegarde les paramètres actuels dans un fichier JSON")
        self._export_btn.clicked.connect(self._export_json)
        self._export_btn.setStyleSheet(self._action_btn_style("#1565C0", "#FFFFFF"))

        self._import_btn = QPushButton("Importer JSON")
        self._import_btn.setToolTip("Charge des paramètres depuis un fichier JSON")
        self._import_btn.clicked.connect(self._import_json)
        self._import_btn.setStyleSheet(self._action_btn_style("#6A1B9A", "#FFFFFF"))

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"font-size: 9pt; color: {W_TEXT_DIM}; font-family: '{FONT_UI}';")

        btn_bar.addWidget(self._apply_btn)
        btn_bar.addWidget(self._reset_btn)
        btn_bar.addSpacing(12)
        btn_bar.addWidget(self._export_btn)
        btn_bar.addWidget(self._import_btn)
        btn_bar.addStretch()
        btn_bar.addWidget(self._status_lbl)

        root.addLayout(btn_bar)

    # ──────────────────────────────────────────────────────────────────
    #  Remplissage du tableau
    # ──────────────────────────────────────────────────────────────────

    def _populate_table(self, filter_cat: str = "Toutes", filter_text: str = ""):
        self._table.blockSignals(True)
        self._table.setRowCount(0)

        ft = filter_text.lower()

        for cls in _tc.ALL_TESTS:
            name = cls.__name__
            d    = self._current[name]
            orig = self._origin[name]

            # Filtres
            if filter_cat != "Toutes" and d["CATEGORY"] != filter_cat:
                continue
            if ft and ft not in d["ID"].lower() and ft not in d["NAME"].lower():
                continue

            row = self._table.rowCount()
            self._table.insertRow(row)

            def _cell(text, editable=False, original=None, current=None):
                item = QTableWidgetItem(str(text) if text is not None else "")
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(QBrush(QColor(W_TEXT_DIM)))
                # N/A
                if current is None and editable:
                    item.setBackground(QBrush(QColor(_BG_NONE)))
                    item.setText("—")
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(QBrush(QColor("#BDBDBD")))
                # Modifié
                elif editable and original is not None and current != original:
                    item.setBackground(QBrush(QColor(_BG_MODIFIED)))
                else:
                    item.setBackground(QBrush(QColor(_BG_DEFAULT if editable else "#FAFAFA")))
                # Stocker le nom de classe pour retrouver la ligne
                item.setData(Qt.ItemDataRole.UserRole, name)
                return item

            self._table.setItem(row, _COL_ID,       _cell(d["ID"]))
            self._table.setItem(row, _COL_NAME,      _cell(d["NAME"]))
            self._table.setItem(row, _COL_CAT,       _cell(d["CATEGORY"]))
            self._table.setItem(row, _COL_LIMIT_MS,  _cell(
                d["LIMIT_MS"],  editable=True,
                original=orig["LIMIT_MS"],  current=d["LIMIT_MS"]))
            self._table.setItem(row, _COL_TOL_MS,    _cell(
                d["TOL_MS"],    editable=True,
                original=orig["TOL_MS"],    current=d["TOL_MS"]))
            self._table.setItem(row, _COL_MIN_MS,    _cell(
                d["MIN_MS"],    editable=True,
                original=orig["MIN_MS"],    current=d["MIN_MS"]))
            self._table.setItem(row, _COL_TIMEOUT_S, _cell(
                d["TEST_TIMEOUT_S"], editable=True,
                original=orig["TEST_TIMEOUT_S"], current=d["TEST_TIMEOUT_S"]))

            # Hauteur de ligne confortable
            self._table.setRowHeight(row, 28)

        self._table.blockSignals(False)

    # ──────────────────────────────────────────────────────────────────
    #  Signaux
    # ──────────────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        """Met à jour _current quand l'utilisateur modifie une cellule."""
        col = item.column()
        if col not in _EDITABLE_COLS:
            return

        cls_name = item.data(Qt.ItemDataRole.UserRole)
        if cls_name is None:
            return

        raw = item.text().strip()
        if raw == "—" or raw == "":
            return

        try:
            val = float(raw) if "." in raw else int(raw)
        except ValueError:
            # Valeur invalide — on restaure le texte précédent
            self._table.blockSignals(True)
            prev = self._current[cls_name]
            key  = _COL_TO_KEY[col]
            item.setText(str(prev[key]) if prev[key] is not None else "—")
            self._table.blockSignals(False)
            return

        key = _COL_TO_KEY[col]
        self._current[cls_name][key] = val

        # Colorier si modifié
        orig_val = self._origin[cls_name][key]
        self._table.blockSignals(True)
        if orig_val is not None and val != orig_val:
            item.setBackground(QBrush(QColor(_BG_MODIFIED)))
        else:
            item.setBackground(QBrush(QColor(_BG_DEFAULT)))
        self._table.blockSignals(False)

    def _on_nspin_changed(self, val: int):
        orig = self._nspin_origin
        color = _BG_MODIFIED if val != orig else _BG_DEFAULT
        self._nspin.setStyleSheet(self._spin_style(color))

    def _apply_filter(self):
        self._populate_table(
            filter_cat  = self._cat_cb.currentText(),
            filter_text = self._search.text(),
        )

    # ──────────────────────────────────────────────────────────────────
    #  Actions
    # ──────────────────────────────────────────────────────────────────

    def _apply_params(self):
        """Écrit les valeurs de _current sur les classes Python (en mémoire)."""
        changed = 0
        for cls in _tc.ALL_TESTS:
            name = cls.__name__
            d    = self._current[name]
            for attr, key in [
                ("LIMIT_MS",      "LIMIT_MS"),
                ("TOL_MS",        "TOL_MS"),
                ("MIN_MS",        "MIN_MS"),
                ("TEST_TIMEOUT_S","TEST_TIMEOUT_S"),
            ]:
                val = d[key]
                if val is not None and getattr(cls, attr, None) != val:
                    setattr(cls, attr, val)
                    # Mettre à jour LIMIT_STR automatiquement pour les cycles
                    if attr == "LIMIT_MS" and hasattr(cls, "TOL_MS") and cls.TOL_MS:
                        cls.LIMIT_STR = f"{val} ms ± {cls.TOL_MS} ms"
                    elif attr == "LIMIT_MS":
                        cls.LIMIT_STR = f"≤ {val} ms"
                    changed += 1

        # N_SAMPLES
        new_n = self._nspin.value()
        if new_n != _tc.N_SAMPLES:
            _tc.N_SAMPLES = new_n
            changed += 1

        ts = time.strftime("%H:%M:%S")
        self._status_lbl.setText(f"✔  {changed} paramètre(s) appliqué(s)  —  {ts}")
        self._status_lbl.setStyleSheet(f"color: #2E7D32; font-size: 9pt; font-family: '{FONT_UI}';")

        # Rafraîchir le tableau pour mettre à jour les fonds
        self._apply_filter()
        self.params_applied.emit()

    def _reset_params(self):
        """Restaure toutes les valeurs d'origine."""
        reply = QMessageBox.question(
            self, "Réinitialiser",
            "Restaurer toutes les valeurs d'origine ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._current = copy.deepcopy(self._origin)

        # Restaurer sur les classes
        for cls in _tc.ALL_TESTS:
            name = cls.__name__
            d    = self._origin[name]
            for attr, key in [("LIMIT_MS","LIMIT_MS"),("TOL_MS","TOL_MS"),
                               ("MIN_MS","MIN_MS"),("TEST_TIMEOUT_S","TEST_TIMEOUT_S")]:
                if d[key] is not None:
                    setattr(cls, attr, d[key])

        _tc.N_SAMPLES = self._nspin_origin
        self._nspin.setValue(self._nspin_origin)

        self._apply_filter()
        self._status_lbl.setText("↺  Paramètres réinitialisés")
        self._status_lbl.setStyleSheet(f"color: #E65100; font-size: 9pt; font-family: '{FONT_UI}';")
        self.params_applied.emit()

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les paramètres", "test_params.json",
            "Fichiers JSON (*.json)"
        )
        if not path:
            return
        payload = {
            "N_SAMPLES": self._nspin.value(),
            "tests": {
                name: {k: v for k, v in d.items()
                       if k in ("LIMIT_MS","TOL_MS","MIN_MS","TEST_TIMEOUT_S") and v is not None}
                for name, d in self._current.items()
            }
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        self._status_lbl.setText(f"⬇  Exporté : {path}")
        self._status_lbl.setStyleSheet(f"color: #1565C0; font-size: 9pt; font-family: '{FONT_UI}';")

    def _import_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer des paramètres", "",
            "Fichiers JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Lecture impossible :\n{e}")
            return

        if "N_SAMPLES" in payload:
            self._nspin.setValue(int(payload["N_SAMPLES"]))

        tests = payload.get("tests", {})
        for name, vals in tests.items():
            if name not in self._current:
                continue
            for key in ("LIMIT_MS","TOL_MS","MIN_MS","TEST_TIMEOUT_S"):
                if key in vals:
                    self._current[name][key] = vals[key]

        self._apply_filter()
        self._status_lbl.setText(f"⬆  Importé : {path}  —  cliquez Appliquer pour valider")
        self._status_lbl.setStyleSheet(f"color: #6A1B9A; font-size: 9pt; font-family: '{FONT_UI}';")

    # ──────────────────────────────────────────────────────────────────
    #  Styles
    # ──────────────────────────────────────────────────────────────────

    def _table_style(self) -> str:
        return f"""
            QTableWidget {{
                background: {W_PANEL};
                gridline-color: {W_BORDER};
                color: {W_TEXT};
                font-family: '{FONT_UI}';
                font-size: 9.5pt;
                border: 1px solid {W_BORDER};
                border-radius: 4px;
            }}
            QTableWidget::item:selected {{
                background: rgba(141,198,63,0.25);
                color: {W_TEXT};
            }}
            QHeaderView::section {{
                background: {W_TOOLBAR};
                color: {W_TEXT};
                font-family: '{FONT_UI}';
                font-size: 9pt;
                font-weight: bold;
                border: none;
                border-right: 1px solid {W_BORDER};
                padding: 4px 6px;
            }}
        """

    def _spin_style(self, bg=None) -> str:
        bg = bg or W_PANEL
        return f"""
            QSpinBox {{
                background: {bg};
                color: {W_TEXT};
                border: 1px solid {W_BORDER};
                border-radius: 3px;
                padding: 2px 6px;
                font-family: '{FONT_MONO}';
                min-width: 100px;
            }}
        """

    def _combo_style(self) -> str:
        return f"""
            QComboBox {{
                background: {W_PANEL};
                color: {W_TEXT};
                border: 1px solid {W_BORDER};
                border-radius: 3px;
                padding: 2px 8px;
                font-family: '{FONT_UI}';
                min-width: 140px;
            }}
        """

    def _input_style(self) -> str:
        return f"""
            QLineEdit {{
                background: {W_PANEL};
                color: {W_TEXT};
                border: 1px solid {W_BORDER};
                border-radius: 3px;
                padding: 3px 8px;
                font-family: '{FONT_UI}';
                min-width: 180px;
            }}
        """

    def _action_btn_style(self, bg: str, fg: str) -> str:
        return f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-family: '{FONT_UI}';
                font-size: 9.5pt;
                font-weight: bold;
            }}
            QPushButton:hover  {{ background: {bg}CC; }}
            QPushButton:pressed {{ background: {bg}99; }}
        """

    def _small_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background: {W_PANEL3};
                color: {W_TEXT};
                border: 1px solid {W_BORDER};
                border-radius: 3px;
                font-size: 12pt;
            }}
            QPushButton:hover {{ background: {W_TOOLBAR}; }}
        """


# ─── Mapping colonne → clé dict ──────────────────────────────────────────
_COL_TO_KEY = {
    _COL_LIMIT_MS  : "LIMIT_MS",
    _COL_TOL_MS    : "TOL_MS",
    _COL_MIN_MS    : "MIN_MS",
    _COL_TIMEOUT_S : "TEST_TIMEOUT_S",
}
