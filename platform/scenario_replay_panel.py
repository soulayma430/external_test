"""
scenario_replay_panel.py  —  Replay de scénarios complet (v2 — Virtual ECU)
============================================================================
Lit un CSV produit par DataSavePanel et REPRODUIT ENTIÈREMENT le comportement
de la plateforme comme si les ECU physiques étaient présents.

Nouveautés v2
-------------
VirtualECU  — maintient l'état complet du banc et pilote TOUS les composants :
  • MotorDashPanel        — état moteur avant/arrière + courant + rest contact
  • PumpPanel             — état pompe + courant + tension + timeout FSR_005
  • VehicleRainPanel      — knobs vitesse + pluie + ignition + reverse
  • CRSLINPanel           — bouton WiperOp actif + statistiques TX + rest contact
  • CANBusPanel           — oscilloscope + table + champs décodés 0x200/0x201
  • CarHTMLWidget (×2)    — wipers, pluie, vitesse, ignition, pompe X-ray
  • Ignition buttons      — synchronisés (OFF/ACC/ON)
  • MainWindow._car_ign_changed / _car_rev_toggled

Architecture
------------
  CsvScenarioLoader   → liste de ScenarioRow (parsing CSV)
  VirtualECU          → état global, pilote widgets
  ScenarioEngine      → moteur de replay (timer), appelle VirtualECU._apply()
  ScenarioReplayPanel → panneau Qt avec toolbar, timeline, log, stats

Intégration dans main_window.py (OBLIGATOIRE après construction)
----------------------------------------------------------------
    self._scenario_panel = ScenarioReplayPanel(
        can_worker   = self._can_worker,
        lin_worker   = self._lin_worker,
        motor_worker = self._motor_worker,
        rte_client   = getattr(self, '_rte_client', None),
    )
    # Connecter les panels AU PLUS TÔT après leur création
    self._scenario_panel.connect_panels(
        motor_panel  = self._motor_panel,
        pump_panel   = self._pump_panel,
        veh_panel    = self._veh_panel,
        crslin_panel = self._crslin_panel,
        can_panel    = self._can_panel,
        car_html     = self._car_html,
        car_html_mp  = self._car_html_mp,
        main_window  = self,
    )
"""

from __future__ import annotations

import csv
import datetime
import os
import time
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from constants import (
    A_AMBER, A_GREEN, A_ORANGE, A_RED, A_TEAL,
    FONT_MONO, FONT_UI,
    KPIT_GREEN,
    W_BG, W_BORDER, W_DOCK_HDR,
    W_PANEL, W_PANEL2,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR, W_TOOLBAR,
)
from widgets_base import _cd_btn, _hsep, _lbl


# ═══════════════════════════════════════════════════════════════
#  Constantes
# ═══════════════════════════════════════════════════════════════
_SRC_COLOR = {
    "motor": A_GREEN,
    "lin":   "#9C27B0",
    "can":   A_TEAL,
    "pump":  A_ORANGE,
}
_SRC_LABEL = {"motor": "MOTOR", "lin": "LIN", "can": "CAN", "pump": "PUMP"}

_WOP_CODE = {
    "OFF": 0, "TOUCH": 1, "SPEED1": 2, "SPEED2": 3,
    "AUTO": 4, "FRONT_WASH": 5, "REAR_WASH": 6, "REAR_WIPE": 7,
}
_WOP_NAME = {v: k for k, v in _WOP_CODE.items()}

# WOP → (front_on, rear_on, pump_state)
_WOP_MOTOR = {
    0: (False, False, "OFF"),
    1: (True,  False, "OFF"),      # TOUCH
    2: (True,  False, "OFF"),      # SPEED1
    3: (True,  False, "OFF"),      # SPEED2
    4: (True,  False, "OFF"),      # AUTO
    5: (True,  False, "FORWARD"),  # FRONT_WASH
    6: (False, True,  "BACKWARD"), # REAR_WASH
    7: (False, True,  "OFF"),      # REAR_WIPE
}


# ═══════════════════════════════════════════════════════════════
#  DataClass : une ligne du CSV parsée
# ═══════════════════════════════════════════════════════════════
@dataclass
class ScenarioRow:
    t_abs:   float
    t_rel:   float
    source:  str
    raw:     dict
    summary: str = ""


# ═══════════════════════════════════════════════════════════════
#  Loader
# ═══════════════════════════════════════════════════════════════
class CsvScenarioLoader:
    @staticmethod
    def load(path: str,
             sources: set | None = None) -> tuple[list[ScenarioRow], str]:
        if not os.path.isfile(path):
            return [], f"Fichier introuvable : {path}"
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                raw_rows = list(csv.DictReader(f))
        except Exception as exc:
            return [], f"Erreur lecture CSV : {exc}"
        if not raw_rows:
            return [], "CSV vide."

        t0: Optional[float] = None
        for r in raw_rows:
            t = _parse_ts(r.get("timestamp", ""))
            if t:
                t0 = t
                break
        if t0 is None:
            return [], "Aucun timestamp valide."

        rows: list[ScenarioRow] = []
        for r in raw_rows:
            src = r.get("source", "").strip().lower()
            if sources and src not in sources:
                continue
            t_abs = _parse_ts(r.get("timestamp", ""))
            if not t_abs:
                continue
            rows.append(ScenarioRow(
                t_abs=t_abs,
                t_rel=t_abs - t0,
                source=src,
                raw=dict(r),
                summary=CsvScenarioLoader._summarise(src, r),
            ))
        rows.sort(key=lambda x: x.t_rel)
        return rows, ""

    @staticmethod
    def _summarise(src: str, r: dict) -> str:
        if src == "motor":
            parts = []
            for k in ("crs_wiper_op", "ignition", "vehicle_speed",
                      "rain_intensity", "state"):
                if r.get(k):
                    parts.append(f"{k}={r[k]}")
            return "  ".join(parts) or r.get("state", "—")
        if src == "lin":
            return (f"{r.get('lin_type','')}  op={r.get('op','')} "
                    f"wiper={r.get('wiper_op','')}")
        if src == "can":
            return (f"{r.get('can_id','')} {r.get('direction','')} "
                    f"{r.get('payload','')}")
        if src == "pump":
            return (f"state={r.get('state','')} dir={r.get('direction','')} "
                    f"I={r.get('current','')}A")
        return str(r)


