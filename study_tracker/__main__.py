"""Punto de entrada.

  python -m study_tracker               → app en la barra de menú
  python -m study_tracker camera-test   → ventana de prueba de la cámara
  python -m study_tracker plan          → imprime el plan día por día
"""
import sys
from datetime import timedelta

from .config import DATA_DIR, load_config
from .planner import Planner


def print_plan(cfg: dict) -> None:
    planner = Planner(cfg)
    day = planner.start_date
    last = max(planner.exams.values())
    while day <= last:
        targets = planner.targets(day)
        plan = "  ".join(f"{planner.name(c)} {h:g}h" for c, h in targets.items())
        exam = next((f"   ← EXAMEN {planner.name(c)}" for c, d in planner.exams.items() if d == day), "")
        print(f"{day:%a %d/%m}  {plan}{exam}")
        day += timedelta(days=1)


def run_app(cfg: dict) -> None:
    from .camera_monitor import CameraMonitor
    from .classifier import Classifier, OllamaClassifier
    from .engine import Engine
    from .menubar import MenuBarApp
    from .notifier import notify
    from .screen_monitor import ScreenMonitor, TabStore, idle_seconds
    from .server import start_server
    from .storage import Storage
    from .syllabus import Syllabus

    DATA_DIR.mkdir(exist_ok=True)
    planner = Planner(cfg)
    tabs = TabStore()
    engine_box: list = [None]
    port = cfg.get("server_port", 8765)
    try:
        start_server(port, tabs, lambda: engine_box[0])
    except OSError:
        notify("📚 Study Tracker", "Ya está abierta (mirá el ícono en la barra de menú).")
        print(f"Study Tracker ya está corriendo (puerto {port} ocupado).")
        return

    camera = None
    if cfg["camera"].get("enabled", True):
        camera = CameraMonitor(cfg["camera"])
        camera.start()

    llm = OllamaClassifier(cfg.get("llm", {}), cfg["subjects"])
    engine = Engine(cfg, planner, Classifier(cfg, llm), ScreenMonitor(tabs, cfg["rules"]["browsers"]),
                    camera, Storage(DATA_DIR / "study.db"), notify, idle_seconds, Syllabus.load(planner))
    engine_box[0] = engine

    if engine.targets:
        plan = " · ".join(f"{planner.name(c)} {h:g}h" for c, h in engine.targets.items())
        notify("📚 Study Tracker", f"Hoy: {plan}. Arrancás con {planner.name(engine.selected)}.", sound=None)
    print(f"Study Tracker corriendo. Panel: http://127.0.0.1:{port}/")
    MenuBarApp(engine, port, camera).run()


def main() -> None:
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "camera-test":
        from .camera_monitor import camera_test
        camera_test(cfg["camera"])
    elif cmd == "plan":
        print_plan(cfg)
    else:
        run_app(cfg)


if __name__ == "__main__":
    main()
