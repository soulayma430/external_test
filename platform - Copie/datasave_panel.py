"""
WipeWash — DataSave Panel
=========================
Enregistrement temps réel de toutes les variables du banc HIL + export CSV.

Sources capturées :
  • Motor  : motor_received  → state, front, rear, speed, current, rest_contact, fault
  • LIN    : lin_received    → type, op, pid, wiper_op, front_motor_on, rest_contact_raw
  • CAN    : can_received    → can_id, direction, payload, dlc
  • Pump   : data_received   → flow, pressure, current, state, direction

Architecture :
  DataRecorder  — collecte thread-safe dans un deque (max 100 000 lignes)
  DataSavePanel — UI Qt : contrôles + preview + export

API publique (appelée depuis MainWindow) :
  panel.on_motor_data(data: dict)
  panel.on_lin_event(ev: dict)
  panel.on_can_event(ev: dict)
  panel.on_pump_data(data: dict)
"""

import csv
import datetime
import os
import time
from collections import deque
from typing import Optional

from PySide6.QtCore    import Qt, QTimer, Signal, QObject, QThread
from PySide6.QtGui     import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFileDialog, QCheckBox, QSpinBox,
    QProgressBar, QSizePolicy, QSplitter, QGroupBox, QComboBox,
    QScrollArea,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR, W_DOCK_HDR,
    W_TOOLBAR,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    KPIT_GREEN,
)
from widgets_base import StatusLed, _lbl, _hsep, _cd_btn

try:
    from mdf_exporter import MDFExporter
    _MDF_AVAILABLE = True
except ImportError:
    _MDF_AVAILABLE = False

# ──────────────────────────────────────────────────────────────
#  Colonnes exportées
# ──────────────────────────────────────────────────────────────
_MOTOR_COLS = [
    "timestamp", "source",
    "state", "front", "rear", "speed",
    "current", "rest_contact", "fault",
    "crs_wiper_op", "ignition", "vehicle_speed", "rain_intensity",
    "front_blade_cycles",
]

_LIN_COLS = [
    "timestamp", "source",
    "lin_type", "pid", "op", "wiper_op",
    "front_motor_on", "rest_contact_raw", "fault",
    "raw",
]

_CAN_COLS = [
    "timestamp", "source",
    "can_id", "direction", "dlc", "payload",
    "wiper_cmd", "wiper_status", "wiper_ack",
    "vehicle_status", "rain_sensor",
]

_PUMP_COLS = [
    "timestamp", "source",
    "flow", "pressure", "current",
    "state", "direction", "active",
    "timeout_elapsed",
]

# Colonnes communes pour la vue "All channels merged"
_ALL_COLS = [
    "timestamp", "source",
    "state", "front", "rear", "speed", "current", "rest_contact", "fault",
    "lin_type", "pid", "op",
    "can_id", "direction", "payload",
    "flow", "pressure",
]

MAX_BUFFER = 100_000   # lignes max en mémoire