def _parse_ts(ts: str) -> float:
    if not ts:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts.strip(), fmt).timestamp()
        except ValueError:
            pass
    return 0.0


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v, default=0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════
#  VirtualECU  — état global + pilotage de TOUS les composants
# ═══════════════════════════════════════════════════════════════
class VirtualECU:
    """
    Maintient l'état complet du banc HIL et applique chaque changement
    à TOUS les composants UI simultanément, exactement comme le ferait
    un ECU physique.

    Composants pilotés :
      • MotorDashPanel  → moteur avant/arrière, courant, rest contact
      • PumpPanel       → état pompe, courant, tension, timeout
      • VehicleRainPanel→ knobs vitesse + pluie, ignition, reverse
      • CRSLINPanel     → bouton WiperOp sélectionné, stats TX, rest
      • CANBusPanel     → oscilloscope + table trames
      • CarHTMLWidget   → wipers, pluie, vitesse, ignition
      • CarXRayWidget   → idem + animation pompe impeller
      • MainWindow      → boutons ignition ON/ACC/OFF, reverse btn
    """

    def __init__(self):
        # État courant
        self.wiper_op    : int   = 0
        self.ignition    : str   = "OFF"
        self.vehicle_spd : float = 0.0
        self.rain        : int   = 0
        self.reverse     : bool  = False
        self.front_on    : bool  = False
        self.rear_on     : bool  = False
        self.pump_state  : str   = "OFF"
        self.pump_cur    : float = 0.0
        self.pump_vol    : float = 0.0
        self.motor_cur   : float = 0.0
        self.rest_contact: bool  = False   # False = parked
        self.blade_cycles: int   = 0
        self.bcm_state   : str   = "OFF"

        # Références UI
        self._motor_panel  = None
        self._pump_panel   = None
        self._veh_panel    = None
        self._crslin_panel = None
        self._can_panel    = None
        self._car_html     = None
        self._car_html_mp  = None
        self._main_window  = None

    def connect_panels(self, motor_panel=None, pump_panel=None,
                       veh_panel=None, crslin_panel=None,
                       can_panel=None, car_html=None,
                       car_html_mp=None, main_window=None):
        self._motor_panel  = motor_panel
        self._pump_panel   = pump_panel
        self._veh_panel    = veh_panel
        self._crslin_panel = crslin_panel
        self._can_panel    = can_panel
        self._car_html     = car_html
        self._car_html_mp  = car_html_mp
        self._main_window  = main_window

    # ─────────────────────────────────────────────────────────
    #  Application des rows CSV
    # ─────────────────────────────────────────────────────────

    def apply_motor_row(self, r: dict):
        """Traite une ligne source='motor' et met tous les widgets à jour."""

        # ── WiperOp ──────────────────────────────────────────
        wop_raw = str(
            r.get("crs_wiper_op", "") or r.get("state", "")).strip()
        op = self._parse_wiper_op(wop_raw)
        if op is not None and op != self.wiper_op:
            self.wiper_op = op
            self._update_wiper_state()

        # ── Ignition ─────────────────────────────────────────
        ign_raw = str(
            r.get("ignition", "") or r.get("ignition_status", "")
        ).strip().upper()
        ign = self._parse_ignition(ign_raw)
        if ign and ign != self.ignition:
            self.ignition = ign
            self._apply_ignition()

        # ── Vitesse ──────────────────────────────────────────
        spd = r.get("vehicle_speed", "")
        if spd not in ("", None):
            v = _safe_float(spd)
            if abs(v - self.vehicle_spd) > 0.05:
                self.vehicle_spd = v
                self._apply_speed()

        # ── Pluie ────────────────────────────────────────────
        rain = r.get("rain_intensity", "")
        if rain not in ("", None):
            v = _safe_int(rain)
            if v != self.rain:
                self.rain = v
                self._apply_rain()

        # ── Reverse ──────────────────────────────────────────
        rev_raw = str(r.get("reverse_gear", "")).strip().lower()
        if rev_raw in ("1", "true", "r", "reverse"):
            if not self.reverse:
                self.reverse = True
                self._apply_reverse()
        elif rev_raw in ("0", "false", "d", "n", "p"):
            if self.reverse:
                self.reverse = False
                self._apply_reverse()

        # ── Courant moteur ────────────────────────────────────
        cur = r.get("current", "")
        if cur not in ("", None):
            self.motor_cur = _safe_float(cur)

        # ── Rest contact ─────────────────────────────────────
        rest_raw = str(r.get("rest_contact", "")).strip().lower()
        if rest_raw:
            rest = rest_raw in ("moving", "1", "true")
            if rest != self.rest_contact:
                self.rest_contact = rest
                self._apply_rest_contact()

        # ── Blade cycles ─────────────────────────────────────
        bc = r.get("front_blade_cycles", "")
        if bc not in ("", None):
            self.blade_cycles = _safe_int(bc)

        # Toujours pousser vers motor panel
        self._push_motor_panel()

    def apply_lin_row(self, r: dict):
        """Traite une ligne source='lin'."""
        op_raw = str(r.get("op", "") or r.get("wiper_op", "")).strip()
        op = self._parse_wiper_op(op_raw)
        if op is not None and op != self.wiper_op:
            self.wiper_op = op
            self._update_wiper_state()

        if self._crslin_panel:
            ev = {
                "type":               "TX",
                "op":                 self.wiper_op,
                "pid":                "0xD6",
                "alive":              0,
                "cs_int":             0,
                "raw":                r.get("raw", ""),
                "time":               time.time(),
                "bcm_state":          self.bcm_state,
                "front_motor_on":     self.front_on,
                "rear_motor_on":      self.rear_on,
                "rest_contact_raw":   self.rest_contact,
                "front_blade_cycles": self.blade_cycles,
            }
            try:
                self._crslin_panel.add_lin_event(ev)
                self._crslin_panel.on_wiper_sent(self.wiper_op, 0)
            except Exception:
                pass

    def apply_can_row(self, r: dict):
        """Traite une ligne source='can'."""
        can_id_str = str(r.get("can_id", "")).strip()
        direction  = str(r.get("direction", "TX")).strip()
        payload    = str(r.get("payload", "")).strip()

        try:
            if can_id_str.startswith(("0x", "0X")):
                can_id_int = int(can_id_str, 16)
            else:
                can_id_int = int(can_id_str)
        except Exception:
            can_id_int = 0

        fields = self._decode_can_fields(can_id_int, r)

        # Mettre à jour l'état interne depuis 0x300 (vehicle) / 0x301 (rain)
        if can_id_int == 0x300:
            ign_code = fields.get("ignition", -1)
            if isinstance(ign_code, int):
                ign = {0: "OFF", 1: "ACC", 2: "ON"}.get(ign_code)
                if ign and ign != self.ignition:
                    self.ignition = ign
                    self._apply_ignition()
            spd = fields.get("speed_kmh")
            if spd is not None:
                self.vehicle_spd = float(spd)
                self._apply_speed()
            rev = fields.get("reverse")
            if rev is not None and bool(rev) != self.reverse:
                self.reverse = bool(rev)
                self._apply_reverse()

        elif can_id_int == 0x301:
            rain = fields.get("intensity")
            if rain is not None:
                v = int(rain)
                if v != self.rain:
                    self.rain = v
                    self._apply_rain()

        elif can_id_int == 0x201:
            mode = fields.get("mode")
            if isinstance(mode, int) and mode != self.wiper_op:
                self.wiper_op = mode
                self._update_wiper_state()
            cur = fields.get("current_A")
            if cur is not None:
                self.motor_cur = float(cur)

        if self._can_panel:
            ev = {
                "type":       direction,
                "can_id":     can_id_str,
                "can_id_int": can_id_int,
                "dlc":        _safe_int(r.get("dlc", 8)),
                "data":       payload,
                "desc":       self._can_desc(can_id_int),
                "time":       time.time(),
                "fields":     fields,
            }
            try:
                self._can_panel.add_can_event(ev)
            except Exception:
                pass

    def apply_pump_row(self, r: dict):
        """Traite une ligne source='pump'."""
        state = str(r.get("state", self.pump_state)).strip().upper()
        direc = str(r.get("direction", "")).strip().upper()
        if direc in ("FWD", "FORWARD"):
            state = "FORWARD"
        elif direc in ("BWD", "BACKWARD"):
            state = "BACKWARD"

        self.pump_state = state
        self.pump_cur   = _safe_float(r.get("current",
                                            r.get("pump_current", 0.0)))
        self.pump_vol   = _safe_float(r.get("voltage", 12.0))
        self._apply_pump()

    # ─────────────────────────────────────────────────────────
    #  Mise à jour des widgets
    # ─────────────────────────────────────────────────────────

    def _update_wiper_state(self):
        """Recalcule front_on/rear_on/pump et pilote tous les widgets."""
        front, rear, pump = _WOP_MOTOR.get(self.wiper_op, (False, False, "OFF"))
        self.front_on  = front
        self.rear_on   = rear
        if pump != "OFF":
            self.pump_state = pump
        self.bcm_state = _WOP_NAME.get(self.wiper_op, "OFF")

        # CRS panel : sélectionner le bouton op
        if self._crslin_panel:
            try:
                self._crslin_panel._select_op(self.wiper_op)
            except Exception:
                pass

        # Voitures HTML
        for car in self._cars():
            try:
                car.set_wiper_op(self.wiper_op)
                car.set_wiper_from_bcm(
                    motor_on=self.front_on or self.rear_on,
                    rest_raw=self.rest_contact,
                    bcm_state=self.bcm_state,
                    op=self.wiper_op,
                )
            except Exception:
                pass

        # Pompe X-ray
        if self._car_html_mp:
            try:
                self._car_html_mp.set_pump_state(self.pump_state, False)
            except Exception:
                pass

    def _apply_ignition(self):
        # MainWindow ignition buttons
        if self._main_window:
            try:
                self._main_window._car_ign_changed(self.ignition)
            except Exception:
                pass
        # VehicleRainPanel
        if self._veh_panel:
            try:
                self._veh_panel.ign._sel(self.ignition)
            except Exception:
                pass
        # Voitures
        for car in self._cars():
            try:
                car.set_ignition(self.ignition)
            except Exception:
                pass

    def _apply_speed(self):
        if self._veh_panel:
            try:
                knob = self._veh_panel._spd_knob
                knob._val = int(self.vehicle_spd * 10)
                knob._arc.set_value(knob._val)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_speed(self.vehicle_spd)
            except Exception:
                pass

    def _apply_rain(self):
        if self._veh_panel:
            try:
                knob = self._veh_panel._rain_knob
                knob._val = self.rain
                knob._arc.set_value(self.rain)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_rain(self.rain)
            except Exception:
                pass

    def _apply_reverse(self):
        if self._veh_panel:
            try:
                self._veh_panel._rev = 1 if self.reverse else 0
                from constants import A_ORANGE
                self._veh_panel._led_rev.set_state(
                    self.reverse, A_ORANGE if self.reverse else "#707070")
                self._veh_panel.lbl_rev.setText(
                    "REVERSE" if self.reverse else "NORMAL")
            except Exception:
                pass
        if self._main_window:
            try:
                self._main_window._rev_btn.setChecked(self.reverse)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_reverse(self.reverse)
            except Exception:
                pass

    def _apply_rest_contact(self):
        if self._crslin_panel:
            try:
                self._crslin_panel.update_rest_contact(
                    self.rest_contact, self.blade_cycles)
            except Exception:
                pass
        if self._motor_panel:
            try:
                from constants import A_GREEN, A_ORANGE
                parked = not self.rest_contact
                self._motor_panel.led_rest.set_state(
                    parked, A_GREEN if parked else A_ORANGE)
                self._motor_panel.lbl_rest.setText(
                    "PARKED" if parked else "MOVING")
            except Exception:
                pass

    def _push_motor_panel(self):
        if not self._motor_panel:
            return
        try:
            speed_str = "Speed2" if self.wiper_op == 3 else "Speed1"
            self._motor_panel.on_motor_data({
                "front":          "ON" if self.front_on else "OFF",
                "rear":           "ON" if self.rear_on  else "OFF",
                "speed":          speed_str,
                "current":        self.motor_cur,
                "fault":          self.motor_cur > 1.2,
                "rest":           "PARKED" if not self.rest_contact else "MOVING",
                "state":          self.bcm_state,
                "vehicle_speed":  self.vehicle_spd,
                "rain_intensity": self.rain,
            })
        except Exception:
            pass

    def _apply_pump(self):
        if self._pump_panel:
            try:
                active = self.pump_state in ("FORWARD", "BACKWARD")
                self._pump_panel.update_display({
                    "state":          self.pump_state,
                    "current":        self.pump_cur,
                    "voltage":        self.pump_vol or 12.0,
                    "fault":          False,
                    "fault_reason":   "",
                    "pump_remaining": 5.0 if active else 0.0,
                    "pump_duration":  5.0,
                    "source":         "REPLAY",
                })
            except Exception:
                pass
        if self._car_html_mp:
            try:
                self._car_html_mp.set_pump_state(self.pump_state, False)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────
    #  Helpers
    # ─────────────────────────────────────────────────────────

    def _cars(self):
        return [c for c in (self._car_html, self._car_html_mp) if c]

    @staticmethod
    def _parse_wiper_op(raw: str) -> Optional[int]:
        if not raw:
            return None
        if raw.upper() in _WOP_CODE:
            return _WOP_CODE[raw.upper()]
        try:
            v = int(float(raw))
            if 0 <= v <= 7:
                return v
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_ignition(raw: str) -> Optional[str]:
        if raw in ("1", "ON", "TRUE"):
            return "ON"
        if raw == "ACC":
            return "ACC"
        if raw in ("0", "OFF", "FALSE"):
            return "OFF"
        return None

    @staticmethod
    def _decode_can_fields(can_id: int, r: dict) -> dict:
        col_map = {
            0x200: "wiper_cmd",
            0x201: "wiper_status",
            0x202: "wiper_ack",
            0x300: "vehicle_status",
            0x301: "rain_sensor",
        }
        col = col_map.get(can_id)
        if col and r.get(col):
            try:
                import json as _j
                return _j.loads(r[col])
            except Exception:
                pass
        return {}

    @staticmethod
    def _can_desc(can_id: int) -> str:
        return {
            0x200: "Wiper_Cmd",
            0x201: "Wiper_Status",
            0x202: "Wiper_Ack",
            0x300: "Vehicle_Status",
            0x301: "RainSensorData",
        }.get(can_id, f"0x{can_id:03X}")


