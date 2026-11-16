"""Ícono en la barra de menú de macOS (librería rumps)."""
import subprocess
import webbrowser

import rumps

from .config import EXTENSION_DIR
from .engine import AWAY_STATE, BREAK, DISTRACTED, IDLE, PAUSED, STUDYING

DOTS = {STUDYING: "🟢", DISTRACTED: "🔴", AWAY_STATE: "🟡", BREAK: "☕", PAUSED: "⏸", IDLE: "⚪"}


def hm(secs: float) -> str:
    m = int(secs // 60)
    return f"{m // 60}:{m % 60:02d}"


class MenuBarApp(rumps.App):
    def __init__(self, engine, port: int, camera):
        super().__init__("Study Tracker", title="📚", quit_button=None)
        self.engine = engine
        self.port = port
        self.camera = camera

        self.status_item = rumps.MenuItem("…")
        self.subject_items = {
            code: rumps.MenuItem(code, callback=self._make_select(code))
            for code in engine.planner.subjects
        }
        self.pause_item = rumps.MenuItem("Pausar", callback=self.on_pause)
        self.break_item = rumps.MenuItem("Tomar descanso ahora", callback=self.on_break)
        self.camera_item = rumps.MenuItem("Cámara", callback=self.on_camera)
        self.menu = [
            self.status_item, None,
            *self.subject_items.values(), None,
            self.pause_item, self.break_item, self.camera_item, None,
            rumps.MenuItem("Abrir panel de estadísticas", callback=self.on_dashboard),
            rumps.MenuItem("Instalar extensión de Chrome…", callback=self.on_extension), None,
            rumps.MenuItem("Salir", callback=self.on_quit),
        ]
        self.timer = rumps.Timer(self.on_tick, 1)
        self.timer.start()

    def _make_select(self, code):
        def select(_):
            self.engine.select(code)
            self.refresh()
        return select

    def on_tick(self, _):
        self.engine.tick()
        self.refresh()

    def refresh(self):
        e, s = self.engine, self.engine.status
        planner = e.planner
        done = e.totals.get(s.subject, 0)
        target = e.targets.get(s.subject)
        if s.state == BREAK:
            self.title = f"☕ {hm(s.break_left)}"
        elif s.subject:
            goal = f"/{hm(target * 3600)}" if target else ""
            self.title = f"{DOTS[s.state]} {planner.emoji(s.subject)} {hm(done)}{goal}"
        else:
            self.title = "📚"

        reason = s.reason if len(s.reason) <= 60 else s.reason[:57] + "…"
        self.status_item.title = f"{DOTS[s.state]} {reason}"
        for code, item in self.subject_items.items():
            t = e.targets.get(code)
            got = e.totals.get(code, 0)
            check = " ✅" if t and got >= t * 3600 else ""
            goal = f" / {hm(t * 3600)}" if t else " (sin plan hoy)"
            item.title = f"{planner.emoji(code)} {planner.name(code)}   {hm(got)}{goal}{check}"
            item.state = int(code == s.subject)
        self.pause_item.title = "Reanudar" if e.paused else "Pausar"
        self.break_item.title = "Saltar descanso" if s.state == BREAK else "Tomar descanso ahora"
        cam_on = self.camera is not None and self.camera.enabled.is_set()
        self.camera_item.title = f"Cámara: {'activada' if cam_on else 'apagada'}"

    def on_pause(self, _):
        self.engine.toggle_pause()
        self.refresh()

    def on_break(self, _):
        if self.engine.status.state == BREAK:
            self.engine.skip_break()
        else:
            self.engine.start_break()
        self.refresh()

    def on_camera(self, _):
        if self.camera is None:
            rumps.alert("La cámara está deshabilitada en config.yaml (camera.enabled).")
            return
        self.camera.set_enabled(not self.camera.enabled.is_set())
        self.refresh()

    def on_dashboard(self, _):
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def on_extension(self, _):
        subprocess.Popen(["open", str(EXTENSION_DIR)])
        subprocess.Popen(["open", "-a", "Google Chrome", "chrome://extensions"])
        rumps.alert(
            "Instalar la extensión",
            "1. En chrome://extensions activá «Modo de desarrollador» (arriba a la derecha).\n"
            "2. Clic en «Cargar descomprimida».\n"
            f"3. Elegí la carpeta:\n{EXTENSION_DIR}")

    def on_quit(self, _):
        self.engine.storage.flush()
        rumps.quit_application()
