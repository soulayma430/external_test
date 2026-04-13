"""
test_runner.py  —  Moteur d'exécution des tests automatiques WipeWash
======================================================================
Se branche sur les workers Qt existants (CANWorker, LINWorker, MotorWorker)
via leurs signaux. Exécute les tests séquentiellement depuis ALL_TESTS.

Modifications :
  - pump_signal : connecté à _on_motor pour T21.
  - rte_client  : RTEClient Redis injecté dans BaseBCMTest.rte_client
                  avant chaque test T30-T39. Permet GET/SET direct sur
                  le RTE du RpiBCM sans TCP LIN/CAN.
  - _tick       : appelle _check_rte() sur les tests BaseBCMTest
                  en plus de check_timeout().
"""

import time
import threading
from typing import List, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Qt

from test_cases import (BaseTest, BaseBCMTest, TestResult, ALL_TESTS,
                        TC_B2103_PositionSensorFault)


class TestRunner(QObject):
    # ─── Signaux publics ──────────────────────────────────────────────
    test_started = Signal(str, str)   # (test_id, test_name)
    test_result  = Signal(object)     # TestResult
    all_done     = Signal(list)       # list[TestResult]
    progress     = Signal(int, int)   # (done, total)
    log_msg      = Signal(str)        # message texte libre

    def __init__(self, can_worker, lin_worker, motor_worker,
                 pump_signal=None, rte_client=None, sim_client=None, parent=None):
        super().__init__(parent)
        self._can_w      = can_worker
        self._lin_w      = lin_worker
        self._motor_w    = motor_worker
        self._rte_client = rte_client   # RTEClient Redis (optionnel)
        self._sim_client = sim_client   # SimClient TCP vers RPi Simulateur (optionnel)

        self._queue  : List[BaseTest]   = []
        self._current: Optional[BaseTest] = None
        self._results: List[TestResult] = []
        self._running  = False
        self._total    = 0

        # DirectConnection : appel immédiat dans le thread GUI
        # (évite la queue Qt qui ne serait jamais drainée par la boucle Python)
        dc = Qt.ConnectionType.DirectConnection
        can_worker.can_received    .connect(self._on_can,   dc)
        lin_worker.lin_received    .connect(self._on_lin,   dc)
        motor_worker.motor_received.connect(self._on_motor, dc)

        # Connexion pompe pour T21 :
        # PumpDataClient émet pump_signal.data_received avec les données
        # de TCPPumpBroadcast :5556 → {"state":"FORWARD"/"OFF", ...}
        # T21.on_motor_data() attend exactement ce format.
        # Sans cette connexion, T21 ne reçoit jamais les données pompe
        # car elles transitent par pump_signal et non motor_received.
        if pump_signal is not None:
            pump_signal.data_received.connect(self._on_motor, dc)

        # Timer de supervision timeout (toutes les 200 ms)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # ─── API publique ─────────────────────────────────────────────────
    def run_all(self):
        self._queue   = [cls() for cls in ALL_TESTS]
        self._results = []
        self._total   = len(self._queue)
        self._running = True
        self._timer.start()
        self._start_next()

    def run_selected(self, ids: list):
        self._queue   = [cls() for cls in ALL_TESTS if cls.ID in ids]
        self._results = []
        self._total   = len(self._queue)
        self._running = True
        self._timer.start()
        self._start_next()

    def stop(self):
        self._running = False
        self._timer.stop()
        self._current = None
        self.log_msg.emit("⏹ Tests interrompus")

    # ─── Logique interne ──────────────────────────────────────────────
    def _start_next(self):
        if not self._queue:
            self._running = False
            self._timer.stop()
            n_pass = sum(1 for r in self._results if r.status == "PASS")
            n_fail = sum(1 for r in self._results if r.status == "FAIL")
            n_to   = sum(1 for r in self._results if r.status == "TIMEOUT")
            self.all_done.emit(self._results)
            self.log_msg.emit(
                f"✅ Terminé — PASS:{n_pass}  FAIL:{n_fail}  TIMEOUT:{n_to}")
            return

        self._current = self._queue.pop(0)
        self.log_msg.emit(f"▶ [{self._current.ID}]  {self._current.NAME}")
        self.test_started.emit(self._current.ID, self._current.NAME)
        # Injecter rte_client dans les tests BCM (T30-T39)
        if isinstance(self._current, BaseBCMTest):
            BaseBCMTest.rte_client = self._rte_client
        self._current.start()
        tid_cur = self._current.ID
        # Après T21 (FRONT_WASH), le BCM met ~2s à terminer les cycles
        # et revenir en OFF. Attendre avant le pre_test des tests suivants.
        if tid_cur in ("T30","T31","T32","T33","T34","T35","T36","T37","T38","T39",
                       "T43","T45","TC_LIN_002","TC_LIN_004","TC_LIN_005",
                       "TC_CAN_003","TC_GEN_001","TC_SPD_001","TC_AUTO_004",
                       "TC_FSR_008","TC_FSR_010","TC_COM_001","TC_B2103"):
            last = getattr(self, "_last_tid", "")
            delay = 8000 if last == "T22" else \
                    3000 if last == "TC_FSR_010" else \
                    2500 if last in ("T40", "T21", "T36", "T37", "T43", "T45") else 0
            if delay:
                QTimer.singleShot(delay, lambda t=self._current: self._pre_test_delayed(t))
            else:
                # Garantir que BCM est en OFF avant d'envoyer le stimulus
                # en resettant wc_timeout/lin_timeout résiduels
                if self._rte_client:
                    self._rte_client.set_cmd("wc_timeout_active",  False)
                    self._rte_client.set_cmd("lin_timeout_active", False)
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("ignition_status", 1)
                    self._rte_client.set_cmd("wc_available", False)  # garantit CAS A
                    # Synchroniser aussi le simulateur via TCP : sans ça, bcmcan
                    # continue à envoyer CAN 0x300 avec ignition=0 → écrase Redis
                    self._motor_w.queue_send(
                        {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                    QTimer.singleShot(300, lambda t=self._current: self._pre_test_delayed(t))
                else:
                    self._pre_test(self._current)
        else:
            self._pre_test(self._current)
        self._last_tid = tid_cur
        done = self._total - len(self._queue) - 1
        self.progress.emit(done, self._total)

    def _pre_test_delayed(self, test: BaseTest):
        """Appelé avec délai pour laisser le BCM se stabiliser en OFF."""
        if self._current is test:
            self._pre_test(test)

    def _pre_test(self, test: BaseTest):
        """Envoie les commandes préalables selon le type de test."""
        tid = test.ID

        # ── Tests réseau ──────────────────────────────────────────────────
        if tid == "T10":
            self.log_msg.emit("  → stop_lin_tx")
            self._lin_w.queue_send({"test_cmd": "stop_lin_tx"})
        elif tid == "T11":
            self.log_msg.emit("  → stop_can_tx")
            # wc_available=True obligatoire : sans CAS B actif, le BCM n'observe
            # pas le timeout CAN 0x201 et ne lève jamais wc_timeout_active=True.
            # On active wc_available, on attend 300ms que bcmcan envoie au moins
            # une trame 0x201 pour initialiser t_last_wiper_status, puis on coupe.
            if self._rte_client:
                self._rte_client.set_cmd("wc_available", True)
            QTimer.singleShot(300, lambda: self._motor_w.queue_send(
                {"test_cmd": "stop_can_tx"}))
        elif tid == "T40":
            self.log_msg.emit("  → T40 : TOUCH — 1 cycle puis retour OFF (no repeat)")
            # Même logique que l'ancien T20, mais T40 vérifie en plus :
            # 1) state=OFF après le cycle  2) cycle_count==1 (pas de répétition)
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            self._lin_w.queue_send({"cmd": "TOUCH"})
            def _send_t40():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                if self._rte_client:
                    self._rte_client.set_cmd("rest_contact_sim_active", True)
                    self._rte_client.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                    self._rte_client.set_cmd("crs_wiper_op", 1)          # WOP_TOUCH
                # Retour repos à 1500ms → fin du 1er (et unique) cycle
                QTimer.singleShot(1500, lambda: self._rte_client and
                    self._rte_client.set_cmd("rest_contact_sim", False))
                # Stick maintenu en TOUCH encore 2s après le cycle pour vérifier
                # qu'aucun 2e cycle ne démarre (T40 vérifie cycle_count==1)
            QTimer.singleShot(200, _send_t40)

        elif tid == "T43":
            self.log_msg.emit("  → T43 : SPEED1 + reverse_gear=True (rear intermittent)")
            # IMPORTANT : envoyer ignition ON + reverse via TCP au simulateur
            # en plus du SET Redis vers le BCM. Sans ça, bcmcan envoie CAN 0x300
            # avec ignition=0 toutes les 200ms → écrase Redis → boucle OFF→SPEED1.
            self._motor_w.queue_send({
                "ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0
            })
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Cycling 2500ms : empêche B2009 sans interférer avec le timer
                # arrière BCM (REVERSE_REAR_PERIOD=1700ms).
                # On choisit 2500ms > 1700ms pour ne jamais coïncider avec
                # les impulsions OFF du moteur arrière.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)
                self._rte_client.set_cmd("crs_wiper_op", 2)
                self._rte_client.set_cmd("reverse_gear", True)

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t43 = self._rc_gen

                def _rc_cycle_t43():
                    if self._rc_gen != _gen_t43:
                        return
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        QTimer.singleShot(100, lambda: self._rte_client and
                            self._rc_gen == _gen_t43 and
                            self._rte_client.set_cmd("rest_contact_sim", True))

                # Cycle toutes les 2500ms (> REVERSE_REAR_PERIOD=1700ms)
                for _d in range(2500, 20000, 2500):
                    QTimer.singleShot(_d, _rc_cycle_t43)

            def _t43_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
            QTimer.singleShot(500, _t43_start)

        elif tid == "T45":
            self.log_msg.emit("  → T45 : SPEED1 puis ignition=0 (blade return to rest)")
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Activer simulation rest_contact : lame EN MOUVEMENT (True)
                # Sans ça, _read_rest_contact() retourne False (GPIO indispo)
                # → BCM voit "lame déjà au repos" → ST_OFF direct → ST_PARK jamais déclenché
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                self._rte_client.set_cmd("crs_wiper_op", 2)
                def _do_ignoff():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    # Envoyer LIN OFF AVANT ignition=0 pour éviter la boucle
                    # SPEED1→OFF→SPEED1 : sans ça le LIN worker continue à émettre
                    # SPEED1 et le BCM redémarre indéfiniment après ST_OFF.
                    self._lin_w.queue_send({"cmd": "OFF"})
                    self._rte_client.set_cmd("ignition_status", 0)
                    # Sync simulateur CAN 0x300
                    self._motor_w.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                    # Simuler retour lame au repos après 1500ms :
                    # ST_PARK maintient le moteur jusqu'au contact repos (False).
                    # Sans cette transition True→False, ST_PARK attend jusqu'au
                    # timeout (5s) au lieu de détecter le repos normalement.
                    QTimer.singleShot(1500, lambda: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False))
                QTimer.singleShot(400, _do_ignoff)
            else:
                def _do_ignoff_fallback():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    self._lin_w.queue_send({"cmd": "OFF"})
                    self._motor_w.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                QTimer.singleShot(400, _do_ignoff_fallback)

        elif tid == "TC_LIN_002":
            self.log_msg.emit("  → TC_LIN_002 : geler AliveCounter LIN (anti-replay)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"test_cmd": "freeze_alive_counter"})

        elif tid == "TC_LIN_004":
            self.log_msg.emit("  → TC_LIN_004 : envoyer stickStatus invalide (0xFF)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"test_cmd": "send_invalid_stick_status"})

        elif tid == "TC_LIN_005":
            self.log_msg.emit("  → TC_LIN_005 : simuler CRS_InternalFault=1 sur LIN 0x17")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"test_cmd": "crs_internal_fault"})

        elif tid == "TC_CAN_003":
            self.log_msg.emit("  → TC_CAN_003 : geler AliveCounter CAN 0x200 (BCM→WC)")
            # Incrémenter _rc_gen pour invalider tout QTimer résiduel du test précédent
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Cycle wc_available False→True avec délai 500ms pour que le simulateur
                # réponde avec au moins 1 trame 0x201 valide avant le setup.
                # Sans ça, t_last_wiper_status périmé → B2005 immédiat → OFF.
                self._rte_client.set_cmd("wc_available",   False)
                def _can003_init():
                    if not self._rte_client:
                        return
                    self._rte_client.set_cmd("lin_op_locked",  True)
                    self._rte_client.set_cmd("wc_available",   True)
                    self._rte_client.set_cmd("wc_alive_fault", False)
                    self._rte_client.set_cmd("crs_wiper_op",   2)
                    def _can003_freeze():
                        if hasattr(test, "reset_t0"): test.reset_t0()
                        self._motor_w.queue_send({"test_cmd": "freeze_can_alive"})
                        if hasattr(test, "_stimulus_sent"):
                            test._stimulus_sent = True
                    QTimer.singleShot(600, _can003_freeze)
                QTimer.singleShot(500, _can003_init)

        elif tid == "TC_GEN_001":
            self.log_msg.emit("  → TC_GEN_001 : ignition=0 puis ON + SPEED1")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available", False)
                self._rte_client.set_cmd("ignition_status", 0)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # TCP vers bcmcan : envoyer ignition=OFF pour que CAN 0x300
            # émette ignition=0 et n'écrase pas Redis toutes les 200ms.
            self._motor_w.queue_send(
                {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
            # 1000ms : assure 5 trames CAN 0x300 avec ignition=0 avant le stimulus.
            # Sans ça, bcmcan envoie encore ignition=2 → BCM ignore SPEED1.
            def _tc_gen001_start():
                if hasattr(test, "reset_t0"): test.reset_t0()
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("ignition_status", 1)
            QTimer.singleShot(1000, _tc_gen001_start)

        elif tid == "TC_SPD_001":
            self.log_msg.emit("  → TC_SPD_001 : LIN SPEED1 continu 5 s")
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Activer simulation rest_contact pour éviter B2009
                # (sans hardware GPIO, BCM détecte STUCK CLOSED après 3s)
                # Cycle : lame EN MOUVEMENT 1600ms / AU REPOS 100ms — simule un cycle complet
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)  # lame en mouvement

                # Génération : incrémentée à chaque test pour annuler les QTimers résiduels
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen

                def _rc_rest():
                    """Passer brièvement au repos (False) puis reprendre mouvement (True)."""
                    if self._rc_gen != _gen:
                        return  # QTimer résiduel d'un test précédent — ignorer
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim", False)
                    QTimer.singleShot(100, lambda: self._rte_client and
                        self._rc_gen == _gen and
                        self._rte_client.set_cmd("rest_contact_sim", True))

                # Cycle toutes les 1700ms pendant 8s (couvre la fenêtre d'observation 5s + marge)
                for _i, _delay in enumerate(range(1700, 8500, 1700)):
                    QTimer.singleShot(_delay, _rc_rest)

            if hasattr(test, "reset_t0"):
                QTimer.singleShot(300, lambda: test.reset_t0())

        elif tid == "TC_AUTO_004":
            self.log_msg.emit("  → TC_AUTO_004 : AUTO avec rain_sensor_installed=False")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                self._rte_client.set_cmd("rain_sensor_installed", False)
                self._rte_client.set_cmd("crs_wiper_op", 4)   # WOP_AUTO
            self._lin_w.queue_send({"cmd": "AUTO"})

        elif tid == "TC_FSR_008":
            self.log_msg.emit("  → TC_FSR_008 : LIN SPEED1 puis watchdog trigger")
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                def _fsr008_trigger():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    self._rte_client.set_cmd("watchdog_test_trigger", True)
                QTimer.singleShot(400, _fsr008_trigger)
            else:
                if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_FSR_010":
            self.log_msg.emit("  → TC_FSR_010 : CRC corrompu sur 0x201 (émission autonome)")
            if self._rte_client:
                self._rte_client.set_cmd("wc_timeout_active", False)
                self._rte_client.set_cmd("wc_crc_fault",      False)
                self._rte_client.set_cmd("wc_available",      False)
                # Verrouiller crs_wiper_op contre LIN 0x16 : empêche le LIN worker
                # d'écraser avec WiperOp=OFF et de faire chuter wc_available
                self._rte_client.set_cmd("lin_op_locked", True)
            self._lin_w.queue_send({"cmd": "SPEED1"})

            def _fsr010_activate():
                if self._rte_client:
                    self._rte_client.set_cmd("wc_crc_fault",  False)
                    self._rte_client.set_cmd("wc_available",  True)
                    self._rte_client.set_cmd("crs_wiper_op",  2)
                def _fsr010_corrupt():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    self._motor_w.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 20})
                    # Autoriser _check_rte() à observer seulement maintenant
                    if hasattr(test, "_stimulus_sent"):
                        test._stimulus_sent = True
                QTimer.singleShot(600, _fsr010_corrupt)

            QTimer.singleShot(600, _fsr010_activate)

        elif tid == "TC_COM_001":
            self.log_msg.emit("  → TC_COM_001 : mesure physique baudrate BREAK LIN")
            if hasattr(test, "reset_t0"): test.reset_t0()
            # Pas de stimulus actif : crslin mesure automatiquement la duree
            # du BREAK a chaque trame LIN recue, accumule 5 mesures, puis
            # envoie lin_baud_measured via TCP. Le LIN schedule tourne deja.

        # ── TC_LIN_CS : Checksum LIN 0x16 invalide (v2 — ordre corrigé) ──
        elif tid == "TC_LIN_CS":
            self.log_msg.emit("  → TC_LIN_CS : corruption checksum AVANT commande SPEED1")
            # ── Préconditions ──────────────────────────────────────────────
            # 1. BCM en OFF, ignition=ON, crs_wiper_op=0
            # 2. lin_checksum_fault=False (effacer résidu éventuel)
            # 3. Activer corruption crslin EN PREMIER
            # 4. Puis envoyer cmd=SPEED1 → crslin émet WOP=2 AVEC checksum corrompu
            #    Le BCM reçoit la trame, détecte rx_cs != calc_cs → rejette
            #    → lin_checksum_fault=True SANS modifier crs_wiper_op
            # ── Ordre temporel ─────────────────────────────────────────────
            # t=0   : reset lin_checksum_fault + crs_wiper_op=0
            # t=200 : corrupt_lin_checksum → corruption active dans crslin
            #         _stimulus_sent=True + reset_t0() → chrono démarre
            # t=300 : LIN cmd="SPEED1" → trame corrompue envoyée au BCM
            # Durée observation : MAX_OBS_MS=1200ms < LIN_TIMEOUT(2000ms)
            if self._rte_client:
                self._rte_client.set_cmd("lin_checksum_fault", False)
                self._rte_client.set_cmd("crs_wiper_op",       0)
                self._rte_client.set_cmd("lin_timeout_active", False)

            def _tc_lin_cs_activate():
                # Étape 1 : activer corruption côté simulateur
                self._lin_w.queue_send({"test_cmd": "corrupt_lin_checksum"})
                # Étape 2 : armer l'observation dans l'objet test
                if hasattr(test, "_stimulus_sent"):
                    test._stimulus_sent = True
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Étape 3 : envoyer SPEED1 — crslin va émettre WOP=2
                # avec checksum XOR 0xFF → BCM doit rejeter
                QTimer.singleShot(100, lambda: self._lin_w.queue_send({"cmd": "SPEED1"}))

            QTimer.singleShot(200, _tc_lin_cs_activate)


        # ── T44 : REAR_WIPE isolé (op=7) ─────────────────────────────────
        elif tid == "T44":
            self.log_msg.emit("  → T44 : REAR_WIPE op=7 (une seule fois) → OFF à 2000ms")
            # Préconditions :
            #  - rear_wiper_available=True  (inscriptible — REDIS_WRITABLE_KEYS)
            #  - wc_available=False         (Cas A)
            #  - reverse_gear=False         (éviter mode intermittent T43)
            #  - ignition=ON
            # PAS de rest_contact_sim : moteur arrière sans capteur fin de course.
            # PAS de _maintain_op7 : WOP=7 est envoyé UNE SEULE FOIS via LIN.
            # Le BCM maintient REAR_WIPE par sa propre logique tant que
            # crs_wiper_op=7 est dans le RTE (mis à jour par _lin_poll_0x16).
            # À t=2000ms (> CYCLE_MS=1700ms), cmd="OFF" est envoyé → crslin
            # émet WOP=0 → _lin_poll_0x16 écrit crs_wiper_op=0 → BCM sort.
            if self._rte_client:
                self._rte_client.set_cmd("rear_wiper_available", True)
                self._rte_client.set_cmd("wc_available",         False)
                self._rte_client.set_cmd("reverse_gear",         False)
                self._rte_client.set_cmd("ignition_status",      1)
                self._rte_client.set_cmd("crs_wiper_op",         0)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t44_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus unique : LIN cmd="REAR_WIPE" → crslin émet WOP=7
                # Le BCM lit WiperOp=7 via _lin_poll_0x16 → entre en REAR_WIPE
                self._lin_w.queue_send({"cmd": "REAR_WIPE"})

                # À t=2000ms (après au moins 1 cycle de 1700ms) : relâcher le levier
                # cmd="OFF" → crslin émet WOP=0 → BCM sort de REAR_WIPE
                QTimer.singleShot(2000, lambda: self._lin_w.queue_send({"cmd": "OFF"}))

            # Délai 300ms : BCM confirme rear_wiper_available=True et ignition=ON
            QTimer.singleShot(300, _t44_start)

        # ── T50 : Cas B — wc_available=True → H-Bridge GPIO non commandé ─
        elif tid == "T50":
            self.log_msg.emit("  → T50 : Cas B wc_available=True + LIN SPEED1 → CAN 0x200, pas RL2=LOW")
            # Préconditions :
            #  - wc_available=True   (Cas B explicite — force le blocage GPIO)
            #  - lin_op_locked=True  : verrouille crs_wiper_op contre LIN 0x16
            #    → empêche B2004 (LIN timeout) de déclencher WSM OFF pendant OBS_MS
            #  - ignition=ON
            # PAS de rest_contact_sim : en Cas B le moteur avant n'est PAS
            # commandé via GPIO → pas de lame → pas de rest_contact physique.
            # La surveillance rest_contact en Cas B utilise wc_blade_position
            # (trame 0x201), pas le GPIO26.
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",  True)
                self._rte_client.set_cmd("lin_op_locked", True)
                self._rte_client.set_cmd("crs_wiper_op",  0)
                self._rte_client.set_cmd("ignition_status", 1)
            # Sync simulateur CAN : ignition=ON
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus : LIN SPEED1 → BCM entre ST_SPEED1
                # → _front_motor_run(1) détecte wc_available=True
                # → "[CAS B] commande CAN 0x200" → return sans GPIO
                # → T-CAN-WC émet CAN 0x200 vers le WC
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)
            # Délai 400ms : laisser wc_available=True se propager dans le BCM
            # avant le stimulus (éviter que _front_motor_run lise False résiduel)
            QTimer.singleShot(400, _t50_start)

        # ── T51 : Cas A — rest contact bloqué → FSR_006 ──────────────────
        elif tid == "T51":
            self.log_msg.emit("  → T51 : Cas A rest_contact bloqué EN MOUVEMENT → FSR_006")
            # Pré-conditions :
            #  - wc_available=False (Cas A obligatoire — FSR_006 Cas A surveille
            #    rest_contact GPIO, Cas B surveille wc_blade_position)
            #  - rest_contact_sim_active=True, rest_contact_sim=True (lame BLOQUÉE
            #    en position "en mouvement" — ne jamais passer à False)
            #  - ignition=ON
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",            False)
                self._rte_client.set_cmd("ignition_status",         1)
                self._rte_client.set_cmd("crs_wiper_op",            0)
                # Activer simulation rest_contact BLOQUÉE en position mouvement.
                # IMPORTANT : ne PAS programmer de retour à False ici.
                # FSR_006 doit détecter ce blocage SEUL, sans aide du test_runner.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim",        True)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            def _t51_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus : SPEED1 → moteur démarre → rest_contact reste bloqué
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)
            # Délai 300 ms : laisser le BCM confirmer wc_available=False
            QTimer.singleShot(300, _t51_start)

        elif tid == "TC_B2103":
            # ── TC_B2103 : WC Position Sensor Fault ─────────────────────────
            # Protocole :
            #   1. reset_b2103()        → guard + blade_sim=-1 côté simulateur
            #   2. wc_b2103_active=False → nettoie clé Redis résiduelle
            #   3. Délai 200 ms         → propagation du reset
            #   4. send_blade_sim(50.0) → injecte cible 50 % (lame ≈ 0 % au repos)
            #                             écart ≈ 50 % >> seuil 10 %
            #   5. reset_t0()           → chrono démarre à l'injection
            # Fenêtre PASS : 1 000–1 500 ms (délai persistance + marge Redis)
            # Nettoyage post-test : reset_b2103() + wc_b2103_active=False
            self.log_msg.emit(
                "  → TC_B2103 : reset guard B2103, puis injection blade_sim=50 % "
                "(écart ≈ 50 % > seuil 10 %)")

            # Étape 1 & 2 : remise à zéro immédiate
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2103()
            if self._rte_client:
                self._rte_client.set_cmd("wc_b2103_active", False)

            def _b2103_inject():
                # Étape 4 : injecter blade_sim après propagation du reset
                if self._sim_client and self._sim_client.is_connected():
                    ok = self._sim_client.send_blade_sim(50.0)
                    self.log_msg.emit(
                        f"  → TC_B2103 : blade_sim=50 % {'injecté' if ok else 'ECHEC INJECTION'}"
                        " — attente détection B2103…")
                else:
                    self.log_msg.emit(
                        "  → TC_B2103 : SimClient non connecté — injection blade_sim impossible")
                # Étape 5 : démarrer le chrono à partir de l'injection physique
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                test._t_inject_ms = time.time() * 1000.0

            def _b2103_cleanup():
                # Nettoyage : désactiver comparaison + Redis pour le test suivant
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2103()
                if self._rte_client:
                    self._rte_client.set_cmd("wc_b2103_active", False)
                self.log_msg.emit("  → TC_B2103 : nettoyage post-test (blade_sim=-1, Redis reset)")

            # Délai 200 ms : propagation du reset côté simulateur avant injection
            QTimer.singleShot(200, _b2103_inject)
            # Nettoyage différé : TIMEOUT + 500 ms pour ne pas polluer le test suivant
            QTimer.singleShot(
                int((TC_B2103_PositionSensorFault.TEST_TIMEOUT_S + 0.5) * 1000),
                _b2103_cleanup)

        elif tid == "T22":
            self.log_msg.emit("  → T22 : pompe FORWARD >5s → overtime FSR_005 (B2008)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                # Cycling 1700ms : empêche B2009 (False→True reset timer chaque 1700ms).
                # La pompe tourne en FORWARD. FSR_005 la coupe à 5000ms (PUMP_MAX_RUNTIME),
                # avant que le 3e cycle se complète à 5100ms → wash ne se termine pas
                # normalement → seul FSR_005 coupe la pompe → B2008 déclenché.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)
                self._rte_client.set_cmd("crs_wiper_op", 5)   # WOP_FRONT_WASH

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t22 = self._rc_gen

                def _rc_cycle_t22():
                    if self._rc_gen != _gen_t22:
                        return
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        QTimer.singleShot(100, lambda: self._rte_client and
                            self._rc_gen == _gen_t22 and
                            self._rte_client.set_cmd("rest_contact_sim", True))

                for _d in range(1700, 10000, 1700):
                    QTimer.singleShot(_d, _rc_cycle_t22)

            self._lin_w.queue_send({"cmd": "FRONT_WASH"})

        elif tid == "T21":
            self.log_msg.emit("  → T21 : FRONT_WASH — 3 cycles lame avant 5s → arrêt pompe normal")
            if self._rte_client:
                # Cycling rest_contact à 900ms pour que 3 cycles se terminent avant 5s.
                # 3 × 900ms ≈ 2.7s → wash_cycles_done=3 → pompe arrêtée AVANT FSR_005 (5s).
                # Si les cycles prenaient 1700ms (ancien), 3e cycle à ~5.1s > 5s → FSR → T22.
                # T21 vérifie l'arrêt normal (avant 5s). T22 vérifie l'arrêt FSR (>5s).
                # Cycle : True (mouvement) 800ms → False (repos) 100ms → True...
                # _track_blade_cycle compte sur front descendant True→False (count_on_rest=True).
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)

                def _t21_start():
                    if self._rte_client:
                        self._rte_client.set_cmd("crs_wiper_op", 5)
                        self._lin_w.queue_send({"cmd": "FRONT_WASH"})
                        # Génération : annule les QTimers résiduels du test précédent
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        _gen_t21 = self._rc_gen
                        # Cycle à 900ms : 3 cycles = 2.7s < PUMP_MAX_RUNTIME (5s)
                        # → le BCM détecte 3 cycles et stoppe la pompe normalement
                        def _rc_cycle_t21():
                            if self._rc_gen != _gen_t21:
                                return
                            if self._rte_client:
                                self._rte_client.set_cmd("rest_contact_sim", False)
                                QTimer.singleShot(100, lambda: self._rte_client and
                                    self._rc_gen == _gen_t21 and
                                    self._rte_client.set_cmd("rest_contact_sim", True))
                        for _d in range(900, 10000, 900):
                            QTimer.singleShot(_d, _rc_cycle_t21)

                QTimer.singleShot(200, _t21_start)
            else:
                self._lin_w.queue_send({"cmd": "FRONT_WASH"})

        # ── Tests WSM BCM — stimulus LIN, observation Redis ──────────────
        # Le stimulus passe par LIN (crslin → bus physique → BCM protocol)
        # pour tester la chaîne complète : trame 0x16 → décodage wiper_op
        # → WSM → state. Redis est utilisé UNIQUEMENT pour observer state.
        # Un SET Redis direct court-circuiterait le protocole LIN et ne
        # testerait que la logique interne WSM, pas la chaîne hardware.
        elif tid == "T30":
            self.log_msg.emit("  → T30 : LIN cmd=SPEED1 (stimulus bus physique)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"cmd": "SPEED1"})

        elif tid == "T31":
            self.log_msg.emit("  → T31 : LIN cmd=SPEED2 (stimulus bus physique)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"cmd": "SPEED2"})

        elif tid == "T32":
            self.log_msg.emit("  → T32 : LIN SPEED1 puis LIN OFF (stimulus bus physique)")
            # reset_t0 au moment de la commande OFF (pas au SPEED1)
            # pour ne mesurer que la transition SPEED1→OFF
            self._lin_w.queue_send({"cmd": "SPEED1"})
            def _send_off_lin_with_t0():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self._lin_w.queue_send({"cmd": "OFF"})
            QTimer.singleShot(300, _send_off_lin_with_t0)

        elif tid == "T33":
            self.log_msg.emit("  → Redis SET SPEED1 puis ignition=0")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                # Sync simulateur : ignition ON pour démarrer en SPEED1
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                self._rte_client.set_cmd("crs_wiper_op", 0)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("crs_wiper_op", 2))
                def _t33_ignoff():
                    test.reset_t0()
                    self._rte_client.set_cmd("ignition_status", 0)
                    # Sync simulateur : ignition OFF → CAN 0x300 avec ign=0
                    self._motor_w.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                QTimer.singleShot(600, _t33_ignoff)
            else:
                self._lin_w.queue_send({"cmd": "SPEED1"})
                QTimer.singleShot(400, lambda: self._motor_w.queue_send(
                    {"ignition_status": 0, "reverse_gear": 0, "vehicle_speed": 0}))

        elif tid == "T34":
            self.log_msg.emit("  → Redis SET AUTO + rain=10")
            if self._rte_client:
                # FIX T34/T35 : synchroniser RPiSIM en AUTO via LIN AVANT
                # l'injection Redis. Sans cela, LIN 0x16 continue à envoyer
                # WiperOp=OFF toutes les 400ms et _lin_poll_0x16() écrase
                # crs_wiper_op=AUTO → BCM repasse en OFF → TIMEOUT.
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._rte_client.set_cmd("rain_sensor_installed", True)
                self._rte_client.set_cmd("rain_intensity", 10)
                self._motor_w.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
                # Activer simulation rest_contact pour que _process_auto →
                # _track_blade_cycle fonctionne (REST_CONTACT_HARDWARE_PRESENT=True).
                # Utiliser _rc_gen pour pouvoir annuler les timers au cleanup.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t34 = self._rc_gen
                QTimer.singleShot(200, lambda: test.reset_t0())
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b, lambda b=b, g=_gen_t34: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True)))
                    QTimer.singleShot(b + 1550, lambda b=b, g=_gen_t34: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False)))
            else:
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._motor_w.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
        elif tid == "T35":
            self.log_msg.emit("  → Redis SET AUTO + rain=25")
            if self._rte_client:
                # FIX T35 : même correctif que T34.
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._rte_client.set_cmd("rain_sensor_installed", True)
                self._rte_client.set_cmd("rain_intensity", 25)
                self._motor_w.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                # Activer simulation rest_contact avec _rc_gen (annulable au cleanup)
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t35 = self._rc_gen
                QTimer.singleShot(200, lambda: test.reset_t0())
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b, lambda b=b, g=_gen_t35: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True)))
                    QTimer.singleShot(b + 1550, lambda b=b, g=_gen_t35: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False)))
            else:
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._motor_w.queue_send({"rain_intensity": 25, "sensor_status": "OK"})

        # ─── REMPLACEMENT T36 ─────────────────────────────────────────────
        elif tid == "T36":
            self.log_msg.emit("  → Redis SET crs_wiper_op=FRONT_WASH (reset cycles)")
            # FIX T36 : synchroniser RPiSIM LIN slave en FRONT_WASH AVANT Redis.
            # Sans cela, RPiSIM continue à renvoyer WiperOp=OFF dans chaque
            # frame LIN 0x16 (400ms) → _lin_poll_0x16() écrit crs_wiper_op=0
            # → WSM sort de WASH_FRONT immédiatement.
            # FIX T36 rest_contact : reset crs_wiper_op=0 d'abord pour que le BCM
            # remette _front_blade_cycles=0 via _enter_off() avant FRONT_WASH.
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)   # force OFF → reset _front_blade_cycles
            self._motor_w.queue_send({"ignition_status": 1, "vehicle_speed": 0})

            def _send_front_wash():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Activer simulation rest_contact pour T36
                # Le runner va simuler 3 cycles complets :
                #   False → True → False (cycle 1)
                #   False → True → False (cycle 2)
                #   False → True → False (cycle 3)
                # Chaque cycle = WIPE_CYCLE_DURATION = 1700ms
                if self._rte_client:
                    self._rte_client.set_cmd("rest_contact_sim_active", True)
                    self._rte_client.set_cmd("rest_contact_sim", False)  # repos initial
                    self._lin_w.queue_send({"cmd": "FRONT_WASH"})  # stimulus LIN uniquement
                    # Simuler 3 cycles via transitions temporisées
                    # Chaque cycle : 150ms → True (départ) puis 1550ms → False (retour repos)
                    # IMPORTANT : capturer b par valeur dans la lambda (pas par référence)
                    for cycle in range(3):
                        b = cycle * 1700
                        QTimer.singleShot(b + 150,  lambda b=b: self._rte_client and
                            self._rte_client.set_cmd("rest_contact_sim", True))
                        QTimer.singleShot(b + 1600, lambda b=b: self._rte_client and
                            self._rte_client.set_cmd("rest_contact_sim", False))

            QTimer.singleShot(400, _send_front_wash)

        # ─── REMPLACEMENT T37 ─────────────────────────────────────────────
        elif tid == "T37":
            self.log_msg.emit("  → T37 : LIN REAR_WASH (stimulus bus physique)")
            self._lin_w.queue_send({"cmd": "REAR_WASH"})
            self._motor_w.queue_send({"ignition_status": 1, "vehicle_speed": 0})
            # reset_t0 décalé de 200ms : absorbe la latence LIN→BCM pour que
            # la mesure démarre quand le BCM est réellement en WASH_REAR
            if hasattr(test, "reset_t0"):
                QTimer.singleShot(200, lambda: test.reset_t0())

        elif tid == "T38":
            self.log_msg.emit("  → T38 : LIN SPEED1 + injection surcourant")
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                def _t38_inject():
                    if hasattr(test, "reset_t0"): test.reset_t0()  # chrono démarre à l'injection
                    self._rte_client.set_cmd("motor_current_a", 0.95)
                QTimer.singleShot(400, _t38_inject)
            else:
                QTimer.singleShot(400, lambda: self._inject_overcurrent(6))

        elif tid == "T39":
            self.log_msg.emit("  → T39 : LIN SPEED1 puis stop_lin_tx (stimulus bus physique)")
            # Attendre 500ms pour que le BCM soit bien en SPEED1 avant de couper le LIN.
            # reset_t0 se déclenche au moment du stop_lin_tx : mesure = temps entre
            # coupure LIN et retour en OFF du BCM = délai détection timeout FSR_001.
            self._lin_w.queue_send({"cmd": "SPEED1"})
            def _t39_stop():
                if hasattr(test, "reset_t0"): test.reset_t0()
                self._lin_w.queue_send({"test_cmd": "stop_lin_tx"})
            QTimer.singleShot(500, _t39_stop)

    def _inject_overcurrent(self, remaining: int):
        """Injecte motor_current=0.95A dans motor_received toutes les 50 ms."""
        if remaining <= 0 or not self._running:
            return
        self._motor_w.motor_received.emit(
            {"state": "SPEED1", "motor_current": 0.95,
             "front_motor_on": True, "fault": False})
        QTimer.singleShot(50, lambda: self._inject_overcurrent(remaining - 1))

    def _post_test(self, test: BaseTest):
        """Restaure l'état nominal après un test actif."""
        tid = test.ID
        if tid == "T10":
            self.log_msg.emit("  → start_lin_tx")
            self._lin_w.queue_send({"test_cmd": "start_lin_tx"})
            if self._rte_client:
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("lin_timeout_active", False))
        elif tid == "T11":
            self.log_msg.emit("  → start_can_tx")
            self._motor_w.queue_send({"test_cmd": "start_can_tx"})
            if self._rte_client:
                # Remettre wc_available=False (CAS A) après le test
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_available", False))
                QTimer.singleShot(400, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_timeout_active", False))
                QTimer.singleShot(600, lambda: self._rte_client and (
                    self._rte_client.set_cmd("ignition_status", 1) or
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                ))
        elif tid == "T21":
            # Invalider génération pour stopper QTimers cycling résiduels
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", True)  # maintenir True
            self._lin_w.queue_send({"cmd": "OFF"})
            # Attendre que BCM soit en OFF (max 4s) avant de désactiver sim.
            # Cela permet aux cycles restants de compléter sans déclencher B2009.
            if self._rte_client:
                _t21_gen = getattr(self, "_rc_gen", 0)
                _t21_attempts = [0]
                def _t21_wait_off():
                    if not self._rte_client:
                        return
                    state = self._rte_client.get("state")
                    _t21_attempts[0] += 1
                    if state == "OFF" or _t21_attempts[0] >= 20:  # max 20×200ms=4s
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        self._rte_client.set_cmd("crs_wiper_op", 0)
                    else:
                        QTimer.singleShot(200, _t21_wait_off)
                QTimer.singleShot(200, _t21_wait_off)
        elif tid == "T22":
            # Même logique que T21 : garder sim=True jusqu'à state=OFF
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", True)  # maintenir True
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                _t22_attempts = [0]
                def _t22_wait_off():
                    if not self._rte_client:
                        return
                    state = self._rte_client.get("state")
                    _t22_attempts[0] += 1
                    if state == "OFF" or _t22_attempts[0] >= 20:
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        self._rte_client.set_cmd("crs_wiper_op", 0)
                    else:
                        QTimer.singleShot(200, _t22_wait_off)
                QTimer.singleShot(200, _t22_wait_off)
        elif tid == "T40":
            # Cleanup T40 : même séquence que l'ancien T20
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            self._lin_w.queue_send({"cmd": "OFF"})
        elif tid in ("T30", "T31", "T32", "T34", "T35", "T36", "T37", "T38"):
            # Remettre le BCM en OFF
            # T30/T31/T32 : LIN OFF obligatoire pour que crslin repasse en OFF
            # (stimulus était LIN — crslin a son état interne à réinitialiser)
            if tid in ("T30", "T31", "T32"):
                self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("rain_intensity", 0)
                if tid in ("T34", "T35"):
                    # Invalider _rc_gen : stoppe les QTimers cycling rest_contact
                    # résiduels qui continuaient à toggler après la fin du test.
                    self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                    self._rte_client.set_cmd("rain_sensor_installed", False)
                    # FIX T34/T35 cleanup : remettre rain=0 dans RPiSIM pour que
                    # CAN 0x301 ne continue pas à diffuser rain>0 aux tests suivants.
                    self._motor_w.queue_send({"rain_intensity": 0, "sensor_status": "OK"})
                    # Désactiver simulation rest_contact activée dans _pre_test T34/T35
                    self._rte_client.set_cmd("rest_contact_sim_active", False)
                    self._rte_client.set_cmd("rest_contact_sim",        False)
                    self._lin_w.queue_send({"cmd": "OFF"})
                if tid == "T37":
                    # FIX T37 cleanup : arrêter pompe BACKWARD + simulateur en OFF
                    self._lin_w.queue_send({"cmd": "OFF"})
                if tid == "T36":
                    # FIX T36 cleanup : remettre simulateur LIN en OFF après FRONT_WASH.
                    # Désactiver simulation rest_contact → retour lecture GPIO hardware.
                    self._rte_client.set_cmd("rest_contact_sim_active", False)
                    self._rte_client.set_cmd("rest_contact_sim",        False)
                    self._lin_w.queue_send({"cmd": "OFF"})
                if tid == "T38":
                    self._rte_client.set_cmd("motor_current_a", 0.0)
                    self._rte_client.set_cmd("wc_timeout_active", False)
                    # FIX T38 cleanup : remettre simulateur en OFF
                    # pour que LIN 0x16 cesse d'envoyer WiperOp=SPEED1
                    self._lin_w.queue_send({"cmd": "OFF"})
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("bcm_error_reset", True)

            # Toujours sync simulateur : ignition ON pour éviter boucle OFF→SPEED1
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "T33":
            if self._rte_client:
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # Toujours sync simulateur (pas seulement en fallback)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "OFF"})
            # Attendre 800ms que le BCM prenne ignition=1 avant le prochain test
            QTimer.singleShot(800, lambda: None)
        elif tid == "T39":
            self.log_msg.emit("  → start_lin_tx + OFF")
            self._lin_w.queue_send({"test_cmd": "start_lin_tx"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
            else:
                self._lin_w.queue_send({"cmd": "OFF"})
        elif tid == "T43":
            # Cleanup T43 : invalider génération + garder sim=True jusqu'à state=OFF
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("reverse_gear", False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", True)  # maintenir True
                _t43_attempts = [0]
                def _t43_wait_off():
                    if not self._rte_client:
                        return
                    state = self._rte_client.get("state")
                    _t43_attempts[0] += 1
                    if state == "OFF" or _t43_attempts[0] >= 15:
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("rest_contact_sim", False)
                    else:
                        QTimer.singleShot(200, _t43_wait_off)
                QTimer.singleShot(200, _t43_wait_off)
        elif tid == "T45":
            # Cleanup T45 : rétablir ignition + OFF + désactiver sim rest_contact
            if self._rte_client:
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rte_client.set_cmd("rest_contact_sim_active", False)
            # Toujours sync simulateur
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "OFF"})
            QTimer.singleShot(800, lambda: None)   # laisser BCM reprendre
        elif tid == "TC_LIN_002":
            # Cleanup : reprendre incrémentation AliveCounter normale
            self._lin_w.queue_send({"test_cmd": "restore_alive_counter"})
            if self._rte_client:
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("lin_alive_fault", False))
        elif tid == "TC_LIN_004":
            # Cleanup : restaurer stickStatus normal
            self._lin_w.queue_send({"test_cmd": "restore_lin_normal"})
        elif tid == "TC_LIN_005":
            # Cleanup : annuler simulation CRS_InternalFault
            self._lin_w.queue_send({"test_cmd": "restore_lin_normal"})
            if self._rte_client:
                self._rte_client.set_cmd("bcm_error_reset", True)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("crs_fault_active", False))
        elif tid == "TC_CAN_003":
            self._motor_w.queue_send({"test_cmd": "restore_can_alive"})
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                # Désactiver wc_available EN PREMIER, puis déverrouiller LIN.
                self._rte_client.set_cmd("wc_available",   False)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("lin_op_locked",  False)
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_alive_fault", False))

        elif tid in ("TC_GEN_001", "TC_SPD_001", "TC_AUTO_004", "TC_FSR_008"):
            # Cleanup commun : moteur OFF + ignition ON simulateur
            # TC_GEN_001 : délai 300ms avant crs_wiper_op=0 pour laisser
            # _check_rte() (poll 200ms) détecter state=SPEED1 avant l'arrêt.
            def _do_cleanup():
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("ignition_status", 1)
                    if tid == "TC_AUTO_004":
                        self._rte_client.set_cmd("rain_sensor_installed", False)
                    if tid == "TC_FSR_008":
                        self._rte_client.set_cmd("watchdog_test_trigger", False)
                        self._rte_client.set_cmd("bcm_error_reset", True)
                    if tid == "TC_SPD_001":
                        # Désactiver simulation rest_contact + invalider génération
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("bcm_error_reset", True)
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                self._lin_w.queue_send({"cmd": "OFF"})
            delay_ms = 300 if tid == "TC_GEN_001" else 0
            if delay_ms:
                QTimer.singleShot(delay_ms, _do_cleanup)
            else:
                _do_cleanup()

        elif tid == "TC_FSR_010":
            self._lin_w.queue_send({"cmd": "OFF"})
            # Incrémenter génération pour invalider ce QTimer si TC_CAN_003 démarre
            # avant les 2000ms — sans ça, _fsr010_cleanup pose lin_op_locked=False
            # pendant que TC_CAN_003 est déjà actif avec lin_op_locked=True.
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            _gen_fsr010 = self._rc_gen
            def _fsr010_cleanup(g=_gen_fsr010):
                if self._rc_gen != g:
                    return   # test suivant déjà démarré, ne pas interférer
                self._motor_w.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 0})
                if self._rte_client:
                    self._rte_client.set_cmd("wc_available", False)
                    self._rte_client.set_cmd("wc_crc_fault", False)
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("lin_op_locked", False)
                self._lin_w.queue_send({"cmd": "OFF"})
            QTimer.singleShot(3500, _fsr010_cleanup)  # 3500ms : garantit arrêt complet trames CRC corrompues avant T50

        elif tid == "TC_COM_001":
            pass   # mesure passive BREAK — rien à nettoyer

        # ── TC_LIN_CS cleanup (v2) ────────────────────────────────────────
        elif tid == "TC_LIN_CS":
            # Ordre impératif :
            # 1. Restaurer checksum normal EN PREMIER (sinon la trame OFF
            #    suivante sera aussi corrompue et le BCM ignorera cmd=OFF)
            self._lin_w.queue_send({"test_cmd": "restore_lin_checksum"})
            # 2. Envoyer OFF après restauration (100 ms pour que crslin
            #    traite restore_lin_checksum avant la prochaine trame)
            QTimer.singleShot(100, lambda: self._lin_w.queue_send({"cmd": "OFF"}))
            # 3. Effacer les flags Redis
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op",       0)
                self._rte_client.set_cmd("lin_timeout_active", False)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("lin_checksum_fault", False))


        # ── T44 cleanup ───────────────────────────────────────────────────
        elif tid == "T44":
            # cmd="OFF" a déjà été envoyé par le QTimer à t=2000ms dans _pre_test.
            # Le BCM est déjà sorti de REAR_WIPE via _process_rear_wipe.
            # Cleanup Redis après 500ms pour laisser le LIN se stabiliser.
            def _t44_cleanup():
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op",         0)
                    self._rte_client.set_cmd("reverse_gear",         False)
                    self._rte_client.set_cmd("ignition_status",      1)
                    self._rte_client.set_cmd("rear_wiper_available", True)
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            QTimer.singleShot(500, _t44_cleanup)

        # ── T50 cleanup ───────────────────────────────────────────────────
        elif tid == "T50":
            # Remettre en OFF
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op",  0)
                self._rte_client.set_cmd("ignition_status", 1)
                # Libérer lin_op_locked (verrouillé pendant l'observation)
                self._rte_client.set_cmd("lin_op_locked", False)
                # Remettre wc_available=False (Cas A) pour T51
                self._rte_client.set_cmd("wc_available",            False)
                # Reset B2101 côté simulateur WC (bcmcan _t_last_0x200=now)
                # → lève B2101 immédiatement sans attendre les 2s de timeout
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2101()
                # Reset des flags Redis résiduels après 400ms
                QTimer.singleShot(400, lambda: self._rte_client and (
                    self._rte_client.set_cmd("wc_timeout_active",  False) or
                    self._rte_client.set_cmd("wc_b2103_active",    False) or
                    self._rte_client.set_cmd("lin_timeout_active", False)
                ))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        # ── T51 cleanup ───────────────────────────────────────────────────
        elif tid == "T51":
            # 1. Débloquer le rest_contact (repassera à False = repos)
            # 2. Réinitialiser BCM via bcm_error_reset (était en ERROR)
            # 3. Désactiver simulation rest_contact
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op",            0)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                # Petit délai avant de désactiver la sim : laisser BCM voir
                # rest_contact=False (lame revenue au repos) et sortir de ERROR
                def _t51_cleanup():
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("bcm_error_reset",         True)
                        self._rte_client.set_cmd("ignition_status",         1)
                QTimer.singleShot(300, _t51_cleanup)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

    def _record(self, result: TestResult):
        if not self._running:
            return
        self._post_test(self._current)
        self._results.append(result)
        self.test_result.emit(result)
        done = self._total - len(self._queue)
        self.progress.emit(done, self._total)
        icon = "✅" if result.status == "PASS" else ("❌" if result.status == "FAIL" else "⚠")
        self.log_msg.emit(
            f"  {icon} {result.status}  mesure={result.measured}  "
            f"limite={result.limit}")
        self._current = None
        QTimer.singleShot(100, self._start_next)

    # ─── Slots (DirectConnection → thread GUI) ────────────────────────
    def _on_can(self, ev: dict):
        if self._current:
            try:
                r = self._current.on_can_frame(ev)
                if r:
                    self._record(r)
            except Exception as e:
                self.log_msg.emit(f"  ⚠ Erreur on_can_frame [{self._current.ID}]: {e}")

    def _on_lin(self, ev: dict):
        if self._current:
            try:
                r = self._current.on_lin_frame(ev)
                if r:
                    self._record(r)
            except Exception as e:
                self.log_msg.emit(f"  ⚠ Erreur on_lin_frame [{self._current.ID}]: {e}")

    def _on_motor(self, data: dict):
        if self._current:
            try:
                r = self._current.on_motor_data(data)
                if r:
                    self._record(r)
            except Exception as e:
                self.log_msg.emit(f"  ⚠ Erreur on_motor_data [{self._current.ID}]: {e}")

    # ─── Timer 200 ms : vérification timeout + Redis GET ─────────────
    def _tick(self):
        if not self._running or not self._current:
            return
        try:
            # 1. Timeout global
            r = self._current.check_timeout()
            if r:
                self._record(r)
                return
            # 2. Redis GET — pour tous les tests BaseBCMTest (T10,T11,T21,T30-T39)
            if isinstance(self._current, BaseBCMTest):
                r = self._current._check_rte()
                if r:
                    self._record(r)
        except Exception as e:
            self.log_msg.emit(f"  ⚠ Erreur _tick [{self._current.ID if self._current else '?'}]: {e}")