# ═══════════════════════════════════════════════════════════
#  DataRecorder — collecte thread-safe
# ═══════════════════════════════════════════════════════════
class DataRecorder(QObject):
    """
    Collecte les événements de tous les workers dans un buffer circulaire.
    Thread-safe : les workers appellent push_*() depuis leurs threads.
    """
    row_added = Signal(dict)   # émis après chaque push (pour la preview)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf:   deque = deque(maxlen=MAX_BUFFER)
        self._active = False
        self._t0: float | None = None
        self._filters: set[str] = {"motor", "lin", "can", "pump"}

    # ── API ──────────────────────────────────────────────────
    def start(self):
        self._active = True
        self._t0 = time.monotonic()

    def stop(self):
        self._active = False

    def clear(self):
        self._buf.clear()

    def is_active(self) -> bool:
        return self._active

    def set_filter(self, src: str, enabled: bool):
        if enabled:
            self._filters.add(src)
        else:
            self._filters.discard(src)

    def row_count(self) -> int:
        return len(self._buf)

    def elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        return time.monotonic() - self._t0

    def get_rows(self) -> list[dict]:
        return list(self._buf)

    # ── Push events ─────────────────────────────────────────
    def _push(self, row: dict):
        if not self._active:
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        row["timestamp"] = ts
        self._buf.append(row)
        self.row_added.emit(row)

    def push_motor(self, data: dict):
        if "motor" not in self._filters:
            return
        def _d(v):
            if isinstance(v, dict):
                return v
            try:
                import json
                return json.loads(v) if isinstance(v, str) else {}
            except Exception:
                return {}

        f = _d(data.get("front", {}))
        r = _d(data.get("rear",  {}))

        # Cas "flat" (certaines versions du BCM émettent directement les champs)
        if isinstance(data.get("front"), str):
            row = {
                "source":             "motor",
                "state":              data.get("state", ""),
                "front":              data.get("front", ""),
                "rear":               data.get("rear", ""),
                "speed":              data.get("speed", ""),
                "current":            data.get("current", ""),
                "rest_contact":       data.get("rest", ""),
                "fault":              data.get("fault", ""),
                "crs_wiper_op":       data.get("crs_wiper_op", ""),
                "ignition":           data.get("ignition_status", ""),
                "vehicle_speed":      data.get("vehicle_speed", ""),
                "rain_intensity":     data.get("rain_intensity", ""),
                "front_blade_cycles": data.get("front_blade_cycles", ""),
            }
        else:
            row = {
                "source":             "motor",
                "state":              data.get("state", ""),
                "front":              "ON" if f.get("enable", 0) else "OFF",
                "rear":               "ON" if r.get("enable", 0) else "OFF",
                "speed":              "Speed2" if f.get("speed", 0) else "Speed1",
                "current":            round(
                    float(f.get("motor_current", 0)) + float(r.get("motor_current", 0)), 4
                ),
                "rest_contact":       "PARKED" if f.get("rest_contact", 0) else "MOVING",
                "fault":              bool(f.get("fault_status", 0)) or bool(r.get("fault_status", 0)),
                "crs_wiper_op":       data.get("crs_wiper_op", ""),
                "ignition":           data.get("ignition_status", ""),
                "vehicle_speed":      data.get("vehicle_speed", ""),
                "rain_intensity":     data.get("rain_intensity", ""),
                "front_blade_cycles": data.get("front_blade_cycles", ""),
            }
        self._push(row)

    def push_lin(self, ev: dict):
        if "lin" not in self._filters:
            return
        row = {
            "source":           "lin",
            "lin_type":         ev.get("type", ""),
            "pid":              ev.get("pid", ""),
            "op":               ev.get("op", ""),
            "wiper_op":         ev.get("wiper_op", ""),
            "front_motor_on":   ev.get("front_motor_on", ""),
            "rest_contact_raw": ev.get("rest_contact_raw", ""),
            "fault":            ev.get("fault", ""),
            "raw":              ev.get("raw", ""),
        }
        self._push(row)

    def push_can(self, ev: dict):
        if "can" not in self._filters:
            return
        row = {
            "source":        "can",
            "can_id":        ev.get("can_id", ev.get("id", "")),
            "direction":     ev.get("direction", ev.get("dir", "")),
            "dlc":           ev.get("dlc", ""),
            "payload":       ev.get("payload", ev.get("data", "")),
            "wiper_cmd":     ev.get("wiper_cmd", ""),
            "wiper_status":  ev.get("wiper_status", ""),
            "wiper_ack":     ev.get("wiper_ack", ""),
            "vehicle_status": ev.get("vehicle_status", ""),
            "rain_sensor":   ev.get("rain_sensor", ""),
        }
        self._push(row)

    def push_pump(self, data: dict):
        if "pump" not in self._filters:
            return
        row = {
            "source":          "pump",
            "flow":            data.get("flow", ""),
            "pressure":        data.get("pressure", ""),
            "current":         data.get("pump_current", data.get("current", "")),
            "state":           data.get("state", ""),
            "direction":       data.get("direction", ""),
            "active":          data.get("active", ""),
            "timeout_elapsed": data.get("timeout_elapsed", ""),
        }
        self._push(row)

    # ── Export CSV ───────────────────────────────────────────
    def export_csv(self, path: str, source_filter: str = "all") -> int:
        """
        Exporte le buffer vers `path` (CSV UTF-8 avec BOM pour Excel).
        source_filter : "all" | "motor" | "lin" | "can" | "pump"
        Retourne le nombre de lignes écrites.
        """
        rows = list(self._buf)
        if source_filter != "all":
            rows = [r for r in rows if r.get("source") == source_filter]

        if not rows:
            return 0

        # Construire les en-têtes : timestamp + source + union de toutes les clés
        fieldnames = ["timestamp", "source"]
        seen = set(fieldnames)
        for r in rows:
            for k in r:
                if k not in seen:
                    fieldnames.append(k)
                    seen.add(k)

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        return len(rows)

    def export_per_source(self, folder: str, base_name: str) -> dict[str, int]:
        """Exporte un CSV par source dans `folder`."""
        results = {}
        for src in ("motor", "lin", "can", "pump"):
            rows = [r for r in self._buf if r.get("source") == src]
            if not rows:
                continue
            path = os.path.join(folder, f"{base_name}_{src}.csv")
            # Colonnes spécifiques à la source
            col_map = {
                "motor": _MOTOR_COLS,
                "lin":   _LIN_COLS,
                "can":   _CAN_COLS,
                "pump":  _PUMP_COLS,
            }
            cols = col_map[src]
            all_keys = list(cols)
            seen_keys = set(all_keys)
            for r in rows:
                for k in r:
                    if k not in seen_keys:
                        all_keys.append(k)
                        seen_keys.add(k)
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            results[src] = len(rows)
        return results


