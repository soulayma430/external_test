"""
WipeWash — Widgets instruments graphiques (redesign v2)
PumpWidget, MotorWidget, WindshieldWidget, CarTopViewWidget (BMW M4 3D).

Design : fond global clair (blanc/vert KPIT), zones canvas internes sombres
pour les widgets moteur/pompe (identique à motor_pump_design_preview.html).
Gauges arc + sparklines pour courant/tension.
CarTopViewWidget (vue 3D BMW) conservé mais optionnel.
"""

import math
import random
from collections import deque

from PySide6.QtWidgets import QWidget, QSizePolicy, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore    import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient,
    QPainterPath, QPolygonF,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2, W_TEXT, W_TEXT_DIM,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    WOP,
)

# ── Palette interne des canvas — thème clair KPIT ────────────────────────────
_C_BG        = QColor("#EDF9E3")   # fond canvas : vert pâle KPIT (W_PANEL2)
_C_GRID      = QColor(90, 150, 60, 30)  # grille subtile verte
_C_GREEN     = QColor("#8DC63F")   # KPIT green principal
_C_TEAL      = QColor("#007ACC")   # bleu teal (A_TEAL)
_C_ORANGE    = QColor("#D35400")   # orange (A_ORANGE)
_C_RED       = QColor("#C0392B")   # rouge (A_RED)
_C_DIM       = QColor("#5A6A4A")   # gris-vert sombre (W_TEXT_DIM)
_C_METAL0    = QColor("#7A9A5A")   # métal vert clair
_C_METAL1    = QColor("#5A7A3A")   # métal vert moyen
_C_METAL2    = QColor("#3A5A20")   # métal vert foncé
_C_COIL_R    = QColor("#C04018")   # bobine rouge
_C_COIL_B    = QColor("#1060C0")   # bobine bleue
_C_COIL_RD   = QColor("#A83010")   # bobine rouge dim
_C_COIL_BD   = QColor("#0840A0")   # bobine bleue dim

def _hex_alpha(qc: QColor, alpha: int) -> QColor:
    c = QColor(qc); c.setAlpha(alpha); return c


