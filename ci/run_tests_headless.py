#!/usr/bin/env python3
"""
run_tests_headless.py  —  WipeWash BCM Test Runner (mode headless / Jenkins Windows)
=====================================================================================
Exécute la campagne de tests sans GUI PySide6, en mode ligne de commande.
Conçu pour être appelé par Jenkins sous Windows.

Ce fichier doit être placé dans :
    <depot>/ci/run_tests_headless.py

Les modules de la plateforme (test_cases.py, rte_client.py, etc.) sont dans :
    <depot>/platform/

Usage :
    python ci\\run_tests_headless.py [options]

Options :
    --bcm-host   IP ou hostname du RPiBCM   (défaut : auto-découverte)
    --sim-host   IP ou hostname du RPiSIM   (défaut : auto-découverte)
    --redis-host IP du serveur Redis        (défaut : même que bcm-host)
    --redis-port Port Redis                 (défaut : 6379)
    --tests      IDs séparés par virgule (ex: T30,T31,T32)
                 Si absent → tous les tests ALL_TESTS
    --output     Chemin rapport HTML        (défaut : report_<ts>.html)
    --json       Chemin rapport JSON        (défaut : results_<ts>.json)
    --junit      Chemin rapport JUnit XML   (défaut : junit_<ts>.xml)
    --timeout    Timeout global en secondes (défaut : 600)
    --bench-id   Identifiant banc           (défaut : WipeWash-Bench-CI)
    --operator   Nom opérateur/job Jenkins  (défaut : jenkins)
    --fail-fast  Arrêter dès le premier FAIL

Exit codes :
    0  → tous les tests PASS
    1  → au moins un FAIL ou TIMEOUT
    2  → erreur de connexion / infrastructure
    3  → timeout global dépassé
"""

import argparse
import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import datetime
import json
import os
import sys
import time
import threading
import signal

# ─────────────────────────────────────────────────────────────────────────────
# Résolution du chemin vers le dossier platform/
# Structure attendue :
#   <depot>/
#     ci/run_tests_headless.py      ← ce fichier
#     platform/test_cases.py
#     platform/rte_client.py
#     platform/sim_client.py
#     platform/network.py
#     platform/report_generator.py
# ─────────────────────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
_DEPOT_ROOT   = os.path.dirname(_HERE)
_PLATFORM_DIR = os.path.join(_DEPOT_ROOT, "platform")

if not os.path.isdir(_PLATFORM_DIR):
    # Fallback : chercher "platform - Copie" (nom original du zip)
    _PLATFORM_DIR = os.path.join(_DEPOT_ROOT, "platform - Copie")

if not os.path.isdir(_PLATFORM_DIR):
    print(
        f"[ERREUR] Dossier platform introuvable.\n"
        f"  Cherché dans : {_PLATFORM_DIR}\n"
        f"  Vérifiez la structure du dépôt (voir README_JENKINS.md).",
        file=sys.stderr,
    )
    sys.exit(2)

sys.path.insert(0, _PLATFORM_DIR)

# ─── Imports plateforme ───────────────────────────────────────────────────────
try:
    from test_cases       import ALL_TESTS, BaseTest, BaseBCMTest, TestResult
    from rte_client       import RTEClient
    from sim_client       import SimClient
    from network          import auto_discover_all
    from report_generator import ReportGenerator
except ImportError as e:
    print(f"[ERREUR] Import plateforme échoué : {e}", file=sys.stderr)
    print(f"  PLATFORM_DIR = {_PLATFORM_DIR}", file=sys.stderr)
    sys.exit(2)


# ═════════════════════════════════════════════════════════════════════════════
#  STUBS TCP HEADLESS  —  remplacent les Workers PySide6 par des threads purs
# ═════════════════════════════════════════════════════════════════════════════

class _FakeSignal:
    """Émule un Signal Qt (connect / emit) sans dépendance à PySide6."""
    def __init__(self):
        self._cbs = []

    def connect(self, cb, *args, **kwargs):
        self._cbs.append(cb)

    def emit(self, *args):
        for cb in self._cbs:
            try:
                cb(*args)
            except Exception as exc:
                print(f"[FakeSignal] callback error: {exc}", file=sys.stderr)


class _TCPReader(threading.Thread):
    """
    Thread générique : connexion TCP, lecture ligne à ligne (JSON\\n),
    dispatch vers un _FakeSignal.
    Reconnexion automatique si la connexion est perdue.
    """
    def __init__(self, host: str, port: int, signal: _FakeSignal, name: str):
        super().__init__(name=name, daemon=True)
        self._host   = host
        self._port   = port
        self._signal = signal
        self._stop   = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        import socket
        buf = b""
        while not self._stop.is_set():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect((self._host, self._port))
                buf = b""
                while not self._stop.is_set():
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            ev = json.loads(line.decode("utf-8", errors="replace"))
                            self._signal.emit(ev)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                s.close()
            except Exception:
                pass
            # Attente avant reconnexion (évite la boucle rapide si hôte absent)
            self._stop.wait(timeout=1.5)


class HeadlessCANWorker:
    """Remplace CANWorker (PySide6) — écoute les frames CAN depuis RPiSIM:5557."""
    PORT = 5557

    def __init__(self, host: str):
        # host = RPiSIM (bcm_tcp_can.py sert le port 5557 sur le simulateur)
        self.can_received   = _FakeSignal()
        self.status_changed = _FakeSignal()
        self._reader = _TCPReader(host, self.PORT, self.can_received, "CAN-RX")

    def start(self):
        self._reader.start()

    def queue_send(self, obj: dict):
        pass  # TX CAN non utilisé par les tests

    def stop(self):
        self._reader.stop()


class HeadlessLINWorker:
    """Remplace LINWorker (PySide6) — bidirectionnel RPiSIM:5555."""
    PORT = 5555

    def __init__(self, host: str):
        self.lin_received   = _FakeSignal()
        self.status_changed = _FakeSignal()
        self._host     = host
        self._reader   = _TCPReader(host, self.PORT, self.lin_received, "LIN-RX")
        self._tx_queue: list = []
        self._tx_lock  = threading.Lock()
        self._stop_ev  = threading.Event()

    def start(self):
        self._reader.start()
        threading.Thread(target=self._tx_loop, name="LIN-TX", daemon=True).start()

    def _tx_loop(self):
        import socket
        while not self._stop_ev.is_set():
            with self._tx_lock:
                cmds = list(self._tx_queue)
                self._tx_queue.clear()
            for cmd in cmds:
                try:
                    with socket.create_connection(
                            (self._host, self.PORT), timeout=2.0) as s:
                        s.sendall((json.dumps(cmd) + "\n").encode())
                except Exception:
                    pass
            self._stop_ev.wait(timeout=0.05)

    def queue_send(self, obj: dict):
        with self._tx_lock:
            self._tx_queue.append(obj)

    def set_wiper_op(self, op: int):
        self.queue_send({"cmd": ["OFF","TOUCH","SPEED1","SPEED2","AUTO",
                                 "FRONT_WASH","REAR_WASH","REAR_WIPE"][op]
                                if 0 <= op <= 7 else "OFF"})

    def stop(self):
        self._stop_ev.set()
        self._reader.stop()


class HeadlessMotorWorker:
    """
    Remplace MotorVehicleWorker (PySide6).
    RX depuis RPiBCM:5000 (état moteur), TX vers RPiSIM:5002 (commandes véhicule).
    """
    PORT_RX = 5000   # RPiBCM bcm_tcp_broadcast
    PORT_TX = 5002   # RPiSIM bcmcan (PORT_BCMCAN)

    def __init__(self, bcm_host: str, sim_host: str):
        self.motor_received = _FakeSignal()
        self.status_changed = _FakeSignal()
        self._bcm_host = bcm_host
        self._sim_host = sim_host
        self._tx_queue: list = []
        self._tx_lock  = threading.Lock()
        self._stop_ev  = threading.Event()
        self._reader   = _TCPReader(bcm_host, self.PORT_RX, self.motor_received, "Motor-RX")

    def start(self):
        self._reader.start()
        # Thread TX (commandes → RPiSIM)
        threading.Thread(target=self._tx_loop, name="Motor-TX", daemon=True).start()

    def _tx_loop(self):
        import socket
        while not self._stop_ev.is_set():
            with self._tx_lock:
                cmds = list(self._tx_queue)
                self._tx_queue.clear()
            for cmd in cmds:
                try:
                    with socket.create_connection(
                            (self._sim_host, self.PORT_TX), timeout=2.0) as s:
                        s.sendall((json.dumps(cmd) + "\n").encode())
                except Exception:
                    pass
            self._stop_ev.wait(timeout=0.05)

    def queue_send(self, obj: dict):
        with self._tx_lock:
            self._tx_queue.append(obj)

    def set_wiper_op(self, op: int):
        self.queue_send({"wiper_op": op})

    def stop(self):
        self._stop_ev.set()
        self._reader.stop()


