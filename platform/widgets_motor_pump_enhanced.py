"""
WipeWash — Widgets visuels améliorés pour Motor/Pump page
RestContactWidget, SystemStatusWidget, TimeoutFSRWidget

Palette KPIT stricte : uniquement les couleurs de constants.py
  W_BG=#FFFFFF  W_PANEL=#F5FFF0  W_PANEL2=#EDF9E3  W_PANEL3=#E0F5D0
  W_TOOLBAR=#D6EFC0  W_TITLEBAR=#0F1A0A  W_BORDER=rgba(141,198,63,0.35)
  W_TEXT=#1A1A1A  W_TEXT_DIM=#5A6A4A  KPIT_GREEN=#8DC63F
  A_TEAL=#007ACC  A_GREEN=#39FF14  A_RED=#C0392B
  A_ORANGE=#D35400  A_AMBER=#F39C12
"""

import math
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore    import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient,
    QPainterPath, QPolygonF,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3, W_TOOLBAR,
    W_TITLEBAR, W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_TEAL, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    KPIT_GREEN,
)

# ── Couleurs locales — palette KPIT ──────────────────────────────────────────
_KPIT         = QColor(KPIT_GREEN)      # #8DC63F
_KPIT_PALE    = QColor(W_PANEL3)        # #E0F5D0
_KPIT_MID     = QColor(W_PANEL2)        # #EDF9E3
_KPIT_TOOLBAR = QColor(W_TOOLBAR)       # #D6EFC0
_KPIT_DARK    = QColor(W_TITLEBAR)      # #0F1A0A
_TEXT         = QColor(W_TEXT)          # #1A1A1A
_TEXT_DIM     = QColor(W_TEXT_DIM)      # #5A6A4A
_TEAL         = QColor(A_TEAL)          # #007ACC
_RED          = QColor(A_RED)           # #C0392B
_ORANGE       = QColor(A_ORANGE)        # #D35400
_AMBER        = QColor(A_AMBER)         # #F39C12
_BORDER       = QColor(141, 198, 63, 90)


def _alpha(c: QColor, a: int) -> QColor:
    cc = QColor(c); cc.setAlpha(a); return cc


def _draw_panel_bg(p: QPainter, W: int, H: int, r: int = 8) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(1, 1, W - 2, H - 2), r, r)
    bg = QLinearGradient(0, 0, 0, H)
    bg.setColorAt(0, QColor(W_BG)); bg.setColorAt(1, QColor(W_PANEL2))
    p.setBrush(QBrush(bg))
    p.setPen(QPen(_BORDER, 1.5))
    p.drawPath(path)


def _draw_stripe(p: QPainter, H: int, color: QColor) -> None:
    sp = QPainterPath()
    sp.addRoundedRect(QRectF(1, 1, 5, H - 2), 2, 2)
    p.setBrush(QBrush(color)); p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(sp)