# ═══════════════════════════════════════════════════════════
#  MOTOR WIDGET (canvas sombre, fond global clair)
# ═══════════════════════════════════════════════════════════
class MotorWidget(QWidget):
    """
    Moteur électrique CC brushless — vue de face, canvas sombre intégré
    dans la page claire (identique au HTML de référence).
    Arc tachymétrique extérieur, rotor animé, shaft.
    """
    def __init__(self, motor_id: str = "FRONT", parent=None) -> None:
        super().__init__(parent)
        self._id    = motor_id
        self._state = "OFF"
        self._speed = "Speed1"
        self._angle = 0.0
        self._heat  = 0.0
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(40)
        self.setMinimumSize(180, 200)

    def set_state(self, state: str, speed: str = "Speed1") -> None:
        self._state = state
        self._speed = speed

    def _tick(self) -> None:
        on = self._state == "ON"
        if on:
            spd = 7.5 if self._speed == "Speed2" else 4.0
            self._angle = (self._angle + spd) % 360
            self._heat  = min(1.0, self._heat + 0.02)
            self.update()
        else:
            if self._angle % 360 > 2:
                self._angle = (self._angle + 0.7) % 360
                self._heat  = max(0.0, self._heat - 0.01)
                self.update()
            elif self._heat > 0:
                self._heat = max(0.0, self._heat - 0.005)
                self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        on   = self._state == "ON"
        spd2 = on and self._speed == "Speed2"
        accent = _C_TEAL if spd2 else (_C_GREEN if on else _C_DIM)

        # ── Fond global du widget : blanc-vert KPIT (s'intègre à la page) ────
        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor("#FFFFFF"))
        bg.setColorAt(1, QColor("#F0F9E8"))
        p.fillRect(0, 0, W, H, QBrush(bg))

        TOP_H = 22
        # ── Header clair ──────────────────────────────────────────────────
        hdr = QLinearGradient(0, 0, W, TOP_H)
        if on:
            hdr.setColorAt(0, QColor("#8DC63F")); hdr.setColorAt(1, QColor("#8DC63F"))
        else:
            hdr.setColorAt(0, QColor("#8DC63F")); hdr.setColorAt(1, QColor("#8DC63F"))
        p.fillRect(0, 0, W, TOP_H, QBrush(hdr))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 120), 1))
        p.drawLine(0, TOP_H, W, TOP_H)

        lbl_txt = ("▲▲ SPEED 2" if spd2 else ("▲ SPEED 1" if on else "◼ STANDBY"))
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(0, 0, W, TOP_H, Qt.AlignmentFlag.AlignCenter, lbl_txt)

        # ── Zone canvas clair KPIT pour le moteur ─────────────────────────
        CANVAS_Y = TOP_H + 2
        CANVAS_H = H - CANVAS_Y - 2
        p.fillRect(0, CANVAS_Y, W, CANVAS_H, QBrush(_C_BG))

        # Grille subtile verte
        p.setPen(QPen(_C_GRID, 1))
        for x in range(0, W, 18):
            p.drawLine(x, CANVAS_Y, x, H - 2)
        for y in range(CANVAS_Y, H, 18):
            p.drawLine(0, y, W, y)

        cx = W // 2
        cy = CANVAS_Y + CANVAS_H // 2
        R  = min(CANVAS_H // 2 - 16, W // 2 - 18, 52)

        # ── Arc tachymétrique (outside) ───────────────────────────────────
        ARC_R = R + 11
        ARC_S = 2.35; ARC_SPAN = 5.23
        p.setPen(QPen(QColor("#C8E6C0"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(cx - ARC_R, cy - ARC_R, ARC_R * 2, ARC_R * 2),
                  int(-math.degrees(ARC_S) * 16), int(-math.degrees(ARC_SPAN) * 16))
        if on:
            pct = 0.85 if spd2 else 0.50
            p.setPen(QPen(_hex_alpha(accent, 220), 5,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx - ARC_R, cy - ARC_R, ARC_R * 2, ARC_R * 2),
                      int(-math.degrees(ARC_S) * 16),
                      int(-math.degrees(ARC_SPAN * pct) * 16))
        # Tick marks
        for i in range(6):
            a = ARC_S + ARC_SPAN * i / 5
            r0 = ARC_R - 3; r1 = ARC_R + 3
            p.setPen(QPen(QColor("#8DC63F"), 1.5))
            p.drawLine(int(cx + r0 * math.cos(a)), int(cy + r0 * math.sin(a)),
                       int(cx + r1 * math.cos(a)), int(cy + r1 * math.sin(a)))

        # ── Carcasse (carter) ─────────────────────────────────────────────
        grad = QLinearGradient(cx - R, cy - R, cx + R, cy + R)
        grad.setColorAt(0, _C_METAL0); grad.setColorAt(0.4, _C_METAL1)
        grad.setColorAt(1, _C_METAL2)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor("#2A4A1A"), 3))
        p.drawEllipse(int(cx - R), int(cy - R), int(R * 2), int(R * 2))

        # ── Stator bobiné (12 encoches) ───────────────────────────────────
        n_slots = 12; slot_out = R * 0.90; slot_in = R * 0.60
        for i in range(n_slots):
            ang = i * (math.pi * 2 / n_slots)
            aw  = 0.18
            pts = [(ang - aw * 0.7, slot_in), (ang + aw * 0.7, slot_in),
                   (ang + aw * 0.7, slot_out), (ang - aw * 0.7, slot_out)]
            path = QPainterPath()
            path.moveTo(cx + pts[0][1] * math.cos(pts[0][0]),
                        cy + pts[0][1] * math.sin(pts[0][0]))
            for a, r in pts[1:]:
                path.lineTo(cx + r * math.cos(a), cy + r * math.sin(a))
            path.closeSubpath()
            p.setBrush(QBrush(QColor("#4A6A2A") if on else QColor("#3A5A1A")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)
            # Bobine
            coil_r = (slot_in + slot_out) / 2
            coil_c = (_C_COIL_R if on else _C_COIL_RD) if i % 2 == 0 else \
                     (_C_COIL_B if on else _C_COIL_BD)
            cr = int(R * 0.07)
            p.setBrush(QBrush(coil_c))
            p.setPen(QPen(_hex_alpha(coil_c, 180), 0.5))
            cx2 = int(cx + coil_r * math.cos(ang))
            cy2 = int(cy + coil_r * math.sin(ang))
            p.drawEllipse(cx2 - cr, cy2 - cr, cr * 2, cr * 2)

        # Entrefer
        p.setBrush(QBrush(QColor("#C8E6C0")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - R * 0.58), int(cy - R * 0.58),
                      int(R * 0.58 * 2), int(R * 0.58 * 2))

        # ── Rotor (4 pôles, aimants permanents) ───────────────────────────
        rotor_r = R * 0.50; n_poles = 4
        for i in range(n_poles):
            a0 = math.radians(self._angle) + i * (math.pi * 2 / n_poles)
            a1 = a0 + math.pi * 2 / n_poles * 0.88
            path = QPainterPath()
            path.moveTo(cx, cy)
            path.arcTo(QRectF(cx - rotor_r, cy - rotor_r, rotor_r * 2, rotor_r * 2),
                       math.degrees(-a0), math.degrees(-(a1 - a0)))
            path.closeSubpath()
            north = i % 2 == 0
            p.setBrush(QBrush(
                (QColor("#C03018") if on else QColor("#A02010")) if north else
                (QColor("#1060C0") if on else QColor("#0848A0"))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        # Disque rotor
        disc_r = int(rotor_r * 0.50)
        dg = QRadialGradient(cx - 3, cy - 3, disc_r)
        dg.setColorAt(0, QColor("#D8EEC8")); dg.setColorAt(1, QColor("#6A8A4A"))
        p.setBrush(QBrush(dg))
        p.setPen(QPen(QColor("#2A4A1A"), 2))
        p.drawEllipse(cx - disc_r, cy - disc_r, disc_r * 2, disc_r * 2)

        # Rayons
        p.setPen(QPen(QColor("#8DC63F"), 1.5))
        for i in range(6):
            a = math.radians(self._angle) + i * math.pi / 3
            p.drawLine(int(cx + 5 * math.cos(a)), int(cy + 5 * math.sin(a)),
                       int(cx + (disc_r - 2) * math.cos(a)),
                       int(cy + (disc_r - 2) * math.sin(a)))

        # Moyeu central
        hg2 = QRadialGradient(cx - 1, cy - 1, 6)
        hg2.setColorAt(0, QColor("#E8F8D8")); hg2.setColorAt(1, QColor("F28E79"))
        p.setBrush(QBrush(hg2))
        p.setPen(QPen(QColor("#2A4A1A"), 1.5))
        p.drawEllipse(cx - 6, cy - 6, 12, 12)

        # ── Flèche de rotation ────────────────────────────────────────────
        if on:
            ar_r = R * 0.65
            p.setPen(QPen(_hex_alpha(accent, 200), 2,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - ar_r, cy - ar_r, ar_r * 2, ar_r * 2),
                      int(0.5 * 180 / math.pi * 16), int((2.8 - 0.5) * 180 / math.pi * 16))
            aT = 2.8
            tx = cx + ar_r * math.cos(aT); ty = cy + ar_r * math.sin(aT)
            pts = QPolygonF([
                QPointF(tx, ty),
                QPointF(tx + 8 * math.cos(aT - 2.0), ty + 8 * math.sin(aT - 2.0)),
                QPointF(tx + 8 * math.cos(aT - 1.6), ty + 8 * math.sin(aT - 1.6)),
            ])
            p.setBrush(QBrush(accent)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(pts)

        # ── LED état ──────────────────────────────────────────────────────
        led_x = W - 12; led_y = CANVAS_Y + 10
        p.setBrush(QBrush(accent if on else QColor("#C8E6C0")))
        p.setPen(QPen(QColor("#2A4A1A"), 1))
        p.drawEllipse(led_x - 5, led_y - 5, 10, 10)
        if on:
            p.setBrush(QBrush(_hex_alpha(accent, 45)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(led_x - 9, led_y - 9, 18, 18)

        # ── Shaft (arbre, vers le bas) ────────────────────────────────────
        shaft_top = int(cy + R + 4)
        shaft_bot = H - 4
        if shaft_bot > shaft_top + 4:
            sg = QLinearGradient(cx - 5, shaft_top, cx + 5, shaft_top)
            sg.setColorAt(0, QColor("#A8C880"))
            sg.setColorAt(0.4, QColor("#D8EEC8"))
            sg.setColorAt(1, QColor("#6A8A4A"))
            p.setBrush(QBrush(sg))
            p.setPen(QPen(QColor("#2A4A1A"), 1.5))
            p.drawRoundedRect(cx - 5, shaft_top, 10, shaft_bot - shaft_top, 3, 3)


# ═══════════════════════════════════════════════════════════
#  PUMP WIDGET (canvas sombre intégré dans fond clair)
# ═══════════════════════════════════════════════════════════
class PumpWidget(QWidget):
    """
    Pompe centrifuge — canvas sombre, aubes animées, état, courant.
    """
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state   = "OFF"
        self._current = 0.0
        self._fault   = False
        self._rain    = 0
        self._angle   = 0.0
        self._flow_offset = 0.0
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(40)
        self.setMinimumSize(200, 160)

    def set_state(self, state: str, current: float = 0.0, fault: bool = False) -> None:
        self._state   = state
        self._current = current
        self._fault   = fault

    def set_rain(self, pct: int) -> None:
        self._rain = pct

    def _tick(self) -> None:
        running = self._state in ("FORWARD", "BACKWARD")
        spd = 6.0 if self._state == "FORWARD" else (-6.0 if self._state == "BACKWARD" else 0.0)
        if running:
            self._angle       = (self._angle + spd) % 360
            self._flow_offset = (self._flow_offset + 3) % 60
        elif abs(self._angle % 360) > 1.5:
            self._angle = (self._angle + 1.2) % 360
        else:
            return
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        running = self._state in ("FORWARD", "BACKWARD")
        fw      = self._state == "FORWARD"
        accent  = _C_GREEN if (running and fw) else (_C_ORANGE if running else _C_DIM)

        # ── Fond global clair ─────────────────────────────────────────────
        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor("#FFFFFF")); bg.setColorAt(1, QColor("#F0F9E8"))
        p.fillRect(0, 0, W, H, QBrush(bg))

        TOP_H = 22
        # Header clair
        hdr = QLinearGradient(0, 0, W, TOP_H)
        if running:
            hdr.setColorAt(0, QColor("#E0F5E8")); hdr.setColorAt(1, QColor("#D0EFD8"))
        else:
            hdr.setColorAt(0, QColor("#F0F4F0")); hdr.setColorAt(1, QColor("#E8EEE8"))
        p.fillRect(0, 0, W, TOP_H, QBrush(hdr))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 120), 1))
        p.drawLine(0, TOP_H, W, TOP_H)

        state_txt = ("FORWARD ▶" if fw else "◀ BACKWARD") if running else "OFF"
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(4, 0, W - 8, TOP_H,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, state_txt)
        # Courant en haut-droite
        cur_c = _C_RED if self._fault else (_C_ORANGE if self._current > 0.7 else _C_TEAL)
        p.setPen(QPen(cur_c))
        p.drawText(0, 0, W - 4, TOP_H,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"I={self._current:.3f}A")

        # ── Zone canvas clair KPIT ────────────────────────────────────────
        CY = TOP_H + 2
        CH = H - CY - 2
        p.fillRect(0, CY, W, CH, QBrush(_C_BG))

        # Grille subtile verte
        p.setPen(QPen(_C_GRID, 1))
        for x in range(0, W, 18):
            p.drawLine(x, CY, x, H - 2)
        for y in range(CY, H, 18):
            p.drawLine(0, y, W, y)

        cx = W // 2
        cy = CY + CH // 2
        R  = min(CH // 2 - 12, W // 2 - 42, 42)

        # Volute
        bg2 = QRadialGradient(cx - R * 0.2, cy - R * 0.2, R * 1.4)
        bg2.setColorAt(0, QColor("#C8EAB8") if running else QColor("#D4ECC8"))
        bg2.setColorAt(1, QColor("#8DC63F" if running else "#A8C880"))
        p.setBrush(QBrush(bg2))
        p.setPen(QPen(_hex_alpha(accent, 120), 2.5))
        p.drawEllipse(int(cx - R), int(cy - R), int(R * 2), int(R * 2))

        # Chambre intérieure
        ig = QRadialGradient(cx, cy, R * 0.82)
        ig.setColorAt(0, QColor("#E8F8D8") if running else QColor("#EDF9E3"))
        ig.setColorAt(1, QColor("#C8E6B0"))
        p.setBrush(QBrush(ig)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - R * 0.80), int(cy - R * 0.80),
                      int(R * 0.80 * 2), int(R * 0.80 * 2))

        # Aubes impeller
        n_blades = 7; b_r = R * 0.72; hub_r = R * 0.20
        for i in range(n_blades):
            a0 = math.radians(self._angle) + i * (math.pi * 2 / n_blades)
            a1 = a0 + 0.45
            x0 = cx + hub_r * math.cos(a0); y0 = cy + hub_r * math.sin(a0)
            xm = cx + b_r * 0.55 * math.cos(a0 + 0.22)
            ym = cy + b_r * 0.55 * math.sin(a0 + 0.22)
            x1 = cx + b_r * math.cos(a1); y1 = cy + b_r * math.sin(a1)
            path = QPainterPath()
            path.moveTo(x0, y0)
            path.quadTo(xm, ym, x1, y1)
            p.setPen(QPen(accent, int(R * 0.12),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        # Hub
        hg = QRadialGradient(cx - 2, cy - 2, hub_r)
        hg.setColorAt(0, QColor("#D8EEC8")); hg.setColorAt(1, QColor("#6A8A4A"))
        p.setBrush(QBrush(hg))
        p.setPen(QPen(QColor("#2A4A1A"), 1.5))
        p.drawEllipse(int(cx - hub_r), int(cy - hub_r),
                      int(hub_r * 2), int(hub_r * 2))
        p.setBrush(QBrush(QColor("#8DC63F"))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # Tuyaux IN / OUT
        pH = 14; pLen = cx - R - 4
        pg = QLinearGradient(2, cy - pH // 2, 2, cy + pH // 2)
        pg.setColorAt(0, QColor("#A8C880")); pg.setColorAt(0.5, QColor("#C8E6A0"))
        pg.setColorAt(1, QColor("#6A8A4A"))
        p.setBrush(QBrush(pg)); p.setPen(QPen(QColor("#2A4A1A"), 1.5))
        if pLen > 4:
            p.drawRoundedRect(2, cy - pH // 2, int(pLen), pH, 3, 3)
            p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
            p.setPen(QPen(QColor("#2A4A1A")))
            p.drawText(2, cy - pH // 2, int(pLen), pH, Qt.AlignmentFlag.AlignCenter, "IN")
        out_x = int(cx + R + 2); out_w = W - out_x - 2
        if out_w > 6:
            p.setBrush(QBrush(pg)); p.setPen(QPen(QColor("#2A4A1A"), 1.5))
            p.drawRoundedRect(out_x, cy - pH // 2, out_w, pH, 3, 3)
            p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
            p.setPen(QPen(accent if running else QColor("#5A6A4A")))
            p.drawText(out_x, cy - pH // 2, out_w, pH,
                       Qt.AlignmentFlag.AlignCenter, "OUT")

        # Particules débit
        if running and pLen > 6:
            p.setPen(QPen(accent, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(3):
                off = int((self._flow_offset + i * 14) % max(pLen - 6, 1))
                ax = 4 + off
                if ax < pLen - 6:
                    p.drawLine(ax, cy - 4, ax + 5, cy)
                    p.drawLine(ax, cy + 4, ax + 5, cy)

        # LED état
        led_x = W - 10; led_y = CY + 10
        p.setBrush(QBrush(accent if running else QColor("#C8E6C0")))
        p.setPen(QPen(QColor("#2A4A1A"), 1))
        p.drawEllipse(led_x - 5, led_y - 5, 10, 10)
        if running:
            p.setBrush(QBrush(_hex_alpha(accent, 45)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(led_x - 9, led_y - 9, 18, 18)

        # Overlay fault
        if self._fault:
            p.setBrush(QBrush(QColor(192, 57, 43, 60)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(cx - R), int(cy - R), int(R * 2), int(R * 2))
            p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            p.setPen(QPen(_C_RED))
            p.drawText(int(cx - R), int(cy - 10), int(R * 2), 20,
                       Qt.AlignmentFlag.AlignCenter, "⚠ FAULT")


# ═══════════════════════════════════════════════════════════
#  ARC GAUGE WIDGET
# ═══════════════════════════════════════════════════════════
class ArcGaugeWidget(QWidget):
    """Gauge arc style HTML preview — canvas sombre."""
    def __init__(self, max_val: float = 1.5, unit: str = "A", parent=None):
        super().__init__(parent)
        self._val    = 0.0
        self._max    = max_val
        self._unit   = unit
        self._fault  = False
        self.setFixedHeight(100)
        self.setMinimumWidth(110)

    def set_value(self, val: float, fault: bool = False) -> None:
        self._val   = val
        self._fault = fault
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # Fond clair KPIT
        bg = QLinearGradient(0, 0, 0, H)
        bg.setColorAt(0, QColor("#F5FFF0")); bg.setColorAt(1, QColor("#EDF9E3"))
        p.fillRect(0, 0, W, H, QBrush(bg))

        cx = W // 2; cy = int(H * 0.72)
        R  = min(cx - 8, cy - 6, 38)
        pct  = min(self._val / max(self._max, 1e-9), 1.0)
        START = math.radians(215); SPAN = math.radians(250)

        # Zone bands
        for z0, z1, zc in [(0, 0.5, "#8DC63F"), (0.5, 0.75, "#D35400"), (0.75, 1.0, "#C0392B")]:
            c = QColor(zc); c.setAlpha(60)
            p.setPen(QPen(c, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx - R, cy - R, R * 2, R * 2),
                      int(-math.degrees(START + SPAN * z0) * 16),
                      int(-math.degrees(SPAN * (z1 - z0)) * 16))

        # Track fond gris-vert
        p.setPen(QPen(QColor("#C8E6C0"), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(cx - R, cy - R, R * 2, R * 2),
                  int(-math.degrees(START) * 16), int(-math.degrees(SPAN) * 16))

        # Fill arc
        if pct > 0.005:
            fc = _C_RED if self._fault else (_C_RED if pct >= 0.75 else
                 _C_ORANGE if pct >= 0.5 else _C_GREEN)
            p.setPen(QPen(fc, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx - R, cy - R, R * 2, R * 2),
                      int(-math.degrees(START) * 16),
                      int(-math.degrees(SPAN * pct) * 16))

        # Needle
        ang = START + SPAN * pct
        nx = cx + (R - 5) * math.cos(ang); ny = cy + (R - 5) * math.sin(ang)
        p.setPen(QPen(QColor("#2A4A1A"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, cy, int(nx), int(ny))
        p.setBrush(QBrush(QColor("#8DC63F"))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # Tick marks
        for i in range(6):
            ta = START + SPAN * i / 5
            p.setPen(QPen(QColor("#5A6A4A"), 1.5))
            p.drawLine(int(cx + (R - 4) * math.cos(ta)), int(cy + (R - 4) * math.sin(ta)),
                       int(cx + (R + 1) * math.cos(ta)), int(cy + (R + 1) * math.sin(ta)))

        # Value text
        vc = _C_RED if self._fault else (_C_ORANGE if pct >= 0.5 else _C_GREEN)
        p.setFont(QFont(FONT_MONO, 11, QFont.Weight.Bold))
        p.setPen(QPen(vc))
        p.drawText(0, cy - 28, W, 18, Qt.AlignmentFlag.AlignCenter, f"{self._val:.3f}")
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(_C_DIM))
        p.drawText(0, cy - 10, W, 12, Qt.AlignmentFlag.AlignCenter, self._unit)


# ═══════════════════════════════════════════════════════════
#  SPARKLINE WIDGET
# ═══════════════════════════════════════════════════════════
class SparklineWidget(QWidget):
    """Mini courbe temps réel — canvas sombre."""
    def __init__(self, max_val: float = 1.5, color: str = "#4DB8FF",
                 max_pts: int = 80, parent=None):
        super().__init__(parent)
        self._max  = max_val
        self._col  = QColor(color)
        self._data : deque = deque(maxlen=max_pts)
        self.setFixedHeight(30)

    def push(self, val: float) -> None:
        self._data.append(val)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # Fond clair KPIT
        p.fillRect(0, 0, W, H, QBrush(QColor("#E0F5D0")))
        if len(self._data) < 2:
            return
        data = list(self._data)
        n = len(data); step = W / (n - 1)
        path = QPainterPath()
        for i, v in enumerate(data):
            x = i * step
            y = H - 2 - max(0, min(v / self._max, 1.0)) * (H - 4)
            if i == 0: path.moveTo(x, y)
            else:      path.lineTo(x, y)
        # Fill
        fill_path = QPainterPath(path)
        fill_path.lineTo((n - 1) * step, H)
        fill_path.lineTo(0, H)
        fill_path.closeSubpath()
        fc = QColor(self._col); fc.setAlpha(34)
        p.fillPath(fill_path, QBrush(fc))
        # Line
        p.setPen(QPen(self._col, 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # Last dot
        lx = (n - 1) * step
        ly = H - 2 - max(0, min(data[-1] / self._max, 1.0)) * (H - 4)
        p.setBrush(QBrush(self._col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(lx) - 3, int(ly) - 3, 6, 6)


# ═══════════════════════════════════════════════════════════
#  ESSUIE-GLACE (vue parebrise) — inchangé
# ═══════════════════════════════════════════════════════════
class WindshieldWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._op             = 0
        self._angle          = -60.0
        self._dir            = 1
        self._front_motor_on = False
        self._rest_contact   = False
        self._blade_cycles   = 0
        self._bcm_state      = "OFF"
        self._returning      = False

        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(20)
        self.setMinimumSize(280, 160)

    def set_op(self, op: int) -> None:
        self._op = op

    def set_bcm_state(self, front_motor_on: bool, rest_contact_raw: bool,
                      blade_cycles: int, bcm_state: str, op: int) -> None:
        prev_motor = self._front_motor_on
        self._front_motor_on = front_motor_on
        self._rest_contact   = rest_contact_raw
        self._blade_cycles   = blade_cycles
        self._bcm_state      = bcm_state
        self._op             = op
        if prev_motor and not front_motor_on:
            self._returning = True

    def _tick(self) -> None:
        if self._front_motor_on:
            self._returning = False
            spd = {
                "SPEED1": 1.8, "SPEED2": 3.5, "TOUCH": 2.0,
                "AUTO": 2.2, "WASH_FRONT": 2.0,
            }.get(self._bcm_state, 2.0)
            self._angle += spd * self._dir
            if self._angle >= 60:
                self._angle = 60.0; self._dir = -1
            elif self._angle <= -60:
                self._angle = -60.0; self._dir = 1
        elif self._returning:
            if self._angle > -60.0:
                self._angle = max(-60.0, self._angle - 3.0)
            else:
                self._angle = -60.0; self._returning = False
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        path = QPainterPath()
        path.moveTo(W * 0.08, H * 0.95)
        path.quadTo(W * 0.03, H * 0.02, W * 0.18, H * 0.05)
        path.lineTo(W * 0.82, H * 0.05)
        path.quadTo(W * 0.97, H * 0.02, W * 0.92, H * 0.95)
        path.closeSubpath()

        p.fillRect(0, 0, W, H, QBrush(QColor("#D0D8E0")))
        glass_g = QLinearGradient(0, 0, 0, H)
        glass_g.setColorAt(0, QColor(200, 220, 255, 180))
        glass_g.setColorAt(1, QColor(180, 200, 240, 120))
        p.setBrush(QBrush(glass_g)); p.setPen(QPen(QColor("#4A5A6A"), 2))
        p.drawPath(path)

        cx = W // 2; cy = int(H * 0.94); R = int(H * 0.82)

        sw_c = QColor("#8DC63F") if self._front_motor_on else QColor("#B0B3B5")
        sw_c.setAlpha(25)
        p.setBrush(QBrush(sw_c)); p.setPen(Qt.PenStyle.NoPen)
        p.drawPie(QRectF(cx - R, cy - R, R * 2, R * 2),
                  int((-60 + 90) * 16), int(-120 * 16))

        if not self._front_motor_on and not self._rest_contact:
            wiper_c = QColor("#8DC63F")
        elif self._front_motor_on:
            wiper_c = QColor(WOP[self._op]["color"]) if self._op > 0 else QColor("#E0A000")
        else:
            wiper_c = QColor("#888888")

        ang_r = math.radians(self._angle - 90)
        ex = cx + R * math.cos(ang_r); ey = cy + R * math.sin(ang_r)

        p.setPen(QPen(QColor(0, 0, 0, 30), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx + 2, cy + 2, int(ex + 2), int(ey + 2))
        p.setPen(QPen(QColor("#505050"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, cy, int(ex), int(ey))
        perp = math.radians(self._angle - 90 + 90); bl = 32
        bx1 = ex + bl * math.cos(perp); by1 = ey + bl * math.sin(perp)
        bx2 = ex - bl * math.cos(perp); by2 = ey - bl * math.sin(perp)
        p.setPen(QPen(wiper_c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx1), int(by1), int(bx2), int(by2))

        p.setBrush(QBrush(QColor("#303030"))); p.setPen(QPen(QColor("#1A1A1A"), 1.5))
        p.drawEllipse(cx - 5, cy - 5, 10, 10)

        rc_color = QColor("#8DC63F") if not self._rest_contact else QColor(A_AMBER)
        p.setBrush(QBrush(rc_color)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(6, H - 16, 10, 10)
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        p.drawText(20, H - 16, 60, 12, Qt.AlignmentFlag.AlignLeft, "REST")

        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(A_TEAL2)))
        p.drawText(W - 70, H - 16, 66, 12,
                   Qt.AlignmentFlag.AlignRight, f"#{self._blade_cycles}")

        if self._bcm_state not in ("OFF", ""):
            lbl_c = QColor(WOP[self._op]["color"]) if self._op > 0 else QColor(W_TEXT_DIM)
            p.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
            p.setPen(QPen(lbl_c))
            p.drawText(4, H - 18, W - 8, 16, Qt.AlignmentFlag.AlignCenter,
                       self._bcm_state)


# ═══════════════════════════════════════════════════════════
#  BMW M4 — Vue 3D rotative (conservée, mais sans fond noir)
# ═══════════════════════════════════════════════════════════
class CarTopViewWidget(QWidget):
    """Vue 3D BMW M4 — fond sombre, drag souris. Conservée intacte."""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ign     = "OFF"
        self._reverse = False
        self._speed   = 0.0
        self._rain    = 0
        self._yaw     = 0.0
        self._pitch   = 70.0
        self._drag    = False
        self._last_mouse = QPointF(0, 0)
        self._rot_inertia = 0.0
        self._wheel_angle = 0.0
        self._car_offset  = 0.0
        self._vibe        = 0.0
        self._vibe_dir    = 1
        self._exhaust     = []
        self._rain_drops  = []
        self._wiper_angle = -28.0
        self._wiper_dir   = 1
        self._arrow_phase = 0.0
        self.setMinimumSize(260, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(60)

    def set_ignition(self, s: str)  -> None: self._ign     = s
    def set_reverse(self, r: bool)  -> None: self._reverse = bool(r)
    def set_speed(self, spd: float) -> None: self._speed   = spd
    def set_rain(self, rain: int)   -> None: self._rain    = rain

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True; self._last_mouse = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = False; self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag:
            dx = e.position().x() - self._last_mouse.x()
            dy = e.position().y() - self._last_mouse.y()
            self._yaw   = (self._yaw + dx * 0.55) % 360
            self._pitch = max(15.0, min(90.0, self._pitch - dy * 0.35))
            self._rot_inertia = dx * 0.3
            self._last_mouse  = e.position()

    def wheelEvent(self, e) -> None:
        self._pitch = max(15.0, min(90.0, self._pitch + e.angleDelta().y() / 40.0))
        self.update()

    def _tick(self) -> None:
        on = self._ign == "ON"
        if not self._drag and abs(self._rot_inertia) > 0.05:
            self._yaw = (self._yaw + self._rot_inertia) % 360
            self._rot_inertia *= 0.92
        if on:
            spd = max(self._speed / 22.0, 0.18 if self._speed > 0 else 0)
            self._wheel_angle = (self._wheel_angle + (-6 if self._reverse else 6) * spd) % 360
            tgt = 12 if self._reverse else -12
            self._car_offset += (tgt - self._car_offset) * 0.08
        else:
            self._car_offset *= 0.88
        if on:
            self._vibe += 0.45 * self._vibe_dir
            if abs(self._vibe) > 1.0: self._vibe_dir *= -1
        else:
            self._vibe *= 0.75
        self._arrow_phase = (self._arrow_phase + 0.07) % 1.0
        if on and random.random() < 0.4:
            for sx in (-1, 1):
                self._exhaust.append([0.0, sx, random.uniform(0.5, 0.95)])
        self._exhaust = [[a + 0.05, s, op - 0.032] for a, s, op in self._exhaust if op > 0]
        if self._rain > 0:
            for _ in range(max(1, int(self._rain / 16))):
                if len(self._rain_drops) < 80:
                    self._rain_drops.append([
                        random.uniform(0, 1), random.uniform(0, 0.2),
                        random.uniform(0.02, 0.055), random.uniform(0.3, 0.75)])
            self._rain_drops = [[x, y + sp, sp, op] for x, y, sp, op in self._rain_drops if y < 1.1]
        else:
            self._rain_drops.clear()
        if self._rain > 10 and on:
            ws = 2.0 + self._rain / 28.0
            self._wiper_angle += ws * self._wiper_dir
            if self._wiper_angle > 28: self._wiper_angle = 28; self._wiper_dir = -1
            if self._wiper_angle < -28: self._wiper_angle = -28; self._wiper_dir = 1
        self.update()

    def _project(self, cx, cy, scale, lx, ly, lz=0.0):
        yr = math.radians(self._yaw); pr = math.radians(self._pitch)
        rx = lx * math.cos(yr) - ly * math.sin(yr)
        ry = lx * math.sin(yr) + ly * math.cos(yr)
        pc = math.cos(pr); ps = math.sin(pr)
        fy = ry * ps - lz * pc
        off_y = self._car_offset * (ps / 90.0) * 0.5
        sx = int(cx + fy * scale)
        sy = int(cy + rx * scale + off_y + self._vibe * ps * 0.3)
        return sx, sy

    def _p3(self, cx, cy, scale, pts3):
        return QPolygonF([QPointF(*self._project(cx, cy, scale, x, y, z)) for x, y, z in pts3])

    def _path3(self, cx, cy, scale, cmds):
        path = QPainterPath()
        for item in cmds:
            cmd = item[0]
            if cmd == "M": path.moveTo(*self._project(cx, cy, scale, *item[1:]))
            elif cmd == "L": path.lineTo(*self._project(cx, cy, scale, *item[1:]))
            elif cmd == "Q":
                if len(item) < 6:
                    path.lineTo(*self._project(cx, cy, scale, item[1], item[2], item[3] if len(item) > 3 else 0))
                else:
                    cx2, cy2 = self._project(cx, cy, scale, item[1], item[2], item[3] if len(item) > 3 else 0)
                    ex, ey   = self._project(cx, cy, scale, item[4], item[5], item[6] if len(item) > 6 else 0)
                    path.quadTo(cx2, cy2, ex, ey)
            elif cmd == "C":
                c1x, c1y = self._project(cx, cy, scale, item[1], item[2], item[3] if len(item) > 3 else 0)
                c2x, c2y = self._project(cx, cy, scale, item[4], item[5], item[6] if len(item) > 6 else 0)
                ex, ey   = self._project(cx, cy, scale, item[7], item[8], item[9] if len(item) > 9 else 0)
                path.cubicTo(c1x, c1y, c2x, c2y, ex, ey)
            elif cmd == "Z": path.closeSubpath()
        return path

    def _draw_wheel(self, p, cx2, cy2, scale, lx, ly, flip=1):
        corners = [(lx - 0.22, ly, 0.18), (lx + 0.22, ly, 0.18),
                   (lx + 0.22, ly, -0.18), (lx - 0.22, ly, -0.18)]
        poly = self._p3(cx2, cy2, scale, corners)
        tg = QLinearGradient(poly.at(0).x(), poly.at(0).y(), poly.at(1).x(), poly.at(1).y())
        tg.setColorAt(0, QColor("#0A0A0A")); tg.setColorAt(0.45, QColor("#242424"))
        tg.setColorAt(0.55, QColor("#181818")); tg.setColorAt(1, QColor("#0A0A0A"))
        p.setBrush(QBrush(tg)); p.setPen(QPen(QColor("#050505"), 1.5)); p.drawPolygon(poly)
        rcx, rcy = self._project(cx2, cy2, scale, lx, ly, 0)
        rrx = int(scale * 0.24 * 0.5 + scale * 0.08); rry = int(scale * 0.44 * 0.41)
        gr = QRadialGradient(rcx, rcy, max(rrx, rry))
        gr.setColorAt(0, QColor("#C8D0D8")); gr.setColorAt(0.55, QColor("#707880")); gr.setColorAt(1, QColor("#383E44"))
        p.setBrush(QBrush(gr)); p.setPen(QPen(QColor("#282E34"), 0.8))
        p.drawEllipse(rcx - rrx, rcy - rry, rrx * 2, rry * 2)
        for i in range(5):
            a = math.radians(self._wheel_angle * flip + i * 72)
            p.setPen(QPen(QColor("#A0A8B0"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(rcx, rcy, int(rcx + (rrx - 1) * math.cos(a)), int(rcy + (rry - 1) * math.sin(a)))
        p.setBrush(QBrush(QColor("#1A2030"))); p.setPen(QPen(QColor("#101820"), 0.8))
        p.drawEllipse(rcx - 4, rcy - 4, 8, 8)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.fillRect(0, 0, W, H, QBrush(QColor("#1E2228")))
        p.setPen(QPen(QColor("#262C34"), 1, Qt.PenStyle.DashLine))
        for x in range(0, W, 30): p.drawLine(x, 0, x, H)
        for y in range(0, H, 30): p.drawLine(0, y, W, y)

        for rx, ry, _, op in self._rain_drops:
            rc = QColor(A_TEAL); rc.setAlphaF(op * 0.4)
            p.setPen(QPen(rc, 1))
            px2 = int(rx * W); py2 = int(ry * H)
            p.drawLine(px2, py2, px2 - 1, py2 + 7)

        margin = 48
        scale = min((H - margin * 2) / 3.2, (W - margin * 2) / 2.0, 78)
        cx, cy = W // 2, H // 2
        on = self._ign == "ON"; acc = self._ign == "ACC"; off = self._ign == "OFF"

        body_col = QColor("#4A5058") if off else (QColor("#2A3A50") if acc else QColor("#1C2A3C"))
        yaw_r = math.radians(self._yaw)
        spec  = 0.5 + 0.5 * math.cos(yaw_r + math.pi / 4)

        def pr(lx, ly, lz=0.0): return self._project(cx, cy, scale, lx, ly, lz)
        def pg(pts3):            return self._p3(cx, cy, scale, pts3)
        def ph(*cmds):           return self._path3(cx, cy, scale, cmds)

        p.setBrush(QBrush(QColor(0, 0, 0, 50))); p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(pg([(-1.55, -0.85, -0.05), (1.60, -0.85, -0.05), (1.60, 0.85, -0.05), (-1.55, 0.85, -0.05)]))

        body = ph(
            ("M",-1.50,0.00,0.04),("Q",-1.52,0.30,0.04,-1.45,0.55,0.06),
            ("Q",-1.38,0.68,0.06,-1.20,0.72,0.08),("Q",-0.80,0.82,0.10,-0.50,0.84,0.12),
            ("Q",0.00,0.80,0.10,0.40,0.82,0.10),("Q",0.70,0.86,0.12,0.95,0.88,0.12),
            ("Q",1.20,0.84,0.10,1.40,0.72,0.08),("Q",1.52,0.55,0.06,1.55,0.28,0.04),
            ("L",1.55,0.00,0.04),("L",1.55,-0.28,0.04),
            ("Q",1.52,-0.55,0.06,1.40,-0.72,0.08),("Q",1.20,-0.84,0.10,0.95,-0.88,0.12),
            ("Q",0.70,-0.86,0.12,0.40,-0.82,0.10),("Q",0.00,-0.80,0.10,-0.50,-0.84,0.12),
            ("Q",-0.80,-0.82,0.10,-1.20,-0.72,0.08),("Q",-1.38,-0.68,0.06,-1.45,-0.55,0.06),
            ("Q",-1.52,-0.30,0.04,-1.50,0.00,0.04),("Z",),
        )
        bx0, by0 = pr(-0.82, 0, 0.08); bx1, by1 = pr(0.82, 0, 0.08)
        gbd = QLinearGradient(bx0, by0, bx1, by1)
        gbd.setColorAt(0, body_col.darker(int(150 - spec * 20)))
        gbd.setColorAt(0.18, body_col.lighter(int(105 + spec * 15)))
        gbd.setColorAt(0.45, body_col.lighter(int(140 + spec * 25)))
        gbd.setColorAt(0.55, body_col.lighter(int(155 + spec * 20)))
        gbd.setColorAt(0.82, body_col.lighter(int(108 + spec * 10)))
        gbd.setColorAt(1, body_col.darker(int(148 - spec * 15)))
        p.setBrush(QBrush(gbd)); p.setPen(QPen(body_col.darker(200), 1.2))
        p.drawPath(body)

        hood = ph(
            ("M",-1.50,0.00,0.04),("Q",-1.48,0.50,0.08,-1.30,0.62,0.10),
            ("Q",-1.00,0.62,0.12,-0.68,0.56,0.14),("Q",-0.50,0.48,0.15,-0.42,0.00,0.16),
            ("Q",-0.50,-0.48,0.15,-0.68,-0.56,0.14),("Q",-1.00,-0.62,0.12,-1.30,-0.62,0.10),
            ("Q",-1.48,-0.50,0.08,-1.50,0.00,0.04),("Z",),
        )
        hx0, hy0 = pr(-1.50, 0, 0.08); hx1, hy1 = pr(-0.42, 0, 0.16)
        gh = QLinearGradient(hx0, hy0, hx1, hy1)
        gh.setColorAt(0, body_col.darker(160))
        gh.setColorAt(0.25, body_col.lighter(int(112 + spec * 20)))
        gh.setColorAt(0.55, body_col.lighter(int(188 + spec * 15)))
        gh.setColorAt(0.85, body_col.lighter(int(110 + spec * 10)))
        gh.setColorAt(1, body_col.darker(140))
        p.setBrush(QBrush(gh)); p.setPen(QPen(body_col.darker(170), 0.8))
        p.drawPath(hood)

        for sy in (-0.14, 0.14):
            dome = ph(
                ("M",-1.46,sy-0.06,0.04),("Q",-1.00,sy-0.07,0.14,-0.46,sy-0.05,0.16),
                ("Q",-0.46,sy+0.05,0.16,-1.00,sy+0.07,0.14),
                ("Q",-1.46,sy+0.06,0.04,-1.46,sy-0.06,0.04),("Z",),
            )
            p.setBrush(QBrush(body_col.lighter(200))); p.setPen(Qt.PenStyle.NoPen); p.drawPath(dome)

        roof = ph(
            ("M",-0.42,0.00,0.16),("Q",-0.40,0.44,0.20,-0.20,0.52,0.52),
            ("Q",0.00,0.50,0.60,0.30,0.48,0.62),("Q",0.65,0.44,0.58,0.80,0.38,0.50),
            ("Q",0.88,0.30,0.38,0.90,0.00,0.28),("Q",0.88,-0.30,0.38,0.80,-0.38,0.50),
            ("Q",0.65,-0.44,0.58,0.30,-0.48,0.62),("Q",0.00,-0.50,0.60,-0.20,-0.52,0.52),
            ("Q",-0.40,-0.44,0.20,-0.42,0.00,0.16),("Z",),
        )
        rx0, ry0 = pr(0, 0, 0.55); rx1, ry1 = pr(0.45, 0, 0.55)
        rc_dark = QColor("#0A1422") if on else (QColor("#141E2C") if acc else QColor("#1E2630"))
        groof = QLinearGradient(rx0, ry0, rx1, ry1)
        for stop, fac in [(0, 1), (0.25, 200), (0.50, 280), (0.75, 190), (1, 1)]:
            groof.setColorAt(stop, rc_dark.lighter(fac) if fac > 1 else rc_dark)
        p.setBrush(QBrush(groof)); p.setPen(QPen(QColor("#050C14"), 1)); p.drawPath(roof)

        wsf = ph(
            ("M",-0.42,0.44,0.20),("Q",-0.38,0.50,0.22,-0.20,0.52,0.52),
            ("Q",0.00,0.50,0.60,0.00,-0.50,0.60),("Q",-0.20,-0.52,0.52,-0.38,-0.50,0.22),
            ("Q",-0.42,-0.44,0.20,-0.42,0.44,0.20),("Z",),
        )
        gwsf = QLinearGradient(*pr(-0.42, 0.44, 0.20), *pr(-0.20, 0.52, 0.52))
        gwsf.setColorAt(0, QColor(140, 195, 230, 170)); gwsf.setColorAt(1, QColor(80, 145, 190, 95))
        p.setBrush(QBrush(gwsf)); p.setPen(QPen(QColor("#1A4060"), 0.8)); p.drawPath(wsf)

        if self._rain > 10 and on:
            wpx, wpy = pr(-0.10, 0, 0.38)
            wr2 = int(scale * 0.40); wa_r = math.radians(self._wiper_angle * 1.6)
            wex = wpx + int(wr2 * math.sin(wa_r)); wey = wpy + int(wr2 * math.cos(wa_r))
            p.setPen(QPen(QColor("#101010"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(wpx, wpy, wex, wey)
            p.setPen(QPen(QColor(A_TEAL), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(wpx, wpy, wex, wey)

        wsr = ph(
            ("M",0.80,0.38,0.50),("Q",0.65,0.44,0.58,0.30,0.48,0.62),
            ("Q",0.30,-0.48,0.62,0.65,-0.44,0.58),("Q",0.80,-0.38,0.50,0.80,0.38,0.50),("Z",),
        )
        gwsr = QLinearGradient(*pr(0.65, 0, 0.58), *pr(0.80, 0, 0.50))
        gwsr.setColorAt(0, QColor(80, 145, 190, 95)); gwsr.setColorAt(1, QColor(140, 195, 230, 160))
        p.setBrush(QBrush(gwsr)); p.setPen(QPen(QColor("#1A4060"), 0.8)); p.drawPath(wsr)

        trunk = ph(
            ("M",0.90,0.00,0.14),("Q",0.88,0.35,0.16,0.80,0.38,0.50),
            ("Q",0.80,-0.38,0.50,0.88,-0.35,0.16),("L",0.90,0.00,0.14),("Z",),
        )
        tx0, ty0 = pr(0.88, 0, 0.16); tx1, ty1 = pr(0.80, 0, 0.50)
        gtrunk = QLinearGradient(tx0, ty0, tx1, ty1)
        gtrunk.setColorAt(0, body_col.darker(140))
        gtrunk.setColorAt(0.5, body_col.lighter(140))
        gtrunk.setColorAt(1, body_col.darker(130))
        p.setBrush(QBrush(gtrunk)); p.setPen(QPen(body_col.darker(175), 0.8)); p.drawPath(trunk)

        wing_blade = ph(
            ("M",1.35,-0.85,0.58),("Q",1.40,-0.85,0.60,1.45,0.00,0.62),
            ("Q",1.40,0.85,0.60,1.35,0.85,0.58),("Q",1.30,0.82,0.55,1.28,0.00,0.54),
            ("Q",1.30,-0.82,0.55,1.35,-0.85,0.58),("Z",),
        )
        wg = QLinearGradient(*pr(1.35, -0.85, 0.60), *pr(1.35, 0.85, 0.60))
        wg.setColorAt(0, QColor("#181C22")); wg.setColorAt(0.45, QColor("#3A4048"))
        wg.setColorAt(0.55, QColor("#2A3038")); wg.setColorAt(1, QColor("#181C22"))
        p.setBrush(QBrush(wg)); p.setPen(QPen(QColor("#0A0E14"), 1)); p.drawPath(wing_blade)

        for sy in (-0.78, 0.78):
            mirror = ph(
                ("M",-0.60,sy,0.38),("Q",-0.62,sy*1.08,0.36,-0.58,sy*1.12,0.34),
                ("Q",-0.50,sy*1.10,0.34,-0.48,sy,0.36),
                ("Q",-0.52,sy*0.96,0.38,-0.60,sy,0.38),("Z",),
            )
            mg = QLinearGradient(*pr(-0.60, sy, 0.38), *pr(-0.55, sy * 1.1, 0.34))
            mg.setColorAt(0, body_col.darker(150)); mg.setColorAt(1, body_col.lighter(120))
            p.setBrush(QBrush(mg)); p.setPen(QPen(body_col.darker(180), 0.8)); p.drawPath(mirror)

        for sy in (-0.18, 0.18):
            grille = ph(
                ("M",-1.52,sy-0.14,0.04),("Q",-1.55,sy-0.14,0.06,-1.56,sy,0.08),
                ("Q",-1.55,sy+0.14,0.06,-1.52,sy+0.14,0.04),
                ("Q",-1.46,sy+0.12,0.04,-1.45,sy,0.04),
                ("Q",-1.46,sy-0.12,0.04,-1.52,sy-0.14,0.04),("Z",),
            )
            p.setBrush(QBrush(QColor("#08090C"))); p.setPen(QPen(QColor("#1A2030"), 0.8)); p.drawPath(grille)

        for lx, ly, flip in [(-0.88, -0.82, 1), (-0.88, 0.82, -1), (0.95, -0.82, 1), (0.95, 0.82, -1)]:
            self._draw_wheel(p, cx, cy, scale, lx, ly, flip)

        for sy in (-0.42, 0.42):
            hx2, hy2 = pr(-1.52, sy, 0.06)
            if on:
                halo = QRadialGradient(hx2, hy2, int(scale * 0.24))
                halo.setColorAt(0, QColor(255, 255, 230, 200)); halo.setColorAt(1, QColor(255, 255, 200, 0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(hx2 - int(scale * 0.24), hy2 - int(scale * 0.12), int(scale * 0.48), int(scale * 0.24))
                p.setBrush(QBrush(QColor("#F2F6FF"))); p.setPen(QPen(QColor("#B0B8C8"), 0.8))
            elif acc:
                p.setBrush(QBrush(QColor("#FF8C00"))); p.setPen(QPen(QColor("#CC6600"), 0.8))
            else:
                p.setBrush(QBrush(QColor("#141820"))); p.setPen(QPen(QColor("#0C1018"), 0.8))
            hw = int(scale * 0.28); hh = int(scale * 0.12)
            p.drawEllipse(hx2 - hw // 2, hy2 - hh // 2, hw, hh)
            drl_c = QColor("#FFFFC0") if on else (QColor("#FFA020") if acc else QColor("#111820"))
            p.setPen(QPen(drl_c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            dl = int(scale * 0.15)
            p.drawLine(hx2 - dl, hy2 - int(scale * 0.10), hx2 + dl, hy2 - int(scale * 0.10))
            p.drawLine(hx2 + dl, hy2 - int(scale * 0.10), hx2 + dl, hy2 + int(scale * 0.02))

        for sy in (-0.42, 0.42):
            tx2, ty2 = pr(1.52, sy, 0.06)
            if on or acc:
                halo = QRadialGradient(tx2, ty2, int(scale * 0.20))
                halo.setColorAt(0, QColor(220, 0, 0, 200)); halo.setColorAt(1, QColor(180, 0, 0, 0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(tx2 - int(scale * 0.20), ty2 - int(scale * 0.10), int(scale * 0.40), int(scale * 0.20))
                p.setBrush(QBrush(QColor("#E01818"))); p.setPen(QPen(QColor("#800000"), 0.8))
            else:
                p.setBrush(QBrush(QColor("#3C0808"))); p.setPen(QPen(QColor("#200404"), 0.8))
            tw = int(scale * 0.24); th = int(scale * 0.10)
            p.drawEllipse(tx2 - tw // 2, ty2 - th // 2, tw, th)

        if self._reverse and on:
            for sy in (-0.18, 0.18):
                rx3, ry3 = pr(1.52, sy, 0.06)
                halo = QRadialGradient(rx3, ry3, int(scale * 0.14))
                halo.setColorAt(0, QColor(255, 255, 255, 220)); halo.setColorAt(1, QColor(255, 255, 255, 0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(rx3 - int(scale * 0.14), ry3 - int(scale * 0.08), int(scale * 0.28), int(scale * 0.16))

        for sy in (-0.32, 0.32):
            ex2, ey2 = pr(1.55, sy, 0.00)
            p.setBrush(QBrush(QColor("#303840"))); p.setPen(QPen(QColor("#1A2028"), 0.8))
            p.drawEllipse(ex2 - int(scale * 0.05), ey2 - int(scale * 0.05), int(scale * 0.10), int(scale * 0.10))

        for adv, sx2, op in self._exhaust:
            ebx, eby = pr(1.55, sx2 * 0.32, 0.00)
            r_e = int(3 + adv * 25)
            smoke = QColor(180, 185, 192); smoke.setAlphaF(max(0, op * 0.25))
            p.setBrush(QBrush(smoke)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(ebx - r_e, int(eby + adv * 28) - r_e, r_e * 2, r_e * 2)

        if self._reverse and on:
            for ai in range(3):
                ph2 = (self._arrow_phase * 3 + ai) % 3
                alpha = int(255 * max(0, 1.0 - abs(ph2 - 1.0)))
                ac = QColor(A_ORANGE); ac.setAlpha(alpha)
                ax2, ay2 = pr(1.75 + ai * 0.20, 0, 0.04)
                pts3 = QPolygonF([QPointF(ax2 + int(scale * 0.13), ay2),
                                  QPointF(ax2, ay2 - int(scale * 0.15)),
                                  QPointF(ax2, ay2 + int(scale * 0.15))])
                p.setBrush(QBrush(ac)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(pts3)

        if not self._reverse and on and self._speed > 3:
            for ai in range(3):
                ph2 = (self._arrow_phase * 3 + ai) % 3
                alpha = int(255 * max(0, 1.0 - abs(ph2 - 1.0)))
                ac = QColor(A_GREEN); ac.setAlpha(alpha)
                ax2, ay2 = pr(-1.75 - ai * 0.20, 0, 0.04)
                pts3 = QPolygonF([QPointF(ax2 - int(scale * 0.13), ay2),
                                  QPointF(ax2, ay2 - int(scale * 0.15)),
                                  QPointF(ax2, ay2 + int(scale * 0.15))])
                p.setBrush(QBrush(ac)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(pts3)

        bw2 = 158; bh2 = 24; bx3 = (W - bw2) // 2; by3 = H - 28
        p.setBrush(QBrush(QColor(0, 0, 0, 155))); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bx3, by3, bw2, bh2, 6, 6)
        lmap2 = {"OFF": ("ENGINE  OFF", "#888888"), "ACC": ("ACCESSORY", A_ORANGE), "ON": ("ENGINE  ON", "#8DC63F")}
        lt2, lc2 = ("REVERSE  <", A_ORANGE) if (self._reverse and on) else lmap2.get(self._ign, ("OFF", "#888888"))
        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QPen(QColor(lc2)))
        p.drawText(bx3, by3, bw2, bh2, Qt.AlignmentFlag.AlignCenter, lt2)
        if on and self._speed > 0:
            sw3 = 62; sx4 = bx3 + bw2 + 5
            p.setBrush(QBrush(QColor(0, 0, 0, 130))); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(sx4, by3, sw3, bh2, 6, 6)
            p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
            p.setPen(QPen(QColor(A_TEAL)))
            p.drawText(sx4, by3, sw3, bh2, Qt.AlignmentFlag.AlignCenter, f"{self._speed:.0f} km/h")

        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(QColor(80, 85, 95)))
        p.drawText(6, H - 12, W - 12, 12, Qt.AlignmentFlag.AlignCenter, "drag to rotate  .  scroll to tilt")