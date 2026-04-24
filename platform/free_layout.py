"""
WipeWash — Free Layout Engine v3  (ControlDesk-style)
"""

from __future__ import annotations
import json, os, math
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QSplitter,
    QPushButton, QFileDialog, QScrollArea, QSizePolicy,
    QInputDialog, QMessageBox, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QApplication,
)
from PySide6.QtCore import (
    Qt, QPoint, QSize, QMimeData, QByteArray, Signal, QObject,
    QRectF, QPointF,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QPainterPath,
    QMouseEvent, QDrag, QPixmap, QLinearGradient, QRadialGradient,
    QAction,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2,
    W_TEXT, W_TEXT_DIM, W_DOCK_HDR, W_TEXT_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    KPIT_GREEN,
)

_SNAP  = 8
_MIN_W = 150
_MIN_H = 120
_GRIP  = 10

DEFAULT_LAYOUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "motor_pump_layout.sdf"
)

MIME_INSTRUMENT = "application/x-wipewash-instrument"

def _snap(v: int) -> int:
    return round(v / _SNAP) * _SNAP


# ═══════════════════════════════════════════════════════════
#  SDF LAYOUT — Lecture / Écriture
#  Format : SDF ASCII dSPACE adapté à la configuration de layout
#
#  Structure du fichier :
#    [FILEINFO]           métadonnées
#    [PANEL:<idx>]        un bloc par instrument positionné
#    [END]
# ═══════════════════════════════════════════════════════════

_SDF_LAYOUT_VERSION = "3.0"
_SDF_COMPONENT      = "MotorPump_Page"
_SDF_PROJECT        = "WipeWash_BCM_HIL"