def _draw_header(p: QPainter, W: int, hdr_h: int, accent: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(1, 1, W - 2, hdr_h), 8, 8)
    rect = QPainterPath()
    rect.addRect(QRectF(1, hdr_h // 2, W - 2, hdr_h // 2))
    path = path.united(rect)
    bg = QLinearGradient(0, 0, 0, hdr_h)
    bg.setColorAt(0, QColor("#0F1A0A")); bg.setColorAt(1, QColor("#070A04"))
    p.setBrush(QBrush(bg))
    p.setPen(QPen(_alpha(accent, 140), 1))
    p.drawPath(path)


# ═══════════════════════════════════════════════════════════
#  REST CONTACT WIDGET
# ═══════════════════════════════════════════════════════════
class RestContactWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parked    = False
        self._pulse     = 0.0
        self._pulse_dir = 1
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(40)
        self.setMinimumSize(150, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, parked: bool) -> None:
        self._parked = parked
        self.update()

    def _tick(self):
        self._pulse += self._pulse_dir * 0.055
        if self._pulse >= 1.0:   self._pulse = 1.0;  self._pulse_dir = -1
        elif self._pulse <= 0.0: self._pulse = 0.0;  self._pulse_dir = 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        accent = _KPIT if self._parked else _ORANGE
        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, accent)

        # Header sombre KPIT
        hdr_h = 26
        _draw_header(p, W, hdr_h, accent)

        # Texte état dans header
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(14, 0, W - 30, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "◼  PARKED" if self._parked else "▶  MOVING")

        # LED pulsante dans header
        led_x = W - 14; led_y = hdr_h // 2
        halo_r = int(7 + 3 * self._pulse)
        p.setBrush(QBrush(_alpha(accent, int(40 * self._pulse))))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(led_x - halo_r // 2, led_y - halo_r // 2, halo_r, halo_r)
        lg = QRadialGradient(led_x - 1, led_y - 1, 5)
        lg.setColorAt(0, accent.lighter(150)); lg.setColorAt(1, accent.darker(110))
        p.setBrush(QBrush(lg)); p.setPen(QPen(accent.darker(130), 1))
        p.drawEllipse(led_x - 5, led_y - 5, 10, 10)

        # GPIO label
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(_TEXT_DIM))
        p.drawText(14, hdr_h + 5, W - 20, 14,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "GPIO26 · Front blade")

        # Track blade
        track_y = hdr_h + 26
        track_x = 14; track_w = W - 28; track_h = 10

        tp = QPainterPath()
        tp.addRoundedRect(QRectF(track_x, track_y, track_w, track_h), 5, 5)
        p.setBrush(QBrush(_KPIT_PALE))
        p.setPen(QPen(_alpha(_KPIT, 80), 1))
        p.drawPath(tp)

        if self._parked:
            bp = QPainterPath()
            bp.addRoundedRect(QRectF(track_x, track_y, 18, track_h), 5, 5)
            p.setBrush(QBrush(_KPIT)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(bp)
        else:
            pos = 0.1 + 0.75 * self._pulse
            bw = max(14, int(track_w * 0.28))
            bx = track_x + int(pos * (track_w - bw))
            bp = QPainterPath()
            bp.addRoundedRect(QRectF(bx, track_y, bw, track_h), 5, 5)
            p.setBrush(QBrush(_ORANGE)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(bp)
            if bx > track_x + 4:
                tr = QPainterPath()
                tr.addRoundedRect(QRectF(track_x + 2, track_y + 3,
                                          bx - track_x - 2, track_h - 6), 2, 2)
                p.setBrush(QBrush(_alpha(_ORANGE, 30)))
                p.drawPath(tr)

        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(QPen(_TEXT_DIM))
        p.drawText(track_x, track_y + track_h + 3, track_w, 12,
                   Qt.AlignmentFlag.AlignCenter, "BLADE POSITION")


# ═══════════════════════════════════════════════════════════
#  SYSTEM STATUS WIDGET
# ═══════════════════════════════════════════════════════════
class SystemStatusWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._front   = "—"; self._rear = "—"
        self._speed   = "—"; self._current = "—A"
        self._fault   = False
        self._status  = "Waiting for connection…"
        self._anim    = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(50)
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, front, rear, speed, current, fault, status):
        self._front = front; self._rear = rear
        self._speed = speed; self._current = current
        self._fault = fault; self._status = status
        self.update()

    def _tick(self):
        self._anim = (self._anim + 0.04) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        bar_accent = _RED if self._fault else _KPIT
        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, bar_accent)

        # Header
        hdr_h = 26
        _draw_header(p, W, hdr_h, bar_accent)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(bar_accent))
        p.drawText(14, 0, W - 20, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "⚠  FAULT DETECTED" if self._fault else "●  SYSTEM NOMINAL")

        # Déterminer la couleur courant
        try:
            cur_val = float(self._current.replace("A", "").strip())
        except Exception:
            cur_val = 0.0
        cur_accent = _RED if self._fault else (_ORANGE if cur_val > 0.8 else _KPIT)

        ITEMS = [
            ("FRONT",   self._front,
             _KPIT if self._front == "ON" else _TEXT_DIM,
             _KPIT_PALE if self._front == "ON" else QColor(W_PANEL)),
            ("REAR",    self._rear,
             _KPIT if self._rear == "ON" else _TEXT_DIM,
             _KPIT_PALE if self._rear == "ON" else QColor(W_PANEL)),
            ("SPEED",   self._speed,  _TEAL,  QColor(W_PANEL2)),
            ("CURRENT", self._current, cur_accent, _KPIT_PALE),
        ]

        n = len(ITEMS)
        pad = 10; status_h = 22
        card_area_y = hdr_h + 5
        card_area_h = H - card_area_y - status_h - 8
        card_w = (W - pad - 6 - (n - 1) * 5) // n
        card_h = max(22, card_area_h)

        for i, (key, val, acc, pale) in enumerate(ITEMS):
            cx = pad + i * (card_w + 5)
            cp = QPainterPath()
            cp.addRoundedRect(QRectF(cx, card_area_y, card_w, card_h), 4, 4)
            p.setBrush(QBrush(pale))
            p.setPen(QPen(_alpha(acc, 100), 1))
            p.drawPath(cp)
            # Bandeau coloré haut
            top = QPainterPath()
            top.addRoundedRect(QRectF(cx, card_area_y, card_w, 3), 1, 1)
            p.setBrush(QBrush(acc)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(top)
            # Clé
            p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
            p.setPen(QPen(acc))
            p.drawText(int(cx), int(card_area_y + 4), int(card_w), 13,
                       Qt.AlignmentFlag.AlignCenter, key)
            # Valeur
            p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            p.setPen(QPen(acc))
            p.drawText(int(cx), int(card_area_y + 16), int(card_w), int(card_h - 18),
                       Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter, val)

        # Barre statut bas — style toolbar KPIT
        bar_y = H - status_h - 2
        bp = QPainterPath()
        bp.addRoundedRect(QRectF(pad - 2, bar_y, W - pad, status_h), 4, 4)
        if self._fault:
            alpha_v = int(180 + 50 * math.sin(self._anim * 3))
            p.setBrush(QBrush(_alpha(_RED, alpha_v)))
            p.setPen(QPen(_RED.darker(120), 1))
            p.drawPath(bp)
            p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            p.setPen(QPen(QColor(W_TEXT_HDR)))
            p.drawText(int(pad), int(bar_y), int(W - pad), status_h,
                       Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                       self._status)
        else:
            p.setBrush(QBrush(_KPIT_TOOLBAR))
            p.setPen(QPen(_alpha(_KPIT, 80), 1))
            p.drawPath(bp)
            dot_x = pad + 7; dot_y = bar_y + status_h // 2
            p.setBrush(QBrush(_KPIT)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - 4, dot_y - 4, 8, 8)
            p.setFont(QFont(FONT_UI, 8))
            p.setPen(QPen(_TEXT))
            p.drawText(pad + 16, bar_y, W - pad - 16, status_h,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self._status)


# ═══════════════════════════════════════════════════════════
#  TIMEOUT FSR WIDGET
# ═══════════════════════════════════════════════════════════
class TimeoutFSRWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._remaining = 0.0; self._duration = 5.0
        self._active = False; self._source = ""; self._info_text = "Pump inactive"
        self._anim = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(40)
        self.setMinimumSize(280, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, remaining, duration, active, source, info):
        self._remaining = remaining; self._duration = max(duration, 0.001)
        self._active = active; self._source = source; self._info_text = info
        self.update()

    def _tick(self):
        self._anim = (self._anim + 0.05) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        pct    = max(0.0, min(1.0, self._remaining / self._duration)) if self._active else 0.0
        urgent = self._active and self._remaining < 1.5

        accent = _RED if urgent else (_AMBER if self._active and pct < 0.4
                 else (_KPIT if self._active else _TEXT_DIM))

        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, accent)

        # Header
        hdr_h = 26
        _draw_header(p, W, hdr_h, accent)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        hdr_txt = ("⚠  TIMEOUT URGENT" if urgent
                   else ("⏱  FSR_005 ACTIVE" if self._active else "◉  FSR_005 STANDBY"))
        p.drawText(14, 0, W - 86, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hdr_txt)

        # Badge source dans le header
        if self._source:
            src_c = _TEAL if self._source == "INTERFACE" else _AMBER
            sbp = QPainterPath()
            sbp.addRoundedRect(QRectF(W - 82, 4, 74, hdr_h - 8), 3, 3)
            p.setBrush(QBrush(_alpha(src_c, 70)))
            p.setPen(QPen(src_c, 1))
            p.drawPath(sbp)
            p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
            p.setPen(QPen(QColor(W_TEXT_HDR)))
            p.drawText(int(W - 82), 4, 74, hdr_h - 8,
                       Qt.AlignmentFlag.AlignCenter, self._source)

        # LED pulsante
        if self._active:
            led_x = W - 90 if self._source else W - 14
            led_y = hdr_h // 2
            halo = int(7 + 3 * abs(math.sin(self._anim * 2)))
            p.setBrush(QBrush(_alpha(accent, int(45 * abs(math.sin(self._anim))))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(led_x - halo // 2, led_y - halo // 2, halo, halo)
            lg = QRadialGradient(led_x - 1, led_y - 1, 5)
            lg.setColorAt(0, accent.lighter(150)); lg.setColorAt(1, accent.darker(110))
            p.setBrush(QBrush(lg)); p.setPen(QPen(accent.darker(140), 1))
            p.drawEllipse(led_x - 5, led_y - 5, 10, 10)

        # Contenu : arc countdown + barre
        content_y = hdr_h + 4
        content_h = H - content_y - 4
        arc_cx = 46; arc_cy = content_y + content_h // 2
        arc_R  = min(content_h // 2 - 4, 26)

        # Arc track — vert pâle KPIT
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_KPIT_PALE.darker(115), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(arc_cx - arc_R, arc_cy - arc_R, arc_R * 2, arc_R * 2),
                  90 * 16, -360 * 16)
        if self._active and pct > 0:
            p.setPen(QPen(accent, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(arc_cx - arc_R, arc_cy - arc_R, arc_R * 2, arc_R * 2),
                      90 * 16, int(-360 * 16 * pct))

        # Valeur dans l'arc
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(arc_cx - arc_R, arc_cy - 10, arc_R * 2, 18,
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self._remaining:.1f}" if self._active else "—")
        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(_TEXT_DIM))
        p.drawText(arc_cx - arc_R, arc_cy + 7, arc_R * 2, 12,
                   Qt.AlignmentFlag.AlignCenter, "sec")

        # Barre linéaire
        bar_x = arc_cx * 2 + 4; bar_y = arc_cy - 8
        bar_h = 16; bar_w = W - bar_x - 10

        bbg = QPainterPath()
        bbg.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h // 2, bar_h // 2)
        p.setBrush(QBrush(_KPIT_PALE))
        p.setPen(QPen(_alpha(_KPIT, 60), 1))
        p.drawPath(bbg)

        if self._active and pct > 0:
            fw = max(bar_h, int(bar_w * pct))
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(bar_x, bar_y, fw, bar_h), bar_h // 2, bar_h // 2)
            fg = QLinearGradient(bar_x, 0, bar_x + fw, 0)
            if urgent:
                fg.setColorAt(0, _alpha(_RED, 140)); fg.setColorAt(1, _RED)
            elif pct < 0.4:
                fg.setColorAt(0, _alpha(_AMBER, 140)); fg.setColorAt(1, _AMBER)
            else:
                fg.setColorAt(0, _alpha(_KPIT, 160)); fg.setColorAt(1, _KPIT)
            p.setBrush(QBrush(fg)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(fp)
            for i in range(1, 5):
                tx = int(bar_x + bar_w * i / 5)
                if tx < bar_x + fw:
                    p.setPen(QPen(QColor(255, 255, 255, 100), 1))
                    p.drawLine(tx, bar_y + 3, tx, bar_y + bar_h - 3)

        # Info text
        p.setFont(QFont(FONT_UI, 8))
        p.setPen(QPen(accent if self._active else _TEXT_DIM))
        p.drawText(int(bar_x), int(bar_y + bar_h + 3), int(bar_w), 14,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._info_text)

        # Pulsation alerte
        if urgent:
            full = QPainterPath()
            full.addRoundedRect(QRectF(1, 1, W - 2, H - 2), 8, 8)
            p.setBrush(QBrush(_alpha(_RED, int(12 * abs(math.sin(self._anim * 3))))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(full)
