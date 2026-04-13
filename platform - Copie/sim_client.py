"""
sim_client.py  —  Client TCP vers le RPi Simulateur (port 5000)
================================================================
Permet à la Platform Qt d'envoyer des commandes d'injection de défauts
directement au RPi Simulateur, qui les applique physiquement via GPIO
sur la POMPE uniquement.

Architecture :
  PC (Platform Qt)
  └── SimClient  ─── TCP → RPi Simulateur :5000
                        └── sim_control.py → GPIO
                              ├── PIN_ISO   (GPIO18)  -- protection ADS1115
                              ├── PIN_MUX_A (GPIO6), PIN_MUX_B (GPIO13)
                              ├── PIN_Y0_GATE (GPIO5)  -- OPEN LOAD / SHORT TO GND
                              ├── PIN_Y2_BASE (GPIO16) -- SHORT TO VCC
                              └── PIN_VLOAD_PWM (GPIO26) -- VARIABLE LOAD

  [POMPE_ONLY] Cible MOTEUR = lecture seule via POT (ADS1115 A3)
               Aucun signal GPIO ne va vers le moteur depuis sim_control.

Commandes supportées (JSON sur TCP port 5000) :
  {"pump_fault_mode": "NORMAL"}
  {"pump_fault_mode": "OPEN LOAD"}
  {"pump_fault_mode": "SHORT TO VCC"}
  {"pump_fault_mode": "SHORT TO GND"}
  {"pump_fault_mode": "VARIABLE LOAD", "duty_cycle": 50.0}
  {"pump_fault_target": "PUMP"}   (MOTOR accepté mais ignoré = pas d'injection)
"""

import json
import socket
import threading


class SimClient:
    """
    Client TCP léger vers le RPi Simulateur.
    Toutes les méthodes sont non-bloquantes (timeout 2s).
    Si le simulateur est injoignable, les appels sont silencieusement ignorés.
    """

    def __init__(self, host: str = "", port: int = 5000):
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._connected = False

    # ----------------------------------------------------------
    # Connexion / état
    # ----------------------------------------------------------

    def connect(self, host: str, port: int = 5000) -> bool:
        """
        Enregistre l'hôte du simulateur.
        La connexion est établie à chaque envoi (TCP stateless léger).
        """
        with self._lock:
            self._host = host
            self._port = port
            self._connected = bool(host)
        if self._connected:
            print(f"[SIM-CLIENT] Simulateur configuré: {host}:{port}")
        return self._connected

    def disconnect(self):
        with self._lock:
            self._host = ""
            self._connected = False
        print("[SIM-CLIENT] Déconnecté")

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and bool(self._host)

    @property
    def host(self) -> str:
        with self._lock:
            return self._host

    # ----------------------------------------------------------
    # Envoi de commandes
    # ----------------------------------------------------------

    def send_fault(self, pump_fault_mode: str = None,
                   pump_fault_target: str = None,
                   duty_cycle: float = None) -> bool:
        """
        Envoie une commande d'injection de défaut au Simulateur.

        Args:
            pump_fault_mode:   "NORMAL" | "OPEN LOAD" | "SHORT TO VCC" |
                               "VARIABLE LOAD" | "SHORT TO GND"
            pump_fault_target: "PUMP"  (MOTOR = lecture seule via POT, pas d'injection)
            duty_cycle:        0.0 .. 100.0  -- duty PWM pour VARIABLE LOAD uniquement

        Returns:
            True si la commande a été envoyée avec succès.
        """
        if not self.is_connected():
            return False

        payload = {}
        if pump_fault_mode is not None:
            payload["pump_fault_mode"] = pump_fault_mode
        if pump_fault_target is not None:
            payload["pump_fault_target"] = pump_fault_target
        if duty_cycle is not None:
            payload["duty_cycle"] = round(float(duty_cycle), 1)

        if not payload:
            return False

        return self._send(payload)

    def send_blade_sim(self, value: float) -> bool:
        """
        Injecte une position cible (blade_sim) dans bcmcan du simulateur WC.
        value = -1.0  -> desactive la comparaison (aucun test B2103 actif).
        value = 0..100 -> active la comparaison avec blade_real (potentiometre).
        Un ecart |blade_real - value| > 10 % pendant > 1 000 ms declenche B2103.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_blade_sim",
                           "value":    round(float(value), 1)})

    def reset_b2103(self) -> bool:
        """
        Reinitialise cote simulateur :
          - guard anti-rearmement _b2103_active = False
          - timer interne _b2103_mismatch_start = 0.0
          - blade_sim = -1.0 (comparaison desactivee)
        A appeler avant et apres chaque execution de TC_B2103.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_b2103"})

    def reset_b2101(self) -> bool:
        """
        Reinitialise B2101 (CAN Timeout WC) cote simulateur bcmcan :
          - _t_last_0x200 = time.time() → elapsed = 0 → B2101 se désactive
          - _can_timeout_b2011 = False
          - DTC B2101 mis inactive via wc_doip._dtc_mgr
        A appeler dans le post_test T50 apres que wc_available=False
        soit remis, pour eviter que B2101 persiste sur les tests suivants.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_b2101"})

    def _send(self, payload: dict) -> bool:
        """Envoi TCP non-bloquant avec timeout."""
        host, port = self._host, self._port
        try:
            with socket.create_connection((host, port), timeout=2.0) as s:
                s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                # Lire l'ACK optionnel (non-bloquant)
                s.settimeout(0.5)
                try:
                    raw = s.recv(256).decode("utf-8", errors="replace").strip()
                    if raw:
                        try:
                            ack = json.loads(raw.split("\n")[0])
                            print(f"[SIM-CLIENT] ACK reçu: {ack}")
                        except json.JSONDecodeError:
                            pass
                except socket.timeout:
                    pass  # ACK optionnel
            return True
        except OSError as e:
            print(f"[SIM-CLIENT] Erreur envoi vers {host}:{port} -- {e}")
            with self._lock:
                self._connected = False
            return False