# ═══════════════════════════════════════════════════════════════
#  Moteur de replay
# ═══════════════════════════════════════════════════════════════
class ScenarioEngine(QObject):
    step_fired       = Signal(int)
    replay_finished  = Signal()
    log_msg          = Signal(str)
    progress_changed = Signal(int, int)

    # Signaux legacy conservés pour compatibilité avec main_window.py
    virtual_motor_data = Signal(dict)
    virtual_lin_event  = Signal(dict)
    virtual_pump_data  = Signal(dict)

    def __init__(self,
                 can_worker=None,
                 lin_worker=None,
                 motor_worker=None,
                 rte_client=None,
                 parent=None):
        super().__init__(parent)
        self._can_w   = can_worker
        self._lin_w   = lin_worker
        self._motor_w = motor_worker
        self._rte     = rte_client

        self._rows:    list[ScenarioRow] = []
        self._idx:     int  = 0
        self._t_start: float = 0.0
        self._speed:   float = 1.0
        self._running  = False
        self._paused   = False
        self._virtual_widgets: bool = True

        self.ecu = VirtualECU()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire_next)

    # ── API publique ──────────────────────────────────────────

    def load(self, rows: list[ScenarioRow]):
        self._rows = rows
        self._idx  = 0

    def set_speed(self, factor: float):
        self._speed = max(0.1, factor)

    def set_virtual_widgets(self, enabled: bool):
        self._virtual_widgets = enabled

    def connect_panels(self, **kwargs):
        self.ecu.connect_panels(**kwargs)

    def start(self):
        if not self._rows:
            return
        self._idx     = 0
        self._t_start = time.monotonic()
        self._running = True
        self._paused  = False
        self.log_msg.emit(
            f"▶ Replay démarré — {len(self._rows)} steps | ×{self._speed:.1f}")
        self._schedule_next()

    def pause(self):
        if not self._running:
            return
        self._paused = True
        self._timer.stop()
        self.log_msg.emit("⏸ Replay en pause")

    def resume(self):
        if not self._paused:
            return
        self._paused  = False
        self._t_start = (time.monotonic()
                         - self._rows[self._idx].t_rel / self._speed)
        self.log_msg.emit("▶ Replay repris")
        self._schedule_next()

    def stop(self):
        self._running = False
        self._paused  = False
        self._timer.stop()
        self.log_msg.emit("⏹ Replay arrêté")

    def seek(self, index: int):
        self._idx = max(0, min(index, len(self._rows) - 1))
        self._t_start = (time.monotonic()
                         - self._rows[self._idx].t_rel / self._speed)
        if self._running and not self._paused:
            self._timer.stop()
            self._schedule_next()

    @property
    def current_index(self) -> int:
        return self._idx

    @property
    def is_running(self) -> bool:
        return self._running and not self._paused

    # ── Logique interne ───────────────────────────────────────

    def _schedule_next(self):
        if self._idx >= len(self._rows):
            self._running = False
            self.replay_finished.emit()
            self.log_msg.emit("✅ Replay terminé")
            return
        row      = self._rows[self._idx]
        elapsed  = time.monotonic() - self._t_start
        delay_ms = max(0, int((row.t_rel / self._speed - elapsed) * 1000))
        self._timer.start(delay_ms)

    def _fire_next(self):
        if not self._running or self._paused:
            return
        if self._idx >= len(self._rows):
            self._running = False
            self.replay_finished.emit()
            return
        row = self._rows[self._idx]
        self._inject(row)
        self.step_fired.emit(self._idx)
        self.progress_changed.emit(self._idx + 1, len(self._rows))
        self._idx += 1
        self._schedule_next()

    def _inject(self, row: ScenarioRow):
        src = row.source
        r   = row.raw

        if src == "motor":
            if self._virtual_widgets:
                self.ecu.apply_motor_row(r)
            self._inject_motor_physical(r)
            self._emit_legacy_motor()

        elif src == "lin":
            if self._virtual_widgets:
                self.ecu.apply_lin_row(r)
            self._inject_lin_physical(r)
            self._emit_legacy_lin()

        elif src == "can":
            if self._virtual_widgets:
                self.ecu.apply_can_row(r)
            self._inject_can_physical(r)

        elif src == "pump":
            if self._virtual_widgets:
                self.ecu.apply_pump_row(r)
            self._emit_legacy_pump()

    # ── Injection physique ────────────────────────────────────

    def _inject_motor_physical(self, r: dict):
        """Envoie vers RTEClient Redis ou MotorWorker TCP si disponibles."""
        stimuli = {}
        op = self.ecu._parse_wiper_op(
            str(r.get("crs_wiper_op", "") or r.get("state", "")).strip())
        if op is not None:
            stimuli["crs_wiper_op"] = op
        ign = self.ecu._parse_ignition(
            str(r.get("ignition", "")).strip().upper())
        if ign:
            stimuli["ignition_status"] = 1 if ign == "ON" else (
                                         2 if ign == "ACC" else 0)
        spd = r.get("vehicle_speed", "")
        if spd not in ("", None):
            stimuli["vehicle_speed"] = _safe_float(spd)
        rain = r.get("rain_intensity", "")
        if rain not in ("", None):
            stimuli["rain_intensity"] = _safe_int(rain)

        if not stimuli:
            return
        if self._rte:
            for k, v in stimuli.items():
                self._rte.set_cmd(k, v)
            self.log_msg.emit(
                f"  → RTE  "
                f"{', '.join(f'{k}={v}' for k, v in stimuli.items())}")
        elif self._motor_w:
            payload = {}
            if "crs_wiper_op" in stimuli:
                payload["wiper_op"] = _WOP_NAME.get(
                    stimuli["crs_wiper_op"], "OFF")
            if "ignition_status" in stimuli:
                payload["ignition_status"] = (
                    "ON" if stimuli["ignition_status"] == 1 else "OFF")
            if "vehicle_speed" in stimuli:
                payload["vehicle_speed"] = stimuli["vehicle_speed"]
            if "rain_intensity" in stimuli:
                payload["rain_intensity"] = stimuli["rain_intensity"]
            if payload:
                self._motor_w.queue_send(payload)

    def _inject_lin_physical(self, r: dict):
        op_raw = str(r.get("op", "") or r.get("wiper_op", "")).strip().upper()
        if op_raw and self._lin_w:
            self._lin_w.queue_send({"cmd": op_raw})

    def _inject_can_physical(self, r: dict):
        can_id = str(r.get("can_id", "")).strip()
        if can_id not in {"0x300", "0x301", "300", "301"}:
            return
        payload = str(r.get("payload", "")).strip()
        if payload and self._motor_w:
            self._motor_w.queue_send({
                "test_cmd": "inject_can",
                "can_id":   can_id,
                "payload":  payload,
            })

    # ── Signaux legacy (connectés dans main_window.py) ────────

    def _emit_legacy_motor(self):
        virt = {
            "state":              self.ecu.bcm_state,
            "front":              "ON" if self.ecu.front_on else "OFF",
            "rear":               "ON" if self.ecu.rear_on  else "OFF",
            "speed":              "Speed2" if self.ecu.wiper_op == 3 else "Speed1",
            "current":            self.ecu.motor_cur,
            "fault":              False,
            "rest":               "PARKED" if not self.ecu.rest_contact else "MOVING",
            "rest_contact_raw":   self.ecu.rest_contact,
            "front_blade_cycles": self.ecu.blade_cycles,
            "crs_fault":          0,
            "vehicle_speed":      self.ecu.vehicle_spd,
            "rain_intensity":     self.ecu.rain,
        }
        self.virtual_motor_data.emit(virt)
        self.log_msg.emit(
            f"  → MOTOR  op={_WOP_NAME.get(self.ecu.wiper_op,'?')}  "
            f"ign={self.ecu.ignition}  "
            f"spd={self.ecu.vehicle_spd:.0f} km/h  rain={self.ecu.rain}%")

    def _emit_legacy_lin(self):
        ev = {
            "type":               "TX",
            "op":                 self.ecu.wiper_op,
            "bcm_state":          self.ecu.bcm_state,
            "front_motor_on":     self.ecu.front_on,
            "rear_motor_on":      self.ecu.rear_on,
            "rest_contact_raw":   self.ecu.rest_contact,
            "front_blade_cycles": self.ecu.blade_cycles,
            "pid":                "0xD6",
            "alive":              0,
            "cs_int":             0,
            "raw":                "",
            "time":               time.time(),
        }
        self.virtual_lin_event.emit(ev)
        self.log_msg.emit(
            f"  → LIN   op={_WOP_NAME.get(self.ecu.wiper_op,'?')}")

    def _emit_legacy_pump(self):
        virt = {
            "state":          self.ecu.pump_state,
            "current":        self.ecu.pump_cur,
            "voltage":        self.ecu.pump_vol or 12.0,
            "fault":          False,
            "fault_reason":   "",
            "pump_remaining": 0.0,
            "pump_duration":  0.0,
            "source":         "REPLAY",
        }
        self.virtual_pump_data.emit(virt)
        self.log_msg.emit(
            f"  → PUMP  state={self.ecu.pump_state}  "
            f"I={self.ecu.pump_cur:.3f} A")


