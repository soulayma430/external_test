"""
mdf_exporter.py  —  Export MDF4 (ASAM MDF 4.1) pour WipeWash
=============================================================
Convertit les données du DataRecorder en fichier .mf4 standard
utilisable dans MATLAB, CANalyzer, INCA, dSPACE, etc.

Architecture des canaux MDF4 produits :
  ┌─ Group "Motor"
  │   ├── t_motor         [s]   axe temps
  │   ├── motor_state     [enum text]
  │   ├── front_motor_on  [bool]
  │   ├── rear_motor_on   [bool]
  │   ├── motor_speed     [RPM]
  │   ├── motor_current   [A]
  │   ├── crs_wiper_op    [int]
  │   ├── vehicle_speed   [km/h]
  │   └── rain_intensity  [%]
  ├─ Group "LIN"
  │   ├── t_lin           [s]
  │   ├── lin_type        [text]
  │   ├── pid             [hex text]
  │   └── wiper_op        [int]
  ├─ Group "CAN"
  │   ├── t_can           [s]
  │   ├── can_id          [hex text]
  │   ├── direction       [text]
  │   └── payload         [hex text]
  └─ Group "Pump"
      ├── t_pump          [s]
      ├── pump_flow       [L/min]
      ├── pump_pressure   [bar]
      ├── pump_current    [A]
      └── pump_state      [text]

Usage :
    from mdf_exporter import MDFExporter
    exp = MDFExporter(bench_id="Banc-A")
    path = exp.export(recorder, output_dir="/tmp")
"""

from __future__ import annotations

import os
import datetime
from typing import Optional

import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> float:
    """
    Convertit un timestamp string "%Y-%m-%d %H:%M:%S.%f" en float epoch.
    Retourne 0.0 si invalide.
    """
    if not ts_str:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts_str, fmt).timestamp()
        except ValueError:
            pass
    return 0.0


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _state_to_int(state: str) -> int:
    """Encode motor state string → int pour canal numérique MDF."""
    MAP = {
        "OFF": 0, "TOUCH": 1, "SPEED1": 2, "SPEED2": 3,
        "AUTO": 4, "FRONT_WASH": 5, "REAR_WASH": 6, "REAR_WIPE": 7,
    }
    return MAP.get(str(state).upper(), 0)


# ── Classe principale ──────────────────────────────────────────────────────

