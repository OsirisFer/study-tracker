"""Servidor HTTP local (solo 127.0.0.1).

  POST /tab          la extensión de Chrome reporta la pestaña activa
  GET  /             panel de estadísticas
  GET  /api/status   estado actual (se consulta cada 2 s desde el panel)
  GET  /api/stats    totales, historial, plan vs. real, distracciones
  GET  /api/goals    metas por práctico (plan vs. completados)
  POST /api/progress marcar/desmarcar un práctico como completo
  GET  /camera.mjpg  video de la cámara con las detecciones (solo mientras lo mirás)
"""
import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import insights

DASHBOARD = Path(__file__).with_name("dashboard.html")


def build_stats(engine) -> dict:
    planner, storage, day = engine.planner, engine.storage, engine.day
    names = {c: planner.name(c) for c in planner.subjects}

    history = {}
    for d, subj, secs in storage.daily_totals(planner.start_date):
        history.setdefault(d, {})[subj] = round(secs / 3600, 2)
    days, cursor = [], planner.start_date
    while cursor <= day:
        iso = cursor.isoformat()
        days.append({"day": iso, "hours": history.get(iso, {}),
                     "target": planner.targets(cursor)})
        cursor += timedelta(days=1)

    planned = planner.planned_cumulative(day)
    actual = storage.subject_totals(planner.start_date)  # _query vuelca el segmento actual antes

    return {
        "names": names,
        "emojis": {c: planner.emoji(c) for c in planner.subjects},
        "today": {
            "day": day.isoformat(),
            "targets": engine.targets,
            "totals": {c: round(s / 3600, 3) for c, s in engine.totals.items()},
            "states": {k: round(v / 3600, 2) for k, v in storage.state_breakdown(day).items()},
        },
        "history": days,
        "cumulative": {c: {"planned": round(planned.get(c, 0), 1),
                           "actual": round(actual.get(c, 0) / 3600, 1)} for c in planner.subjects},
        "exams": [{"code": c, "name": names.get(c, c), "date": d.isoformat(), "days_left": n}
                  for c, d, n in planner.upcoming_exams(day)],
        "insights": engine.insights(),
        "log": insights.history(planner, storage, day),
        "distractions": [{"reason": r, "minutes": round(s / 60, 1), "times": n}
                         for r, s, n in storage.top_distractions(day - timedelta(days=6))],
    }


def build_status(engine) -> dict:
    s = engine.status
    return {
        "state": s.state, "reason": s.reason, "subject": s.subject,
        "subject_name": engine.planner.name(s.subject), "camera": s.camera,
        "detected": s.detected_subject, "break_left": round(s.break_left),
        "focus_secs": round(s.focus_secs), "source": engine.screen.source,
        "today": {c: round(sec) for c, sec in engine.totals.items()},
        "targets": engine.targets,
        "states_today": {k: round(v) for k, v in engine.state_totals.items()},
    }


def make_handler(tabs, engine_ref):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silencio en la terminal
            pass

        def _send(self, code: int, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data):
            self._send(200, json.dumps(data, ensure_ascii=False).encode())

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, b"{}")
            if self.path == "/tab":
                tabs.update(str(data.get("url", "")), str(data.get("title", "")))
                return self._send(204, b"")
            engine = engine_ref()
            if self.path == "/api/progress" and engine:
                engine.set_unit_done(str(data["subject"]), str(data["unit"]), bool(data.get("done")))
                return self._json(engine.goal_summaries())
            self._send(404, b"{}")

        def do_GET(self):
            engine = engine_ref()
            self.path = urlparse(self.path).path  # ignorar ?t=... (evita la caché del navegador)
            if self.path in ("/", "/index.html"):
                return self._send(200, DASHBOARD.read_bytes(), "text/html; charset=utf-8")
            if engine is None:
                return self._send(503, b"{}")
            if self.path == "/api/status":
                return self._json(build_status(engine))
            if self.path == "/api/stats":
                return self._json(build_stats(engine))
            if self.path == "/api/goals":
                return self._json(engine.goal_summaries())
            if self.path == "/camera.mjpg":
                return self._stream_camera(engine.camera)
            self._send(404, b"{}")

        def _stream_camera(self, camera):
            """MJPEG: una respuesta HTTP que nunca termina, con un JPEG tras otro.
            Un <img src="/camera.mjpg"> lo muestra como video. Al cerrar la vista se corta."""
            if camera is None or not camera.enabled.is_set():
                return self._send(404, b"{}")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while camera.enabled.is_set():
                    jpg = camera.preview_jpeg()
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    time.sleep(0.12)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def start_server(port: int, tabs, engine_ref) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(tabs, engine_ref))
    threading.Thread(target=server.serve_forever, daemon=True, name="server").start()
    return server
