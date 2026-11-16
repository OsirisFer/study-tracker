"""Qué hay en pantalla: app al frente, pestaña activa de Chrome e inactividad.

La pestaña llega por dos caminos:
  - la extensión de Chrome (preferido): avisa al servidor local cada vez que cambiás de pestaña
  - AppleScript (respaldo): si la extensión no reportó en el último minuto y medio
"""
import subprocess
import threading
import time

import Quartz
from AppKit import NSWorkspace

EXTENSION_FRESH_SECONDS = 90
APPLESCRIPT_EVERY = 2.0

_CHROME_SCRIPT = '''
tell application "Google Chrome"
    if (count of windows) is 0 then return ""
    set t to active tab of front window
    return (URL of t) & linefeed & (title of t)
end tell
'''


class TabStore:
    """Última pestaña reportada por la extensión (se escribe desde el hilo del servidor)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.url = ""
        self.title = ""
        self.updated = 0.0

    def update(self, url: str, title: str) -> None:
        with self.lock:
            self.url, self.title, self.updated = url, title, time.time()

    def get(self) -> tuple[str, str, float]:
        with self.lock:
            return self.url, self.title, self.updated


def frontmost_app() -> str:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()) if app else ""


def idle_seconds() -> float:
    """Segundos desde la última tecla o movimiento de mouse."""
    return Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType
    )


class ScreenMonitor:
    def __init__(self, tabs: TabStore, browsers: list[str]):
        self.tabs = tabs
        self.browsers = set(browsers)
        self._as_cache = ("", "")
        self._as_time = 0.0
        self.source = "—"

    def snapshot(self) -> tuple[str, str, str]:
        """(app, url, título). url/título solo si el navegador está al frente."""
        app = frontmost_app()
        if app not in self.browsers:
            return app, "", ""
        url, title, updated = self.tabs.get()
        if time.time() - updated < EXTENSION_FRESH_SECONDS:
            self.source = "extensión"
            return app, url, title
        self.source = "AppleScript"
        return (app, *self._applescript_tab())

    def _applescript_tab(self) -> tuple[str, str]:
        if time.time() - self._as_time < APPLESCRIPT_EVERY:
            return self._as_cache
        self._as_time = time.time()
        try:
            out = subprocess.run(["osascript", "-e", _CHROME_SCRIPT],
                                 capture_output=True, text=True, timeout=2).stdout
            url, _, title = out.strip().partition("\n")
            self._as_cache = (url, title)
        except Exception:
            pass
        return self._as_cache
