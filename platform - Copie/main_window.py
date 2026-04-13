
"""
WipeWash — Fenêtre principale
MainWindow (3 onglets : Motor/Pompe | LIN/CRS | CAN/Vehicle) + NetworkScanDialog.

MODIFICATION v2 :
  _make_runner() passe pump_signal=self._pump_signal à TestRunner.

OPTIMISATIONS :
  - Architecture 3 pages QTabWidget au lieu de 6 docks flottants → moins de
    widgets rendus simultanément.
  - Oscilloscopes LIN et CAN mis en pause sur les onglets cachés (timer.stop/start).
  - DOCK_STYLE supprimé (inutile).
  - _set_tb_status : dict PORT→name pré-calculé en attribut de classe.
"""

import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QToolBar, QStatusBar,
    QTabWidget, QSplitter, QMenu, QDialog, QMessageBox,
    QProgressBar, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui  import QFont, QColor, QAction
from data_replay_panel import DataRecorder, DataReplayPanel

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_TOOLBAR,
    W_TEXT, W_TEXT_DIM, W_DOCK_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE,
    PORT_MOTOR, PORT_LIN, PORT_PUMP_RX,
)
try:
    from constants import PORT_CAN, CAN_VEH_C
except ImportError:
    PORT_CAN  = 5557
    CAN_VEH_C = "#007ACC"
from network  import scan_async
from workers  import (
    MotorVehicleWorker, LINWorker, PumpSignal, PumpDataClient, CANWorker,
)
from widgets_base import StatusLed, _lbl, _hsep, _cd_btn
from panels import (
    MotorDashPanel, PumpPanel, VehicleRainPanel, CRSLINPanel, CANBusPanel,
)
from fault_injection_panel import FaultInjectionPanel
from auto_test_panel  import AutoTestPanel
from test_runner      import TestRunner
from test_params_panel import TestParamsPanel
from car_html_widget  import CarHTMLWidget, CarXRayWidget
from rte_client       import RTEClient
from sim_client       import SimClient
# datasave_panel / scenario_replay_panel merged → data_replay_panel
from PySide6.QtCore    import QThread


# ═══════════════════════════════════════════════════════════
#  SCAN DIALOG
# ═══════════════════════════════════════════════════════════
class NetworkScanDialog(QDialog):
    connected = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Auto-Discovery — WipeWash BCM")
        self.setMinimumSize(520, 380)
        self.setStyleSheet(f"background:{W_PANEL};color:{W_TEXT};")
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(10)

        lay.addWidget(_lbl("LOCAL NETWORK SCAN", 18, True, A_TEAL2))
        lay.addWidget(_hsep())

        self._results_lay = QVBoxLayout(); self._results_lay.setSpacing(4)
        lay.addLayout(self._results_lay, 1)

        self._bars: dict[str, QProgressBar] = {}
        self._bar_lbls: dict[str, QLabel]  = {}
        for name in ["Motors / Wiper", "Pump", "LIN Bus"]:
            row = QHBoxLayout(); row.setSpacing(8)
            lbl = _lbl(name, 10, True, W_TEXT_DIM); lbl.setFixedWidth(140)
            bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(0)
            bar.setFixedHeight(16); bar.setTextVisible(False)
            bar.setStyleSheet(
                f"QProgressBar{{background:{W_PANEL3};border:1px solid {W_BORDER};border-radius:2px;}}"
                f"QProgressBar::chunk{{background:{A_TEAL};border-radius:2px;}}")
            status = _lbl("Waiting", 10, False, W_TEXT_DIM, True); status.setFixedWidth(80)
            row.addWidget(lbl); row.addWidget(bar, 1); row.addWidget(status)
            self._results_lay.addLayout(row)
            self._bars[name] = bar; self._bar_lbls[name] = status
        lay.addWidget(_hsep())

        lay.addWidget(_lbl("HOSTS FOUND", 18, True, W_TEXT_DIM))
        self._hosts_lay = QVBoxLayout(); self._hosts_lay.setSpacing(3)
        lay.addLayout(self._hosts_lay, 1)

        btn_row = QHBoxLayout()
        self.btn_scan  = _cd_btn("RUN SCAN", A_TEAL,    h=32)
        self.btn_close = _cd_btn("Close",     "#707070", h=32)
        self.btn_scan.clicked.connect(self._launch)
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_scan); btn_row.addStretch(); btn_row.addWidget(self.btn_close)
        lay.addLayout(btn_row)

        self._scanning = False; self._pending = 0

    def _launch(self) -> None:
        if self._scanning: return
        self._scanning = True; self._pending = 3
        self.btn_scan.setEnabled(False); self.btn_scan.setText("Scanning...")
        while self._hosts_lay.count():
            it = self._hosts_lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        for bar in self._bars.values(): bar.setValue(0)
        for lbl in self._bar_lbls.values():
            lbl.setText("Scan...")
            lbl.setStyleSheet(f"color:{A_ORANGE};background:transparent;")

        services = [
            (PORT_MOTOR,   "Motors / Wiper", A_TEAL),
            (PORT_PUMP_RX, "Pump",           A_GREEN),
            (PORT_LIN,     "LIN Bus",        "#6A1B9A"),
        ]
        for port, name, color in services:
            def _prog(pct, n=name):
                QTimer.singleShot(0, lambda v=pct, nn=n: self._bars[nn].setValue(v))
            def _done(hosts, n=name, c=color):
                QTimer.singleShot(0, lambda h=hosts, nn=n, cc=c: self._on_done(nn, h, cc))
            scan_async(port, _prog, _done)

    def _on_done(self, name: str, hosts: list[str], color: str) -> None:
        bar_lbl = self._bar_lbls[name]
        if hosts:
            bar_lbl.setText(f"{len(hosts)} found")
            bar_lbl.setStyleSheet(f"color:{A_GREEN};font-weight:bold;background:transparent;")
            for h in hosts:
                card = QFrame()
                card.setStyleSheet(
                    f"QFrame{{background:{W_PANEL2};border:1px solid {W_BORDER};"
                    f"border-left:3px solid {color};border-radius:2px;}}")
                cl = QHBoxLayout(card); cl.setContentsMargins(10, 4, 10, 4); cl.setSpacing(10)
                cl.addWidget(_lbl(h, 11, True, W_TEXT, True))
                cl.addWidget(_lbl(name, 10, False, W_TEXT_DIM))
                cl.addStretch()
                b = _cd_btn("CONNECT", color, h=24, w=90)
                b.clicked.connect(lambda _, ip=h: self._on_connect(ip))
                cl.addWidget(b); self._hosts_lay.addWidget(card)
        else:
            bar_lbl.setText("Not found")
            bar_lbl.setStyleSheet(f"color:{A_RED};background:transparent;")
        self._pending -= 1
        if self._pending <= 0:
            self._scanning = False
            self.btn_scan.setEnabled(True); self.btn_scan.setText("RESCAN")

    def _on_connect(self, ip: str) -> None:
        QMessageBox.information(
            self, "Connection",
            f"Module {ip} selected.\nAutomatic reconnection started.", "OK")
        self.connected.emit(); self.close()