class HeadlessPumpSignal:
    """Remplace PumpDataClient (PySide6) — écoute données pompe RPiBCM:5556."""
    PORT = 5556

    def __init__(self, host: str):
        self.data_received = _FakeSignal()
        self._reader = _TCPReader(host, self.PORT, self.data_received, "Pump-RX")

    def start(self):
        self._reader.start()

    def stop(self):
        self._reader.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  RUNNER HEADLESS
#  Reproduit fidèlement la logique de TestRunner (test_runner.py)
#  sans aucune dépendance Qt.
# ═════════════════════════════════════════════════════════════════════════════

class HeadlessTestRunner:
    """
    Exécute les tests séquentiellement dans un thread pur.
    Signaux Qt → threading.Event + callbacks directs.
    """

    TICK_INTERVAL_S   = 0.2    # 200 ms, identique au QTimer de TestRunner
    SETTLE_AFTER_INIT = 2.0    # secondes d'attente après connexion workers

    def __init__(self,
                 can_worker, lin_worker, motor_worker,
                 pump_signal  = None,
                 rte_client   = None,
                 sim_client   = None,
                 fail_fast    = False):

        self._can_w      = can_worker
        self._lin_w      = lin_worker
        self._motor_w    = motor_worker
        self._rte_client = rte_client
        self._sim_client = sim_client
        self._fail_fast  = fail_fast

        self._results:   list = []
        self._log_lines: list = []

        self._current: BaseTest | None = None
        self._lock     = threading.Lock()
        self._done_ev  = threading.Event()

        # Câblage des callbacks
        can_worker.can_received.connect(self._on_can)
        lin_worker.lin_received.connect(self._on_lin)
        motor_worker.motor_received.connect(self._on_motor)
        if pump_signal is not None:
            pump_signal.data_received.connect(self._on_motor)

    # ── Callbacks workers ─────────────────────────────────────────────────
    def _on_can(self, ev):
        with self._lock:
            if not self._current:
                return
            res = self._current.on_can_frame(ev)
        if res:
            self._finish(res)

    def _on_lin(self, ev):
        with self._lock:
            if not self._current:
                return
            res = self._current.on_lin_frame(ev)
        if res:
            self._finish(res)

    def _on_motor(self, data):
        with self._lock:
            if not self._current:
                return
            res = self._current.on_motor_data(data)
        if res:
            self._finish(res)

    # ── Gestion résultat ─────────────────────────────────────────────────
    def _finish(self, result: TestResult):
        """Appelé dès qu'un test produit un résultat (depuis n'importe quel thread)."""
        with self._lock:
            if self._current is None:
                return        # déjà traité (double callback rare)
            self._current = None
        self._results.append(result)
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "TIMEOUT": "[TIMEOUT]"}.get(result.status, "?")
        msg  = f"  {icon} [{result.test_id}] {result.name:<50} → {result.status}"
        if result.details:
            msg += f"  ({result.details})"
        self._log(msg)
        self._done_ev.set()

    # ── Log horodaté ─────────────────────────────────────────────────────
    def _log(self, msg: str):
        ts   = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line.encode("utf-8", errors="replace").decode("utf-8"), flush=True)
        self._log_lines.append(line)

    # ── Délai inter-test (reproduit test_runner.py exactement) ───────────
    @staticmethod
    def _inter_delay_ms(tid: str, last_tid: str) -> int:
        NEEDS_DELAY = {
            "T30","T31","T32","T33","T34","T35","T36","T37","T38","T38b","T38c","T39",
            "T43","T45","TC_LIN_002","TC_LIN_004","TC_LIN_005",
            "TC_CAN_003","TC_GEN_001","TC_SPD_001","TC_AUTO_004",
            "TC_FSR_008","TC_FSR_010","TC_COM_001","TC_B2103",
            "LIN_INVALID_CMD_001","T_RAIN_AUTO_SENSOR_ERROR","T_B2009_CAN",
        }
        if tid not in NEEDS_DELAY:
            return 0
        if last_tid == "T22":
            return 8000
        if last_tid == "TC_FSR_010":
            return 3000
        if last_tid in ("T40", "T21", "T36", "T37", "T43", "T45"):
            return 2500
        return 300

    # ── Stimuli avant test (port headless de test_runner._pre_test) ──────
    def _pre_test(self, test: BaseTest):
        """Port headless — QTimer.singleShot → threading.Thread + time.sleep."""
        tid = test.ID
        rc  = self._rte_client
        lw  = self._lin_w
        mw  = self._motor_w
        # ── Tests réseau ──────────────────────────────────────────────────
        if tid == "T10":
            self._log("  → stop_lin_tx")
            lw.queue_send({"test_cmd": "stop_lin_tx"})
        elif tid == "T11":
            self._log("  → stop_can_tx")
            # wc_available=True obligatoire : sans CAS B actif, le BCM n'observe
            # pas le timeout CAN 0x201 et ne lève jamais wc_timeout_active=True.
            # On active wc_available, on attend 300ms que bcmcan envoie au moins
            # une trame 0x201 pour initialiser t_last_wiper_status, puis on coupe.
            if rc:
                rc.set_cmd("wc_available", True)
            threading.Thread(target=lambda: (time.sleep(300/1000.0), mw.queue_send({"test_cmd": "stop_can_tx"})), daemon=True).start()
        elif tid == "T40":
            self._log("  → T40 : TOUCH — 1 cycle puis retour OFF (no repeat)")
            # Même logique que l'ancien T20, mais T40 vérifie en plus :
            # 1) state=OFF après le cycle  2) cycle_count==1 (pas de répétition)
            if rc:
                rc.set_cmd("rest_contact_sim_active", False)
                rc.set_cmd("rest_contact_sim",        False)
                rc.set_cmd("crs_wiper_op", 0)
            lw.queue_send({"cmd": "TOUCH"})
            def _send_t40():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                if rc:
                    rc.set_cmd("rest_contact_sim_active", True)
                    rc.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                    rc.set_cmd("crs_wiper_op", 1)          # WOP_TOUCH
                # Retour repos à 1500ms → fin du 1er (et unique) cycle
                threading.Thread(target=lambda: (time.sleep(1500/1000.0), rc and rc.set_cmd("rest_contact_sim", False)), daemon=True).start()
                # Stick maintenu en TOUCH encore 2s après le cycle pour vérifier
                # qu'aucun 2e cycle ne démarre (T40 vérifie cycle_count==1)
            threading.Thread(target=lambda: (time.sleep(200/1000.0), _send_t40), daemon=True).start()

        elif tid == "T43":
            self._log("  → T43 : SPEED1 + reverse_gear=True (rear intermittent)")
            # IMPORTANT : envoyer ignition ON + reverse via TCP au simulateur
            # en plus du SET Redis vers le BCM. Sans ça, bcmcan envoie CAN 0x300
            # avec ignition=0 toutes les 200ms → écrase Redis → boucle OFF→SPEED1.
            mw.queue_send({
                "ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0
            })
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                # Cycling 2500ms : empêche B2009 sans interférer avec le timer
                # arrière BCM (REVERSE_REAR_PERIOD=1700ms).
                # On choisit 2500ms > 1700ms pour ne jamais coïncider avec
                # les impulsions OFF du moteur arrière.
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)
                rc.set_cmd("crs_wiper_op", 2)
                rc.set_cmd("reverse_gear", True)

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t43 = self._rc_gen

                def _rc_cycle_t43():
                    if self._rc_gen != _gen_t43:
                        return
                    if rc:
                        rc.set_cmd("rest_contact_sim", False)
                        threading.Thread(target=lambda: (time.sleep(100/1000.0), rc and self._rc_gen == _gen_t43 and rc.set_cmd("rest_contact_sim", True)), daemon=True).start()

                # Cycle toutes les 2500ms (> REVERSE_REAR_PERIOD=1700ms)
                for _d in range(2500, 20000, 2500):
                    threading.Thread(target=lambda: (time.sleep(_d/1000.0), _rc_cycle_t43), daemon=True).start()

            def _t43_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
            threading.Thread(target=lambda: (time.sleep(500/1000.0), _t43_start), daemon=True).start()

        elif tid == "T45":
            self._log("  → T45 : SPEED1 puis ignition=0 (blade return to rest)")
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                # Activer simulation rest_contact : lame EN MOUVEMENT (True)
                # Sans ça, _read_rest_contact() retourne False (GPIO indispo)
                # → BCM voit "lame déjà au repos" → ST_OFF direct → ST_PARK jamais déclenché
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                rc.set_cmd("crs_wiper_op", 2)
                def _do_ignoff():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    # Envoyer LIN OFF AVANT ignition=0 pour éviter la boucle
                    # SPEED1→OFF→SPEED1 : sans ça le LIN worker continue à émettre
                    # SPEED1 et le BCM redémarre indéfiniment après ST_OFF.
                    lw.queue_send({"cmd": "OFF"})
                    rc.set_cmd("ignition_status", 0)
                    # Sync simulateur CAN 0x300
                    mw.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                    # Simuler retour lame au repos après 1500ms :
                    # ST_PARK maintient le moteur jusqu'au contact repos (False).
                    # Sans cette transition True→False, ST_PARK attend jusqu'au
                    # timeout (5s) au lieu de détecter le repos normalement.
                    threading.Thread(target=lambda: (time.sleep(1500/1000.0), rc and rc.set_cmd("rest_contact_sim", False)), daemon=True).start()
                threading.Thread(target=lambda: (time.sleep(400/1000.0), _do_ignoff), daemon=True).start()
            else:
                def _do_ignoff_fallback():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    lw.queue_send({"cmd": "OFF"})
                    mw.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                threading.Thread(target=lambda: (time.sleep(400/1000.0), _do_ignoff_fallback), daemon=True).start()

        elif tid == "TC_LIN_002":
            self._log("  → TC_LIN_002 : geler AliveCounter LIN (anti-replay)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "freeze_alive_counter"})

        elif tid == "TC_LIN_004":
            self._log("  → TC_LIN_004 : envoyer stickStatus invalide (0xFF)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "send_invalid_stick_status"})

        elif tid == "TC_LIN_005":
            self._log("  → TC_LIN_005 : simuler CRS_InternalFault=1 sur LIN 0x17")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "crs_internal_fault"})

        elif tid == "TC_CAN_003":
            self._log("  → TC_CAN_003 : geler AliveCounter CAN 0x200 (BCM→WC)")
            # Incrémenter _rc_gen pour invalider tout QTimer résiduel du test précédent
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                # Cycle wc_available False→True avec délai 500ms pour que le simulateur
                # réponde avec au moins 1 trame 0x201 valide avant le setup.
                # Sans ça, t_last_wiper_status périmé → B2005 immédiat → OFF.
                rc.set_cmd("wc_available",   False)
                # FIX TC_CAN_003 : s'assurer que alive_tx_frozen est remis à False
                # avant le test (résidu éventuel d'un test précédent interrompu)
                rc.set_cmd("alive_tx_frozen", False)
                def _can003_init():
                    if not rc:
                        return
                    rc.set_cmd("lin_op_locked",  True)
                    rc.set_cmd("wc_available",   True)
                    rc.set_cmd("wc_alive_fault", False)
                    rc.set_cmd("crs_wiper_op",   2)
                    def _can003_freeze():
                        if hasattr(test, "reset_t0"): test.reset_t0()
                        # FIX TC_CAN_003 : geler l'AliveCounter_TX côté BCM EN PREMIER
                        # (bcm_protocol._build_wiper_command arrête d'incrémenter wc_can_alive_tx)
                        # PUIS envoyer freeze_can_alive au simulateur (activation de la détection WC).
                        # Sans ce fix, le BCM continuait d'incrémenter normalement → WC simulé
                        # ne voyait jamais deux trames consécutives avec le même counter → timeout.
                        if rc:
                            rc.set_cmd("alive_tx_frozen", True)
                        mw.queue_send({"test_cmd": "freeze_can_alive"})
                        if hasattr(test, "_stimulus_sent"):
                            test._stimulus_sent = True
                    threading.Thread(target=lambda: (time.sleep(600/1000.0), _can003_freeze), daemon=True).start()
                threading.Thread(target=lambda: (time.sleep(500/1000.0), _can003_init), daemon=True).start()

        elif tid == "TC_GEN_001":
            self._log("  → TC_GEN_001 : ignition=0 puis ON + SPEED1")
            if rc:
                rc.set_cmd("wc_available", False)
                rc.set_cmd("ignition_status", 0)
                rc.set_cmd("crs_wiper_op", 0)
            # TCP vers bcmcan : envoyer ignition=OFF pour que CAN 0x300
            # émette ignition=0 et n'écrase pas Redis toutes les 200ms.
            mw.queue_send(
                {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
            # 1000ms : assure 5 trames CAN 0x300 avec ignition=0 avant le stimulus.
            # Sans ça, bcmcan envoie encore ignition=2 → BCM ignore SPEED1.
            def _tc_gen001_start():
                if hasattr(test, "reset_t0"): test.reset_t0()
                mw.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("ignition_status", 1)
            threading.Thread(target=lambda: (time.sleep(1000/1000.0), _tc_gen001_start), daemon=True).start()

        elif tid == "TC_SPD_001":
            self._log("  → TC_SPD_001 : LIN SPEED1 continu 5 s")
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                # Activer simulation rest_contact pour éviter B2009
                # (sans hardware GPIO, BCM détecte STUCK CLOSED après 3s)
                # Cycle : lame EN MOUVEMENT 1600ms / AU REPOS 100ms — simule un cycle complet
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)  # lame en mouvement

                # Génération : incrémentée à chaque test pour annuler les QTimers résiduels
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen

                def _rc_rest():
                    """Passer brièvement au repos (False) puis reprendre mouvement (True)."""
                    if self._rc_gen != _gen:
                        return  # QTimer résiduel d'un test précédent — ignorer
                    if rc:
                        rc.set_cmd("rest_contact_sim", False)
                    threading.Thread(target=lambda: (time.sleep(100/1000.0), rc and self._rc_gen == _gen and rc.set_cmd("rest_contact_sim", True)), daemon=True).start()

                # Cycle toutes les 1700ms pendant 8s (couvre la fenêtre d'observation 5s + marge)
                for _i, _delay in enumerate(range(1700, 8500, 1700)):
                    threading.Thread(target=lambda: (time.sleep(_delay/1000.0), _rc_rest), daemon=True).start()

            if hasattr(test, "reset_t0"):
                threading.Thread(target=lambda: (time.sleep(300/1000.0), test.reset_t0()), daemon=True).start()

        elif tid == "TC_AUTO_004":
            self._log("  → TC_AUTO_004 : AUTO avec rain_sensor_installed=False")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if rc:
                rc.set_cmd("rain_sensor_installed", False)
                rc.set_cmd("crs_wiper_op", 4)   # WOP_AUTO
            lw.queue_send({"cmd": "AUTO"})

        elif tid == "TC_FSR_008":
            self._log("  → TC_FSR_008 : LIN SPEED1 puis watchdog trigger")
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                def _fsr008_trigger():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    rc.set_cmd("watchdog_test_trigger", True)
                threading.Thread(target=lambda: (time.sleep(400/1000.0), _fsr008_trigger), daemon=True).start()
            else:
                if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_FSR_010":
            self._log("  → TC_FSR_010 : CRC corrompu sur 0x201 (émission autonome)")
            if rc:
                rc.set_cmd("wc_timeout_active", False)
                rc.set_cmd("wc_crc_fault",      False)
                rc.set_cmd("wc_available",      False)
                # Verrouiller crs_wiper_op contre LIN 0x16 : empêche le LIN worker
                # d'écraser avec WiperOp=OFF et de faire chuter wc_available
                rc.set_cmd("lin_op_locked", True)
            lw.queue_send({"cmd": "SPEED1"})

            def _fsr010_activate():
                if rc:
                    # FIX : re-asserter wc_available=False à mi-chemin pour écraser
                    # tout résidu de commande Redis du cleanup TC_CAN_003 qui pourrait
                    # arriver en retard et écraser notre wc_available=True à venir.
                    rc.set_cmd("wc_available",  False)
                    rc.set_cmd("wc_crc_fault",  False)
                def _fsr010_set_available():
                    if rc:
                        rc.set_cmd("wc_available",  True)
                        rc.set_cmd("crs_wiper_op",  2)
                    def _fsr010_corrupt():
                        if hasattr(test, "reset_t0"): test.reset_t0()
                        mw.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 20})
                        # Autoriser _check_rte() à observer seulement maintenant
                        if hasattr(test, "_stimulus_sent"):
                            test._stimulus_sent = True
                    threading.Thread(target=lambda: (time.sleep(600/1000.0), _fsr010_corrupt), daemon=True).start()
                # FIX : délai 400ms entre le re-assert False et le set True
                # pour laisser tous les Redis en transit du cleanup précédent arriver
                threading.Thread(target=lambda: (time.sleep(400/1000.0), _fsr010_set_available), daemon=True).start()

            # FIX : délai porté à 400ms (était 600ms) — la 2e étape ajoute 400ms supplémentaires
            # Total jusqu'au corrupt : 400ms + 400ms + 600ms = 1400ms (était 600ms+600ms=1200ms)
            threading.Thread(target=lambda: (time.sleep(400/1000.0), _fsr010_activate), daemon=True).start()

        elif tid == "TC_COM_001":
            self._log("  → TC_COM_001 : mesure physique baudrate BREAK LIN")
            if hasattr(test, "reset_t0"): test.reset_t0()
            # Pas de stimulus actif : crslin mesure automatiquement la duree
            # du BREAK a chaque trame LIN recue, accumule 5 mesures, puis
            # envoie lin_baud_measured via TCP. Le LIN schedule tourne deja.

        # ── TC_LIN_CS : Checksum LIN 0x16 invalide (v2 — ordre corrigé) ──
        elif tid == "TC_LIN_CS":
            self._log("  → TC_LIN_CS : corruption checksum AVANT commande SPEED1")
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
            if rc:
                rc.set_cmd("lin_checksum_fault", False)
                rc.set_cmd("crs_wiper_op",       0)
                rc.set_cmd("lin_timeout_active", False)

            def _tc_lin_cs_activate():
                # Étape 1 : activer corruption côté simulateur
                lw.queue_send({"test_cmd": "corrupt_lin_checksum"})
                # Étape 2 : armer l'observation dans l'objet test
                if hasattr(test, "_stimulus_sent"):
                    test._stimulus_sent = True
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Étape 3 : envoyer SPEED1 — crslin va émettre WOP=2
                # avec checksum XOR 0xFF → BCM doit rejeter
                threading.Thread(target=lambda: (time.sleep(100/1000.0), lw.queue_send({"cmd": "SPEED1"})), daemon=True).start()

            threading.Thread(target=lambda: (time.sleep(200/1000.0), _tc_lin_cs_activate), daemon=True).start()


        # ── T44 : REAR_WIPE isolé (op=7) ─────────────────────────────────
        elif tid == "T44":
            self._log("  → T44 : REAR_WIPE op=7 (une seule fois) → OFF à 2000ms")
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
            if rc:
                rc.set_cmd("rear_wiper_available", True)
                rc.set_cmd("wc_available",         False)
                rc.set_cmd("reverse_gear",         False)
                rc.set_cmd("ignition_status",      1)
                rc.set_cmd("crs_wiper_op",         0)
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t44_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus unique : LIN cmd="REAR_WIPE" → crslin émet WOP=7
                # Le BCM lit WiperOp=7 via _lin_poll_0x16 → entre en REAR_WIPE
                lw.queue_send({"cmd": "REAR_WIPE"})

                # À t=2000ms (après au moins 1 cycle de 1700ms) : relâcher le levier
                # cmd="OFF" → crslin émet WOP=0 → BCM sort de REAR_WIPE
                threading.Thread(target=lambda: (time.sleep(2000/1000.0), lw.queue_send({"cmd": "OFF"})), daemon=True).start()

            # Délai 300ms : BCM confirme rear_wiper_available=True et ignition=ON
            threading.Thread(target=lambda: (time.sleep(300/1000.0), _t44_start), daemon=True).start()

        # ── T50 : Cas B — wc_available=True → H-Bridge GPIO non commandé ─
        elif tid == "T50":
            self._log("  → T50 : Cas B wc_available=True + LIN SPEED1 → CAN 0x200, pas RL2=LOW")
            # Préconditions :
            #  - wc_available=True   (Cas B — force le blocage GPIO)
            #  - lin_op_locked=True  : verrouille crs_wiper_op contre LIN 0x16
            #  - ignition=ON
            # PAS de rest_contact_sim : la garde "if rest_contact_sim_active: return"
            # dans _check_rest_contact_stuck() désactiverait B2009 artificiellement.
            # PAS de blade_cycling : incohérent avec rest_contact qui resterait fixe
            # (si lame ne bouge pas physiquement, les deux capteurs sont fixes).
            # L'observation dure OBS_MS=2000ms < REST_STUCK_DELAY=3000ms :
            # B2009 ne peut pas se déclencher dans cette fenêtre temporelle.
            if rc:
                rc.set_cmd("wc_available",  True)
                rc.set_cmd("lin_op_locked", True)
                rc.set_cmd("crs_wiper_op",  0)
                rc.set_cmd("ignition_status", 1)
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 2)
            threading.Thread(target=lambda: (time.sleep(400/1000.0), _t50_start), daemon=True).start()

        # ── T51 : Cas A — rest contact bloqué → FSR_006 ──────────────────
        elif tid == "T51":
            self._log("  → T51 : Cas A rest_contact bloqué EN MOUVEMENT → FSR_006")
            # Pré-conditions :
            #  - wc_available=False (Cas A obligatoire — FSR_006 Cas A surveille
            #    rest_contact GPIO, Cas B surveille wc_blade_position)
            #  - rest_contact_sim_active=True, rest_contact_sim=True (lame BLOQUÉE
            #    en position "en mouvement" — ne jamais passer à False)
            #  - ignition=ON
            if rc:
                rc.set_cmd("wc_available",            False)
                rc.set_cmd("ignition_status",         1)
                rc.set_cmd("crs_wiper_op",            0)
                # Activer simulation rest_contact BLOQUÉE en position mouvement.
                # IMPORTANT : ne PAS programmer de retour à False ici.
                # FSR_006 doit détecter ce blocage SEUL, sans aide du test_runner.
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim",        True)
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            def _t51_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus : SPEED1 → moteur démarre → rest_contact reste bloqué
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 2)
            # Délai 300 ms : laisser le BCM confirmer wc_available=False
            threading.Thread(target=lambda: (time.sleep(300/1000.0), _t51_start), daemon=True).start()

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
            self._log(
                "  → TC_B2103 : reset guard B2103, puis injection blade_sim=50 % "
                "(écart ≈ 50 % > seuil 10 %)")

            # Étape 1 & 2 : remise à zéro immédiate
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2103()
            if rc:
                rc.set_cmd("wc_b2103_active", False)

            def _b2103_inject():
                # Étape 4 : injecter blade_sim après propagation du reset
                if self._sim_client and self._sim_client.is_connected():
                    ok = self._sim_client.send_blade_sim(50.0)
                    self._log(
                        f"  → TC_B2103 : blade_sim=50 % {'injecté' if ok else 'ECHEC INJECTION'}"
                        " — attente détection B2103…")
                else:
                    self._log(
                        "  → TC_B2103 : SimClient non connecté — injection blade_sim impossible")
                # Étape 5 : démarrer le chrono à partir de l'injection physique
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                test._t_inject_ms = time.time() * 1000.0

            def _b2103_cleanup():
                # Nettoyage : désactiver comparaison + Redis pour le test suivant
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2103()
                if rc:
                    rc.set_cmd("wc_b2103_active", False)
                self._log("  → TC_B2103 : nettoyage post-test (blade_sim=-1, Redis reset)")

            # Délai 200 ms : propagation du reset côté simulateur avant injection
            threading.Thread(target=lambda: (time.sleep(200/1000.0), _b2103_inject), daemon=True).start()
            # Nettoyage différé : TIMEOUT + 500 ms pour ne pas polluer le test suivant
            threading.Thread(target=lambda: (time.sleep(int((TC_B2103_PositionSensorFault.TEST_TIMEOUT_S + 0.5) * 1000)/1000.0), _b2103_cleanup), daemon=True).start()

        elif tid == "T22":
            self._log("  → T22 : pompe FORWARD >5s → overtime FSR_005 (B2008)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if rc:
                # Cycling 1700ms : empêche B2009 (False→True reset timer chaque 1700ms).
                # La pompe tourne en FORWARD. FSR_005 la coupe à 5000ms (PUMP_MAX_RUNTIME),
                # avant que le 3e cycle se complète à 5100ms → wash ne se termine pas
                # normalement → seul FSR_005 coupe la pompe → B2008 déclenché.
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)
                rc.set_cmd("crs_wiper_op", 5)   # WOP_FRONT_WASH

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t22 = self._rc_gen

                def _rc_cycle_t22():
                    if self._rc_gen != _gen_t22:
                        return
                    if rc:
                        rc.set_cmd("rest_contact_sim", False)
                        threading.Thread(target=lambda: (time.sleep(100/1000.0), rc and self._rc_gen == _gen_t22 and rc.set_cmd("rest_contact_sim", True)), daemon=True).start()

                for _d in range(1700, 10000, 1700):
                    threading.Thread(target=lambda: (time.sleep(_d/1000.0), _rc_cycle_t22), daemon=True).start()

            lw.queue_send({"cmd": "FRONT_WASH"})

        elif tid == "T21":
            self._log("  → T21 : FRONT_WASH — 3 cycles lame avant 5s → arrêt pompe normal")
            if rc:
                # Cycling rest_contact à 900ms pour que 3 cycles se terminent avant 5s.
                # 3 × 900ms ≈ 2.7s → wash_cycles_done=3 → pompe arrêtée AVANT FSR_005 (5s).
                # Si les cycles prenaient 1700ms (ancien), 3e cycle à ~5.1s > 5s → FSR → T22.
                # T21 vérifie l'arrêt normal (avant 5s). T22 vérifie l'arrêt FSR (>5s).
                # Cycle : True (mouvement) 800ms → False (repos) 100ms → True...
                # _track_blade_cycle compte sur front descendant True→False (count_on_rest=True).
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)

                def _t21_start():
                    if rc:
                        rc.set_cmd("crs_wiper_op", 5)
                        lw.queue_send({"cmd": "FRONT_WASH"})
                        # Génération : annule les QTimers résiduels du test précédent
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        _gen_t21 = self._rc_gen
                        # Cycle à 900ms : 3 cycles = 2.7s < PUMP_MAX_RUNTIME (5s)
                        # → le BCM détecte 3 cycles et stoppe la pompe normalement
                        def _rc_cycle_t21():
                            if self._rc_gen != _gen_t21:
                                return
                            if rc:
                                rc.set_cmd("rest_contact_sim", False)
                                threading.Thread(target=lambda: (time.sleep(100/1000.0), rc and self._rc_gen == _gen_t21 and rc.set_cmd("rest_contact_sim", True)), daemon=True).start()
                        for _d in range(900, 10000, 900):
                            threading.Thread(target=lambda: (time.sleep(_d/1000.0), _rc_cycle_t21), daemon=True).start()

                threading.Thread(target=lambda: (time.sleep(200/1000.0), _t21_start), daemon=True).start()
            else:
                lw.queue_send({"cmd": "FRONT_WASH"})

        # ── Tests WSM BCM — stimulus LIN, observation Redis ──────────────
        # Le stimulus passe par LIN (crslin → bus physique → BCM protocol)
        # pour tester la chaîne complète : trame 0x16 → décodage wiper_op
        # → WSM → state. Redis est utilisé UNIQUEMENT pour observer state.
        # Un SET Redis direct court-circuiterait le protocole LIN et ne
        # testerait que la logique interne WSM, pas la chaîne hardware.
        elif tid == "T30":
            self._log("  → T30 : LIN cmd=SPEED1 (stimulus bus physique)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED1"})

        elif tid == "T31":
            self._log("  → T31 : LIN cmd=SPEED2 (stimulus bus physique)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED2"})

        elif tid == "T32":
            self._log("  → T32 : LIN SPEED1 puis LIN OFF (stimulus bus physique)")
            # reset_t0 au moment de la commande OFF (pas au SPEED1)
            # pour ne mesurer que la transition SPEED1→OFF
            lw.queue_send({"cmd": "SPEED1"})
            def _send_off_lin_with_t0():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                lw.queue_send({"cmd": "OFF"})
            threading.Thread(target=lambda: (time.sleep(300/1000.0), _send_off_lin_with_t0), daemon=True).start()

        elif tid == "T33":
            self._log("  → Redis SET SPEED1 puis ignition=0")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if rc:
                # Sync simulateur : ignition ON pour démarrer en SPEED1
                mw.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                rc.set_cmd("crs_wiper_op", 0)
                threading.Thread(target=lambda: (time.sleep(200/1000.0), rc and rc.set_cmd("crs_wiper_op", 2)), daemon=True).start()
                def _t33_ignoff():
                    test.reset_t0()
                    rc.set_cmd("ignition_status", 0)
                    # Sync simulateur : ignition OFF → CAN 0x300 avec ign=0
                    mw.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                threading.Thread(target=lambda: (time.sleep(600/1000.0), _t33_ignoff), daemon=True).start()
            else:
                lw.queue_send({"cmd": "SPEED1"})
                threading.Thread(target=lambda: (time.sleep(400/1000.0), mw.queue_send( {"ignition_status": 0, "reverse_gear": 0, "vehicle_speed": 0})), daemon=True).start()

        elif tid == "T34":
            self._log("  → Redis SET AUTO + rain=10")
            if rc:
                lw.queue_send({"cmd": "AUTO"})
                rc.set_cmd("rain_sensor_installed", True)
                rc.set_cmd("rain_intensity", 10)
                mw.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t34 = self._rc_gen
                time.sleep(0.2)
                if hasattr(test, "reset_t0"): test.reset_t0()
                for cycle in range(3):
                    b_ms = 300 + cycle * 1700
                    def _send_true(b=b_ms, g=_gen_t34):
                        time.sleep(b / 1000.0)
                        if self._rc_gen == g and rc:
                            rc.set_cmd("rest_contact_sim", True)
                    def _send_false(b=b_ms, g=_gen_t34):
                        time.sleep((b + 1550) / 1000.0)
                        if self._rc_gen == g and rc:
                            rc.set_cmd("rest_contact_sim", False)
                    threading.Thread(target=_send_true, daemon=True).start()
                    threading.Thread(target=_send_false, daemon=True).start()
            else:
                lw.queue_send({"cmd": "AUTO"})
                mw.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
        elif tid == "T35":
            self._log("  → Redis SET AUTO + rain=25")
            if rc:
                # FIX T35 : même correctif que T34.
                lw.queue_send({"cmd": "AUTO"})
                rc.set_cmd("rain_sensor_installed", True)
                rc.set_cmd("rain_intensity", 25)
                mw.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                # Activer simulation rest_contact avec _rc_gen (annulable au cleanup)
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t35 = self._rc_gen
                threading.Thread(target=lambda: (time.sleep(200/1000.0), test.reset_t0()), daemon=True).start()
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    threading.Thread(target=lambda b=b, g=_gen_t35: (time.sleep(b/1000.0), ( self._rc_gen == g and rc and rc.set_cmd("rest_contact_sim", True))), daemon=True).start()
                    threading.Thread(target=lambda b=b, g=_gen_t35: (time.sleep(b + 1550/1000.0), ( self._rc_gen == g and rc and rc.set_cmd("rest_contact_sim", False))), daemon=True).start()
            else:
                lw.queue_send({"cmd": "AUTO"})
                mw.queue_send({"rain_intensity": 25, "sensor_status": "OK"})

        # ─── REMPLACEMENT T36 ─────────────────────────────────────────────
        elif tid == "T36":
            self._log("  → Redis SET crs_wiper_op=FRONT_WASH (reset cycles)")
            # FIX T36 : synchroniser RPiSIM LIN slave en FRONT_WASH AVANT Redis.
            # Sans cela, RPiSIM continue à renvoyer WiperOp=OFF dans chaque
            # frame LIN 0x16 (400ms) → _lin_poll_0x16() écrit crs_wiper_op=0
            # → WSM sort de WASH_FRONT immédiatement.
            # FIX T36 rest_contact : reset crs_wiper_op=0 d'abord pour que le BCM
            # remette _front_blade_cycles=0 via _enter_off() avant FRONT_WASH.
            if rc:
                rc.set_cmd("crs_wiper_op", 0)   # force OFF → reset _front_blade_cycles
            mw.queue_send({"ignition_status": 1, "vehicle_speed": 0})

            def _send_front_wash():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Activer simulation rest_contact pour T36
                # Le runner va simuler 3 cycles complets :
                #   False → True → False (cycle 1)
                #   False → True → False (cycle 2)
                #   False → True → False (cycle 3)
                # Chaque cycle = WIPE_CYCLE_DURATION = 1700ms
                if rc:
                    rc.set_cmd("rest_contact_sim_active", True)
                    rc.set_cmd("rest_contact_sim", False)  # repos initial
                    lw.queue_send({"cmd": "FRONT_WASH"})  # stimulus LIN uniquement
                    # Simuler 3 cycles via transitions temporisées
                    # Chaque cycle : 150ms → True (départ) puis 1550ms → False (retour repos)
                    # IMPORTANT : capturer b par valeur dans la lambda (pas par référence)
                    for cycle in range(3):
                        b = cycle * 1700
                        threading.Thread(target=lambda b=b: (time.sleep(b + 150/1000.0), rc and rc.set_cmd("rest_contact_sim", True)), daemon=True).start()
                        threading.Thread(target=lambda b=b: (time.sleep(b + 1600/1000.0), rc and rc.set_cmd("rest_contact_sim", False)), daemon=True).start()

            threading.Thread(target=lambda: (time.sleep(400/1000.0), _send_front_wash), daemon=True).start()

        # ─── REMPLACEMENT T37 ─────────────────────────────────────────────
        elif tid == "T37":
            self._log("  → T37 : LIN REAR_WASH (stimulus bus physique)")
            lw.queue_send({"cmd": "REAR_WASH"})
            mw.queue_send({"ignition_status": 1, "vehicle_speed": 0})
            # reset_t0 décalé de 200ms : absorbe la latence LIN→BCM pour que
            # la mesure démarre quand le BCM est réellement en WASH_REAR
            if hasattr(test, "reset_t0"):
                threading.Thread(target=lambda: (time.sleep(200/1000.0), test.reset_t0()), daemon=True).start()

        elif tid == "T38":
            self._log("  → T38 : LIN SPEED1 + injection surcourant")
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                # t=200ms : B2001 INACTIVE (200ms avant injection)
                threading.Thread(target=lambda: (time.sleep(200/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2001")), daemon=True).start()
                # t=400ms : injection + chrono
                def _t38_inject():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    rc.set_cmd("motor_current_a", 0.95)
                threading.Thread(target=lambda: (time.sleep(400/1000.0), _t38_inject), daemon=True).start()
            else:
                threading.Thread(target=lambda: (time.sleep(400/1000.0), self._inject_overcurrent(6)), daemon=True).start()

        elif tid == "T39":
            self._log("  → T39 : LIN SPEED1 puis stop_lin_tx (stimulus bus physique)")
            # Attendre 500ms pour que le BCM soit bien en SPEED1 avant de couper le LIN.
            # reset_t0 se déclenche au moment du stop_lin_tx : mesure = temps entre
            # coupure LIN et retour en OFF du BCM = délai détection timeout FSR_001.
            lw.queue_send({"cmd": "SPEED1"})
            def _t39_stop():
                if hasattr(test, "reset_t0"): test.reset_t0()
                lw.queue_send({"test_cmd": "stop_lin_tx"})
            threading.Thread(target=lambda: (time.sleep(500/1000.0), _t39_stop), daemon=True).start()

        elif tid == "T38b":
            self._log("  → T38b : LIN REAR_WIPE + injection surcourant moteur arrière (B2002)")
            if rc:
                rc.set_cmd("rear_wiper_available", True)
            lw.queue_send({"cmd": "REAR_WIPE"})
            if rc:
                # Étape 1 à t=300ms : remettre B2002 INACTIVE
                # Laisser 200ms de marge avant l'injection pour que le BCM
                # traite set_inactive (cycle Redis ~100ms) → already_active=False
                threading.Thread(target=lambda: (time.sleep(300/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2002")), daemon=True).start()
                # Étape 2 à t=500ms : injection surcourant + démarrage chrono
                def _t38b_inject():
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                    rc.set_cmd("motor_current_a", 0.95)
                threading.Thread(target=lambda: (time.sleep(500/1000.0), _t38b_inject), daemon=True).start()

        elif tid == "T38c":
            self._log("  → T38c : LIN FRONT_WASH (pompe active) + injection surcourant pompe (B2003)")
            if rc:
                # Activer simulation rest_contact : WASH_FRONT utilise le moteur avant,
                # sans cycles rest_contact le BCM déclenche B2009 après 3s → ERROR
                # avant que pump_error ne soit vu par _check_rte.
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t38c = self._rc_gen
                for cycle in range(4):
                    b = 200 + cycle * 1700
                    threading.Thread(target=lambda g=_gen_t38c: (time.sleep(b/1000.0), ( self._rc_gen == g and rc and rc.set_cmd("rest_contact_sim", True))), daemon=True).start()
                    threading.Thread(target=lambda g=_gen_t38c: (time.sleep(b + 1550/1000.0), ( self._rc_gen == g and rc and rc.set_cmd("rest_contact_sim", False))), daemon=True).start()
            lw.queue_send({"cmd": "FRONT_WASH"})
            if rc:
                # t=400ms : B2003 INACTIVE (200ms avant injection)
                threading.Thread(target=lambda: (time.sleep(400/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2003")), daemon=True).start()
                # t=600ms : injection + chrono
                def _t38c_inject():
                    # Invalider les cycles rest_contact avant l'injection
                    self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                    rc.set_cmd("pump_current_a", 1.0)
                threading.Thread(target=lambda: (time.sleep(600/1000.0), _t38c_inject), daemon=True).start()

        elif tid == "LIN_INVALID_CMD_001":
            self._log("  → LIN_INVALID_CMD_001 : BCM en OFF → envoi trame LIN op=10 (hors plage)")
            # S'assurer que le BCM est en OFF
            if rc:
                rc.set_cmd("crs_wiper_op", 0)
            # Injecter op=10 brut dans la prochaine trame LIN 0x16 via test_cmd.
            # Le simulateur insère la valeur dans les bits 3:0 de byte0 de 0x16
            # sans passer par l'enum WOp → le BCM reçoit WiperOp=0x0A (hors [0..7]).
            def _lin_invalid_inject():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                lw.queue_send({"test_cmd": "set_raw_wiper_op", "op": 10})
            threading.Thread(target=lambda: (time.sleep(200/1000.0), _lin_invalid_inject), daemon=True).start()

        elif tid == "T_RAIN_AUTO_SENSOR_ERROR":
            self._log("  → T_RAIN_AUTO_SENSOR_ERROR : rain_sensor_installed + AUTO + SensorStatus=ERROR")
            if rc:
                # Étape 1 : déclarer le capteur disponible et sain
                # rain_intensity=25 > RAIN_SPEED2_THRESH(20) : démarre le moteur en Speed2
                # sans ça le moteur reste STOP et B2009 peut quand même se déclencher.
                rc.set_cmd("rain_sensor_installed", True)
                rc.set_cmd("rain_sensor_ok", True)
                rc.set_cmd("rain_intensity", 25)
                # Sync CAN 0x301 simulateur : évite que bcmcan écrase rain_intensity
                mw.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                # Simulation rest_contact avec cycles True→False (comme T34/T35)
                # pour éviter B2009 STUCK CLOSED pendant la phase AUTO.
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_rain = self._rc_gen

                for cycle in range(4):
                    b = 300 + cycle * 1700
                    threading.Thread(target=lambda g=_gen_rain: (time.sleep(b/1000.0), ( self._rc_gen == g and rc and rc.set_cmd("rest_contact_sim", True))), daemon=True).start()
                    threading.Thread(target=lambda g=_gen_rain: (time.sleep(b + 1550/1000.0), ( self._rc_gen == g and rc and rc.set_cmd("rest_contact_sim", False))), daemon=True).start()

            # Étape 2 : envoyer commande AUTO via LIN
            threading.Thread(target=lambda: (time.sleep(200/1000.0), lw.queue_send({"cmd": "AUTO"})), daemon=True).start()

            # Étape 3a à t=1000ms : B2007 INACTIVE (200ms avant injection)
            threading.Thread(target=lambda: (time.sleep(1000/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2007")), daemon=True).start()

            # Étape 3b à t=1200ms : injection erreur capteur + chrono
            def _inject_sensor_error():
                # Invalider les cycles rest_contact
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                if hasattr(test, "notify_injection"):
                    test.notify_injection()
                if rc:
                    rc.set_cmd("rain_intensity", 0xFF)
                    rc.set_cmd("rain_sensor_ok", False)
                mw.queue_send({"rain_intensity": 255, "sensor_status": "ERROR"})
            threading.Thread(target=lambda: (time.sleep(1200/1000.0), _inject_sensor_error), daemon=True).start()

        elif tid == "T_B2009_CAN":
            self._log("  → T_B2009_CAN : CAS B + blade figée → B2009 (GPIO rest_contact=False naturel)")
            if rc:
                rc.set_cmd("wc_available",   True)
                rc.set_cmd("lin_op_locked",  True)
                rc.set_cmd("crs_wiper_op",   0)
                rc.set_cmd("ignition_status", 1)
                # BladePosition figée à 50% → lame bloquée mécaniquement
                # → rest_contact reste False naturellement (lame ne revient pas au repos)
                # → GPIO hardware lu directement, aucune simulation rest_contact nécessaire
                # B2009 INACTIVE avant le test pour affichage complet
                threading.Thread(target=lambda: (time.sleep(200/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2009")), daemon=True).start()
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t_b2009_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # BladePosition figée à 50% (>0 → front_motor_running=True en CAS B)
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.freeze_blade_position(50.0)
                # LIN SPEED1 → BCM entre ST_SPEED1 → CAN 0x200 → 0x201 CurrentSpeed>0
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 2)
            # 400ms : laisser wc_available=True + dtc_inactivate se propager
            threading.Thread(target=lambda: (time.sleep(400/1000.0), _t_b2009_start), daemon=True).start()

        elif tid == "T50b":
            self._log("  → T50b : CAS B SPEED1 + inject_motor_current → B2001")
            if rc:
                rc.set_cmd("wc_available",   True)
                rc.set_cmd("lin_op_locked",  True)
                rc.set_cmd("crs_wiper_op",   0)
                rc.set_cmd("ignition_status", 1)
                # B2001 INACTIVE avant injection pour affichage complet
                threading.Thread(target=lambda: (time.sleep(200/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2001")), daemon=True).start()
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50b_start():
                # LIN SPEED1 → BCM ST_SPEED1 → CAN 0x200 → WC répond 0x201 speed=1
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 2)

            def _t50b_inject():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Injecter MotorCurrent=0.95A dans trame 0x201
                # BCM lira motor_current_a > OVERCURRENT_THRESH(0.8A) → B2001
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.inject_motor_current(0.95)

            # 400ms : wc_available propagé + SPEED1 établi
            threading.Thread(target=lambda: (time.sleep(400/1000.0), _t50b_start), daemon=True).start()
            # 700ms : SPEED1 stabilisé → injection overcurrent
            threading.Thread(target=lambda: (time.sleep(700/1000.0), _t50b_inject), daemon=True).start()

        elif tid == "T_IGN_OFF_WIPER_IGNORED":
            self._log("  → T_IGN_OFF_WIPER_IGNORED : ignition=OFF + LIN SPEED1 → ignorée")
            if rc:
                # Mettre ignition=OFF avant le stimulus
                rc.set_cmd("ignition_status", 0)
                rc.set_cmd("crs_wiper_op",    0)
                rc.set_cmd("wc_available",    False)
            # Sync simulateur : ignition OFF
            mw.queue_send(
                {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})

            def _ign_off_inject():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Envoyer WiperOp=SPEED1 via LIN — doit être ignoré par le BCM
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 2)
            # 300ms : laisser ignition=0 se propager dans le BCM
            threading.Thread(target=lambda: (time.sleep(300/1000.0), _ign_off_inject), daemon=True).start()

        elif tid == "T_B2009_CASA":
            self._log("  → T_B2009_CASA : CAS A SPEED1 sans rest_contact → B2009")
            if rc:
                rc.set_cmd("wc_available",   False)
                rc.set_cmd("crs_wiper_op",   0)
                rc.set_cmd("ignition_status", 1)
                # PAS de rest_contact_sim_active : la garde BCM bloquerait B2009
                # GPIO hardware lu directement → False permanent → B2009 après 3s
                rc.set_cmd("rest_contact_sim_active", False)
                rc.set_cmd("rest_contact_sim", False)
                # B2009 INACTIVE avant test pour affichage complet
                threading.Thread(target=lambda: (time.sleep(200/1000.0), rc and rc.set_cmd("dtc_inactivate", "B2009")), daemon=True).start()
            mw.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t_b2009_casa_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                lw.queue_send({"cmd": "SPEED1"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 2)
            # 400ms : laisser dtc_inactivate se propager avant le stimulus
            threading.Thread(target=lambda: (time.sleep(400/1000.0), _t_b2009_casa_start), daemon=True).start()

    def _reset_bcm_state(self):
        """Remet le BCM dans un état stable (ignition ON, pas de timeout actif)."""
        rc = self._rte_client
        mw = self._motor_w
        lw = self._lin_w
        if rc:
            rc.set_cmd("wc_timeout_active",    False)
            rc.set_cmd("lin_timeout_active",   False)
            rc.set_cmd("crs_wiper_op",         0)
            rc.set_cmd("ignition_status",      1)
            rc.set_cmd("wc_available",         False)
            rc.set_cmd("rain_intensity",       0)
            rc.set_cmd("rain_sensor_installed", False)
            rc.set_cmd("rest_contact_sim_active", False)
            rc.set_cmd("rest_contact_sim",     False)
        if mw:
            mw.queue_send({"ignition_status": "ON",
                           "reverse_gear": 0,
                           "vehicle_speed": 0})
        if lw:
            lw.queue_send({"cmd": "OFF"})

    # ── Boucle principale ─────────────────────────────────────────────────
    def run(self, test_classes: list, global_timeout_s: float = 600.0) -> list:
        """
        Exécute les tests séquentiellement.
        Retourne la liste complète des TestResult.
        """
        t_global = time.monotonic()
        total    = len(test_classes)

        self._log(f"[START] Démarrage campagne  —  {total} test(s) à exécuter")
        if self._rte_client:
            self._log(f"   Redis  : {self._rte_client.host}:{self._rte_client.port}")
        self._log(f"   Timeout global : {global_timeout_s}s")
        self._log("-" * 70)

        last_tid = ""

        for idx, cls in enumerate(test_classes):

            # ── Timeout global ────────────────────────────────────────────
            if time.monotonic() - t_global > global_timeout_s:
                self._log(f"[WARN]  Timeout global ({global_timeout_s}s) — arrêt après {idx} tests")
                return self._results

            test = cls()
            self._log(f"\n>> [{idx+1}/{total}]  [{test.ID}]  {test.NAME}")

            # ── Injecter rte_client pour BaseBCMTest ──────────────────────
            if isinstance(test, BaseBCMTest):
                BaseBCMTest.rte_client = self._rte_client

            # ── Délai inter-test ──────────────────────────────────────────
            delay_ms = self._inter_delay_ms(test.ID, last_tid)
            if delay_ms > 0:
                self._log(f"   [WAIT] Pause inter-test {delay_ms} ms…")
                time.sleep(delay_ms / 1000.0)

            # ── Reset état BCM ────────────────────────────────────────────
            self._reset_bcm_state()
            time.sleep(1.0)   # laisser Redis + BCM traiter les commandes (1s minimum)

            # ── Attendre que le BCM confirme l'état OFF avant de démarrer ─
            # Evite que _check_rte lise déjà la cible dès la première lecture
            # (ce qui empêche _had_different de devenir True → TIMEOUT garanti)
            if self._rte_client and isinstance(test, BaseBCMTest):
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    state = self._rte_client.get("state")
                    speed = self._rte_client.get_int("front_motor_speed")
                    if state in (None, "OFF", "PARK") and speed == 0:
                        break
                    time.sleep(0.1)

            # ── Démarrer le test ──────────────────────────────────────────
            self._done_ev.clear()
            with self._lock:
                self._current = test
            test.start()
            self._pre_test(test)            # stimulus envoyé (avec ses sleep internes)

            # ── Boucle supervision (tick 200ms) ───────────────────────────
            while True:
                finished = self._done_ev.wait(timeout=self.TICK_INTERVAL_S)
                if finished:
                    break

                # Vérifications timeout et _check_rte
                with self._lock:
                    cur = self._current
                if cur is None:
                    break   # _finish() déjà appelé

                res = cur.check_timeout()
                if res is None and isinstance(cur, BaseBCMTest):
                    res = cur._check_rte()

                if res is not None:
                    with self._lock:
                        self._current = None
                    self._results.append(res)
                    icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "TIMEOUT": "[TIMEOUT]"}.get(res.status, "?")
                    msg  = (f"  {icon} [{res.test_id}] {res.name:<50}"
                            f" → {res.status}")
                    if res.details:
                        msg += f"  ({res.details})"
                    self._log(msg)
                    break

            last_tid = test.ID

            # ── Fail-fast ─────────────────────────────────────────────────
            if self._fail_fast and self._results:
                last = self._results[-1]
                if last.status != "PASS":
                    self._log(f"[STOP] --fail-fast : arrêt après [{test.ID}]  "
                              f"status={last.status}")
                    break

        # ── Résumé ────────────────────────────────────────────────────────
        elapsed = time.monotonic() - t_global
        n_pass  = sum(1 for r in self._results if r.status == "PASS")
        n_fail  = sum(1 for r in self._results if r.status == "FAIL")
        n_to    = sum(1 for r in self._results if r.status == "TIMEOUT")
        self._log("")
        self._log("=" * 70)
        self._log(f"Campagne terminée en {elapsed:.1f}s")
        self._log(f"PASS={n_pass}   FAIL={n_fail}   TIMEOUT={n_to}   "
                  f"TOTAL={len(self._results)}")
        self._log("=" * 70)

        return self._results


# ═════════════════════════════════════════════════════════════════════════════
#  EXPORTS RAPPORTS
# ═════════════════════════════════════════════════════════════════════════════

def export_json(results: list, t_start: datetime.datetime,
                t_end: datetime.datetime, bench_id: str, path: str):
    """Écrit un fichier JSON structuré — compatible importateurs XRAY/Jira."""
    data = {
        "bench_id":   bench_id,
        "t_start":    t_start.isoformat(),
        "t_end":      t_end.isoformat(),
        "duration_s": (t_end - t_start).total_seconds(),
        "summary": {
            "total":   len(results),
            "pass":    sum(1 for r in results if r.status == "PASS"),
            "fail":    sum(1 for r in results if r.status == "FAIL"),
            "timeout": sum(1 for r in results if r.status == "TIMEOUT"),
        },
        "tests": [
            {
                "id":       r.test_id,
                "name":     r.name,
                "category": r.category,
                "ref":      r.ref,
                "status":   r.status,
                "limit":    r.limit,
                "measured": r.measured,
                "details":  r.details,
            }
            for r in results
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_junit(results: list, t_start: datetime.datetime,
                 t_end: datetime.datetime, bench_id: str, path: str):
    """
    Écrit un rapport JUnit XML compatible Jenkins (plugin JUnit).
    Chaque TestResult → <testcase> ; FAIL → <failure> ; TIMEOUT → <error>.
    """
    import xml.etree.ElementTree as ET

    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_to   = sum(1 for r in results if r.status == "TIMEOUT")
    dur    = (t_end - t_start).total_seconds()

    suite = ET.Element("testsuite", {
        "name":      bench_id,
        "tests":     str(len(results)),
        "failures":  str(n_fail),
        "errors":    str(n_to),
        "skipped":   "0",
        "time":      f"{dur:.3f}",
        "timestamp": t_start.isoformat(),
    })

    for r in results:
        tc = ET.SubElement(suite, "testcase", {
            "classname": r.category or "WipeWash",
            "name":      f"[{r.test_id}] {r.name}",
            "time":      "0",
        })
        if r.status == "FAIL":
            fail = ET.SubElement(tc, "failure", {
                "message": r.details or "FAIL",
                "type":    "AssertionError",
            })
            fail.text = (
                f"Test   : {r.test_id} — {r.name}\n"
                f"Réf    : {r.ref}\n"
                f"Limite : {r.limit}\n"
                f"Mesuré : {r.measured}\n"
                f"Détail : {r.details}"
            )
        elif r.status == "TIMEOUT":
            err = ET.SubElement(tc, "error", {
                "message": r.details or "TIMEOUT",
                "type":    "TimeoutError",
            })
            err.text = f"Test {r.test_id} n'a pas répondu dans le délai imparti."

    ET.indent(suite, space="  ")
    tree = ET.ElementTree(suite)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(ET.tostring(suite, encoding="unicode"))


def export_all(results, t_start, t_end, args, ts):
    """Génère les trois formats de rapport."""
    bench_id = args.bench_id

    # ── HTML ──────────────────────────────────────────────────────────────
    html_path = args.output or f"report_{ts}.html"
    try:
        gen = ReportGenerator(bench_id=bench_id, operator=args.operator)
        gen.generate(results, html_path, t_start=t_start, t_end=t_end)
        print(f"[RAPPORT] HTML  → {os.path.abspath(html_path)}", flush=True)
    except Exception as exc:
        print(f"[WARN]  Rapport HTML échoué : {exc}", file=sys.stderr)

    # ── JSON ──────────────────────────────────────────────────────────────
    json_path = args.json or f"results_{ts}.json"
    try:
        export_json(results, t_start, t_end, bench_id, json_path)
        print(f"[RAPPORT] JSON  → {os.path.abspath(json_path)}", flush=True)
    except Exception as exc:
        print(f"[WARN]  Rapport JSON échoué : {exc}", file=sys.stderr)

    # ── JUnit XML ─────────────────────────────────────────────────────────
    junit_path = args.junit or f"junit_{ts}.xml"
    try:
        export_junit(results, t_start, t_end, bench_id, junit_path)
        print(f"[RAPPORT] JUnit → {os.path.abspath(junit_path)}", flush=True)
    except Exception as exc:
        print(f"[WARN]  Rapport JUnit échoué : {exc}", file=sys.stderr)


# ═════════════════════════════════════════════════════════════════════════════
#  POINT D'ENTRÉE CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="WipeWash BCM — Runner de tests headless (Windows/Jenkins)"
    )
    p.add_argument("--bcm-host",   default="",
                   help="IP RPiBCM (défaut : auto-découverte)")
    p.add_argument("--sim-host",   default="",
                   help="IP RPiSIM (défaut : auto-découverte)")
    p.add_argument("--redis-host", default="",
                   help="IP Redis  (défaut : même que bcm-host)")
    p.add_argument("--redis-port", type=int, default=6379,
                   help="Port Redis (défaut : 6379)")
    p.add_argument("--tests",      default="",
                   help="IDs séparés par virgule. Ex: T30,T31,T32")
    p.add_argument("--output",     default="",
                   help="Chemin rapport HTML")
    p.add_argument("--json",       default="",
                   help="Chemin rapport JSON")
    p.add_argument("--junit",      default="",
                   help="Chemin rapport JUnit XML")
    p.add_argument("--timeout",    type=float, default=600.0,
                   help="Timeout global en secondes (défaut : 600)")
    p.add_argument("--bench-id",   default="WipeWash-Bench-CI",
                   help="Identifiant banc pour les rapports")
    p.add_argument("--operator",   default="jenkins",
                   help="Nom opérateur / job Jenkins")
    p.add_argument("--fail-fast",  action="store_true",
                   help="Arrêter dès le premier FAIL")
    return p.parse_args()


def main():
    args = parse_args()
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 70, flush=True)
    print("  WipeWash BCM — Test Runner Headless (Windows / Jenkins CI)", flush=True)
    print(f"  Démarrage : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print("=" * 70, flush=True)

    # ── 1. Découverte réseau ──────────────────────────────────────────────
    bcm_host = args.bcm_host.strip()
    sim_host = args.sim_host.strip()

    if not bcm_host or not sim_host:
        print("[INFRA] Auto-découverte des RPi sur 10.20.0.0/28…", flush=True)
        hosts = auto_discover_all(port=5000, timeout=4.0)
        print(f"[INFRA] Hôtes détectés (port 5000) : {hosts}", flush=True)
        if not bcm_host:
            bcm_host = hosts[0] if hosts else ""
        if not sim_host:
            sim_host = hosts[1] if len(hosts) > 1 else (hosts[0] if hosts else "")

    if not bcm_host:
        print("[ERREUR] RPiBCM introuvable. Vérifiez le réseau ou utilisez --bcm-host.",
              file=sys.stderr)
        sys.exit(2)

    redis_host = args.redis_host.strip() or bcm_host
    print(f"[INFRA] BCM={bcm_host}  SIM={sim_host}  "
          f"Redis={redis_host}:{args.redis_port}", flush=True)

    # ── 2. Connexion Redis ────────────────────────────────────────────────
    rte_client = RTEClient(redis_host, args.redis_port)
    if not rte_client.is_connected():
        print(f"[ERREUR] Redis inaccessible sur {redis_host}:{args.redis_port}",
              file=sys.stderr)
        sys.exit(2)
    print(f"[INFRA] Redis OK", flush=True)

    # ── 3. SimClient ──────────────────────────────────────────────────────
    sim_client = SimClient()
    sim_client.connect(sim_host)

    # ── 4. Workers TCP ────────────────────────────────────────────────────
    # CAN (port 5557) et LIN (port 5555) sont servis par le RPiSIM (crslin.py + bcm_tcp_can.py)
    # Motor RX (port 5000) et Pump (port 5556) sont servis par le RPiBCM
    can_worker   = HeadlessCANWorker(sim_host)
    lin_worker   = HeadlessLINWorker(sim_host)
    motor_worker = HeadlessMotorWorker(bcm_host, sim_host)
    pump_signal  = HeadlessPumpSignal(bcm_host)

    can_worker.start()
    lin_worker.start()
    motor_worker.start()
    pump_signal.start()

    print("[INFRA] Workers TCP démarrés — attente stabilisation 2s…", flush=True)
    time.sleep(HeadlessTestRunner.SETTLE_AFTER_INIT)

    # ── 5. Sélection des tests ────────────────────────────────────────────
    if args.tests.strip():
        ids      = [t.strip() for t in args.tests.split(",") if t.strip()]
        selected = [cls for cls in ALL_TESTS if cls.ID in ids]
        if not selected:
            print(f"[ERREUR] Aucun test trouvé pour IDs : {ids}", file=sys.stderr)
            sys.exit(2)
        print(f"[INFO] {len(selected)} test(s) sélectionné(s) : {ids}", flush=True)
    else:
        selected = list(ALL_TESTS)
        print(f"[INFO] {len(selected)} test(s) — campagne complète", flush=True)

    # ── 6. Gestionnaire SIGINT/SIGTERM (rapport partiel si interruption) ──
    t_start = datetime.datetime.now()

    def _on_interrupt(sig, frame):
        print("\n[CI] Interruption — génération rapport partiel…", flush=True)
        export_all(runner._results, t_start, datetime.datetime.now(), args, ts)
        sys.exit(130)

    signal.signal(signal.SIGINT,  _on_interrupt)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_interrupt)

    # ── 7. Exécution ──────────────────────────────────────────────────────
    runner = HeadlessTestRunner(
        can_worker, lin_worker, motor_worker,
        pump_signal  = pump_signal,
        rte_client   = rte_client,
        sim_client   = sim_client,
        fail_fast    = args.fail_fast,
    )

    results = runner.run(selected, global_timeout_s=args.timeout)
    t_end   = datetime.datetime.now()

    # ── 8. Export rapports ────────────────────────────────────────────────
    export_all(results, t_start, t_end, args, ts)

    # ── 9. Code de sortie ─────────────────────────────────────────────────
    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_to   = sum(1 for r in results if r.status == "TIMEOUT")

    if n_fail > 0 or n_to > 0:
        print(f"\n[CI] [FAIL]  FAIL={n_fail}  TIMEOUT={n_to}  →  exit 1", flush=True)
        sys.exit(1)
    else:
        print(f"\n[CI] [PASS]  Tous les tests PASS  →  exit 0", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
