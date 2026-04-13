#!/usr/bin/env python3
"""
external_test_runner.py  —  Runner CI headless pour WipeWash Platform
======================================================================
Exécute les tests automatiques SANS interface Qt (mode headless).
Conçu pour GitLab CI : se connecte au BCM (Redis) et au Simulateur (TCP),
lance tous les tests, puis génère un rapport JUnit XML + JSON.

Usage :
    python external_test_runner.py \
        --bcm-host 192.168.1.10 \
        --sim-host 192.168.1.20 \
        --output reports/ \
        [--tests T01,T02,T30] \
        [--timeout 60]

Variables d'environnement (GitLab CI) :
    BCM_HOST      : IP du RPi BCM  (ex: 192.168.1.10)
    SIM_HOST      : IP du RPi Sim  (ex: 192.168.1.20)
    TEST_IDS      : liste séparée par virgules (vide = tous)
    TEST_TIMEOUT  : timeout global par test en secondes (défaut: 45)
"""

import argparse
import json
import os
import sys
import time
import threading
import socket
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import xml.etree.ElementTree as ET

# ── Ajout du répertoire platform au PYTHONPATH ─────────────────────────────
PLATFORM_DIR = Path(__file__).parent / "platform - Copie"
sys.path.insert(0, str(PLATFORM_DIR))

# ── Import des modules platform (sans Qt) ─────────────────────────────────
try:
    from test_cases import BaseTest, BaseBCMTest, TestResult, ALL_TESTS
    from rte_client import RTEClient
    from sim_client import SimClient
except ImportError as e:
    print(f"[ERREUR] Import platform échoué : {e}", file=sys.stderr)
    print(f"  Assurez-vous que 'platform - Copie/' est dans le même dossier.", file=sys.stderr)
    sys.exit(2)


# ══════════════════════════════════════════════════════════════════════════════
#  Stub workers  (remplacent les workers Qt CAN/LIN/Motor)
# ══════════════════════════════════════════════════════════════════════════════

class _SignalStub:
    """Simule un Signal Qt avec connect/emit sans Qt."""

    def __init__(self):
        self._callbacks = []

    def connect(self, cb, *args, **kwargs):
        self._callbacks.append(cb)

    def emit(self, *args):
        for cb in self._callbacks:
            try:
                cb(*args)
            except Exception:
                pass


class _WorkerStub:
    """Stub minimaliste qui expose les mêmes signaux que les vrais workers Qt."""

    def __init__(self):
        self.can_received   = _SignalStub()
        self.lin_received   = _SignalStub()
        self.motor_received = _SignalStub()
        self.data_received  = _SignalStub()   # pour pump_signal


class _PumpSignalStub:
    def __init__(self):
        self.data_received = _SignalStub()


# ══════════════════════════════════════════════════════════════════════════════
#  HeadlessTestRunner  —  clone synchrone de TestRunner sans Qt
# ══════════════════════════════════════════════════════════════════════════════