def _sdf_write(path: str, profile: str, locked: bool, panels: list[dict]) -> None:
    """Sérialise la configuration de layout dans un fichier SDF ASCII."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines += [
        "[FILEINFO]",
        f"VERSION={_SDF_LAYOUT_VERSION}",
        f"PROJECT={_SDF_PROJECT}",
        f"COMPONENT={_SDF_COMPONENT}",
        f"PROFILE={profile}",
        f"TIMESTAMP={now_str}",
        f"LOCKED={'true' if locked else 'false'}",
        f"PANEL_COUNT={len(panels)}",
        "",
    ]

    for idx, p in enumerate(panels):
        lines += [
            f"[PANEL:{idx:04d}]",
            f"INSTRUMENT_ID={p.get('instrument_id', '')}",
            f"X={p.get('x', 0)}",
            f"Y={p.get('y', 0)}",
            f"W={p.get('w', 200)}",
            f"H={p.get('h', 160)}",
            "",
        ]

    lines.append("[END]")

    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines))


def _sdf_read(path: str) -> dict:
    """
    Lit un fichier de configuration SDF ou JSON (rétrocompatibilité).
    Retourne un dict avec les clés : _profile, _locked, panels.
    """
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Lecture SDF ASCII
    result = {"_profile": "default", "_locked": False, "panels": []}
    current_panel: dict | None = None

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[PANEL:"):
                if current_panel is not None:
                    result["panels"].append(current_panel)
                current_panel = {}
                continue

            if line == "[FILEINFO]" or line == "[END]":
                if current_panel is not None:
                    result["panels"].append(current_panel)
                    current_panel = None
                continue

            if "=" not in line:
                continue

            key, _, val = line.partition("=")
            key = key.strip().upper()
            val = val.strip()

            if current_panel is None:
                # Bloc FILEINFO
                if key == "PROFILE":
                    result["_profile"] = val
                elif key == "LOCKED":
                    result["_locked"] = val.lower() == "true"
            else:
                # Bloc PANEL
                if key == "INSTRUMENT_ID":
                    current_panel["instrument_id"] = val
                elif key in ("X", "Y", "W", "H"):
                    try:
                        current_panel[key.lower()] = int(val)
                    except ValueError:
                        current_panel[key.lower()] = 0

    return result


# ─── Catalogue ────────────────────────────────────────────────────
INSTRUMENT_CATALOG = [
    ("pump_widget",   "Pump",            "PUMP",   "#007ACC", "Animation pompe"),
    ("pump_current",  "Pump Current",    "PUMP",   "#007ACC", "Courant (A)"),
    ("pump_voltage",  "Pump Voltage",    "PUMP",   "#007ACC", "Tension (V)"),
    ("pump_timeout",  "Timeout FSR_005", "PUMP",   A_AMBER,   "Countdown FSR"),
    ("front_motor",   "Front Motor",     "MOTOR",  A_GREEN,   "Moteur avant"),
    ("front_current", "Front Current",   "MOTOR",  A_GREEN,   "Courant avant (A)"),
    ("rear_motor",    "Rear Motor",      "MOTOR",  A_TEAL,    "Moteur arriere"),
    ("rear_current",  "Rear Current",    "MOTOR",  A_TEAL,    "Courant arriere (A)"),
    ("rest_contact",  "Rest Contact",    "MOTOR",  A_AMBER,   "Position lame"),
    ("sys_status",       "System Status",      "SYSTEM", KPIT_GREEN, "Synthese systeme"),
    ("pump_current_curve",  "Pump Current Curve",  "PUMP",   A_TEAL,    "Courbe courant pompe"),
    ("motor_front_curve",   "Motor Front Curve",   "MOTOR",  A_GREEN,   "Courbe courant moteur avant"),
    ("rest_contact_edge",   "Rest Contact Edges",  "MOTOR",  A_AMBER,   "Fronts montant/descendant contact"),
]
_BY_ID = {r[0]: r for r in INSTRUMENT_CATALOG}

_GROUP_ICON  = {"PUMP": "●", "MOTOR": "●", "SYSTEM": "●"}
_GROUP_COLOR = {"PUMP": "#4FC3F7", "MOTOR": "#8DC63F", "SYSTEM": "#FFB74D"}
_GROUP_BG    = {"PUMP": "#0D1E2E", "MOTOR": "#0D1E0D", "SYSTEM": "#1E1A0A"}


# ═══════════════════════════════════════════════════════════
#  SIGNAL HUB
# ═══════════════════════════════════════════════════════════
class SignalHub(QObject):
    motor_data = Signal(dict)
    pump_data  = Signal(dict)
    lin_data   = Signal(dict)   # événements LIN : rest_contact_raw, front_blade_cycles …

    def __init__(self, parent=None):
        super().__init__(parent)

    def on_motor_data(self, data: dict) -> None:
        self.motor_data.emit(data)

    def on_pump_data(self, data: dict) -> None:
        self.pump_data.emit(data)

    def on_lin_event(self, data: dict) -> None:
        self.lin_data.emit(data)


# ═══════════════════════════════════════════════════════════
#  INSTRUMENT FACTORY + CONNECT
# ═══════════════════════════════════════════════════════════
def _build_widget(iid: str) -> Optional[QWidget]:
    from widgets_instruments import MotorWidget, PumpWidget, ArcGaugeWidget
    from widgets_motor_pump_enhanced import (
        RestContactWidget, SystemStatusWidget, TimeoutFSRWidget,
        CurrentCurveWidget, RestContactEdgeWidget,
    )
    if iid == "pump_widget":    return PumpWidget()
    if iid == "pump_current":   return ArcGaugeWidget(max_val=2.0,  unit="A")
    if iid == "pump_voltage":   return ArcGaugeWidget(max_val=15.0, unit="V")
    if iid == "pump_timeout":   return TimeoutFSRWidget()
    if iid == "front_motor":    return MotorWidget("FRONT")
    if iid == "front_current":  return ArcGaugeWidget(max_val=1.5,  unit="A")
    if iid == "rear_motor":     return MotorWidget("REAR")
    if iid == "rear_current":   return ArcGaugeWidget(max_val=1.5,  unit="A")
    if iid == "rest_contact":   return RestContactWidget()
    if iid == "sys_status":          return SystemStatusWidget()
    if iid == "pump_current_curve":  return CurrentCurveWidget("PUMP",         max_val=2.0)
    if iid == "motor_front_curve":   return CurrentCurveWidget("MOTOR_FRONT",  max_val=1.5)
    if iid == "rest_contact_edge":   return RestContactEdgeWidget()
    return None


def _connect_widget(iid: str, widget: QWidget, hub: SignalHub) -> None:
    from widgets_instruments import MotorWidget, PumpWidget, ArcGaugeWidget
    from widgets_motor_pump_enhanced import (
        RestContactWidget, SystemStatusWidget, TimeoutFSRWidget,
        CurrentCurveWidget, RestContactEdgeWidget,
    )
    import json as _j

    def _d(v):
        if isinstance(v, dict): return v
        try:    return _j.loads(v) if isinstance(v, str) else {}
        except: return {}

    if iid == "pump_widget" and isinstance(widget, PumpWidget):
        def _f(data): widget.set_state(data.get("state","OFF"), bool(data.get("fault",False)))
        hub.pump_data.connect(_f)
    elif iid == "pump_current" and isinstance(widget, ArcGaugeWidget):
        def _f(data): widget.set_value(float(data.get("current", data.get("pump_current",0))), bool(data.get("fault",False)))
        hub.pump_data.connect(_f)
    elif iid == "pump_voltage" and isinstance(widget, ArcGaugeWidget):
        def _f(data): widget.set_value(float(data.get("voltage", data.get("pump_voltage",0))), bool(data.get("fault",False)))
        hub.pump_data.connect(_f)
    elif iid == "pump_timeout" and isinstance(widget, TimeoutFSRWidget):
        def _f(data):
            widget.set_state(float(data.get("timeout_remaining",0)), float(data.get("timeout_duration",5)),
                             bool(data.get("timeout_active",False)), str(data.get("timeout_source","")),
                             str(data.get("timeout_info","Pump inactive")))
        hub.pump_data.connect(_f)
    elif iid == "front_motor" and isinstance(widget, MotorWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                widget.set_state(data.get("front","OFF"), data.get("speed","Speed1"))
            else:
                f = _d(data.get("front",{}))
                widget.set_state("ON" if f.get("enable",0) else "OFF", "Speed2" if f.get("speed",0) else "Speed1")
        hub.motor_data.connect(_f)
    elif iid == "front_current" and isinstance(widget, ArcGaugeWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                widget.set_value(float(data.get("current",0))*0.6, bool(data.get("fault",False)))
            else:
                f = _d(data.get("front",{}))
                widget.set_value(float(f.get("motor_current",0)), bool(f.get("fault_status",False)))
        hub.motor_data.connect(_f)
    elif iid == "rear_motor" and isinstance(widget, MotorWidget):
        def _f(data):
            if isinstance(data.get("rear"), str):
                widget.set_state(data.get("rear","OFF"), data.get("speed","Speed1"))
            else:
                r = _d(data.get("rear",{}))
                widget.set_state("ON" if r.get("enable",0) else "OFF", "Speed2" if r.get("speed",0) else "Speed1")
        hub.motor_data.connect(_f)
    elif iid == "rear_current" and isinstance(widget, ArcGaugeWidget):
        def _f(data):
            if isinstance(data.get("rear"), str):
                widget.set_value(float(data.get("current",0))*0.4, bool(data.get("fault",False)))
            else:
                r = _d(data.get("rear",{}))
                widget.set_value(float(r.get("motor_current",0)), bool(r.get("fault_status",False)))
        hub.motor_data.connect(_f)
    elif iid == "rest_contact" and isinstance(widget, RestContactWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                parked = data.get("rest","") == "PARKED"
            else:
                parked = bool(_d(data.get("front",{})).get("rest_contact",0))
            widget.set_state(parked)
        hub.motor_data.connect(_f)
    elif iid == "sys_status" and isinstance(widget, SystemStatusWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                fs = data.get("front","?"); rs = data.get("rear","?")
                sp = data.get("speed","?"); cur = float(data.get("current",0))
                fault = bool(data.get("fault",False))
            else:
                f = _d(data.get("front",{})); r = _d(data.get("rear",{}))
                fs = "ON" if f.get("enable",0) else "OFF"
                rs = "ON" if r.get("enable",0) else "OFF"
                sp = "Speed2" if f.get("speed",0) else "Speed1"
                cur = float(f.get("motor_current",0)) + float(r.get("motor_current",0))
                fault = bool(f.get("fault_status",0)) or bool(r.get("fault_status",0))
            widget.set_values(fs, rs, sp, f"{cur:.3f} A", fault,
                              "FAULT FSR_003" if fault else "System nominal")
        hub.motor_data.connect(_f)
    elif iid == "pump_current_curve" and isinstance(widget, CurrentCurveWidget):
        def _f(data):
            widget.set_value(float(data.get("current", data.get("pump_current", 0))),
                             bool(data.get("fault", False)))
        hub.pump_data.connect(_f)
    elif iid == "motor_front_curve" and isinstance(widget, CurrentCurveWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                widget.set_value(float(data.get("current", 0)) * 0.6,
                                 bool(data.get("fault", False)))
            else:
                import json as _j
                def _d(v):
                    if isinstance(v, dict): return v
                    try: return _j.loads(v) if isinstance(v, str) else {}
                    except: return {}
                f = _d(data.get("front", {}))
                widget.set_value(float(f.get("motor_current", 0)),
                                 bool(f.get("fault_status", False)))
        hub.motor_data.connect(_f)
    elif iid == "rest_contact_edge" and isinstance(widget, RestContactEdgeWidget):
        def _f_motor(data):
            # Priorité : rest_contact_raw à la racine du TCP broadcast BCM
            # raw=True  → lame EN MOUVEMENT (GPIO HIGH) → parked=False
            # raw=False → lame AU REPOS (GPIO LOW)      → parked=True
            if "rest_contact_raw" in data:
                widget.set_state(not bool(data["rest_contact_raw"]))
                return
            # Fallback format "aplati"
            if isinstance(data.get("front"), str):
                widget.set_state(data.get("rest", "") == "PARKED")
            else:
                import json as _j
                def _d(v):
                    if isinstance(v, dict): return v
                    try: return _j.loads(v) if isinstance(v, str) else {}
                    except: return {}
                # rest_contact=1 dans dict TCP = lame EN MOUVEMENT → parked=False
                widget.set_state(not bool(_d(data.get("front", {})).get("rest_contact", 0)))
        hub.motor_data.connect(_f_motor)

        def _f_lin(data):
            # Mise à jour depuis événement LIN (rest_contact_raw GPIO26 direct)
            if "rest_contact_raw" in data:
                widget.set_state(not bool(data["rest_contact_raw"]))
        hub.lin_data.connect(_f_lin)


# ═══════════════════════════════════════════════════════════
#  INSTRUMENT TREE  — sidebar sombre pro
# ═══════════════════════════════════════════════════════════
class InstrumentTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAnimated(False)
        self.setIndentation(0)
        self.setRootIsDecorated(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QTreeWidget {
                background: #111D11;
                color: #C8DDB8;
                border: none;
                font-family: 'Segoe UI';
                font-size: 10pt;
                outline: none;
            }
            QTreeWidget::item {
                padding: 6px 16px;
                min-height: 28px;
            }
            QTreeWidget::item:selected {
                background: rgba(141,198,63,0.25);
                color: #FFFFFF;
                border-left: 3px solid #8DC63F;
                padding-left: 13px;
            }
            QTreeWidget::item:hover:!selected {
                background: rgba(255,255,255,0.07);
                color: #FFFFFF;
            }
            QScrollBar:vertical {
                background: #0A1208;
                width: 4px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #4A6A3A;
                border-radius: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self._build()

    def _build(self):
        groups: dict[str, list] = {}
        for row in INSTRUMENT_CATALOG:
            groups.setdefault(row[2], []).append(row)

        for group, items in groups.items():
            color = _GROUP_COLOR.get(group, "#AAAAAA")
            bg    = _GROUP_BG.get(group, "#161616")

            # ── Séparateur visuel avant chaque groupe ──────────
            sep = QTreeWidgetItem(self, [""])
            sep.setFlags(Qt.ItemFlag.NoItemFlags)
            sep.setSizeHint(0, QSize(0, 4))
            sep.setBackground(0, QColor("#0A1208"))

            # ── Header groupe ───────────────────────────────────
            grp = QTreeWidgetItem(self, [f"  {color and '●'}  {group}"])
            grp.setFont(0, QFont("Segoe UI", 8, QFont.Weight.Bold))
            grp.setForeground(0, QColor(color))
            grp.setBackground(0, QColor(bg))
            grp.setSizeHint(0, QSize(0, 28))
            grp.setFlags(Qt.ItemFlag.NoItemFlags)
            # Lettre colorée simulée via le texte
            grp.setText(0, f"  {group}")

            # ── Items ───────────────────────────────────────────
            for (iid, label, _, accent, desc) in items:
                child = QTreeWidgetItem(self, [f"   {label}"])
                child.setFont(0, QFont("Segoe UI", 10))
                child.setForeground(0, QColor("#C8DDB8"))
                child.setBackground(0, QColor("#111D11"))
                child.setSizeHint(0, QSize(0, 30))
                child.setData(0, Qt.ItemDataRole.UserRole, iid)
                child.setToolTip(0, f"{label}  —  {desc}")

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        iid = item.data(0, Qt.ItemDataRole.UserRole)
        if not iid:
            return
        mime = QMimeData()
        mime.setData(MIME_INSTRUMENT, QByteArray(iid.encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)

        row    = _BY_ID.get(iid)
        accent = row[3] if row else KPIT_GREEN
        label  = row[1] if row else iid
        pm = QPixmap(220, 32)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 220, 32), 5, 5)
        c = QColor(accent); c.setAlpha(230)
        p.fillPath(path, QBrush(c))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.setPen(QPen(QColor("white")))
        p.drawText(12, 0, 200, 32, Qt.AlignmentFlag.AlignVCenter, label)
        p.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(110, 16))
        drag.exec(Qt.DropAction.CopyAction)


# ═══════════════════════════════════════════════════════════
#  DROPPED PANEL
# ═══════════════════════════════════════════════════════════
class DroppedPanel(QWidget):
    close_requested = Signal(object)
    focused         = Signal(object)

    def __init__(self, iid: str, widget: QWidget,
                 accent: str, label: str, parent=None):
        super().__init__(parent)
        self.instrument_id = iid
        self._accent   = accent
        self._widget   = widget
        self._locked   = False
        self._dragging = False
        self._drag_off = QPoint()
        self._resizing = False
        self._rs_pos   = QPoint()
        self._rs_size  = QSize()

        self.setMinimumSize(_MIN_W, _MIN_H)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._build(label)

    def _build(self, label: str):
        self.setStyleSheet(f"""
            DroppedPanel {{
                background: {W_PANEL};
                border: 1px solid {W_BORDER2};
                border-top: 3px solid {self._accent};
                border-radius: 4px;
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._hdr = QFrame()
        self._hdr.setObjectName("dp_hdr")
        self._hdr.setFixedHeight(28)
        self._hdr.setStyleSheet(f"""
            QFrame#dp_hdr {{
                background: {W_PANEL2};
                border-bottom: 1px solid {W_BORDER};
            }}
        """)
        self._hdr.setMouseTracking(True)
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(8, 0, 4, 0)
        hl.setSpacing(6)

        badge = QLabel(f" {label} ")
        badge.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        badge.setStyleSheet(
            f"color:{self._accent};"
            f"background:rgba(0,0,0,0.05);"
            f"border:1px solid {self._accent};"
            f"border-radius:9px;padding:0 5px;"
        )
        hl.addWidget(badge)
        hl.addStretch()

        btn = QPushButton("x")
        btn.setFixedSize(18, 18)
        btn.setFont(QFont(FONT_MONO, 7))
        btn.setStyleSheet(
            "QPushButton{background:transparent;color:#AAAAAA;border:none;border-radius:3px;}"
            "QPushButton:hover{background:#FFEBEE;color:#C62828;}"
        )
        btn.clicked.connect(lambda: self.close_requested.emit(self))
        hl.addWidget(btn)
        root.addWidget(self._hdr)

        self._widget.setParent(self)
        root.addWidget(self._widget, 1)

        gr = QHBoxLayout()
        gr.setContentsMargins(0, 0, 2, 1)
        gr.addStretch()
        self._grip = QLabel("◢")
        self._grip.setFixedSize(11, 11)
        self._grip.setFont(QFont(FONT_MONO, 7))
        self._grip.setStyleSheet(f"color:{W_TEXT_DIM};background:transparent;")
        self._grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        gr.addWidget(self._grip)
        root.addLayout(gr)
        self._hdr.installEventFilter(self)

    def set_locked(self, v: bool):
        self._locked = v
        self._hdr.setCursor(Qt.CursorShape.ArrowCursor if v else Qt.CursorShape.SizeAllCursor)
        self._grip.setVisible(not v)

    def to_dict(self) -> dict:
        return {"instrument_id": self.instrument_id,
                "x": self.x(), "y": self.y(),
                "w": self.width(), "h": self.height()}

    def eventFilter(self, obj, event):
        if obj is self._hdr and not self._locked:
            t = event.type()
            if t == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_off = event.globalPosition().toPoint() - self.pos()
                self.focused.emit(self); return True
            if t == event.Type.MouseButtonDblClick:
                self.close_requested.emit(self); return True
            if t == event.Type.MouseMove and self._dragging and \
               event.buttons() & Qt.MouseButton.LeftButton:
                np = event.globalPosition().toPoint() - self._drag_off
                self.move(_snap(max(0, np.x())), _snap(max(0, np.y()))); return True
            if t == event.Type.MouseButtonRelease:
                self._dragging = False; return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton and self._in_grip(e.pos()) and not self._locked:
            self._resizing = True
            self._rs_pos  = e.globalPosition().toPoint()
            self._rs_size = self.size()
            self.focused.emit(self)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._resizing and not self._locked:
            d = e.globalPosition().toPoint() - self._rs_pos
            self.resize(_snap(max(_MIN_W, self._rs_size.width()  + d.x())),
                        _snap(max(_MIN_H, self._rs_size.height() + d.y())))
        elif not self._resizing:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor
                           if self._in_grip(e.pos()) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._resizing = False
        super().mouseReleaseEvent(e)

    def _in_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width()-_GRIP and pos.y() >= self.height()-_GRIP

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(3):
            p.setPen(QPen(QColor(0, 0, 0, 12), 1))
            p.drawRoundedRect(i, i, self.width()-1-i, self.height()-1-i, 4, 4)
        p.setPen(QPen(QColor(self._accent), 1))
        p.drawRoundedRect(0, 0, self.width()-1, self.height()-1, 4, 4)


# ═══════════════════════════════════════════════════════════
#  CIRCULAR DROP CANVAS (SANS CERCLE - VERSION MODIFIÉE)
# ═══════════════════════════════════════════════════════════
class CircularDropCanvas(QWidget):
    CAR_W = 560
    CAR_H = 560

    def __init__(self, car_widget: QWidget, hub: SignalHub, parent=None):
        super().__init__(parent)
        self._car    = car_widget
        self._hub    = hub
        self._panels: list[DroppedPanel] = []
        self._locked = False
        self._grid   = True
        self._drag_over = False

        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(500, 400)
        self.setStyleSheet(f"background:{W_BG};")

        self._car.setParent(self)
        self._car.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._apply_car_mask()
        self._car.show()
        self._reposition_car()

    def _car_radius(self) -> int:
        return min(self.CAR_W, self.CAR_H) // 2

    def _apply_car_mask(self):
        from PySide6.QtGui import QPolygon, QRegion
        r = self._car_radius()
        hw = self.CAR_W // 2
        hh = self.CAR_H // 2
        pts = [QPoint(int(hw + r * math.cos(2 * math.pi * i / 128)),
                      int(hh + r * math.sin(2 * math.pi * i / 128))) for i in range(128)]
        self._car.setMask(QRegion(QPolygon(pts)))

    def _reposition_car(self):
        cx = (self.width() - self.CAR_W) // 2
        cy = (self.height() - self.CAR_H) // 2
        ox = max(0, cx)
        oy = max(0, cy)
        self._car.setGeometry(ox, oy, self.CAR_W, self.CAR_H)
        self._car.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reposition_car()
        self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_INSTRUMENT):
            self._drag_over = True
            self.update()
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_INSTRUMENT):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self.update()

    def dropEvent(self, e):
        self._drag_over = False
        self.update()
        if e.mimeData().hasFormat(MIME_INSTRUMENT):
            raw = bytes(e.mimeData().data(MIME_INSTRUMENT)).decode()
            pos = e.position().toPoint()
            self._spawn(raw, _snap(max(0, pos.x() - 90)), _snap(max(0, pos.y() - 20)))
            e.acceptProposedAction()

    def _spawn(self, iid: str, x: int, y: int,
               w: int = 200, h: int = 160) -> Optional[DroppedPanel]:
        row = _BY_ID.get(iid)
        if row is None:
            return None
        widget = _build_widget(iid)
        if widget is None:
            return None
        _connect_widget(iid, widget, self._hub)
        dp = DroppedPanel(iid, widget, row[3], row[1], self)
        dp.close_requested.connect(self._remove)
        dp.focused.connect(self._raise_panel)
        dp.setGeometry(x, y, w, h)
        if self._locked:
            dp.set_locked(True)
        dp.show()
        self._car.raise_()
        self._panels.append(dp)
        return dp

    def _remove(self, dp: DroppedPanel):
        if dp in self._panels:
            self._panels.remove(dp)
        dp.hide()
        dp.deleteLater()

    def _raise_panel(self, dp: DroppedPanel):
        dp.raise_()
        self._car.raise_()

    def set_locked(self, v: bool):
        self._locked = v
        for dp in self._panels:
            dp.set_locked(v)

    def set_grid(self, v: bool):
        self._grid = v
        self.update()

    def get_layout(self) -> list:
        return [dp.to_dict() for dp in self._panels]

    def apply_layout(self, items: list):
        for dp in list(self._panels):
            dp.hide()
            dp.deleteLater()
        self._panels.clear()
        for d in items:
            self._spawn(d.get("instrument_id", ""), d.get("x", 10), d.get("y", 10),
                        d.get("w", 200), d.get("h", 160))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Grille optionnelle — gris neutre doux (zone canvas complète)
        if self._grid:
            for x in range(0, W, _SNAP):
                alpha = 55 if x % (8 * _SNAP) == 0 else 22
                p.setPen(QPen(QColor(160, 160, 160, alpha), 1))
                p.drawLine(x, 0, x, H)
            for y in range(0, H, _SNAP):
                alpha = 55 if y % (8 * _SNAP) == 0 else 22
                p.setPen(QPen(QColor(160, 160, 160, alpha), 1))
                p.drawLine(0, y, W, y)

        # ── Grille dans le cercle de la voiture ────────────────────────
        # Calcul position/rayon du cercle (idem _reposition_car)
        car_cx = (W - self.CAR_W) // 2 + self.CAR_W // 2
        car_cy = (H - self.CAR_H) // 2 + self.CAR_H // 2
        r = self._car_radius()
        car_ox = max(0, (W - self.CAR_W) // 2)
        car_oy = max(0, (H - self.CAR_H) // 2)

        if self._grid:
            # Clip le dessin dans le cercle exact de la voiture
            clip_path = QPainterPath()
            clip_path.addEllipse(QRectF(car_cx - r, car_cy - r, r * 2, r * 2))
            p.save()
            p.setClipPath(clip_path)

            for x in range(car_ox, car_ox + self.CAR_W + _SNAP, _SNAP):
                alpha = 70 if x % (8 * _SNAP) == 0 else 30
                p.setPen(QPen(QColor(160, 160, 160, alpha), 1))
                p.drawLine(x, car_oy, x, car_oy + self.CAR_H)
            for y in range(car_oy, car_oy + self.CAR_H + _SNAP, _SNAP):
                alpha = 70 if y % (8 * _SNAP) == 0 else 30
                p.setPen(QPen(QColor(160, 160, 160, alpha), 1))
                p.drawLine(car_ox, y, car_ox + self.CAR_W, y)

            p.restore()

        # Bordure du cercle voiture — contour discret
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(160, 160, 160, 60), 1))
        p.drawEllipse(QRectF(car_cx - r, car_cy - r, r * 2, r * 2))