# ═══════════════════════════════════════════════════════════════
#  Panneau Qt — ScenarioReplayPanel
# ═══════════════════════════════════════════════════════════════
_CARD = (
    f"QFrame{{background:{W_PANEL};border:1px solid {W_BORDER};"
    "border-radius:6px;padding:4px;}}"
)
_HDR = f"QFrame{{background:{W_DOCK_HDR};border-radius:4px;}}"


class ScenarioReplayPanel(QWidget):
    """
    Panneau Scenario Replay avec VirtualECU intégré.

    IMPORTANT : Appeler connect_panels() depuis MainWindow après
    que tous les autres panels soient construits, pour que le
    VirtualECU puisse les piloter directement.
    """

    def __init__(self,
                 can_worker=None,
                 lin_worker=None,
                 motor_worker=None,
                 rte_client=None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("ScenarioReplayPanel")
        self.setStyleSheet(
            f"QWidget#ScenarioReplayPanel{{background:{W_BG};}}")

        self._engine = ScenarioEngine(
            can_worker=can_worker,
            lin_worker=lin_worker,
            motor_worker=motor_worker,
            rte_client=rte_client,
            parent=self,
        )
        self._rows: list[ScenarioRow] = []
        self._csv_path: str = ""

        self._engine.step_fired.connect(self._on_step_fired)
        self._engine.replay_finished.connect(self._on_finished)
        self._engine.log_msg.connect(self._log)
        self._engine.progress_changed.connect(self._on_progress)

        self._build_ui()
        self._set_controls_enabled(False)

    # ── API publique ──────────────────────────────────────────

    def connect_panels(self, motor_panel=None, pump_panel=None,
                       veh_panel=None, crslin_panel=None,
                       can_panel=None, car_html=None,
                       car_html_mp=None, main_window=None):
        """
        Connecte tous les composants UI au VirtualECU.
        À appeler depuis MainWindow après construction de tous les panels.
        """
        self._engine.ecu.connect_panels(
            motor_panel=motor_panel,
            pump_panel=pump_panel,
            veh_panel=veh_panel,
            crslin_panel=crslin_panel,
            can_panel=can_panel,
            car_html=car_html,
            car_html_mp=car_html_mp,
            main_window=main_window,
        )
        self._log("🔗 VirtualECU connecté à tous les composants plateforme")

    def set_rte_client(self, rte_client):
        self._engine._rte = rte_client

    # ── Construction UI ───────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet(_HDR)
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 4, 12, 4)
        lbl_title = QLabel("Scenario Replay  —  Full Platform Simulation")
        lbl_title.setFont(QFont(FONT_UI, 13, QFont.Weight.Bold))
        lbl_title.setStyleSheet(f"color:{W_TEXT_HDR};background:transparent;")
        self._mode_badge = QLabel("NO FILE")
        self._mode_badge.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        self._mode_badge.setStyleSheet(
            "color:#AAA;background:rgba(255,255,255,0.07);"
            "border-radius:3px;padding:2px 8px;")
        hl.addWidget(lbl_title)
        hl.addStretch()
        hl.addWidget(self._mode_badge)
        root.addWidget(hdr)

        # Toolbar
        tb = QFrame()
        tb.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border:none;border-radius:4px;}}")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(8, 4, 8, 4)
        tbl.setSpacing(8)

        self._btn_load  = _cd_btn("📂  Load CSV",  "#607D8B", h=28)
        self._btn_play  = _cd_btn("▶  Play",       A_GREEN,   h=28)
        self._btn_pause = _cd_btn("⏸  Pause",      A_AMBER,   h=28)
        self._btn_stop  = _cd_btn("⏹  Stop",       A_RED,     h=28)
        self._btn_load.clicked.connect(self._on_load)
        self._btn_play.clicked.connect(self._on_play)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_stop.clicked.connect(self._on_stop)

        tbl.addWidget(self._btn_load)
        tbl.addWidget(_vsep())
        tbl.addWidget(self._btn_play)
        tbl.addWidget(self._btn_pause)
        tbl.addWidget(self._btn_stop)
        tbl.addWidget(_vsep())

        lbl_spd = _lbl("Vitesse ×", 10, color=W_TEXT_DIM)
        self._spd_spin = QDoubleSpinBox()
        self._spd_spin.setRange(0.1, 10.0)
        self._spd_spin.setSingleStep(0.5)
        self._spd_spin.setValue(1.0)
        self._spd_spin.setFixedWidth(70)
        self._spd_spin.setFont(QFont(FONT_MONO, 10))
        self._spd_spin.valueChanged.connect(
            lambda v: self._engine.set_speed(v))
        self._spd_spin.setStyleSheet(
            f"background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:3px;")
        tbl.addWidget(lbl_spd)
        tbl.addWidget(self._spd_spin)
        tbl.addStretch()

        virt_badge = QLabel("⚡ Virtual ECU — toute la plateforme pilotée")
        virt_badge.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        virt_badge.setStyleSheet(
            f"color:{KPIT_GREEN};background:rgba(141,198,63,0.12);"
            f"border:1px solid rgba(141,198,63,0.4);"
            f"border-radius:4px;padding:2px 8px;")
        tbl.addWidget(virt_badge)
        tbl.addWidget(_vsep())

        self._chk = {}
        for src in ("motor", "lin", "can", "pump"):
            c = QCheckBox(_SRC_LABEL[src])
            c.setChecked(True)
            c.setFont(QFont(FONT_MONO, 9))
            c.setStyleSheet(f"color:{_SRC_COLOR[src]};background:transparent;")
            self._chk[src] = c
            tbl.addWidget(c)
        root.addWidget(tb)

        # Progress
        pl = QHBoxLayout()
        pl.setSpacing(6)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(10)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{W_PANEL2};border:none;border-radius:5px;}}"
            f"QProgressBar::chunk{{background:{KPIT_GREEN};border-radius:5px;}}")
        self._lbl_prog = _lbl("0 / 0", 9, mono=True, color=W_TEXT_DIM)
        self._lbl_time = _lbl("0.0 s", 9, mono=True, color=W_TEXT_DIM)
        pl.addWidget(self._progress, 1)
        pl.addWidget(self._lbl_prog)
        pl.addWidget(self._lbl_time)
        root.addLayout(pl)

        # Splitter
        spl = QSplitter(Qt.Orientation.Horizontal)

        # Timeline
        tl = QFrame()
        tl.setStyleSheet(_CARD)
        tll = QVBoxLayout(tl)
        tll.setContentsMargins(6, 6, 6, 6)
        tll.setSpacing(4)
        tll.addWidget(_lbl("Timeline", 10, bold=True, color=KPIT_GREEN))
        tll.addWidget(_hsep())
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["#", "T (s)", "Source", "Stimulus", "État"])
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 48)
        self._table.setColumnWidth(1, 72)
        self._table.setColumnWidth(2, 64)
        self._table.setColumnWidth(4, 64)
        self._table.setFont(QFont(FONT_MONO, 9))
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{W_PANEL};color:{W_TEXT};"
            f"gridline-color:{W_BORDER};border:none;}}"
            f"QTableWidget::item:alternate{{background:{W_PANEL2};}}"
            f"QTableWidget::item:selected{{"
            f"background:rgba(141,198,63,0.22);color:{W_TEXT};}}")
        self._table.cellDoubleClicked.connect(self._on_seek)
        tll.addWidget(self._table)
        spl.addWidget(tl)

        # Droite : stats + log
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        sf = QFrame()
        sf.setStyleSheet(_CARD)
        sl = QVBoxLayout(sf)
        sl.setContentsMargins(8, 6, 8, 6)
        sl.setSpacing(4)
        sl.addWidget(_lbl("Statistiques", 10, bold=True, color=KPIT_GREEN))
        sl.addWidget(_hsep())
        self._stat_lbl = QLabel("—")
        self._stat_lbl.setFont(QFont(FONT_MONO, 9))
        self._stat_lbl.setStyleSheet(
            f"color:{W_TEXT};background:transparent;")
        self._stat_lbl.setWordWrap(True)
        sl.addWidget(self._stat_lbl)
        rl.addWidget(sf)

        lf = QFrame()
        lf.setStyleSheet(_CARD)
        ll = QVBoxLayout(lf)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.setSpacing(4)
        ll.addWidget(_lbl("Journal d'exécution", 10,
                          bold=True, color=KPIT_GREEN))
        ll.addWidget(_hsep())
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFont(QFont(FONT_MONO, 9))
        self._log_edit.setStyleSheet(
            f"background:{W_PANEL2};color:{W_TEXT};"
            "border:none;border-radius:3px;")
        ll.addWidget(self._log_edit)
        btn_clr = _cd_btn("Effacer log", "#607D8B", h=100, w=400)
        btn_clr.clicked.connect(self._log_edit.clear)
        ll.addWidget(btn_clr, alignment=Qt.AlignmentFlag.AlignRight)
        rl.addWidget(lf, 1)

        spl.addWidget(rw)
        spl.setSizes([620, 320])
        root.addWidget(spl, 1)

    # ── Contrôles ─────────────────────────────────────────────

    def _set_controls_enabled(self, loaded: bool):
        self._btn_play.setEnabled(loaded)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)

    def _set_playing(self, playing: bool):
        self._btn_play.setEnabled(not playing)
        self._btn_pause.setEnabled(playing)
        self._btn_stop.setEnabled(True)

    # ── Slots boutons ─────────────────────────────────────────

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger un CSV enregistré", "", "CSV (*.csv);;Tous (*)")
        if path:
            self._csv_path = path
            self._load_csv(path)

    def _load_csv(self, path: str):
        sources = {s for s, c in self._chk.items() if c.isChecked()}
        rows, err = CsvScenarioLoader.load(path, sources or None)
        if err:
            self._log(f"❌ {err}")
            return
        self._rows = rows
        self._engine.load(rows)
        self._populate_table(rows)
        self._update_stats(rows)
        self._set_controls_enabled(True)
        self._progress.setValue(0)
        self._lbl_prog.setText(f"0 / {len(rows)}")
        fname = os.path.basename(path)
        self._mode_badge.setText(fname)
        self._mode_badge.setStyleSheet(
            f"color:{KPIT_GREEN};background:rgba(141,198,63,0.12);"
            "border-radius:3px;padding:2px 8px;")
        self._log(f"📂 Chargé : {fname}  ({len(rows)} steps)")

    def _on_play(self):
        if self._engine._paused:
            self._engine.resume()
        else:
            self._engine.start()
        self._set_playing(True)

    def _on_pause(self):
        self._engine.pause()
        self._btn_play.setEnabled(True)
        self._btn_pause.setEnabled(False)

    def _on_stop(self):
        self._engine.stop()
        self._set_controls_enabled(bool(self._rows))
        self._set_playing(False)
        self._btn_play.setEnabled(bool(self._rows))
        self._progress.setValue(0)

    def _on_seek(self, row_idx: int, _: int):
        if self._engine._running:
            self._engine.seek(row_idx)
            self._log(f"⏩ Seek → step {row_idx}")

    # ── Slots moteur ──────────────────────────────────────────

    def _on_step_fired(self, idx: int):
        self._table.selectRow(idx)
        self._table.scrollTo(self._table.model().index(idx, 0))
        item = QTableWidgetItem("✓")
        item.setForeground(QColor(A_GREEN))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(idx, 4, item)
        if self._rows:
            self._lbl_time.setText(f"{self._rows[idx].t_rel:.1f} s")

    def _on_finished(self):
        self._set_playing(False)
        self._btn_play.setEnabled(bool(self._rows))
        self._progress.setValue(100)
        self._log("✅ Replay terminé — tous les composants synchronisés")

    def _on_progress(self, done: int, total: int):
        pct = int(done / total * 100) if total else 0
        self._progress.setValue(pct)
        self._lbl_prog.setText(f"{done} / {total}")

    # ── Table ─────────────────────────────────────────────────

    def _populate_table(self, rows: list[ScenarioRow]):
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            color = _SRC_COLOR.get(row.source, W_TEXT)

            def _item(text, align=Qt.AlignmentFlag.AlignLeft,
                      c=color) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setForeground(QColor(c))
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                return it

            self._table.setItem(i, 0, _item(str(i),
                Qt.AlignmentFlag.AlignCenter, W_TEXT_DIM))
            self._table.setItem(i, 1, _item(f"{row.t_rel:.3f}",
                Qt.AlignmentFlag.AlignRight))
            self._table.setItem(i, 2, _item(
                _SRC_LABEL.get(row.source, row.source.upper()),
                Qt.AlignmentFlag.AlignCenter))
            self._table.setItem(i, 3, _item(row.summary))
            self._table.setItem(i, 4, _item("·",
                Qt.AlignmentFlag.AlignCenter, W_TEXT_DIM))
        self._table.resizeRowsToContents()

    # ── Stats ─────────────────────────────────────────────────

    def _update_stats(self, rows: list[ScenarioRow]):
        if not rows:
            self._stat_lbl.setText("—")
            return
        dur = (rows[-1].t_rel - rows[0].t_rel) if len(rows) > 1 else 0.0
        cnt = {s: sum(1 for r in rows if r.source == s)
               for s in ("motor", "lin", "can", "pump")}
        fname = os.path.basename(self._csv_path)
        lines = [
            f"Fichier : {fname}",
            f"Steps   : {len(rows)}",
            f"Durée   : {dur:.2f} s",
            f"Mode    : Virtual ECU (toutes sources pilotées)",
            "",
        ]
        for src, n in cnt.items():
            if n:
                lines.append(f"  {_SRC_LABEL[src]:<7} : {n} événements")
        self._stat_lbl.setText("\n".join(lines))

    # ── Log ───────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_edit.append(f"[{ts}]  {msg}")
        sb = self._log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())


# ── Séparateur vertical ───────────────────────────────────────

def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setStyleSheet(
        "background:rgba(141,198,63,0.35);border:none;max-width:1px;")
    return f