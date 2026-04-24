"""
xcp_panel.py  —  XCP Panel UI (Platform WipeWash)
==================================================
Panneau de calibration XCP-like intégré dans le QTabWidget principal.

NOUVEAUTÉS v2 — 3 fonctionnalités ControlDesk-inspired

  1. DIRTY INDICATOR
     Chaque carte détecte quand spinbox != valeur BCM live -> badge
     amber animé "PENDING" + bouton DOWNLOAD passe amber.
     Compteur global dans la barre d'actions.

  2. SAVE / LOAD PROFIL JSON
     Snapshot nommé de toutes les valeurs courantes -> fichier JSON.
     Load : recharge + DOWNLOAD batch vers le BCM.
     Historique des 5 derniers profils dans un QComboBox.

  3. MINI-OSCILLOSCOPE intégré
     Splitter droite du scroll : oscilloscope multi-courbes alimenté
     par les polls SHORT_UPLOAD (500ms).
     Sélection des paramètres à tracer via checkboxes dans les cartes.
     Palette de couleurs dédiée, curseur de temps, légende live.

ARCHITECTURE — toujours 1 seule page XCP

  ┌───────────────────────────────────────────────────────────────┐
  │  HEADER DARK  —  XCP CALIBRATION · BCM LIVE                  │
  ├───────────────────────────────────────────────────────────────┤
  │  BARRE ACTIONS                                                │
  │  [filtre] [cat] [Apply N pending] [Save] [Load]              │
  ├───────────────────┬───────────────────────────────────────────┤
  │                   │  OSCILLOSCOPE                             │
  │  CARTES           │  (multi-courbes, légende, time window)    │
  │  PARAMETRES       │                                           │
  │  (scroll)         ├───────────────────────────────────────────┤
  │                   │  SELECTED PARAMS — checkboxes             │
  ├───────────────────┴───────────────────────────────────────────┤
  │  XCP LOG  (bas)                                               │
  └───────────────────────────────────────────────────────────────┘

INTÉGRATION dans main_window.py  — IDENTIQUE À AVANT (0 modif)

    from xcp_panel import XCPPanel
    self._xcp_panel = XCPPanel()
    self._tabs.addTab(self._xcp_panel, "XCP Calibration")
    ...
    if hasattr(self, '_xcp_panel'):
        self._xcp_panel.set_host(host)

"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from typing import Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QFrame, QScrollArea,
    QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit,
    QSizePolicy, QMessageBox, QFileDialog, QCheckBox,
    QToolButton,
)
from PySide6.QtCore import Qt, Signal, QTimer, QObject, QPointF
from PySide6.QtGui  import (
    QColor, QFont, QPainter, QPen, QBrush,
    QPainterPath, QLinearGradient,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_TOOLBAR, W_TITLEBAR,
    W_BORDER, W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    KPIT_GREEN, KPIT_GREEN, KPIT_GREEN, KPIT_GREEN,
)
from xcp_master import XCPMaster, XCPError

# Palette locale
_C_BG      = W_BG
_C_SURF    = W_PANEL
_C_TEXT    = W_TEXT
_C_DIM     = W_TEXT_DIM
_C_GREEN   = "#2E7003"
_C_GREEN   = KPIT_GREEN
_C_GREEN     = KPIT_GREEN
_C_GREEN    = KPIT_GREEN
_C_KPIT    = KPIT_GREEN

# Couleur par catégorie
_CAT = {
    "TIMING":     {"hdr": "#2E7003", "bg": "#E8F5E0", "plot": "#2E7003"},
    "PUMP":       {"hdr": "#1A4A0A", "bg": "#E8F5E0", "plot": "#2E7003"},
    "WASH":       {"hdr": "#2E7003", "bg": "#E8F5E0", "plot": "#2E7003"},
    "RAIN":       {"hdr": "#2E7003", "bg": "#E8F5E0", "plot": "#2E7003"},
    "PROTECTION": {"hdr": "#2E7003", "bg": "#E8F5E0", "plot": "#2E7003"},
    "WATCHDOG":   {"hdr": "#2E7003", "bg": "#E8F5E0", "plot": "#2E7003"},
}
_CAT_DEFAULT = _CAT["TIMING"]

# Couleurs oscilloscope (une par paramètre, jusqu'à 14)
_PLOT_PALETTE = [
    "#2E7003", "#2E7003", "#2E7003", "#2E7003", "#2E7003",
    "#2E7003", "#2E7003", "#4CAF50", "#2E7003", "#2E7003",
    "#2E7003", "#2E7003", "#8DC63F", "#4CAF50",
]

# Mapping catégories (fallback statique)
_PARAM_CAT_STATIC = {
    "TOUCH_DURATION":          "TIMING",
    "PARK_TIMEOUT":            "TIMING",
    "REVERSE_REAR_PERIOD":     "TIMING",
    "WIPE_CYCLE_DURATION":     "TIMING",
    "PUMP_MAX_RUNTIME":        "PUMP",
    "WASH_FRONT_CYCLES":       "WASH",
    "WASH_REAR_CYCLES":        "WASH",
    "RAIN_SPEED2_THRESH":      "RAIN",
    "OVERCURRENT_THRESH":      "PROTECTION",
    "OVERCURRENT_DELAY":       "PROTECTION",
    "PUMP_OVERCURRENT_THRESH": "PROTECTION",
    "PUMP_OVERCURRENT_DELAY":  "PROTECTION",
    "REST_STUCK_DELAY":        "PROTECTION",
    "WATCHDOG_MAX_MS":         "WATCHDOG",
}

try:
    from xcp_master import _LOCAL_A2L as _A2L_FOR_CAT
except Exception:
    _A2L_FOR_CAT = None

if _A2L_FOR_CAT:
    _PARAM_CAT: dict[str, str] = {
        k: v.get("category", "TIMING") for k, v in _A2L_FOR_CAT.items()
    }
else:
    _PARAM_CAT = _PARAM_CAT_STATIC


# XCPBridge — pont thread-safe -> signaux Qt
class XCPBridge(QObject):
    response_received = Signal(str, str, object, object)
    get_a2l_err       = Signal(str)
    download_ok       = Signal(str, object)
    download_err      = Signal(str, str)
    log_msg           = Signal(str, bool)
    poll_values       = Signal(object)


# DeltaArc — jauge arc déviation
class DeltaArc(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct   = 0.0
        self._color = _C_GREEN
        self.setFixedSize(40, 40)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_deviation(self, default_val, current_val, warn_pct=25):
        if default_val is None or current_val is None or default_val == 0:
            self._pct = 0.0; self._color = _C_GREEN
        else:
            ratio = (current_val - default_val) / abs(default_val)
            self._pct = max(-1.0, min(1.0, ratio))
            thresh    = warn_pct / 100
            if abs(self._pct) < thresh * 0.5: self._color = _C_GREEN
            elif abs(self._pct) < thresh:      self._color = _C_GREEN
            else:                              self._color = _C_GREEN
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy, r = W//2, H//2, min(W, H)//2 - 3
        p.setPen(QPen(QColor("#CCCCCC"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx-r, cy-r, r*2, r*2)
        if abs(self._pct) > 0.005:
            p.setPen(QPen(QColor(self._color), 3.5,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(cx-r, cy-r, r*2, r*2, 90*16, int(-self._pct*180*16))
        pct = int(abs(self._pct)*100)
        sgn = "+" if self._pct > 0.005 else ("-" if self._pct < -0.005 else "")
        txt = f"{sgn}{pct}%" if pct > 0 else "DEF"
        p.setPen(QColor(self._color))
        p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter, txt)


# PendingBadge — badge animé "PENDING" (dirty indicator)
class PendingBadge(QLabel):
    """Badge amber pulsant affiché quand spin != BCM live."""

    def __init__(self, parent=None):
        super().__init__(" PENDING ", parent)
        self.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        self._visible_state = False
        self._pulse = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._blink)
        self._timer.setInterval(700)
        self.hide()

    def set_pending(self, on: bool):
        if on == self._visible_state:
            return
        self._visible_state = on
        if on:
            self.show()
            self._timer.start()
        else:
            self._timer.stop()
            self.hide()

    def _blink(self):
        self._pulse = not self._pulse
        alpha = "FF" if self._pulse else "AA"
        self.setStyleSheet(
            f"color:#{alpha}6600; background:#E8F5E0;"
            f"border:1px solid {_C_GREEN}; border-radius:3px; padding:1px 4px;"
        )


# XCPParamCard — carte paramètre (dirty + oscillo checkbox)
class XCPParamCard(QFrame):
    download_requested = Signal(str, object)
    plot_toggled       = Signal(str, bool, str)   # key, checked, plot_color
    dirty_changed      = Signal(str, bool)         # key, is_dirty

    def __init__(self, key: str, meta: dict, plot_color: str = "#2E7003",
                 parent=None):
        super().__init__(parent)
        self._key       = key
        self._meta      = meta
        self._live_val  = meta["default"]
        self._plot_col  = plot_color
        self._is_dirty  = False

        cat   = meta.get("category") or _PARAM_CAT.get(key, "TIMING")
        theme = _CAT.get(cat, _CAT_DEFAULT)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._base_style = (
            f"XCPParamCard {{"
            f"  background:{theme['bg']};"
            f"  border:1px solid #CCC;"
            f"  border-left:4px solid {theme['hdr']};"
            f"  border-radius:4px;"
            f"}}"
        )
        self.setStyleSheet(self._base_style)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)

        # Oscillo checkbox
        self._plot_cb = QCheckBox()
        self._plot_cb.setToolTip("Tracer dans l'oscilloscope")
        self._plot_cb.setFixedSize(18, 18)
        self._plot_cb.setStyleSheet(
            f"QCheckBox::indicator {{ width:14px; height:14px; }}"
            f"QCheckBox::indicator:checked {{ background:{plot_color}; "
            f"border:2px solid {plot_color}; border-radius:2px; }}"
            f"QCheckBox::indicator:unchecked {{ background:#EEE; "
            f"border:1px solid #BBB; border-radius:2px; }}"
        )
        self._plot_cb.stateChanged.connect(
            lambda s: self.plot_toggled.emit(
                self._key, s == Qt.CheckState.Checked.value, self._plot_col
            )
        )
        lay.addWidget(self._plot_cb)

        # Arc gauge
        self._gauge = DeltaArc()
        lay.addWidget(self._gauge)

        # Info colonne
        info = QVBoxLayout(); info.setSpacing(2)

        name_row = QHBoxLayout(); name_row.setSpacing(6)
        name_lbl = QLabel(key.replace("_", " "))
        name_lbl.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color:{theme['hdr']};")
        name_row.addWidget(name_lbl)

        badge = QLabel(f" {cat} ")
        badge.setFont(QFont(FONT_UI, 7))
        badge.setStyleSheet(
            f"background:{theme['hdr']};color:white;"
            f"border-radius:3px;padding:1px 4px;"
        )
        name_row.addWidget(badge)

        # Pending badge (dirty indicator)
        self._pending_badge = PendingBadge()
        name_row.addWidget(self._pending_badge)
        name_row.addStretch()
        info.addLayout(name_row)

        desc = QLabel(meta["desc"])
        desc.setFont(QFont(FONT_UI, 8))
        desc.setStyleSheet(f"color:{_C_DIM};")
        info.addWidget(desc)

        # Status line avec min/max/avg
        self._status = QLabel(
            f"BCM: {meta['default']} {meta['unit']}  |  "
            f"DEF: {meta['default']} {meta['unit']}  |  "
            f"[{meta['min']} ... {meta['max']}]"
        )
        self._status.setFont(QFont(FONT_MONO, 8))
        self._status.setStyleSheet(f"color:{_C_DIM};")
        info.addWidget(self._status)
        lay.addLayout(info, 1)

        # Spinbox
        if meta["type"] == "float":
            self._spin = QDoubleSpinBox()
            self._spin.setDecimals(3)
            self._spin.setSingleStep(meta.get("step", 0.1))
        else:
            self._spin = QSpinBox()
            self._spin.setSingleStep(meta.get("step", 1))

        self._spin.setRange(meta["min"], meta["max"])
        self._spin.setValue(meta["default"])
        self._spin.setSuffix(f" {meta['unit']}")
        self._spin.setFixedWidth(120)
        self._spin.setFont(QFont(FONT_MONO, 10))
        self._spin.setEnabled(False)
        # Dirty detection
        self._spin.valueChanged.connect(self._check_dirty)
        lay.addWidget(self._spin)

        # Bouton DOWNLOAD (devient amber quand dirty)
        self._dl_btn = QPushButton("DOWNLOAD")
        self._dl_btn.setFixedSize(100, 30)
        self._dl_btn.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        self._dl_btn.setEnabled(False)
        self._dl_style_clean = (
            f"QPushButton {{ background:{_C_GREEN};color:white;"
            f"border:none;border-radius:4px; }}"
            f"QPushButton:hover {{ background:#3A8A0A; }}"
            f"QPushButton:disabled {{ background:#AAAAAA; }}"
        )
        self._dl_style_dirty = (
            f"QPushButton {{ background:{_C_GREEN};color:white;"
            f"border:none;border-radius:4px; }}"
            f"QPushButton:hover {{ background:#3A8A0A; }}"
            f"QPushButton:disabled {{ background:#AAAAAA; }}"
        )
        self._dl_btn.setStyleSheet(self._dl_style_clean)
        self._dl_btn.clicked.connect(self._on_download)
        lay.addWidget(self._dl_btn)

        # Reset défaut
        self._reset_btn = QPushButton("R")
        self._reset_btn.setFixedSize(28, 30)
        self._reset_btn.setFont(QFont(FONT_UI, 11))
        self._reset_btn.setEnabled(False)
        self._reset_btn.setToolTip(f"Défaut: {meta['default']} {meta['unit']}")
        self._reset_btn.setStyleSheet(
            f"QPushButton {{ background:{W_TOOLBAR};border:1px solid #BBB;border-radius:4px; }}"
            f"QPushButton:hover {{ background:{_C_GREEN};color:white; }}"
            f"QPushButton:disabled {{ background:#EEE;color:#AAA; }}"
        )
        self._reset_btn.clicked.connect(
            lambda: self._spin.setValue(self._meta["default"]))
        lay.addWidget(self._reset_btn)

    # Dirty detection
    def _check_dirty(self, _=None):
        if not self._spin.isEnabled():
            return
        spin_val = self._spin.value()
        try:
            live = float(self._live_val) if self._meta["type"] == "float" \
                   else int(float(self._live_val))
        except (TypeError, ValueError):
            return
        dirty = abs(spin_val - live) > 1e-9
        if dirty != self._is_dirty:
            self._is_dirty = dirty
            self._pending_badge.set_pending(dirty)
            self._dl_btn.setStyleSheet(
                self._dl_style_dirty if dirty else self._dl_style_clean
            )
            self.dirty_changed.emit(self._key, dirty)

    # API publique
    def set_session_active(self, active: bool = True):
        self._spin.setEnabled(True)
        self._dl_btn.setEnabled(True)
        self._reset_btn.setEnabled(True)

    def update_live(self, val: Any):
        try:
            v = float(val) if self._meta["type"] == "float" else int(float(val))
        except (TypeError, ValueError):
            return
        self._live_val = v
        self._status.setText(
            f"BCM: {v} {self._meta['unit']}  |  "
            f"DEF: {self._meta['default']} {self._meta['unit']}  |  "
            f"[{self._meta['min']} ... {self._meta['max']}]"
        )
        self._gauge.set_deviation(self._meta["default"], v)
        if self._spin.value() == self._meta["default"]:
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
        # Recheck dirty après mise à jour BCM
        self._check_dirty()

    def flash_ack(self):
        orig = self._spin.styleSheet()
        self._spin.setStyleSheet(
            "QSpinBox,QDoubleSpinBox{background:#C8F7C5;"
            "border:1.5px solid #2E7003;border-radius:3px;padding:3px;}")
        QTimer.singleShot(700, lambda: self._spin.setStyleSheet(""))
        # On efface le dirty après confirmation BCM
        self._is_dirty = False
        self._pending_badge.set_pending(False)
        self._dl_btn.setStyleSheet(self._dl_style_clean)
        self.dirty_changed.emit(self._key, False)

    def get_spin_value(self):
        v = self._spin.value()
        return int(v) if self._meta["type"] == "int" else v

    def set_spin_value(self, val):
        self._spin.blockSignals(True)
        self._spin.setValue(val)
        self._spin.blockSignals(False)
        self._check_dirty()

    def is_dirty(self) -> bool:
        return self._is_dirty

    def _on_download(self):
        val = self.get_spin_value()
        self.download_requested.emit(self._key, val)


# XCPOscilloscope — mini-oscilloscope multi-courbes
class XCPOscilloscope(QWidget):
    """
    Mini-oscilloscope alimenté par les polls XCP (500ms).
    Affiche jusqu'à 6 paramètres simultanément.
    Chaque courbe est normalisée sur [min, max] A2L.
    """

    WINDOW_SECS = 60   # fenêtre temporelle visible
    MAX_PTS     = 120  # 60s x 2Hz

    def __init__(self, parent=None):
        super().__init__(parent)
        self._traces: dict[str, dict] = {}   # key -> {color, meta, buf: deque}
        self._t0    = time.time()
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background:{W_TITLEBAR}; border-radius:4px;")

        # Timer repaint 500ms (synchro avec poll BCM)
        self._repaint_timer = QTimer(self)
        self._repaint_timer.timeout.connect(self.update)
        self._repaint_timer.start(500)

    def add_trace(self, key: str, color: str, meta: dict):
        if key not in self._traces:
            self._traces[key] = {
                "color": color,
                "meta":  meta,
                "buf":   deque(maxlen=self.MAX_PTS),
            }

    def remove_trace(self, key: str):
        self._traces.pop(key, None)
        self.update()

    def push_value(self, key: str, val: float):
        if key in self._traces:
            t = time.time() - self._t0
            self._traces[key]["buf"].append((t, val))

    def clear_all(self):
        for tr in self._traces.values():
            tr["buf"].clear()
        self._t0 = time.time()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Fond
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor("#0F1A0A"))
        grad.setColorAt(1.0, QColor("#0A1200"))
        p.fillRect(0, 0, W, H, grad)

        # Marges
        ML, MR, MT, MB = 42, 12, 14, 26

        # Grille
        p.setPen(QPen(QColor("rgba(141,198,63,0.12)"), 0.8))
        for i in range(1, 5):
            y = MT + (H - MT - MB) * i // 4
            p.drawLine(ML, y, W - MR, y)
        for i in range(1, 7):
            x = ML + (W - ML - MR) * i // 6
            p.drawLine(x, MT, x, H - MB)

        now = time.time() - self._t0

        # Axe temps (bas)
        p.setPen(QPen(QColor("#5A6A4A"), 0.8))
        p.setFont(QFont(FONT_MONO, 7))
        for i in range(7):
            x = ML + (W - ML - MR) * i // 6
            t_label = now - self.WINDOW_SECS * (6 - i) / 6
            if t_label >= 0:
                p.drawText(x - 10, H - 6, f"{t_label:.0f}s")

        # Courbes
        if not self._traces:
            p.setPen(QColor("#3A4A30"))
            p.setFont(QFont(FONT_UI, 9))
            p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter,
                       "Cochez un paramètre pour le tracer ici")
            return

        legend_y = MT + 4
        for key, tr in self._traces.items():
            buf   = tr["buf"]
            meta  = tr["meta"]
            color = QColor(tr["color"])

            if len(buf) < 2:
                # Légende seule
                p.setPen(color)
                p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
                p.drawText(ML + 4, legend_y + 10, f"-- {key}")
                legend_y += 14
                continue

            vmin = meta["min"]; vmax = meta["max"]
            vrange = vmax - vmin if vmax != vmin else 1.0

            # Construire path
            path = QPainterPath()
            first = True
            for (t, v) in buf:
                x = ML + (W - ML - MR) * (t - (now - self.WINDOW_SECS)) / self.WINDOW_SECS
                y = MT + (H - MT - MB) * (1.0 - (v - vmin) / vrange)
                x = max(ML, min(W - MR, x))
                y = max(MT, min(H - MB, y))
                if first:
                    path.moveTo(x, y); first = False
                else:
                    path.lineTo(x, y)

            # Glow effect (2 passes)
            glow = QPen(QColor(color.red(), color.green(), color.blue(), 40), 5,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(glow)
            p.drawPath(path)

            main_pen = QPen(color, 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap)
            p.setPen(main_pen)
            p.drawPath(path)

            # Point live (dernière valeur)
            last_t, last_v = buf[-1]
            lx = ML + (W - ML - MR) * (last_t - (now - self.WINDOW_SECS)) / self.WINDOW_SECS
            ly = MT + (H - MT - MB) * (1.0 - (last_v - vmin) / vrange)
            lx = max(ML, min(W - MR, lx))
            ly = max(MT, min(H - MB, ly))
            p.setBrush(QBrush(color))
            p.setPen(QPen(QColor("#0F1A0A"), 1.2))
            p.drawEllipse(QPointF(lx, ly), 4, 4)

            # Légende + valeur live
            p.setPen(color)
            p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
            short = key.replace("_", " ")
            unit  = meta.get("unit", "")
            p.drawText(ML + 4, legend_y + 10,
                       f"-- {short}  {last_v:.2f} {unit}")
            legend_y += 14

        # Bord KPIT
        p.setPen(QPen(QColor("rgba(141,198,63,0.30)"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W-2, H-2, 4, 4)

        # Label "OSCILLOSCOPE"
        p.setPen(QColor(_C_KPIT))
        p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        p.drawText(W - 100, MT + 2, "OSCILLOSCOPE")


# XCPPanel — panneau principal
class XCPPanel(QWidget):
    """
    Panneau XCP complet v2 — intègre dirty indicator, save/load profil, oscillo.
    Interface main_window.py inchangée.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host   = None
        self._master: XCPMaster | None = None
        self._bridge = XCPBridge()
        self._cards: dict[str, XCPParamCard] = {}
        self._a2l:   dict = {}
        self._dirty_keys: set[str] = set()

        # Historique profils
        self._profiles: list[dict] = []   # [{name, values, ts}]

        # Timer poll 500ms
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_all)
        self._poll_timer.setInterval(500)

        # Connexions bridge
        self._bridge.response_received.connect(self._on_response)
        self._bridge.get_a2l_err.connect(
            lambda e: self._log(f"GET_A2L échoué: {e}", error=True))
        self._bridge.download_ok.connect(self._on_download_ok)
        self._bridge.download_err.connect(
            lambda key, e: self._log(f"DOWNLOAD {key} échoué: {e}", error=True))
        self._bridge.log_msg.connect(self._log)
        self._bridge.poll_values.connect(self._apply_poll)

        self._build_ui()
        self._poll_timer.start()

    # Construction UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header dark
        hdr = QFrame()
        hdr.setFixedHeight(40)
        hdr.setStyleSheet(f"background:{W_TITLEBAR};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 0, 12, 0)

        ttl = QLabel("XCP CALIBRATION  —  PARAMETRES BCM LIVE")
        ttl.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{W_TEXT_HDR};")
        hdr_lay.addWidget(ttl)
        hdr_lay.addStretch()

        # Badge connexion Redis
        self._conn_badge = QLabel("  O OFFLINE  ")
        self._conn_badge.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        self._conn_badge.setStyleSheet(
            f"color:#AAA; background:#1E2A18; border:1px solid #333;"
            f"border-radius:3px; padding:1px 6px;"
        )
        hdr_lay.addWidget(self._conn_badge)
        root.addWidget(hdr)

        # Barre actions
        act_bar = QFrame()
        act_bar.setFixedHeight(44)
        act_bar.setStyleSheet(
            f"background:{W_TOOLBAR};border-bottom:1px solid #CCC;")
        act_lay = QHBoxLayout(act_bar)
        act_lay.setContentsMargins(12, 0, 12, 0)
        act_lay.setSpacing(8)

        # Filtre texte
        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher...")
        self._search.setFixedWidth(150)
        self._search.setFixedHeight(28)
        self._search.setFont(QFont(FONT_UI, 8))
        self._search.setStyleSheet(
            f"QLineEdit {{ background:#fff; border:1px solid #BBB;"
            f"border-radius:4px; padding:0 8px; }}"
        )
        self._search.textChanged.connect(self._apply_filter)
        act_lay.addWidget(self._search)

        # Filtre catégorie
        self._cat_filter = QComboBox()
        self._cat_filter.addItem("Toutes catégories")
        for cat in _CAT:
            self._cat_filter.addItem(cat)
        self._cat_filter.setFixedWidth(140)
        self._cat_filter.setFixedHeight(28)
        self._cat_filter.setFont(QFont(FONT_UI, 8))
        self._cat_filter.setStyleSheet(
            f"QComboBox {{ background:#fff; border:1px solid #BBB;"
            f"border-radius:4px; padding:0 6px; }}"
        )
        self._cat_filter.currentIndexChanged.connect(self._apply_filter)
        act_lay.addWidget(self._cat_filter)

        act_lay.addStretch()

        # Apply pending
        self._apply_btn = QPushButton("Apply  0  pending")
        self._apply_btn.setFixedHeight(28)
        self._apply_btn.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            f"QPushButton {{ background:#CCCCCC; color:#888;"
            f"border:none; border-radius:4px; padding:0 12px; }}"
        )
        self._apply_btn.clicked.connect(self._on_apply_all)
        act_lay.addWidget(self._apply_btn)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color:#CCC;"); sep.setFixedWidth(1)
        act_lay.addWidget(sep)

        # Save profil
        self._save_btn = QPushButton("Sauvegarder")
        self._save_btn.setFixedHeight(28)
        self._save_btn.setFont(QFont(FONT_UI, 8))
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background:{_C_GREEN};color:white;"
            f"border:none;border-radius:4px;padding:0 10px; }}"
            f"QPushButton:hover {{ background:#3A8A0A; }}"
        )
        self._save_btn.clicked.connect(self._on_save_profile)
        act_lay.addWidget(self._save_btn)

        # Load profil
        self._load_combo = QComboBox()
        self._load_combo.addItem("Charger profil...")
        self._load_combo.setFixedWidth(170)
        self._load_combo.setFixedHeight(28)
        self._load_combo.setFont(QFont(FONT_UI, 8))
        self._load_combo.setStyleSheet(
            f"QComboBox {{ background:{W_PANEL2}; border:1px solid #BBB;"
            f"border-radius:4px; padding:0 6px; }}"
        )
        self._load_combo.activated.connect(self._on_load_profile)
        act_lay.addWidget(self._load_combo)

        # Reset all
        self._reset_all_btn = QPushButton("Defauts")
        self._reset_all_btn.setFixedHeight(28)
        self._reset_all_btn.setFont(QFont(FONT_UI, 8))
        self._reset_all_btn.setStyleSheet(
            f"QPushButton {{ background:{_C_GREEN};color:white;"
            f"border:none;border-radius:4px;padding:0 10px; }}"
            f"QPushButton:hover {{ background:#3A8A0A; }}"
        )
        self._reset_all_btn.clicked.connect(self._on_reset_all)
        act_lay.addWidget(self._reset_all_btn)

        root.addWidget(act_bar)

        # Zone centrale : splitter H (cartes | oscillo)
        center_split = QSplitter(Qt.Orientation.Horizontal)
        center_split.setStyleSheet("QSplitter::handle{background:#DDD; width:3px;}")

        # Colonne gauche : scroll cartes
        scroll_w = QWidget()
        scroll_w.setStyleSheet(f"background:{_C_BG};")
        self._cards_lay = QVBoxLayout(scroll_w)
        self._cards_lay.setContentsMargins(10, 8, 10, 8)
        self._cards_lay.setSpacing(5)

        self._no_session_lbl = QLabel("En attente de connexion Redis au BCM...")
        self._no_session_lbl.setFont(QFont(FONT_UI, 11))
        self._no_session_lbl.setStyleSheet(f"color:{_C_DIM};")
        self._no_session_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cards_lay.addWidget(self._no_session_lbl)
        self._cards_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_w)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        center_split.addWidget(scroll)

        # Colonne droite : oscilloscope + légende
        right_w = QWidget()
        right_w.setStyleSheet(f"background:{W_TITLEBAR}; border-radius:4px;")
        right_w.setMinimumWidth(280)
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(6, 6, 6, 6)
        right_lay.setSpacing(4)

        # Header oscillo
        osc_hdr = QHBoxLayout()
        osc_lbl = QLabel("  OSCILLOSCOPE  XCP")
        osc_lbl.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        osc_lbl.setStyleSheet(f"color:{_C_KPIT};")
        osc_hdr.addWidget(osc_lbl)
        osc_hdr.addStretch()

        clr_btn = QToolButton()
        clr_btn.setText("Clear")
        clr_btn.setFont(QFont(FONT_UI, 7))
        clr_btn.setStyleSheet(
            f"QToolButton {{ color:#8DC63F; background:transparent;"
            f"border:1px solid #333; border-radius:3px; padding:2px 5px; }}"
            f"QToolButton:hover {{ background:#1E2A18; }}"
        )
        clr_btn.clicked.connect(self._on_osc_clear)
        osc_hdr.addWidget(clr_btn)
        right_lay.addLayout(osc_hdr)

        # Widget oscilloscope
        self._osc = XCPOscilloscope()
        right_lay.addWidget(self._osc, 1)

        # Hint
        hint = QLabel("Cochez sur une carte pour tracer le paramètre")
        hint.setFont(QFont(FONT_UI, 7))
        hint.setStyleSheet(f"color:#3A4A30; padding:2px 4px;")
        hint.setWordWrap(True)
        right_lay.addWidget(hint)

        center_split.addWidget(right_w)
        center_split.setSizes([560, 300])
        center_split.setStretchFactor(0, 2)
        center_split.setStretchFactor(1, 1)

        root.addWidget(center_split, 1)

        # Log bas
        log_frame = QFrame()
        log_frame.setFixedHeight(72)
        log_frame.setStyleSheet(f"background:{W_TITLEBAR};")
        log_lay = QVBoxLayout(log_frame)
        log_lay.setContentsMargins(10, 4, 10, 4)

        log_hdr = QLabel("  XCP LOG")
        log_hdr.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        log_hdr.setStyleSheet(f"color:{_C_KPIT};")
        log_lay.addWidget(log_hdr)

        self._log_lbl = QLabel("En attente de connexion...")
        self._log_lbl.setFont(QFont(FONT_MONO, 8))
        self._log_lbl.setStyleSheet("color:#AAAAAA;")
        self._log_lbl.setWordWrap(True)
        log_lay.addWidget(self._log_lbl)

        root.addWidget(log_frame)

    # Construction cartes
    def _build_cards(self, a2l: dict):
        self._a2l = a2l

        while self._cards_lay.count():
            item = self._cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._cards.clear()
        self._dirty_keys.clear()

        # Grouper par catégorie
        groups: dict[str, list] = {}
        for key in a2l:
            cat = a2l[key].get("category") or _PARAM_CAT.get(key, "TIMING")
            groups.setdefault(cat, []).append(key)

        # Couleur oscillo par index global
        color_idx = 0
        for cat, keys in groups.items():
            theme = _CAT.get(cat, _CAT_DEFAULT)
            grp_hdr = QLabel(f"  {cat}")
            grp_hdr.setFixedHeight(22)
            grp_hdr.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            grp_hdr.setStyleSheet(
                f"background:{theme['hdr']};color:white;"
                f"border-radius:3px;padding-left:6px;"
            )
            self._cards_lay.addWidget(grp_hdr)

            for key in keys:
                plot_col = _PLOT_PALETTE[color_idx % len(_PLOT_PALETTE)]
                color_idx += 1
                card = XCPParamCard(key, a2l[key], plot_color=plot_col)
                card.download_requested.connect(self._on_download)
                card.plot_toggled.connect(self._on_plot_toggle)
                card.dirty_changed.connect(self._on_dirty_changed)
                self._cards[key] = card
                self._cards_lay.addWidget(card)

        self._cards_lay.addStretch()

        # Mettre à jour le conn badge
        self._conn_badge.setText(f"  LIVE  {len(a2l)} params  ")
        self._conn_badge.setStyleSheet(
            f"color:#39FF14; background:#0A1200; border:1px solid #2E7003;"
            f"border-radius:3px; padding:1px 6px;"
        )

    # Feature 1 : Dirty indicator
    def _on_dirty_changed(self, key: str, dirty: bool):
        if dirty:
            self._dirty_keys.add(key)
        else:
            self._dirty_keys.discard(key)
        n = len(self._dirty_keys)
        if n > 0:
            self._apply_btn.setText(f"Apply  {n}  pending")
            self._apply_btn.setEnabled(True)
            self._apply_btn.setStyleSheet(
                f"QPushButton {{ background:{_C_GREEN};color:white;"
                f"border:none;border-radius:4px;padding:0 12px; }}"
                f"QPushButton:hover {{ background:#3A8A0A; }}"
            )
        else:
            self._apply_btn.setText("Apply  0  pending")
            self._apply_btn.setEnabled(False)
            self._apply_btn.setStyleSheet(
                f"QPushButton {{ background:#CCCCCC;color:#888;"
                f"border:none;border-radius:4px;padding:0 12px; }}"
            )

    def _on_apply_all(self):
        """Envoie en batch tous les DOWNLOAD pending."""
        dirty = list(self._dirty_keys)
        if not dirty:
            return
        reply = QMessageBox.question(
            self, "Apply All Pending",
            f"Appliquer {len(dirty)} paramètre(s) modifié(s) vers le BCM ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for key in dirty:
            card = self._cards.get(key)
            if card and self._master:
                val = card.get_spin_value()
                self._on_download(key, val)
        self._log(f"Apply All — {len(dirty)} DOWNLOAD envoyés")

    # Feature 2 : Save / Load profil
    def _on_save_profile(self):
        """Snapshot des valeurs spinbox courantes -> fichier JSON."""
        if not self._cards:
            self._log("Aucun paramètre chargé", error=True)
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Sauvegarder profil XCP", "",
            "JSON Calibration (*.json)"
        )
        if not path:
            return

        snapshot = {}
        for key, card in self._cards.items():
            snapshot[key] = card.get_spin_value()

        profile = {
            "version":   "wipewash_xcp_v1",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host":      self._host or "unknown",
            "values":    snapshot,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
            name = os.path.basename(path)
            self._log(f"Profil sauvegardé : {name}  ({len(snapshot)} params)")
            self._add_profile_to_history(name, path, snapshot)
        except Exception as e:
            self._log(f"Sauvegarde échouée : {e}", error=True)

    def _add_profile_to_history(self, name: str, path: str, values: dict):
        """Ajoute au QComboBox de chargement (5 max)."""
        # Eviter les doublons
        for i in range(1, self._load_combo.count()):
            if self._load_combo.itemData(i) == path:
                return
        self._load_combo.addItem(f"  {name}", userData=path)
        # Limiter à 6 entrées (1 placeholder + 5 profils)
        while self._load_combo.count() > 6:
            self._load_combo.removeItem(1)

    def _on_load_profile(self, idx: int):
        """Charge un profil JSON et applique les valeurs (+ DOWNLOAD batch optionnel)."""
        if idx == 0:
            return  # placeholder sélectionné

        path = self._load_combo.itemData(idx)
        if not path:
            # Demander un fichier
            path, _ = QFileDialog.getOpenFileName(
                self, "Charger profil XCP", "",
                "JSON Calibration (*.json)"
            )
            if not path:
                self._load_combo.setCurrentIndex(0)
                return

        try:
            with open(path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except Exception as e:
            self._log(f"Lecture profil échouée : {e}", error=True)
            self._load_combo.setCurrentIndex(0)
            return

        values = profile.get("values", {})
        n_applied = 0
        for key, val in values.items():
            card = self._cards.get(key)
            if card:
                card.set_spin_value(val)
                n_applied += 1

        self._log(
            f"Profil chargé : {os.path.basename(path)}  "
            f"({n_applied}/{len(values)} params appliqués)"
        )

        # Proposer d'envoyer vers le BCM
        reply = QMessageBox.question(
            self, "Envoyer vers BCM ?",
            f"Profil chargé ({n_applied} paramètres).\n"
            f"Envoyer maintenant vers le BCM (DOWNLOAD batch) ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for key, val in values.items():
                if key in self._cards and self._master:
                    self._on_download(key, val)
            self._log(f"DOWNLOAD batch — {n_applied} paramètres envoyés au BCM")

        self._load_combo.setCurrentIndex(0)

    # Feature 3 : Oscilloscope toggle + push
    def _on_plot_toggle(self, key: str, checked: bool, color: str):
        if checked:
            meta = self._a2l.get(key, {})
            self._osc.add_trace(key, color, meta)
            self._log(f"Oscillo : + {key}")
        else:
            self._osc.remove_trace(key)
            self._log(f"Oscillo : - {key}")

    def _on_osc_clear(self):
        self._osc.clear_all()
        self._log("Oscilloscope réinitialisé")

    # Filtre recherche / catégorie
    def _apply_filter(self):
        text = self._search.text().lower().strip()
        cat_filter = self._cat_filter.currentText()
        if cat_filter == "Toutes catégories":
            cat_filter = ""

        for i in range(self._cards_lay.count()):
            item = self._cards_lay.itemAt(i)
            if not item or not item.widget():
                continue
            w = item.widget()

            # Headers catégorie
            if isinstance(w, QLabel):
                hdr_cat = w.text().strip()
                if cat_filter:
                    w.setVisible(hdr_cat == cat_filter)
                else:
                    w.setVisible(True)
                continue

            # Cartes
            if isinstance(w, XCPParamCard):
                key = w._key
                meta = self._a2l.get(key, {})
                card_cat = meta.get("category") or _PARAM_CAT.get(key, "")
                match_text = text in key.lower() or text in meta.get("desc", "").lower()
                match_cat  = (not cat_filter) or (card_cat == cat_filter)
                w.setVisible(match_text and match_cat)

    # Download / Poll
    def _on_download(self, key: str, value: Any):
        if self._master is None:
            return

        def _do():
            try:
                self._master.download(key, value)
                self._bridge.download_ok.emit(key, value)
            except XCPError as e:
                self._bridge.download_err.emit(key, str(e))

        threading.Thread(target=_do, daemon=True).start()
        self._log(f"DOWNLOAD {key} = {value}")

    def _on_download_ok(self, key: str, value: Any):
        card = self._cards.get(key)
        if card:
            card.flash_ack()
        self._log(f"DOWNLOAD {key} = {value}  ok BCM")

    def _poll_all(self):
        if self._master is None:
            return

        def _do():
            try:
                status = self._master.get_status()
                vals   = status.get("current_values", {})
                self._bridge.poll_values.emit(vals)
            except XCPError:
                pass

        threading.Thread(target=_do, daemon=True).start()

    def _apply_poll(self, vals: dict):
        if vals.get("__a2l__"):
            a2l = {k: v for k, v in vals.items() if k != "__a2l__"}
            self._a2l = a2l
            self._build_cards(a2l)
            for card in self._cards.values():
                card.set_session_active(True)
            return
        for key, val in vals.items():
            card = self._cards.get(key)
            if card and val is not None:
                card.update_live(val)
            # Push vers oscilloscope
            self._osc.push_value(key, float(val) if val is not None else 0)

    # Reset all
    def _on_reset_all(self):
        reply = QMessageBox.question(
            self, "Reset All",
            "Remettre TOUS les paramètres BCM aux valeurs A2L par défaut ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def _do():
            try:
                results = self._master.restore_all_defaults()
                n_ok    = sum(1 for ok in results.values() if ok)
                self._bridge.log_msg.emit(
                    f"RESET ALL — {n_ok}/{len(results)} paramètres remis à défaut", False)
            except XCPError as e:
                self._bridge.log_msg.emit(f"RESET ALL échoué: {e}", True)

        threading.Thread(target=_do, daemon=True).start()

    # Response handler / Log
    def _on_response(self, cmd: str, status: str,
                     data: object, error: object):
        pass

    def _log(self, msg: str, error: bool = False):
        ts  = time.strftime("%H:%M:%S")
        col = _C_GREEN if error else "#AAAAAA"
        self._log_lbl.setStyleSheet(f"color:{col};")
        self._log_lbl.setText(f"[{ts}]  {msg}")

    # API publique — INCHANGEE
    def set_host(self, host: str):
        """
        Appelé depuis main_window quand la connexion BCM est établie.
        Interface identique à v1.
        """
        if self._host == host and self._master is not None:
            return

        self._host = host

        def _on_resp(cmd, status, data, error):
            self._bridge.response_received.emit(cmd, status or "", data, error)

        self._master = XCPMaster(host, on_response=_on_resp)
        self._log(f"BCM host: {host} — chargement A2L...")

        def _load():
            try:
                a2l = self._master.get_a2l()
                self._bridge.log_msg.emit(
                    f"BCM {host} — {len(a2l)} paramètres XCP prêts", False)
                self._bridge.poll_values.emit({"__a2l__": True, **a2l})
                try:
                    status = self._master.get_status()
                    live   = status.get("current_values", {})
                    if live:
                        self._bridge.poll_values.emit(live)
                except Exception:
                    pass
            except Exception as e:
                self._bridge.get_a2l_err.emit(str(e))

        threading.Thread(target=_load, daemon=True).start()