class MDFExporter:
    """
    Exporte les données du DataRecorder en fichier MDF4 (.mf4).

    Args:
        bench_id : identifiant du banc HIL (inscrit dans les metadata MDF)
        project  : nom du projet
    """

    def __init__(self,
                 bench_id: str = "WipeWash-Bench",
                 project:  str = "WipeWash Automotive HIL"):
        self._bench_id = bench_id
        self._project  = project

    # ── API publique ──────────────────────────────────────────────────

    def export(self,
               recorder,
               output_dir: str = ".",
               base_name:  str = "") -> Optional[str]:
        """
        Convertit toutes les données du recorder en MDF4.

        Args:
            recorder   : instance DataRecorder
            output_dir : dossier de sortie
            base_name  : préfixe fichier (auto si vide)

        Returns:
            chemin du fichier .mf4 créé, ou None en cas d'erreur.
        """
        try:
            from asammdf import MDF, Signal
        except ImportError:
            print("[MDFExporter] asammdf non installé — pip install asammdf")
            return None

        os.makedirs(output_dir, exist_ok=True)
        if not base_name:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"wipewash_{ts}"

        rows = recorder.get_rows()
        if not rows:
            print("[MDFExporter] Aucune donnée à exporter.")
            return None

        mdf = MDF(version="4.10")

        # ── Metadata globale ──────────────────────────────────────────
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mdf.header.comment = (
            f"WipeWash HIL Test Bench\n"
            f"Project : {self._project}\n"
            f"Bench   : {self._bench_id}\n"
            f"Date    : {now_str}\n"
            f"Tool    : WipeWash Platform v4 / asammdf"
        )

        # ── Dispatcher par source ─────────────────────────────────────
        src_rows: dict[str, list] = {}
        for r in rows:
            src = r.get("source", "motor")
            src_rows.setdefault(src, []).append(r)

        builders = {
            "motor": self._build_motor_group,
            "lin":   self._build_lin_group,
            "can":   self._build_can_group,
            "pump":  self._build_pump_group,
        }

        for src, builder in builders.items():
            data = src_rows.get(src, [])
            if data:
                signals = builder(data, Signal)
                if signals:
                    mdf.append(signals, acq_name=src.upper())

        # ── Écriture ──────────────────────────────────────────────────
        path = os.path.join(output_dir, base_name + ".mf4")
        mdf.save(path, overwrite=True)
        print(f"[MDFExporter] Fichier MDF4 créé : {path}  "
              f"({len(rows)} échantillons)")
        return path

    # ── Builders par groupe ───────────────────────────────────────────

    def _build_motor_group(self, rows: list, Signal) -> list:
        """Groupe Motor : état wiper, courant, vitesse, etc."""
        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts, state_i, front, rear, speed, current, crs, vspeed, rain = (
            [] for _ in range(9)
        )
        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)
            state_i.append(_state_to_int(r.get("state", "OFF")))
            front.append(1 if r.get("front") in (True, "True", "1", 1) else 0)
            rear.append(1  if r.get("rear")  in (True, "True", "1", 1) else 0)
            speed.append(_safe_float(r.get("speed")))
            current.append(_safe_float(r.get("current")))
            crs.append(_safe_int(r.get("crs_wiper_op", 0)))
            vspeed.append(_safe_float(r.get("vehicle_speed")))
            rain.append(_safe_float(r.get("rain_intensity")))

        ta = np.array(ts, dtype=np.float64)

        return [
            Signal(samples=np.array(state_i, dtype=np.int32), timestamps=ta,
                   name="motor_state_int",  unit="",    comment="Wiper state (0=OFF…7=REAR_WIPE)"),
            Signal(samples=np.array(front, dtype=np.uint8), timestamps=ta,
                   name="front_motor_on",   unit="bool"),
            Signal(samples=np.array(rear,  dtype=np.uint8), timestamps=ta,
                   name="rear_motor_on",    unit="bool"),
            Signal(samples=np.array(speed,   dtype=np.float32), timestamps=ta,
                   name="motor_speed",      unit="RPM"),
            Signal(samples=np.array(current, dtype=np.float32), timestamps=ta,
                   name="motor_current",    unit="A"),
            Signal(samples=np.array(crs,     dtype=np.int32),   timestamps=ta,
                   name="crs_wiper_op",     unit="",    comment="0=OFF 1=TOUCH 2=SPD1…"),
            Signal(samples=np.array(vspeed,  dtype=np.float32), timestamps=ta,
                   name="vehicle_speed",    unit="km/h"),
            Signal(samples=np.array(rain,    dtype=np.float32), timestamps=ta,
                   name="rain_intensity",   unit="%"),
        ]

    def _build_lin_group(self, rows: list, Signal) -> list:
        """Groupe LIN : type trame, PID, wiper_op."""
        if not rows:
            return []
        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts, wiper_op, pid_i = [], [], []
        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)
            wiper_op.append(_safe_int(r.get("wiper_op", 0)))
            # PID hex string → int
            pid_str = r.get("pid", "0x00") or "0x00"
            try:
                pid_i.append(int(pid_str, 16) if pid_str.startswith("0x") else int(pid_str))
            except (ValueError, TypeError):
                pid_i.append(0)

        ta = np.array(ts, dtype=np.float64)
        return [
            Signal(samples=np.array(wiper_op, dtype=np.int32), timestamps=ta,
                   name="lin_wiper_op",  unit="", comment="Wiper op from LIN frame"),
            Signal(samples=np.array(pid_i, dtype=np.uint8), timestamps=ta,
                   name="lin_pid",       unit="", comment="LIN PID (7-bit frame ID)"),
        ]

    def _build_can_group(self, rows: list, Signal) -> list:
        """Groupe CAN : ID, direction (0=RX / 1=TX)."""
        if not rows:
            return []
        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts, can_id, direction = [], [], []
        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)
            cid_str = r.get("can_id", "0x000") or "0x000"
            try:
                cid = int(cid_str, 16) if cid_str.startswith("0x") else int(cid_str)
            except (ValueError, TypeError):
                cid = 0
            can_id.append(cid)
            direction.append(0 if str(r.get("direction", "RX")).upper() == "RX" else 1)

        ta = np.array(ts, dtype=np.float64)
        return [
            Signal(samples=np.array(can_id,    dtype=np.uint32), timestamps=ta,
                   name="can_id",        unit="", comment="CAN frame ID"),
            Signal(samples=np.array(direction, dtype=np.uint8),  timestamps=ta,
                   name="can_direction", unit="", comment="0=RX 1=TX"),
        ]

    def _build_pump_group(self, rows: list, Signal) -> list:
        """Groupe Pump : débit, pression, courant, état."""
        if not rows:
            return []
        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts, flow, pressure, current, active = [], [], [], [], []
        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)
            flow.append(_safe_float(r.get("flow")))
            pressure.append(_safe_float(r.get("pressure")))
            current.append(_safe_float(r.get("current")))
            active.append(1 if r.get("active") in (True, "True", "1", 1) else 0)

        ta = np.array(ts, dtype=np.float64)
        return [
            Signal(samples=np.array(flow,     dtype=np.float32), timestamps=ta,
                   name="pump_flow",     unit="L/min"),
            Signal(samples=np.array(pressure, dtype=np.float32), timestamps=ta,
                   name="pump_pressure", unit="bar"),
            Signal(samples=np.array(current,  dtype=np.float32), timestamps=ta,
                   name="pump_current",  unit="A"),
            Signal(samples=np.array(active,   dtype=np.uint8),   timestamps=ta,
                   name="pump_active",   unit="bool"),
        ]