# ═══════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════
_PORT_NAMES = {
    PORT_MOTOR:   "Motors",
    PORT_LIN:     "LIN",
    PORT_PUMP_RX: "Pump",
    PORT_CAN:     "CAN",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._motor_worker = MotorVehicleWorker()
        self._motor_thread = QThread()
        self._motor_worker.moveToThread(self._motor_thread)
        self._motor_thread.started.connect(self._motor_worker.run)

        self._lin_worker  = LINWorker()
        self._lin_thread  = QThread()
        self._lin_worker.moveToThread(self._lin_thread)
        self._lin_thread.started.connect(self._lin_worker.run)

        self._pump_signal = PumpSignal()
        self._pump_client = PumpDataClient(self._pump_signal)

        self._can_worker  = CANWorker()
        self._can_thread  = QThread()
        self._can_worker.moveToThread(self._can_thread)
        self._can_thread.started.connect(self._can_worker.run)

        # Client Redis — connecté au RpiBCM (host résolu après connexion)
        self._rte_client: RTEClient | None = None
        # Client TCP — connecté au RPi Simulateur (injection de défauts GPIO)
        self._sim_client: SimClient = SimClient()

        # DataSave — enregistrement & export CSV
        self._data_recorder = DataRecorder()

        self._build_ui()
        self._connect_signals()

        self._motor_thread.start()
        self._lin_thread.start()
        self._pump_client.start()
        self._can_thread.start()

    # ── Construction UI ──────────────────────────────────────
    def _build_ui(self) -> None:
        self.setWindowTitle("WipeWash  —  HIL Test Bench  |  Wipe & Wash System")
        self.setMinimumSize(1100, 720); self.resize(1440, 900)
        self.setStyleSheet(f"QMainWindow {{ background:{W_BG}; }}")

        self._build_menubar()
        self._build_toolbar()

        # ── Panneaux ─────────────────────────────────────────
        self._motor_panel  = MotorDashPanel()
        self._pump_panel   = PumpPanel(
            lambda: self._pump_client,
            rte_getter=lambda: self._rte_client,
            sim_getter=lambda: self._sim_client,
        )
        self._veh_panel    = VehicleRainPanel(lambda: self._motor_worker)
        self._crslin_panel = CRSLINPanel(
            wiper_setter=self._lin_worker.set_wiper_op,
            lin_sender=self._lin_worker.queue_send,
        )
        self._can_panel    = CANBusPanel()

        # ── Fault Injection Panel (standalone — pompe directe + défauts H-Bridge) ──
        self._fi_panel = FaultInjectionPanel(
            pump_data_signal=self._pump_signal,
            rte_getter=lambda: self._rte_client,
            sim_getter=lambda: self._sim_client,
        )

        def _make_runner():
            return TestRunner(
                self._can_worker,
                self._lin_worker,
                self._motor_worker,
                pump_signal=self._pump_signal,
                rte_client=self._rte_client,
                sim_client=self._sim_client,
            )
        self._auto_test_panel = AutoTestPanel(runner_factory=_make_runner)

        # ── QTabWidget central — 4 pages ─────────────────────
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border:none; background:{W_BG}; }}
            QTabBar {{ background:#1A1A2E; border-bottom:2px solid {A_TEAL}; }}
            QTabBar::tab {{
                background:#1A1A2E; color:#FFFFFF; border:none;
                border-right:1px solid #2A2A3E;
                padding:8px 26px;
                font-family:{FONT_UI}; font-size:10pt; font-weight:bold;
                min-width:160px;
            }}
            QTabBar::tab:selected {{
                background:#0D1117; color:{A_TEAL};
                border-top:2px solid {A_TEAL};
            }}
            QTabBar::tab:hover:!selected {{ background:#252535; color:#FFFFFF; }}
        """)

        # ── CarHTMLWidget — voiture BMW 3D centrale (LIN/CRS/Vehicle tab) ──────────
        self._car_html = CarHTMLWidget()
        self._car_html.setMinimumWidth(380)

        # ── CarXRayWidget — controldesk-xray.html pour l'onglet Motor / Pump ──────
        self._car_html_mp = CarXRayWidget()
        self._car_html_mp.setMinimumWidth(360)

        # Brancher la voiture HTML sur les 3 panneaux qui la contrôlent
        self._crslin_panel.set_car_widget(self._car_html)
        self._veh_panel.set_car_widget(self._car_html)
        self._can_panel.set_car_widget(self._car_html)
        # Enregistrer la voiture de l'onglet Motor/Pump pour qu'elle reçoive
        # les mêmes mises à jour (ignition, speed, rain, reverse) que la voiture principale
        self._veh_panel.add_car_widget(self._car_html_mp)

        if hasattr(self._veh_panel, "set_pump_panel"):
            self._veh_panel.set_pump_panel(self._pump_panel)

        # ── Page 1 : Motor / Pompe — splitter 3 colonnes avec voiture au centre ──
        pg1 = QWidget(); pg1.setStyleSheet(f"background:{W_BG};")
        l1  = QHBoxLayout(pg1); l1.setContentsMargins(0, 0, 0, 0); l1.setSpacing(0)

        spl1 = QSplitter(Qt.Orientation.Horizontal)
        spl1.setStyleSheet(f"QSplitter::handle{{background:{W_BORDER};width:3px;}}")

        # Colonne gauche : Motor panel
        spl1.addWidget(self._motor_panel)

        # Colonne centrale : voiture HTML animée — prend tout le vertical
        car_mp_wrapper = QWidget(); car_mp_wrapper.setStyleSheet(f"background:{W_BG};")
        car_mp_lay = QVBoxLayout(car_mp_wrapper)
        car_mp_lay.setContentsMargins(0, 0, 0, 0); car_mp_lay.setSpacing(0)
        car_mp_lay.addWidget(self._car_html_mp, 1)
        from PySide6.QtWidgets import QSizePolicy as _SP
        self._car_html_mp.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
        spl1.addWidget(car_mp_wrapper)

        # Colonne droite : Pump panel
        spl1.addWidget(self._pump_panel)

        # Proportions : Motor 28% | Car 44% | Pump 28%
        spl1.setSizes([300, 460, 300])
        spl1.setChildrenCollapsible(False)

        l1.addWidget(spl1)
        self._tabs.addTab(pg1, "Motor / Pump")

        # ── Page 2 : LIN / CRS / Vehicle — voiture HTML au centre ──────────
        pg2 = QWidget(); pg2.setStyleSheet(f"background:{W_BG};")
        l2  = QHBoxLayout(pg2); l2.setContentsMargins(0, 0, 0, 0); l2.setSpacing(0)

        # Splitter horizontal 3 colonnes
        spl2 = QSplitter(Qt.Orientation.Horizontal)
        spl2.setStyleSheet(f"QSplitter::handle{{background:{W_BORDER};width:3px;}}")

        # Colonne gauche : CRS / LIN panel (tabs: CRS Control, LIN Signal, LIN Table)
        spl2.addWidget(self._crslin_panel)

        # Colonne centrale : voiture HTML BMW + contrôles sous la voiture
        car_wrapper = QWidget(); car_wrapper.setStyleSheet(f"background:{W_BG};")
        car_lay = QVBoxLayout(car_wrapper)
        car_lay.setContentsMargins(4, 4, 4, 4); car_lay.setSpacing(6)
        car_lay.addWidget(self._car_html, 1)

        # ── Contrôles sous la voiture : Ignition / Speed / Rain ──────────────
        ctrl_bar = self._build_car_controls()
        car_lay.addWidget(ctrl_bar)
        spl2.addWidget(car_wrapper)

        # Colonne droite : CAN Bus seulement (VehicleRain déplacé sous la voiture)
        spl2.addWidget(self._can_panel)

        # Proportions : LIN/CRS 28% | Car 50% | CAN 22%
        spl2.setSizes([300, 600, 330])

        l2.addWidget(spl2)
        self._tabs.addTab(pg2, "LIN / CRS / Vehicle")

        # ── Page 4 : Tests Auto ───────────────────────────────
        self._tabs.addTab(self._auto_test_panel, "Auto Tests")

        # ── Page 4b : Paramétrage des tests ──────────────────
        self._test_params_panel = TestParamsPanel()
        self._tabs.addTab(self._test_params_panel, "⚙ Params Tests")

        # ── Page 5 : Fault Injection ──────────────────────────
        self._tabs.addTab(self._fi_panel, "Fault Injection")

        # ── Page 6 : Data / Replay (fusionné) ────────────────
        self._data_replay_panel = DataReplayPanel(
            recorder     = self._data_recorder,
            can_worker   = self._can_worker,
            lin_worker   = self._lin_worker,
            motor_worker = self._motor_worker,
            rte_client   = getattr(self, '_rte_client', None),
        )
        self._data_replay_panel.connect_panels(
            motor_panel  = self._motor_panel,
            pump_panel   = self._pump_panel,
            veh_panel    = self._veh_panel,
            crslin_panel = self._crslin_panel,
            can_panel    = self._can_panel,
            car_html     = self._car_html,
            car_html_mp  = self._car_html_mp,
            main_window  = self,
        )
        # Alias de compatibilité (accès depuis d'autres modules)
        self._datasave_panel  = self._data_replay_panel
        self._scenario_panel  = self._data_replay_panel
        self._tabs.addTab(self._data_replay_panel, "Data / Replay")

        # Pause oscilloscopes sur onglets cachés → gain CPU
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

        self.setCentralWidget(self._tabs)

        sb = QStatusBar(); sb.setFont(QFont(FONT_MONO, 10))
        sb.setStyleSheet(
            f"background:{W_TOOLBAR};color:{W_TEXT_DIM};border-top:1px solid {W_BORDER};")
        self.setStatusBar(sb); self._qsb = sb

    def _build_car_controls(self) -> QWidget:
        """Widget sous la voiture : Ignition+Reverse | SpeedKnob | RainKnob.
        Réutilise directement les widgets originaux _SpeedKnob et _RainKnob."""
        from panels import _SpeedKnob, _RainKnob

        # ── Conteneur principal ──────────────────────────────────────────────
        bar = QWidget()
        bar.setStyleSheet("background:#0a0e0a;border-top:2px solid rgba(141,198,63,0.5);")

        main_row = QHBoxLayout(bar)
        main_row.setContentsMargins(12, 8, 12, 8)
        main_row.setSpacing(8)

        # ── Séparateur vertical ──────────────────────────────────────────────
        def _vsep():
            s = QFrame(); s.setFrameShape(QFrame.Shape.VLine)
            s.setStyleSheet(f"color:{W_BORDER};"); return s

        # ══════════════════════════════════════════════
        # SECTION 1 — IGNITION + REVERSE
        # ══════════════════════════════════════════════
        from widgets_base import _lbl as _wlbl
        from PySide6.QtWidgets import QPushButton

        ign_w = QWidget(); ign_w.setStyleSheet("background:transparent;")
        ign_lay = QVBoxLayout(ign_w)
        ign_lay.setContentsMargins(4, 4, 4, 4); ign_lay.setSpacing(6)

        lbl_ign = QLabel("IGNITION")
        lbl_ign.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        lbl_ign.setStyleSheet(f"color:#4fc3f7;background:transparent;letter-spacing:2px;")
        lbl_ign.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ign_lay.addWidget(lbl_ign)

        ign_btn_row = QHBoxLayout(); ign_btn_row.setSpacing(8)
        ign_btn_row.setContentsMargins(0, 0, 0, 0)
        self._ign_btns = {}
        ign_btn_row.addStretch()
        for state in ["OFF", "ACC", "ON"]:
            b = QPushButton(state)
            b.setFixedSize(40, 26)
            b.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, s=state: self._car_ign_changed(s))
            self._ign_btns[state] = b
            ign_btn_row.addWidget(b)
        ign_btn_row.addStretch()
        ign_lay.addLayout(ign_btn_row)

        self._rev_btn = QPushButton("⇄  REVERSE")
        self._rev_btn.setFixedHeight(28)
        self._rev_btn.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        self._rev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rev_btn.setCheckable(True)
        self._rev_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;color:#888888;"
            "border:1px solid #444;border-radius:4px;}"
            f"QPushButton:checked{{background:#2a1a00;color:{A_ORANGE};"
            f"border:1px solid {A_ORANGE};}}"
            "QPushButton:hover{border-color:#aaaaaa;}")
        self._rev_btn.toggled.connect(self._car_rev_toggled)
        ign_lay.addWidget(self._rev_btn)

        self._car_ign_state = "OFF"
        self._car_ign_changed("OFF")

        main_row.addWidget(ign_w, 2)
        main_row.addWidget(_vsep())

        # ══════════════════════════════════════════════
        # SECTION 2 — SPEED KNOB (widget original)
        # ══════════════════════════════════════════════
        spd_w = QWidget(); spd_w.setStyleSheet("background:transparent;")
        spd_lay = QVBoxLayout(spd_w)
        spd_lay.setContentsMargins(4, 0, 4, 0); spd_lay.setSpacing(0)

        lbl_spd = QLabel("VEHICLE SPEED")
        lbl_spd.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        lbl_spd.setStyleSheet(f"color:{A_GREEN};background:transparent;letter-spacing:0px;")
        lbl_spd.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spd_lay.addWidget(lbl_spd)

        # Réutilise le knob existant du veh_panel directement
        self._veh_panel._spd_knob.setParent(spd_w)
        spd_lay.addWidget(self._veh_panel._spd_knob, 1)
        self._spd_disp = QLabel("")  # stub inutilisé mais référencé

        main_row.addWidget(spd_w, 3)
        main_row.addWidget(_vsep())

        # ══════════════════════════════════════════════
        # SECTION 3 — RAIN KNOB (widget original)
        # ══════════════════════════════════════════════
        rain_w = QWidget(); rain_w.setStyleSheet("background:transparent;")
        rain_lay = QVBoxLayout(rain_w)
        rain_lay.setContentsMargins(4, 0, 4, 0); rain_lay.setSpacing(0)

        lbl_rain = QLabel("RAIN INTENSITY")
        lbl_rain.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        lbl_rain.setStyleSheet("color:#4fc3f7;background:transparent;letter-spacing:0px;")
        lbl_rain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rain_lay.addWidget(lbl_rain)

        # Réutilise le knob existant du veh_panel directement
        self._veh_panel._rain_knob.setParent(rain_w)
        rain_lay.addWidget(self._veh_panel._rain_knob, 1)
        self._rain_disp = QLabel("")       # stub
        self._rain_lbl_mode = QLabel("")   # stub

        main_row.addWidget(rain_w, 3)
        main_row.addWidget(_vsep())

        # ══════════════════════════════════════════════
        # SECTION 4 — SENSOR STATUS + SIMULATE ERROR
        # ══════════════════════════════════════════════
        sens_w = QWidget(); sens_w.setStyleSheet("background:transparent;")
        sens_lay = QVBoxLayout(sens_w)
        sens_lay.setContentsMargins(4, 2, 4, 2); sens_lay.setSpacing(4)

        # Ligne 1 : titre + LED + label condensés sur une seule ligne
        top_row = QHBoxLayout(); top_row.setSpacing(5)
        lbl_sens_title = QLabel("SENSOR STATUS")
        lbl_sens_title.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        lbl_sens_title.setStyleSheet("color:#4fc3f7;background:transparent;letter-spacing:1px;")
        self._veh_panel._led_sens.setParent(sens_w)
        self._veh_panel.lbl_sens.setParent(sens_w)
        self._veh_panel.lbl_sens.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        top_row.addWidget(lbl_sens_title)
        top_row.addStretch()
        top_row.addWidget(self._veh_panel._led_sens)
        top_row.addWidget(self._veh_panel.lbl_sens)
        sens_lay.addLayout(top_row)

        # Ligne 2 : bouton pleine largeur
        self._veh_panel.btn_sens.setParent(sens_w)
        self._veh_panel.btn_sens.setFixedHeight(24)
        self._veh_panel.btn_sens.setFixedWidth(120)
        self._veh_panel.btn_sens.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        sens_lay.addWidget(self._veh_panel.btn_sens)

        main_row.addWidget(sens_w, 2)

        return bar

    def _car_ign_changed(self, state: str) -> None:
        # OFF=gris, ACC=orange clair, ON=vert fluo (même que ACCEL)
        ACTIVE = {
            "OFF": ("#1a1a1a", "#CCCCCC", "#888888"),
            "ACC": ("#2a1200", "#FFA040", "#FFA040"),
            "ON":  ("#0a1a00", "#39FF14", "#39FF14"),
        }
        INACTIVE = {
            "OFF": ("#0e0e0e", "#555555", "#333333"),
            "ACC": ("#150900", "#7A4500", "#4A2A00"),
            "ON":  ("#060e00", "#1A6600", "#0E3A00"),
        }
        for s, b in self._ign_btns.items():
            if s == state:
                bg, fg, brd = ACTIVE[s]
                b.setStyleSheet(
                    f"QPushButton{{background:{bg};color:{fg};"
                    f"border:1.5px solid {brd};border-radius:4px;"
                    f"font-weight:bold;font-size:8pt;}}")
            else:
                bg, fg, brd = INACTIVE[s]
                b.setStyleSheet(
                    f"QPushButton{{background:{bg};color:{fg};"
                    f"border:1px solid {brd};border-radius:4px;font-size:8pt;}}"
                    f"QPushButton:hover{{color:{ACTIVE[s][1]};}}")
        self._car_ign_state = state
        self._veh_panel.ign._sel(state)

    def _car_rev_toggled(self, checked: bool) -> None:
        self._veh_panel._toggle_rev()
        # Sync visual label
        self._rev_btn.setText("⇄  REVERSE ON" if checked else "⇄  REVERSE")

    def _car_accel(self) -> None:
        knob = self._veh_panel._spd_knob
        step = 100 if knob._val > 200 else 10
        knob._val = min(2000, knob._val + step)
        knob._arc.set_value(knob._val)
        knob.value_changed.emit(knob._val)

    def _car_brake(self) -> None:
        knob = self._veh_panel._spd_knob
        step = 100 if knob._val > 500 else 10
        knob._val = max(0, knob._val - step)
        knob._arc.set_value(knob._val)
        knob.value_changed.emit(knob._val)

    def _car_rain_up(self) -> None:
        knob = self._veh_panel._rain_knob
        knob._val = min(100, knob._val + 5)
        knob._arc.set_value(knob._val)
        knob.value_changed.emit(knob._val)

    def _car_rain_dn(self) -> None:
        knob = self._veh_panel._rain_knob
        knob._val = max(0, knob._val - 5)
        knob._arc.set_value(knob._val)
        knob.value_changed.emit(knob._val)

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        mb.setStyleSheet(f"""
            QMenuBar{{background:#FFFFFF;color:#1A1A1A;
                border-bottom:1px solid {W_BORDER};
                font-family:{FONT_UI};font-size:11pt;padding:2px;}}
            QMenuBar::item{{padding:4px 12px;border-radius:2px;}}
            QMenuBar::item:selected{{background:#EDF9E3;}}
            QMenu{{background:#FFFFFF;color:#1A1A1A;
                border:1px solid {W_BORDER};font-size:11pt;}}
            QMenu::item{{padding:5px 28px;}}
            QMenu::item:selected{{background:#EDF9E3;color:{A_TEAL2};}}
            QMenu::separator{{height:1px;background:{W_BORDER};margin:3px 8px;}}
        """)
        m_conn = mb.addMenu("Connection")
        act_scan  = QAction("Network Scan...", self); act_scan.setShortcut("Ctrl+Shift+S")
        act_scan.triggered.connect(self._open_scan); m_conn.addAction(act_scan)
        act_recon = QAction("Reconnect all services", self); act_recon.setShortcut("Ctrl+R")
        act_recon.triggered.connect(self._on_rescan); m_conn.addAction(act_recon)
        m_conn.addSeparator()
        act_quit  = QAction("Quit", self); act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close); m_conn.addAction(act_quit)

        m_view = mb.addMenu("View")
        for i, (name, shortcut) in enumerate([
            ("Motor / Pump",          "Ctrl+1"),
            ("LIN / CRS / Vehicle",   "Ctrl+2"),
            ("Auto Tests",            "Ctrl+3"),
            ("⚙ Params Tests",        "Ctrl+P"),
            ("Fault Injection",       "Ctrl+4"),
            ("Data / Replay",         "Ctrl+5"),
        ]):
            act = QAction(f"  {name}", self); act.setShortcut(shortcut)
            act.triggered.connect(lambda _, idx=i: self._tabs.setCurrentIndex(idx))
            m_view.addAction(act)

        m_help = mb.addMenu("Help")
        act_about = QAction("About...", self)
        act_about.triggered.connect(lambda: QMessageBox.about(
            self, "WipeWash HIL Dashboard",
            "WipeWash Unified Dashboard v5\n\nHIL Test Bench Platform\n"
            "Automotive Wipe & Wash System\n\ndSPACE SCALEXIO compatible"))
        m_help.addAction(act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main Toolbar"); tb.setMovable(False); tb.setFixedHeight(34)
        tb.setStyleSheet(
            f"QToolBar{{background:#FFFFFF;border:none;"
            f"border-bottom:1px solid {W_BORDER};spacing:4px;padding:2px 8px;}}"
            f"QToolButton{{background:transparent;border:none;border-radius:2px;"
            f"color:{W_TEXT};padding:3px 8px;font-family:{FONT_UI};font-size:10pt;}}"
            f"QToolButton:hover{{background:#EDF9E3;}}")
        self.addToolBar(tb)

        self._toolbar_leds:   dict[int, StatusLed] = {}
        self._toolbar_labels: dict[int, QLabel]    = {}
        for port, name, color in [
            (PORT_MOTOR,   "Motors", A_GREEN),
            (PORT_LIN,     "LIN",    A_TEAL),
            (PORT_PUMP_RX, "Pump",   A_ORANGE),
            (PORT_CAN,     "CAN",    CAN_VEH_C),
        ]:
            led = StatusLed(9); lbl = _lbl(f" {name} ", 10, True, W_TEXT_DIM)
            self._toolbar_leds[port]   = led
            self._toolbar_labels[port] = lbl
            cw = QWidget(); cw.setStyleSheet("background:transparent;")
            cl = QHBoxLayout(cw); cl.setContentsMargins(4, 0, 10, 0); cl.setSpacing(4)
            cl.addWidget(led); cl.addWidget(lbl); tb.addWidget(cw)
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet(f"background:{W_BORDER};max-width:1px;"); tb.addWidget(sep)

        tb.addSeparator()
        btn_scan = _cd_btn("Scan", A_TEAL, h=26, w=80)
        btn_scan.clicked.connect(self._open_scan); tb.addWidget(btn_scan)

        self._lbl_dt = _lbl("", 10, False, W_TEXT_DIM, True)
        spacer = QWidget(); spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer); tb.addWidget(self._lbl_dt)
        dt_t = QTimer(self); dt_t.timeout.connect(self._upd_dt); dt_t.start(1000)
        self._upd_dt()

    def _on_tab_changed(self, idx: int) -> None:
        """Pause les timers d'animation sur les pages cachées → gain CPU.
        Page 0 = Motor/Pompe   Page 1 = LIN/CRS/Vehicle   Page 2 = Tests   Page 3 = Fault
        """
        # ── Page 0 : timers MotorWidget + PumpWidget ──────────
        for attr in ("motor_front", "motor_rear"):
            t = getattr(getattr(self._motor_panel, attr, None), "_t", None)
            if t:
                t.start(60) if idx == 0 else t.stop()
        pump_t = getattr(getattr(self._pump_panel, "pump_widget", None), "_t", None)
        if pump_t:
            pump_t.start(60) if idx == 0 else pump_t.stop()

        # ── Page 1 : oscilloscope LIN (CAN toujours actif sur la même page) ──
        osc_lin = getattr(getattr(self._crslin_panel, "_osc", None), "_t", None)
        if osc_lin:
            osc_lin.start(100) if idx == 1 else osc_lin.stop()
        osc_can = getattr(getattr(self._can_panel, "_osc", None), "_t", None)
        if osc_can:
            osc_can.start(100) if idx == 1 else osc_can.stop()

    # ── Helpers ──────────────────────────────────────────────
    def _upd_dt(self) -> None:
        self._lbl_dt.setText(datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def _open_scan(self) -> None:
        dlg = NetworkScanDialog(self)
        dlg.connected.connect(self._on_rescan)
        dlg.exec()

    # ── Connexion des signaux ────────────────────────────────
    def _connect_signals(self) -> None:
        self._motor_worker.motor_received.connect(self._motor_panel.on_motor_data)
        self._motor_worker.motor_received.connect(self._on_motor_data_ws)   # ← BCM→CarHTMLWidget (LIN tab)
        self._motor_worker.motor_received.connect(self._on_motor_data_mp)   # ← BCM→CarHTMLWidget (Motor/Pump tab)
        self._motor_worker.motor_received.connect(self._datasave_panel.on_motor_data)  # ← DataSave
        self._motor_worker.status_changed.connect(self._on_motor_status)
        self._motor_worker.wiper_sent.connect(self._on_wiper_sent)
        self._motor_worker.sim_host_found.connect(self._on_sim_host_found)
        self._lin_worker.lin_received.connect(self._on_lin_event)
        self._lin_worker.lin_received.connect(self._datasave_panel.on_lin_event)       # ← DataSave
        self._lin_worker.status_changed.connect(self._on_lin_status)
        self._pump_signal.data_received.connect(self._pump_panel.update_display)
        self._pump_signal.data_received.connect(self._fi_panel.on_pump_data)
        self._pump_signal.data_received.connect(self._datasave_panel.on_pump_data)     # ← DataSave
        self._pump_signal.data_received.connect(self._on_pump_data_mp)                 # ← Pump rain→CarHTMLWidget (Motor/Pump tab)
        self._pump_signal.connection_ok.connect(self._on_pump_ok)
        self._pump_signal.connection_lost.connect(self._on_pump_lost)
        self._can_worker.can_received.connect(self._can_panel.add_can_event)
        self._can_worker.can_received.connect(self._datasave_panel.on_can_event)       # ← DataSave
        self._can_worker.status_changed.connect(self._on_can_status)
        self._can_panel.ack_needed.connect(
            self._can_worker.send_0x202,
            Qt.ConnectionType.DirectConnection
        )

        # ── Signaux virtuels ScenarioReplay → widgets (sans ECU physique) ──
        eng = self._scenario_panel._engine
        eng.virtual_motor_data.connect(self._motor_panel.on_motor_data)
        eng.virtual_motor_data.connect(self._on_motor_data_ws)
        eng.virtual_motor_data.connect(self._on_motor_data_mp)
        eng.virtual_lin_event.connect(self._on_lin_event)
        eng.virtual_pump_data.connect(self._pump_panel.update_display)
        eng.virtual_pump_data.connect(self._on_pump_data_mp)

    def _on_motor_data_ws(self, data: dict) -> None:
        """
        Dispatch données BCM temps réel vers CarHTMLWidget + Rest Contact + CRS Fault.
        Appelé à chaque motor_received (~200 ms) depuis le TCP broadcast du BCM.
        Complète _on_lin_event : données moteur sur port 5000, events LIN sur 5555.
        """
        front_on     = data.get("front", "OFF") == "ON"
        rear_on      = data.get("rear",  "OFF") == "ON"
        motor_on     = front_on or rear_on          # REAR_WASH/REAR_WIPE use rear motor
        rest_raw     = bool(data.get("rest_contact_raw",   False))
        blade_cycles = int(data.get("front_blade_cycles",  0))
        bcm_state    = str(data.get("state",               "OFF"))
        crs_fault    = int(data.get("crs_fault",           0))
        cur_op       = getattr(self._crslin_panel, "_cur_op", 0)

        # Voiture HTML : état wiper temps réel BCM
        self._car_html.set_wiper_from_bcm(
            motor_on  = motor_on,
            rest_raw  = rest_raw,
            bcm_state = bcm_state,
            op        = cur_op,
        )

        # Rest Contact panel
        self._crslin_panel.update_rest_contact(rest_raw, blade_cycles)

        # CRS Fault (depuis rte.crs_fault broadcasté par BCM)
        self._crslin_panel.update_crs_fault(crs_fault)

        # ── Barres Rear Wiper Status (CRS panel) pour REAR_WASH/REAR_WIPE ──
        # Le moteur arrière transmet ses données via le flux TCP moteur (port 5000).
        # On extrait current et blade et on les pousse dans le panneau CRS.
        if cur_op in (6, 7):
            def _d(v):
                if isinstance(v, dict): return v
                try:
                    import json as _j
                    return _j.loads(v) if isinstance(v, str) else {}
                except Exception:
                    return {}
            r          = _d(data.get("rear", {}))
            rear_cur   = float(r.get("motor_current", 0.0))
            rear_blade = float(r.get("blade_position", 0.0))
            rear_fault = bool(r.get("fault_status", False))
            self._crslin_panel.update_rear_wiper_status(
                blade_pct = rear_blade,
                current_A = rear_cur,
                fault     = rear_fault,
            )

    def _on_motor_data_mp(self, data: dict) -> None:
        """
        Dispatch données BCM temps réel vers CarHTMLWidget de l'onglet Motor/Pump.
        Même logique que _on_motor_data_ws mais pilote self._car_html_mp.
        """
        front_on  = data.get("front", "OFF") == "ON"
        rear_on   = data.get("rear",  "OFF") == "ON"
        motor_on  = front_on or rear_on
        rest_raw  = bool(data.get("rest_contact_raw", False))
        bcm_state = str(data.get("state", "OFF"))
        cur_op    = getattr(self._crslin_panel, "_cur_op", 0)

        self._car_html_mp.set_wiper_from_bcm(
            motor_on  = motor_on,
            rest_raw  = rest_raw,
            bcm_state = bcm_state,
            op        = cur_op,
        )

    def _on_pump_data_mp(self, data: dict) -> None:
        """
        Sync pump state + rain → CarHTMLWidget onglet Motor/Pump.
        Pilote l'animation pompe (impeller, fluid lines) et la pluie.
        """
        # Animation pompe (impeller + lignes fluide)
        state = data.get("state", "OFF")
        fault = bool(data.get("fault", False))
        self._car_html_mp.set_pump_state(state, fault)
        # Sync pluie depuis slider VehicleRainPanel
        rain_pct = getattr(self._pump_panel, "_rain_pct", 0)
        self._car_html_mp.set_rain(rain_pct)

    def _set_tb_status(self, port: int, ok: bool, host: str = "") -> None:
        led = self._toolbar_leds.get(port)
        lbl = self._toolbar_labels.get(port)
        if not led or not lbl: return
        led.set_state(ok, A_GREEN if ok else A_RED)
        n = _PORT_NAMES.get(port, "?")
        if ok:
            lbl.setText(f" {n}  ")
            lbl.setStyleSheet(
                f"color:{A_GREEN};background:transparent;"
                f"font-family:{FONT_UI};font-size:10pt;font-weight:bold;")
        else:
            lbl.setText(f" {n}  ")
            lbl.setStyleSheet(
                f"color:{W_TEXT_DIM};background:transparent;"
                f"font-family:{FONT_UI};font-size:10pt;")

    # ── Slots ─────────────────────────────────────────────────
    def _on_motor_status(self, msg: str, ok: bool) -> None:
        self._set_tb_status(PORT_MOTOR, ok, self._motor_worker.host)
        self._qsb.showMessage(f"[Motors] {msg}")
        # Cree RTEClient Redis des qu'un host BCM est connu
        host = self._motor_worker.host
        if ok and host and self._rte_client is None:
            self._rte_client = RTEClient(host)
            connected = self._rte_client.is_connected()
            if hasattr(self, '_auto_test_panel'):
                self._auto_test_panel.set_redis_status(connected, host)
            if hasattr(self, '_fi_panel'):
                if connected:
                    self._fi_panel.on_connected_bcm(host)
                else:
                    self._fi_panel.on_disconnected_bcm()
            if connected:
                self._qsb.showMessage(f"[Redis] Connecte sur {host}:6379")
        elif not ok:
            self._rte_client = None
            if hasattr(self, '_auto_test_panel'):
                self._auto_test_panel.set_redis_status(False)
            if hasattr(self, '_fi_panel'):
                self._fi_panel.on_disconnected_bcm()

    def _on_wiper_sent(self, op: int, seq: int) -> None:
        self._crslin_panel.on_wiper_sent(op, seq)

    def _on_lin_event(self, ev: dict) -> None:
        self._crslin_panel.add_lin_event(ev)
        t = ev.get("type", "")

        # ── Trame 0x16 TX (BCM → slave) : wiper_op + état moteur + rest contact ──
        if t == "TX":
            op          = int(ev.get("op",            0))
            bcm_state   = str(ev.get("bcm_state",     "OFF"))
            motor_on    = bool(ev.get("front_motor_on", False)) or bool(ev.get("rear_motor_on", False))  # REAR_WASH/REAR_WIPE use rear motor
            rest_raw    = bool(ev.get("rest_contact_raw", False))
            blade_cycles= int(ev.get("front_blade_cycles", 0))

            # Voiture HTML : état wiper temps réel BCM
            self._car_html.set_wiper_from_bcm(
                motor_on  = motor_on,
                rest_raw  = rest_raw,
                bcm_state = bcm_state,
                op        = op,
            )
            # Rest contact panel
            self._crslin_panel.update_rest_contact(rest_raw, blade_cycles)

        # ── Trame 0x17 RX (slave → BCM) : CRS_InternalFault ──────────────────
        elif t == "RX_HDR" and ev.get("pid") == "0x97":
            fault_val = int(ev.get("fault", "0x00"), 16) if isinstance(
                ev.get("fault"), str) else int(ev.get("fault", 0))
            self._crslin_panel.update_crs_fault(fault_val)

        # ── Ack injection fault depuis simulateur ─────────────────────────────
        elif t == "crs_fault_ack":
            try:
                fault_val = int(ev.get("fault", "0x00"), 16)
                self._crslin_panel.update_crs_fault(fault_val)
            except (ValueError, TypeError):
                pass

    def _on_lin_status(self, msg: str, ok: bool) -> None:
        self._set_tb_status(PORT_LIN, ok, self._lin_worker.host)
        self._crslin_panel.set_lin_status(msg, ok)
        self._qsb.showMessage(f"[LIN] {msg}")

    def _on_pump_ok(self, host: str) -> None:
        self._set_tb_status(PORT_PUMP_RX, True, host)
        self._pump_panel.on_connected(host)

    def _on_pump_lost(self) -> None:
        self._set_tb_status(PORT_PUMP_RX, False)
        self._pump_panel.on_disconnected()

    def _on_can_status(self, msg: str, ok: bool) -> None:
        self._set_tb_status(PORT_CAN, ok, self._can_worker.host)
        self._can_panel.set_can_status(msg, ok)
        self._qsb.showMessage(f"[CAN] {msg}")

    def _on_sim_host_found(self, host: str) -> None:
        """Connecte le SimClient au RPi Simulateur dès que son IP est connue."""
        if not self._sim_client.is_connected():
            ok = self._sim_client.connect(host)
            if ok:
                self._qsb.showMessage(f"[SimClient] Simulator connected: {host}:5000")
                print(f"[SimClient] Injection de défauts -> {host}:5000")
                if hasattr(self, '_fi_panel'):
                    self._fi_panel.on_connected_sim(host)

    def _on_rescan(self) -> None:
        # Moteurs
        self._motor_worker.stop()
        self._motor_thread.quit(); self._motor_thread.wait(2000)
        self._motor_worker = MotorVehicleWorker()
        self._motor_thread = QThread()
        self._motor_worker.moveToThread(self._motor_thread)
        self._motor_thread.started.connect(self._motor_worker.run)
        self._motor_worker.motor_received.connect(self._motor_panel.on_motor_data)
        self._motor_worker.motor_received.connect(self._on_motor_data_ws)   # ← BCM→CarHTMLWidget (LIN tab)
        self._motor_worker.motor_received.connect(self._on_motor_data_mp)   # ← BCM→CarHTMLWidget (Motor/Pump tab)
        self._motor_worker.status_changed.connect(self._on_motor_status)
        self._motor_worker.wiper_sent.connect(self._on_wiper_sent)
        self._motor_worker.sim_host_found.connect(self._on_sim_host_found)
        self._sim_client.disconnect()   # reset SimClient — sera reconnecté via sim_host_found
        self._veh_panel._getter          = lambda: self._motor_worker
        self._crslin_panel._wiper_setter = self._lin_worker.set_wiper_op
        self._motor_thread.start()
        # LIN
        self._lin_worker.stop()
        self._lin_thread.quit(); self._lin_thread.wait(2000)
        self._lin_worker = LINWorker()
        self._lin_thread = QThread()
        self._lin_worker.moveToThread(self._lin_thread)
        self._lin_thread.started.connect(self._lin_worker.run)
        self._lin_worker.lin_received.connect(self._on_lin_event)
        self._lin_worker.lin_received.connect(self._datasave_panel.on_lin_event)       # ← DataSave
        self._lin_worker.status_changed.connect(self._on_lin_status)
        self._crslin_panel._lin_sender = self._lin_worker.queue_send   # ← CRS fault injection
        self._lin_thread.start()
        # CAN
        self._can_worker.stop()
        self._can_thread.quit(); self._can_thread.wait(2000)
        self._can_worker = CANWorker()
        self._can_thread = QThread()
        self._can_worker.moveToThread(self._can_thread)
        self._can_thread.started.connect(self._can_worker.run)
        self._can_worker.can_received.connect(self._can_panel.add_can_event)
        self._can_worker.can_received.connect(self._datasave_panel.on_can_event)       # ← DataSave
        self._can_worker.status_changed.connect(self._on_can_status)
        self._can_panel.ack_needed.connect(
            self._can_worker.send_0x202,
            Qt.ConnectionType.DirectConnection
        )
        self._can_thread.start()
        # Pompe — PumpDataClient se reconnecte automatiquement

    def closeEvent(self, e) -> None:
        self._motor_worker.stop(); self._motor_thread.quit(); self._motor_thread.wait(2000)
        self._lin_worker.stop();   self._lin_thread.quit();   self._lin_thread.wait(2000)
        self._can_worker.stop();   self._can_thread.quit();   self._can_thread.wait(2000)
        e.accept()