# ═══════════════════════════════════════════════════════════
#  DataSavePanel — Interface graphique
# ═══════════════════════════════════════════════════════════
_SRC_COLORS = {
    "motor": A_GREEN,
    "lin":   "#9C27B0",
    "can":   A_TEAL,
    "pump":  A_ORANGE,
}
_SRC_LABELS = {
    "motor": "MOTOR",
    "lin":   "LIN",
    "can":   "CAN",
    "pump":  "PUMP",
}

_PREVIEW_COLS = ["Time", "Source", "Key Info 1", "Key Info 2", "Key Info 3"]
_PREVIEW_MAX  = 500   # lignes max affichées dans la preview

_CARD_STYLE = (
    f"QFrame{{background:{W_PANEL};border:1px solid {W_BORDER};"
    "border-radius:6px;padding:4px;}}"
)


class DataSavePanel(QWidget):
    """
    Panneau DataSave style ControlDesk :
      - Contrôles Rec/Stop/Clear
      - Compteurs par source (LED + nb lignes)
      - Preview table (dernières N lignes)
      - Export CSV (tout / par source)
    """

    def __init__(self, recorder: DataRecorder, parent=None):
        super().__init__(parent)
        self._rec = recorder
        self._preview_rows: list[dict] = []
        self._paused = False
        self._setStyleSheet()
        self._build()
        self._rec.row_added.connect(self._on_row_added)

        # Timer refresh (compteurs, elapsed)
        self._t_refresh = QTimer(self)
        self._t_refresh.timeout.connect(self._refresh_stats)
        self._t_refresh.start(500)

    # ── Style ────────────────────────────────────────────────
    def _setStyleSheet(self):
        self.setStyleSheet(f"background:{W_BG};color:{W_TEXT};")

    # ── Build ────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # ── Titre ─────────────────────────────────────────────
        title_row = QHBoxLayout()
        ic = _lbl("◉", 20, True, A_RED)
        ic.setFixedWidth(26)
        title_row.addWidget(ic)
        title_row.addWidget(_lbl("DATA SAVE", 15, True, W_DOCK_HDR))
        title_row.addWidget(_lbl("— Enregistrement & Export CSV", 10, False, W_TEXT_DIM))
        title_row.addStretch()
        self._lbl_elapsed = _lbl("00:00:00", 13, True, A_TEAL, True)
        self._lbl_elapsed.setFont(QFont(FONT_MONO, 13, QFont.Weight.Bold))
        title_row.addWidget(self._lbl_elapsed)
        root.addLayout(title_row)
        root.addWidget(_hsep())

        # ── Splitter vertical : contrôles haut | preview bas ──
        spl = QSplitter(Qt.Orientation.Vertical)
        spl.setStyleSheet(f"QSplitter::handle{{background:{W_BORDER};height:3px;}}")

        top = QWidget(); top.setStyleSheet(f"background:{W_BG};")
        top_lay = QVBoxLayout(top); top_lay.setContentsMargins(0, 0, 0, 0); top_lay.setSpacing(8)

        # ── Ligne 1 : boutons Rec/Stop/Clear ──────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        self._btn_rec   = self._mk_btn("REC",   A_RED,    160, 48)
        self._btn_stop  = self._mk_btn("STOP",  "#707070", 140, 48)
        self._btn_clear = self._mk_btn("CLEAR", A_AMBER,  140, 48)
        self._btn_stop.setEnabled(False)

        self._btn_rec.clicked.connect(self._on_rec)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_clear.clicked.connect(self._on_clear)

        btn_row.addWidget(self._btn_rec)
        btn_row.addWidget(self._btn_stop)
        btn_row.addWidget(self._btn_clear)
        btn_row.addStretch()

        # Pause preview
        self._btn_pause = self._mk_btn("Pause Preview", "#444466", 170, 38)
        self._btn_pause.setCheckable(True)
        self._btn_pause.toggled.connect(self._on_pause_preview)
        btn_row.addWidget(self._btn_pause)

        top_lay.addLayout(btn_row)

        # ── Ligne 2 : filtres sources + compteurs ─────────────
        src_card = QFrame(); src_card.setStyleSheet(_CARD_STYLE)
        src_lay  = QHBoxLayout(src_card); src_lay.setContentsMargins(10, 6, 10, 6); src_lay.setSpacing(16)

        src_lay.addWidget(_lbl("SOURCES :", 10, True, W_TEXT_DIM))
        self._src_checks: dict[str, QCheckBox] = {}
        self._src_leds:   dict[str, StatusLed] = {}
        self._src_counts: dict[str, QLabel]    = {}

        for src in ("motor", "lin", "can", "pump"):
            col = _SRC_COLORS[src]
            # Checkbox filtre
            cb = QCheckBox(_SRC_LABELS[src])
            cb.setChecked(True)
            cb.setStyleSheet(
                f"QCheckBox{{color:{col};font-weight:bold;font-family:{FONT_MONO};"
                f"font-size:10pt;background:transparent;}}"
                f"QCheckBox::indicator{{width:14px;height:14px;border:2px solid {col};"
                f"border-radius:3px;background:{W_PANEL2};}}"
                f"QCheckBox::indicator:checked{{background:{col};}}"
            )
            cb.toggled.connect(lambda checked, s=src: self._rec.set_filter(s, checked))
            self._src_checks[src] = cb

            led = StatusLed(8)
            led.set_state(False, col)
            self._src_leds[src] = led

            cnt = _lbl("0", 10, True, col, True)
            cnt.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
            cnt.setFixedWidth(60)
            self._src_counts[src] = cnt

            grp = QHBoxLayout(); grp.setSpacing(4)
            grp.addWidget(cb); grp.addWidget(led); grp.addWidget(cnt)
            src_lay.addLayout(grp)

        src_lay.addStretch()

        # Total
        self._lbl_total = _lbl("0 lignes", 11, True, W_TEXT_DIM, True)
        self._lbl_total.setFont(QFont(FONT_MONO, 11, QFont.Weight.Bold))
        src_lay.addWidget(self._lbl_total)

        top_lay.addWidget(src_card)

        # ── Ligne 3 : barre de progression (buffer) ───────────
        prog_row = QHBoxLayout(); prog_row.setSpacing(8)
        prog_row.addWidget(_lbl("Buffer :", 9, False, W_TEXT_DIM))
        self._prog = QProgressBar()
        self._prog.setRange(0, MAX_BUFFER)
        self._prog.setValue(0)
        self._prog.setFixedHeight(10)
        self._prog.setTextVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{W_PANEL3};border:1px solid {W_BORDER};"
            f"border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{A_TEAL};border-radius:3px;}}"
        )
        prog_row.addWidget(self._prog, 1)
        self._lbl_buf = _lbl(f"0 / {MAX_BUFFER:,}", 9, False, W_TEXT_DIM, True)
        prog_row.addWidget(self._lbl_buf)
        top_lay.addLayout(prog_row)

        # ── Ligne 4 : export ──────────────────────────────────
        exp_card = QFrame(); exp_card.setStyleSheet(_CARD_STYLE)
        exp_lay  = QHBoxLayout(exp_card); exp_lay.setContentsMargins(10, 6, 10, 6); exp_lay.setSpacing(12)

        exp_lay.addWidget(_lbl("EXPORT :", 10, True, W_TEXT_DIM))

        # Filtre source pour l'export
        self._exp_filter = QComboBox()
        self._exp_filter.addItems(["Tout (merged)", "Motor", "LIN", "CAN", "Pump", "Par source (4 fichiers)"])
        self._exp_filter.setStyleSheet(
            f"QComboBox{{background:{W_PANEL2};border:1px solid {W_BORDER};"
            f"color:{W_TEXT};border-radius:4px;padding:3px 8px;"
            f"font-family:{FONT_MONO};font-size:10pt;}}"
            f"QComboBox::drop-down{{border:none;}}"
            f"QComboBox QAbstractItemView{{background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};}}"
        )
        self._exp_filter.setFixedWidth(200)
        exp_lay.addWidget(self._exp_filter)

        self._btn_export = self._mk_btn("Export CSV", A_TEAL, 170, 40)
        self._btn_export.clicked.connect(self._on_export)
        exp_lay.addWidget(self._btn_export)

        self._btn_export_mdf = self._mk_btn("Export MDF4", "#6A1B9A", 175, 40)
        self._btn_export_mdf.clicked.connect(self._on_export_mdf)
        if not _MDF_AVAILABLE:
            self._btn_export_mdf.setEnabled(False)
            self._btn_export_mdf.setToolTip("pip install asammdf")
        exp_lay.addWidget(self._btn_export_mdf)

        exp_lay.addStretch()

        self._lbl_export_status = _lbl("", 10, False, W_TEXT_DIM, True)
        self._lbl_export_status.setWordWrap(True)
        exp_lay.addWidget(self._lbl_export_status, 1)

        top_lay.addWidget(exp_card)

        spl.addWidget(top)

        # ── Preview table ─────────────────────────────────────
        bot = QWidget(); bot.setStyleSheet(f"background:{W_BG};")
        bot_lay = QVBoxLayout(bot); bot_lay.setContentsMargins(0, 4, 0, 0); bot_lay.setSpacing(4)

        prev_hdr = QHBoxLayout()
        prev_hdr.addWidget(_lbl("PREVIEW", 11, True, W_DOCK_HDR))
        prev_hdr.addWidget(_lbl(f"(dernières {_PREVIEW_MAX} lignes)", 9, False, W_TEXT_DIM))
        prev_hdr.addStretch()
        self._lbl_preview_count = _lbl("0 lignes visibles", 9, False, W_TEXT_DIM, True)
        prev_hdr.addWidget(self._lbl_preview_count)
        bot_lay.addLayout(prev_hdr)

        self._table = QTableWidget(0, len(_PREVIEW_COLS))
        self._table.setHorizontalHeaderLabels(_PREVIEW_COLS)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{W_PANEL};border:1px solid {W_BORDER};"
            f"gridline-color:{W_BORDER};color:{W_TEXT};"
            f"font-family:{FONT_MONO};font-size:9pt;}}"
            f"QHeaderView::section{{background:{W_DOCK_HDR};color:{W_TEXT_HDR};"
            f"border:none;padding:4px;font-family:{FONT_MONO};font-size:9pt;font-weight:bold;}}"
            f"QTableWidget::item:selected{{background:{A_TEAL};color:#FFFFFF;}}"
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            self._table.styleSheet() +
            f"QTableWidget{{alternate-background-color:{W_PANEL2};}}"
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 160)
        self._table.setColumnWidth(1, 60)
        self._table.setColumnWidth(2, 200)
        self._table.setColumnWidth(3, 200)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        bot_lay.addWidget(self._table, 1)

        spl.addWidget(bot)
        spl.setSizes([280, 400])
        root.addWidget(spl, 1)

    # ── Helpers ──────────────────────────────────────────────
    def _mk_btn(self, text: str, color: str, w: int = 120, h: int = 36) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(w, h)
        b.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{color};color:#FFFFFF;"
            f"border:none;border-radius:6px;padding:4px 12px;}}"
            f"QPushButton:hover{{background:{color}CC;}}"
            f"QPushButton:disabled{{background:#555555;color:#888888;}}"
            f"QPushButton:checked{{background:{color};border:2px solid #FFFFFF;}}"
        )
        return b

    def _row_to_preview(self, row: dict) -> tuple[str, str, str, str, str]:
        """Extrait 5 colonnes de preview depuis un row."""
        ts  = row.get("timestamp", "")
        src = row.get("source", "?")
        if src == "motor":
            k1 = f"state={row.get('state','?')}"
            k2 = f"front={row.get('front','?')}  rear={row.get('rear','?')}"
            k3 = f"cur={row.get('current','?')} A  rest={row.get('rest_contact','?')}"
        elif src == "lin":
            k1 = f"type={row.get('lin_type','?')}  pid={row.get('pid','?')}"
            k2 = f"op={row.get('op','?')}  wiper_op={row.get('wiper_op','?')}"
            k3 = f"fault={row.get('fault','?')}"
        elif src == "can":
            k1 = f"id={row.get('can_id','?')}  dir={row.get('direction','?')}"
            k2 = f"dlc={row.get('dlc','?')}  payload={str(row.get('payload',''))[:24]}"
            k3 = ""
        elif src == "pump":
            k1 = f"state={row.get('state','?')}  dir={row.get('direction','?')}"
            k2 = f"flow={row.get('flow','?')}  pres={row.get('pressure','?')}"
            k3 = f"cur={row.get('current','?')} A"
        else:
            k1 = str(row)[:60]
            k2 = k3 = ""
        return ts, src.upper(), k1, k2, k3

    def _add_preview_row(self, row: dict):
        ts, src, k1, k2, k3 = self._row_to_preview(row)
        col = _SRC_COLORS.get(row.get("source", ""), W_TEXT_DIM)

        r = self._table.rowCount()
        self._table.insertRow(r)

        for ci, val in enumerate((ts, src, k1, k2, k3)):
            item = QTableWidgetItem(str(val))
            item.setForeground(QColor(col))
            self._table.setItem(r, ci, item)

        # Garder au max _PREVIEW_MAX lignes
        while self._table.rowCount() > _PREVIEW_MAX:
            self._table.removeRow(0)

        # Auto-scroll vers le bas
        self._table.scrollToBottom()
        self._lbl_preview_count.setText(f"{self._table.rowCount()} lignes visibles")

    # ── Slots ────────────────────────────────────────────────
    def _on_rec(self):
        self._rec.start()
        self._btn_rec.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_rec.setStyleSheet(
            self._btn_rec.styleSheet() +
            f"QPushButton{{background:#8B0000;}}"
        )
        # Allumer les LEDs
        for src, led in self._src_leds.items():
            if self._src_checks[src].isChecked():
                led.set_state(True, _SRC_COLORS[src])

    def _on_stop(self):
        self._rec.stop()
        self._btn_rec.setEnabled(True)
        self._btn_stop.setEnabled(False)
        # Éteindre les LEDs
        for src, led in self._src_leds.items():
            led.set_state(False, _SRC_COLORS[src])

    def _on_clear(self):
        self._rec.clear()
        self._table.setRowCount(0)
        self._preview_rows.clear()
        self._lbl_total.setText("0 lignes")
        self._lbl_preview_count.setText("0 lignes visibles")
        self._prog.setValue(0)
        self._lbl_buf.setText(f"0 / {MAX_BUFFER:,}")
        for cnt in self._src_counts.values():
            cnt.setText("0")
        self._lbl_export_status.setText("")

    def _on_pause_preview(self, paused: bool):
        self._paused = paused
        self._btn_pause.setText("Resume Preview" if paused else "Pause Preview")

    def _on_row_added(self, row: dict):
        if not self._paused:
            self._add_preview_row(row)

    def _on_export(self):
        """Export CSV selon le filtre choisi."""
        if self._rec.row_count() == 0:
            self._lbl_export_status.setText("[!] Buffer vide — rien à exporter.")
            self._lbl_export_status.setStyleSheet(f"color:{A_ORANGE};background:transparent;")
            return

        choice = self._exp_filter.currentIndex()
        ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        if choice == 5:  # Par source
            folder = QFileDialog.getExistingDirectory(
                self, "Choisir le dossier d'export", os.path.expanduser("~"))
            if not folder:
                return
            results = self._rec.export_per_source(folder, f"wipewash_{ts_str}")
            lines = [f"{src}: {n} lignes" for src, n in results.items()]
            total = sum(results.values())
            self._lbl_export_status.setText(
                f"[OK] {total} lignes → {len(results)} fichiers\n" + "  |  ".join(lines))
            self._lbl_export_status.setStyleSheet(f"color:{A_GREEN};background:transparent;")
        else:
            src_map = {0: "all", 1: "motor", 2: "lin", 3: "can", 4: "pump"}
            src = src_map.get(choice, "all")
            label = src if src != "all" else "merged"
            path, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer CSV",
                os.path.join(os.path.expanduser("~"), f"wipewash_{ts_str}_{label}.csv"),
                "CSV Files (*.csv)",
            )
            if not path:
                return
            n = self._rec.export_csv(path, src)
            if n:
                self._lbl_export_status.setText(f"[OK] {n:,} lignes exportées → {os.path.basename(path)}")
                self._lbl_export_status.setStyleSheet(f"color:{A_GREEN};background:transparent;")
            else:
                self._lbl_export_status.setText("[!] Aucune donnée pour ce filtre.")
                self._lbl_export_status.setStyleSheet(f"color:{A_ORANGE};background:transparent;")

    def _on_export_mdf(self):
        """Export MDF4 de toutes les données enregistrées."""
        if self._rec.row_count() == 0:
            self._lbl_export_status.setText("[!] Buffer vide — rien à exporter.")
            self._lbl_export_status.setStyleSheet(f"color:{A_ORANGE};background:transparent;")
            return

        folder = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier MDF4", os.path.expanduser("~"))
        if not folder:
            return

        try:
            exp = MDFExporter(bench_id="WipeWash-Bench",
                              project="WipeWash Automotive HIL")
            ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = exp.export(self._rec,
                              output_dir=folder,
                              base_name=f"wipewash_{ts_str}")
            if path:
                self._lbl_export_status.setText(
                    f"[OK] MDF4 exporté → {os.path.basename(path)}")
                self._lbl_export_status.setStyleSheet(
                    f"color:{A_GREEN};background:transparent;")
            else:
                self._lbl_export_status.setText("[!] Export MDF4 échoué.")
                self._lbl_export_status.setStyleSheet(
                    f"color:{A_ORANGE};background:transparent;")
        except Exception as e:
            self._lbl_export_status.setText(f"❌ Erreur MDF4 : {e}")
            self._lbl_export_status.setStyleSheet(
                f"color:#B71C1C;background:transparent;")

    def _refresh_stats(self):
        """Rafraîchit les compteurs et le chrono toutes les 500 ms."""
        rows = self._rec.get_rows()
        total = len(rows)

        # Compteurs par source
        counts = {"motor": 0, "lin": 0, "can": 0, "pump": 0}
        for r in rows:
            s = r.get("source", "")
            if s in counts:
                counts[s] += 1

        for src, cnt in self._src_counts.items():
            cnt.setText(f"{counts[src]:,}")

        self._lbl_total.setText(f"{total:,} lignes")
        self._prog.setValue(min(total, MAX_BUFFER))
        self._lbl_buf.setText(f"{total:,} / {MAX_BUFFER:,}")

        # Chrono
        if self._rec.is_active():
            e = self._rec.elapsed()
            h  = int(e // 3600)
            m  = int((e % 3600) // 60)
            s  = int(e % 60)
            self._lbl_elapsed.setText(f"{h:02d}:{m:02d}:{s:02d}")
            self._lbl_elapsed.setStyleSheet(f"color:{A_RED};background:transparent;font-weight:bold;")
        else:
            self._lbl_elapsed.setStyleSheet(f"color:{A_TEAL};background:transparent;font-weight:bold;")

    # ── Slots publics (connectés depuis MainWindow) ───────────
    def on_motor_data(self, data: dict):
        self._rec.push_motor(data)

    def on_lin_event(self, ev: dict):
        self._rec.push_lin(ev)

    def on_can_event(self, ev: dict):
        self._rec.push_can(ev)

    def on_pump_data(self, data: dict):
        self._rec.push_pump(data)