class HeadlessTestRunner:
    """
    Exécute les tests séquentiellement dans un thread dédié.
    Remplace QTimer par threading.Event + boucle manuelle.
    """

    def __init__(self, rte_client: Optional[RTEClient],
                 sim_client: Optional[SimClient],
                 tick_interval: float = 0.2,
                 log_fn=None):
        self._rte    = rte_client
        self._sim    = sim_client
        self._tick   = tick_interval
        self._log    = log_fn or print

        # Workers stubs
        self._can_w   = _WorkerStub()
        self._lin_w   = _WorkerStub()
        self._motor_w = _WorkerStub()
        self._pump    = _PumpSignalStub()

        self.results: List[TestResult] = []
        self._stop_event = threading.Event()

    # ── Injection de frames simulées ──────────────────────────────────────
    def inject_can(self, frame: dict):
        self._can_w.can_received.emit(frame)

    def inject_lin(self, frame: dict):
        self._lin_w.lin_received.emit(frame)

    def inject_motor(self, data: dict):
        self._motor_w.motor_received.emit(data)

    # ── Exécution ─────────────────────────────────────────────────────────
    def run(self, test_ids: Optional[List[str]] = None) -> List[TestResult]:
        """Lance les tests sélectionnés (ou tous) et retourne les résultats."""
        if test_ids:
            queue = [cls() for cls in ALL_TESTS if cls.ID in test_ids]
        else:
            queue = [cls() for cls in ALL_TESTS]

        self.results = []
        self._log(f"\n{'═'*60}")
        self._log(f"  WipeWash CI — {len(queue)} test(s) à exécuter")
        self._log(f"  BCM  : {self._rte._host if self._rte else 'N/A (stub)'}")
        self._log(f"  Sim  : {self._sim._host if self._sim else 'N/A (stub)'}")
        self._log(f"{'═'*60}\n")

        for idx, test in enumerate(queue, 1):
            result = self._run_one(test, idx, len(queue))
            self.results.append(result)
            if self._stop_event.is_set():
                break

        n_pass = sum(1 for r in self.results if r.status == "PASS")
        n_fail = sum(1 for r in self.results if r.status == "FAIL")
        n_to   = sum(1 for r in self.results if r.status == "TIMEOUT")
        self._log(f"\n{'═'*60}")
        self._log(f"  RÉSUMÉ : {n_pass} PASS  {n_fail} FAIL  {n_to} TIMEOUT")
        self._log(f"{'═'*60}")

        return self.results

    def _run_one(self, test: BaseTest, idx: int, total: int) -> TestResult:
        """Exécute un test unique avec sa boucle de supervision."""
        self._log(f"[{idx:02d}/{total:02d}] ▶ {test.ID:15s} {test.NAME}")

        # Injecter rte_client pour les tests BCM
        if isinstance(test, BaseBCMTest) and self._rte:
            BaseBCMTest.rte_client = self._rte

        # Connecter les signaux workers → callbacks du test
        self._can_w.can_received.connect(
            lambda ev: self._dispatch(test, "can", ev))
        self._lin_w.lin_received.connect(
            lambda ev: self._dispatch(test, "lin", ev))
        self._motor_w.motor_received.connect(
            lambda data: self._dispatch(test, "motor", data))

        # Reset RTE avant chaque test BCM
        if isinstance(test, BaseBCMTest) and self._rte:
            try:
                self._rte.set_cmd("wc_timeout_active",  False)
                self._rte.set_cmd("lin_timeout_active", False)
                self._rte.set_cmd("crs_wiper_op", 0)
                self._rte.set_cmd("ignition_status", 1)
                self._rte.set_cmd("wc_available", False)
                time.sleep(0.15)
            except Exception:
                pass

        test.start()
        self._result_holder = None
        deadline = time.time() + test.TEST_TIMEOUT_S

        while time.time() < deadline:
            if self._result_holder is not None:
                break
            # check_timeout du test
            to_result = test.check_timeout()
            if to_result:
                self._result_holder = to_result
                break
            # Pour les tests BCM, vérifier _check_rte si disponible
            if hasattr(test, "_check_rte") and callable(test._check_rte):
                rte_result = test._check_rte()
                if rte_result:
                    self._result_holder = rte_result
                    break
            time.sleep(self._tick)

        if self._result_holder is None:
            self._result_holder = TestResult(
                test_id=test.ID, name=test.NAME,
                category=test.CATEGORY, ref=test.REF,
                status="TIMEOUT", limit=test.LIMIT_STR,
                measured="—",
                details=f"Timeout global {test.TEST_TIMEOUT_S}s dépassé"
            )

        r = self._result_holder
        icon = "✅" if r.status == "PASS" else ("❌" if r.status == "FAIL" else "⚠️")
        self._log(f"         {icon} {r.status:8s}  mesure={r.measured}  limite={r.limit}")
        if r.details:
            self._log(f"              {r.details}")

        # Cleanup RTE
        if isinstance(test, BaseBCMTest) and self._rte:
            try:
                self._rte.set_cmd("crs_wiper_op", 0)
                self._rte.set_cmd("ignition_status", 1)
                time.sleep(0.1)
            except Exception:
                pass

        return r

    def _dispatch(self, test, kind, data):
        if self._result_holder is not None:
            return
        try:
            if kind == "can":
                r = test.on_can_frame(data)
            elif kind == "lin":
                r = test.on_lin_frame(data)
            else:
                r = test.on_motor_data(data)
            if r:
                self._result_holder = r
        except Exception:
            pass

    def stop(self):
        self._stop_event.set()


# ══════════════════════════════════════════════════════════════════════════════
#  Export des rapports
# ══════════════════════════════════════════════════════════════════════════════

def export_junit(results: List[TestResult], out_path: Path, suite_name="WipeWash"):
    """Génère un rapport JUnit XML compatible GitLab CI / Jenkins."""
    n_fail = sum(1 for r in results if r.status in ("FAIL", "TIMEOUT"))
    total  = len(results)
    ts     = datetime.utcnow().isoformat()

    suite = ET.Element("testsuite",
                        name=suite_name,
                        tests=str(total),
                        failures=str(n_fail),
                        errors="0",
                        time=str(total * 5),
                        timestamp=ts)

    for r in results:
        case = ET.SubElement(suite, "testcase",
                             classname=r.category,
                             name=f"{r.test_id} — {r.name}",
                             time="5")
        if r.status == "PASS":
            pass  # pas de sous-élément → succès JUnit
        elif r.status == "FAIL":
            f = ET.SubElement(case, "failure",
                              message=f"FAIL — mesuré={r.measured} limite={r.limit}")
            f.text = r.details
        else:  # TIMEOUT
            e = ET.SubElement(case, "error",
                              message=f"TIMEOUT — {r.details}")
            e.text = r.details

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out_path), encoding="utf-8", xml_declaration=True)
    print(f"[CI] JUnit XML → {out_path}")


def export_json(results: List[TestResult], out_path: Path):
    """Génère un rapport JSON structuré (pour artefact GitLab)."""
    payload = {
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "total":   len(results),
            "pass":    sum(1 for r in results if r.status == "PASS"),
            "fail":    sum(1 for r in results if r.status == "FAIL"),
            "timeout": sum(1 for r in results if r.status == "TIMEOUT"),
        },
        "tests": [asdict(r) for r in results],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"[CI] JSON       → {out_path}")