# ═══════════════════════════════════════════════════════════
#  LAYOUT TOOLBAR
# ═══════════════════════════════════════════════════════════
class LayoutToolbar(QWidget):
    reset_requested = Signal()

    def __init__(self, canvas: CircularDropCanvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._locked = False
        self._dir    = os.path.dirname(DEFAULT_LAYOUT_PATH) or "."
        self._build()

    def _build(self):
        self.setFixedHeight(42)
        self.setStyleSheet(f"background:{W_PANEL2};border-top:2px solid {KPIT_GREEN}55;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)

        def _btn(text, bg, fg, tip, cb, check=False):
            b = QPushButton(text)
            b.setFixedHeight(30); b.setMinimumWidth(90)
            b.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            b.setCheckable(check); b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};border:1px solid {bg};border-radius:5px;padding:0 14px;}}"
                f"QPushButton:hover{{background:{W_PANEL};color:{bg};border:1px solid {bg};}}"
                f"QPushButton:checked{{background:{W_PANEL};color:{bg};border:2px solid {bg};}}"
            )
            b.clicked.connect(cb); return b

        def _sep():
            s = QFrame(); s.setFrameShape(QFrame.Shape.VLine)
            s.setFixedWidth(1); s.setStyleSheet(f"background:{W_BORDER2};"); return s

        lay.addWidget(_btn("Save",   KPIT_GREEN, "#FFF", "Sauvegarder", self._save))
        lay.addWidget(_btn("Load",   A_TEAL,     "#FFF", "Charger",     self._load))
        lay.addWidget(_sep())
        lay.addWidget(_btn("Reset",  A_ORANGE,   "#FFF", "Vider",       self._reset))
        lay.addWidget(_sep())
        self._btn_lock = _btn("Unlock", A_AMBER,    "#FFF", "Verrouiller", self._toggle_lock, True)
        lay.addWidget(self._btn_lock)
        self._btn_grid = _btn("Grid",   W_TEXT_DIM, "#FFF", "Grille",      self._toggle_grid, True)
        self._btn_grid.setChecked(True)
        lay.addWidget(self._btn_grid)
        lay.addStretch()
        self._lbl = QLabel("profile: —")
        self._lbl.setFont(QFont(FONT_MONO, 9))
        self._lbl.setStyleSheet(f"color:{W_TEXT_DIM};background:transparent;")
        lay.addWidget(self._lbl)

    def _save(self):
        name, ok = QInputDialog.getText(self, "Sauvegarder", "Nom du profil :", text="default")
        if not ok or not name.strip(): return
        name = name.strip().replace(" ", "_")
        path = os.path.join(self._dir, f"{name}.sdf")
        try:
            os.makedirs(self._dir, exist_ok=True)
            _sdf_write(path, profile=name, locked=self._locked,
                       panels=self._canvas.get_layout())
            self._lbl.setText(f"profile: {name}  OK")
        except Exception as ex:
            QMessageBox.warning(self, "Erreur", f"Impossible de sauvegarder:\n{ex}")

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger", self._dir,
            "Layout SDF (*.sdf);;Layout JSON (*.json);;Tous (*.*)"
        )
        if not path: return
        try:
            data = _sdf_read(path)
        except Exception as ex:
            QMessageBox.warning(self, "Erreur", f"Impossible de lire:\n{ex}"); return
        self._canvas.apply_layout(data.get("panels", []))
        profile = data.get("_profile", "?")
        if data.get("_locked", False):
            self._btn_lock.setChecked(True); self._apply_lock(True)
        self._lbl.setText(f"profile: {profile}  OK")

    def _reset(self):
        self._canvas.apply_layout([]); self._lbl.setText("profile: —")
        self.reset_requested.emit()

    def _toggle_lock(self): self._apply_lock(self._btn_lock.isChecked())

    def _apply_lock(self, v: bool):
        self._locked = v; self._canvas.set_locked(v)
        self._btn_lock.setText("Locked" if v else "Unlock")

    def _toggle_grid(self): self._canvas.set_grid(self._btn_grid.isChecked())

    def auto_load(self, path: str):
        # Chercher d'abord le .sdf, puis le .json legacy si absent
        candidates = [path]
        if path.endswith(".sdf"):
            candidates.append(path[:-4] + ".json")
        elif path.endswith(".json"):
            candidates.insert(0, path[:-5] + ".sdf")

        for candidate in candidates:
            if not os.path.exists(candidate):
                continue
            try:
                data = _sdf_read(candidate)
                self._canvas.apply_layout(data.get("panels", []))
                self._lbl.setText(f"profile: {data.get('_profile', 'default')}  OK")
                return
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
#  MOTOR PUMP FREE PAGE
# ═══════════════════════════════════════════════════════════
class MotorPumpFreePage(QWidget):
    def __init__(self, motor_panel: QWidget, car_xray: QWidget,
                 pump_panel: QWidget, signal_hub: SignalHub, parent=None):
        super().__init__(parent)
        self._hub = signal_hub
        self._car = car_xray
        self.setStyleSheet(f"background:{W_BG};")
        self._build()
        self._toolbar.auto_load(DEFAULT_LAYOUT_PATH)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # ── Panneau gauche ────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(240)
        left_panel.setStyleSheet("background:#111D11;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        hdr = QWidget()
        hdr.setFixedHeight(42)
        hdr.setStyleSheet(f"background:#0A1208;border-bottom:2px solid {KPIT_GREEN};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(14, 0, 10, 0)
        lbl_hdr = QLabel("INSTRUMENTS")
        lbl_hdr.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        lbl_hdr.setStyleSheet(f"color:{KPIT_GREEN};background:transparent;letter-spacing:3px;")
        hdr_lay.addWidget(lbl_hdr)
        left_layout.addWidget(hdr)

        self._tree = InstrumentTree()
        left_layout.addWidget(self._tree, 1)
        body.addWidget(left_panel)

        # ── Canvas central ────────────────────────────────────
        self._canvas = CircularDropCanvas(self._car, self._hub)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._canvas)
        scroll.setStyleSheet("background:transparent;border:none;")
        body.addWidget(scroll, 1)

        root.addLayout(body, 1)

        self._toolbar = LayoutToolbar(self._canvas, self)
        root.addWidget(self._toolbar)