"""
splash_launcher.py — Écran d'accueil KPIT ControlDesk
======================================================
1. Affiche car_controldesk_v7_particles.html en PLEIN ÉCRAN.
2. L'utilisateur clique "WELCOME TO KPIT ControlDesk".
3. L'animation wiper se joue entièrement dans la splash.
4. À la fin de l'animation le HTML navigue vers kpit://launch.
5. On intercepte → on crée et affiche MainWindow IMMÉDIATEMENT.
6. Une fois MainWindow visible (200 ms après), on ferme la splash.

Lancement : python splash_launcher.py   (remplace main.py)
"""

import sys
from pathlib import Path

from PySide6.QtWidgets  import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore    import QWebEngineSettings, QWebEnginePage
from PySide6.QtCore  import QUrl, Qt, QTimer
from PySide6.QtGui   import QPalette, QColor

from constants    import W_BG, W_TEXT, W_PANEL, W_PANEL2, A_TEAL
from main_window  import MainWindow


# ─────────────────────────────────────────────────────────────────────────────
#  Page WebEngine — intercepte kpit://launch
# ─────────────────────────────────────────────────────────────────────────────
class _SplashPage(QWebEnginePage):
    def __init__(self, on_launch, parent=None):
        super().__init__(parent)
        self._on_launch = on_launch
        self._launched  = False

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "kpit" and url.host() == "launch":
            if not self._launched:
                self._launched = True
                QTimer.singleShot(0, self._on_launch)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source):
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Vue splash plein écran
# ─────────────────────────────────────────────────────────────────────────────
class SplashView(QWebEngineView):
    def __init__(self, html_path, on_launch_cb):
        super().__init__()
        self._page = _SplashPage(on_launch_cb, self)
        self.setPage(self._page)

        s = self._page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,               True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,   True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages,                  True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,     False)

        self._page.setBackgroundColor(QColor(5, 5, 8, 255))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.showFullScreen()
        self.load(QUrl.fromLocalFile(str(html_path)))


# ─────────────────────────────────────────────────────────────────────────────
#  Palette sombre (identique à main.py)
# ─────────────────────────────────────────────────────────────────────────────
def _apply_dark_palette(app):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(W_BG))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(W_TEXT))
    pal.setColor(QPalette.ColorRole.Base,            QColor(W_PANEL))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(W_PANEL2))
    pal.setColor(QPalette.ColorRole.Text,            QColor(W_TEXT))
    pal.setColor(QPalette.ColorRole.Button,          QColor(W_PANEL2))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(W_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(A_TEAL))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(W_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(W_TEXT))
    app.setPalette(pal)


def _set_dark_titlebar(widget):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd  = int(widget.winId())
        value = ctypes.c_int(1)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Point d'entrée
# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    _apply_dark_palette(app)

    html_path = Path(__file__).parent / "car_controldesk_v7_particles.html"

    state = {"splash": None}

    def _on_launch():
        # 1) Crée et affiche MainWindow IMMÉDIATEMENT (splash toujours visible)
        win = MainWindow()
        win.showMaximized()
        _set_dark_titlebar(win)

        # 2) Ferme la splash 200 ms après (le temps que MainWindow soit rendue)
        def _close_splash():
            s = state["splash"]
            if s:
                s.hide()
                s.close()
                s.deleteLater()
                state["splash"] = None

        QTimer.singleShot(200, _close_splash)

    if not html_path.exists():
        print(f"[splash_launcher] Fichier introuvable : {html_path}")
        win = MainWindow()
        win.showMaximized()
        _set_dark_titlebar(win)
        sys.exit(app.exec())
        return

    splash = SplashView(html_path, _on_launch)
    state["splash"] = splash

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