def export_text_summary(results: List[TestResult], out_path: Path):
    """Génère un résumé texte lisible dans les logs GitLab."""
    lines = ["WipeWash — Rapport de tests automatiques",
             f"Généré le : {datetime.utcnow().isoformat()}",
             "=" * 70]
    for r in results:
        icon = "PASS" if r.status == "PASS" else r.status
        lines.append(
            f"{icon:8s} {r.test_id:15s} {r.name[:35]:35s} "
            f"mesuré={r.measured:12s} limite={r.limit}"
        )
        if r.details:
            lines.append(f"         → {r.details}")
    lines += ["=" * 70,
              f"PASS={sum(1 for r in results if r.status=='PASS')} "
              f"FAIL={sum(1 for r in results if r.status=='FAIL')} "
              f"TIMEOUT={sum(1 for r in results if r.status=='TIMEOUT')}"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[CI] Résumé TXT → {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
#  Vérification connectivité (pré-flight)
# ══════════════════════════════════════════════════════════════════════════════

def check_host(host: str, port: int, timeout: float = 3.0) -> bool:
    """Tente une connexion TCP pour vérifier que l'hôte est joignable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Arguments CLI ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="WipeWash — Runner de tests externe headless (GitLab CI)")
    parser.add_argument("--bcm-host",
                        default=os.environ.get("BCM_HOST", ""),
                        help="IP du RPi BCM (Redis port 6379)")
    parser.add_argument("--sim-host",
                        default=os.environ.get("SIM_HOST", ""),
                        help="IP du RPi Simulateur (TCP port 5000)")
    parser.add_argument("--tests",
                        default=os.environ.get("TEST_IDS", ""),
                        help="IDs de tests séparés par virgules (vide = tous)")
    parser.add_argument("--output",
                        default=os.environ.get("REPORT_DIR", "reports"),
                        help="Répertoire de sortie des rapports")
    parser.add_argument("--timeout",
                        type=int,
                        default=int(os.environ.get("TEST_TIMEOUT", "45")),
                        help="Timeout par test en secondes (défaut: 45)")
    parser.add_argument("--dry-run",
                        action="store_true",
                        help="Exécute sans connexion matérielle (tests = TIMEOUT attendu)")
    args = parser.parse_args()

    out_dir = Path(args.output)
    test_ids = [t.strip() for t in args.tests.split(",") if t.strip()] or None

    # ── Connexion BCM ─────────────────────────────────────────────────────────
    rte_client: Optional[RTEClient] = None
    if args.bcm_host and not args.dry_run:
        print(f"[CI] Connexion BCM Redis  {args.bcm_host}:6379 …")
        if check_host(args.bcm_host, 6379):
            rte_client = RTEClient(host=args.bcm_host)
            if not rte_client.is_connected():
                print("[CI] ⚠ Redis injoignable — tests BCM seront TIMEOUT")
                rte_client = None
        else:
            print("[CI] ⚠ BCM host injoignable — tests BCM seront TIMEOUT")
    elif args.dry_run:
        print("[CI] Mode dry-run — aucune connexion matérielle")

    # ── Connexion Simulateur ──────────────────────────────────────────────────
    sim_client: Optional[SimClient] = None
    if args.sim_host and not args.dry_run:
        print(f"[CI] Connexion Sim  TCP  {args.sim_host}:5000 …")
        if check_host(args.sim_host, 5000):
            sim_client = SimClient()
            sim_client.connect(args.sim_host, 5000)
        else:
            print("[CI] ⚠ Sim host injoignable — injections défauts désactivées")

    # ── Overrider le timeout global si spécifié ───────────────────────────────
    if args.timeout != 45:
        for cls in ALL_TESTS:
            cls.TEST_TIMEOUT_S = args.timeout

    # ── Lancement des tests ───────────────────────────────────────────────────
    runner = HeadlessTestRunner(
        rte_client=rte_client,
        sim_client=sim_client,
        log_fn=print
    )

    start_ts = time.time()
    results = runner.run(test_ids=test_ids)
    elapsed = time.time() - start_ts

    print(f"\n[CI] Durée totale : {elapsed:.1f}s")

    # ── Export rapports ───────────────────────────────────────────────────────
    export_junit(results, out_dir / "junit.xml")
    export_json(results,  out_dir / "results.json")
    export_text_summary(results, out_dir / "summary.txt")

    # ── Code de retour (0 = tous PASS, 1 = au moins un FAIL/TIMEOUT) ─────────
    n_fail = sum(1 for r in results if r.status != "PASS")
    if n_fail > 0:
        print(f"\n[CI] ❌ {n_fail} test(s) en échec — exit code 1")
        sys.exit(1)
    else:
        print(f"\n[CI] ✅ Tous les tests PASS — exit code 0")
        sys.exit(0)


if __name__ == "__main__":
    main()
