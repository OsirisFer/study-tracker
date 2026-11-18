"""Motor: cada segundo junta pantalla + cámara, decide el estado y suma tiempo.

Estados:
  STUDYING    cuenta tiempo
  DISTRACTED  pantalla de distracción, celular o mirando a otro lado
  AWAY        no estás (cámara) o sin actividad / pantalla bloqueada
  BREAK       descanso pomodoro
  PAUSED      pausa manual
  IDLE        no hay materia elegida
"""
import time
from dataclasses import dataclass
from datetime import timedelta

from . import camera_monitor as cam
from . import insights
from .classifier import AWAY, DISTRACTION, NEUTRAL, STUDY, Classification

STUDYING, DISTRACTED, AWAY_STATE, BREAK, PAUSED, IDLE = (
    "STUDYING", "DISTRACTED", "AWAY", "BREAK", "PAUSED", "IDLE")

MAX_TICK = 5.0              # si la compu se durmió, no sumar el hueco
MISMATCH_AFTER = 180        # segundos con material de otra materia antes de avisar
MISMATCH_REPEAT = 1800


def decide(cls: Classification, camera_state: str, *, context_recent: bool, paused: bool,
           on_break: bool, has_subject: bool, idle_s: float, idle_limit: float) -> tuple[str, str]:
    """Función pura (fácil de testear): combina todas las señales en (estado, motivo)."""
    if paused:
        return PAUSED, "Pausado manualmente"
    if on_break:
        return BREAK, "Descanso"
    if not has_subject:
        return IDLE, "Elegí una materia en el menú"
    if cls.kind == AWAY:
        return AWAY_STATE, cls.reason
    if camera_state == cam.ABSENT:
        return AWAY_STATE, "No te veo frente a la compu"
    if camera_state in (cam.OFF, cam.UNAVAILABLE) and idle_s > idle_limit:
        return AWAY_STATE, f"Sin actividad hace {int(idle_s // 60)} min"
    if camera_state == cam.PHONE:
        return DISTRACTED, "Celular detectado"
    if camera_state == cam.LOOKING_AWAY:
        return DISTRACTED, "Mirando hacia otro lado"
    if cls.kind == DISTRACTION:
        return DISTRACTED, cls.reason
    if cls.kind == STUDY:
        return STUDYING, cls.reason
    if context_recent:
        return STUDYING, f"{cls.reason} (contexto de estudio)"
    return DISTRACTED, f"Sin material de estudio: {cls.reason}"


@dataclass
class Status:
    state: str = IDLE
    reason: str = ""
    subject: str | None = None
    app: str = ""
    title: str = ""
    camera: str = cam.OFF
    detected_subject: str | None = None
    break_left: float = 0.0
    focus_secs: float = 0.0


class Engine:
    def __init__(self, cfg, planner, classifier, screen, camera, storage, notify, idle_fn, syllabus=None):
        self.cfg = cfg
        self.planner = planner
        self.classifier = classifier
        self.screen = screen
        self.camera = camera
        self.storage = storage
        self.notify = notify
        self.idle_fn = idle_fn
        self.syllabus = syllabus

        pomo = cfg.get("pomodoro", {})
        self.pomodoro = pomo.get("enabled", False)
        self.focus_limit = pomo.get("focus_minutes", 50) * 60
        self.break_len = pomo.get("break_minutes", 10) * 60
        self.context_window = cfg.get("context_window_seconds", 300)
        self.notify_after = cfg.get("distraction_notify_after", 60)
        self.notify_repeat = cfg.get("distraction_notify_repeat", 600)
        self.idle_limit = cfg.get("idle_limit_seconds", 300)
        self.auto_switch = cfg.get("auto_switch_on_goal", True)
        hh, mm = str(cfg.get("daily_summary_time", "22:30")).split(":")
        self.summary_minute = int(hh) * 60 + int(mm)

        self.paused = False
        self.break_until: float | None = None
        self.focus_secs = 0.0
        self.last_study = 0.0
        self.last_tick = time.time()
        self.status = Status()

        self._distracted_since: float | None = None
        self._last_distraction_notice = 0.0
        self._away_since: float | None = None
        self._mismatch_since: float | None = None
        self._last_mismatch_notice = 0.0

        self._start_day(self.planner.study_day(), announce=False)

    # ── día ──────────────────────────────────────────────────
    def _start_day(self, day, announce=True) -> None:
        self.day = day
        self.totals = self.storage.totals_for_day(day)
        self.state_totals = self.storage.state_breakdown(day)
        self.targets = self.planner.targets(day)
        self.completed = {c for c, h in self.targets.items() if self.totals.get(c, 0) >= h * 3600}
        # en un día sin plan (antes del inicio) igual se cuenta, con la primera materia de mañana
        self.selected = (self.planner.next_subject(day, self.totals)
                         or next(iter(self.planner.targets(day + timedelta(days=1))), None))
        if announce and self.targets:
            plan = " · ".join(f"{self.planner.name(c)} {h:g}h" for c, h in self.targets.items())
            self.notify("☀️ Nuevo día de estudio", f"Hoy: {plan}")

    # ── metas por práctico ───────────────────────────────────
    def goal_summaries(self) -> list[dict]:
        if not self.syllabus:
            return []
        progress = self.storage.progress()
        out = []
        for code, exam in self.planner.exams.items():
            if exam >= self.day and (s := self.syllabus.summary(code, progress.get(code, {}), self.day)):
                out.append(s)
        return out

    def set_unit_done(self, subject: str, unit: str, done: bool) -> None:
        self.storage.set_progress(subject, unit, done)

    # ── resumen del día ──────────────────────────────────────
    def insights(self) -> dict:
        return insights.build(self.planner, self.storage, self.day)

    def _crossed_summary_time(self, prev: float, now: float) -> bool:
        """True en el tick en que el reloj pasa la hora del resumen (solo si la app estaba abierta)."""
        def minute(ts):
            t = time.localtime(ts)
            return t.tm_hour * 60 + t.tm_min
        return now - prev < 60 and minute(prev) < self.summary_minute <= minute(now)

    def summary(self) -> tuple[str, str]:
        """(resumen de hoy, plan de mañana)."""
        ins = self.insights()
        today = insights.summary_text(ins, sum(self.totals.values()), sum(self.targets.values()))
        tomorrow = self.planner.targets(self.day + timedelta(days=1))
        nxt = {s["code"]: s["next"] for s in self.goal_summaries()}
        plan = " · ".join(f"{self.planner.name(c)} {h:g}h" + (f" ({nxt[c]['id']})" if nxt.get(c) else "")
                          for c, h in tomorrow.items())
        return today, (f"Mañana: {plan}" if plan else "Mañana no hay plan")

    def send_summary(self) -> None:
        today, tomorrow = self.summary()
        self.notify("📊 Resumen del día", f"{today}. {tomorrow}")

    # ── acciones del menú ────────────────────────────────────
    def select(self, subject: str) -> None:
        self.selected = subject
        self._mismatch_since = None

    def toggle_pause(self) -> None:
        self.paused = not self.paused

    def skip_break(self) -> None:
        self.break_until = None
        self.focus_secs = 0

    def start_break(self) -> None:
        self.break_until = time.time() + self.break_len
        self.focus_secs = 0

    # ── tick ─────────────────────────────────────────────────
    def tick(self) -> Status:
        now = time.time()
        prev = self.last_tick
        dt = min(now - prev, MAX_TICK)
        self.last_tick = now

        today = self.planner.study_day()
        if today != self.day:
            self._start_day(today)

        app, url, title = self.screen.snapshot()
        cls = self.classifier.classify(app, url, title)
        if cls.kind == STUDY:
            self.last_study = now

        if self.break_until and now >= self.break_until:
            self.break_until = None
            self.notify("⏰ Fin del descanso", f"A seguir con {self.planner.name(self.selected)}")
        on_break = self.break_until is not None

        camera_state = self.camera.status.state if self.camera else cam.OFF
        state, reason = decide(
            cls, camera_state,
            context_recent=now - self.last_study <= self.context_window,
            paused=self.paused, on_break=on_break, has_subject=self.selected is not None,
            idle_s=self.idle_fn(), idle_limit=self.idle_limit)

        if state == STUDYING:
            self._count(dt)
            self._check_mismatch(cls.subject, now)
        else:
            self._mismatch_since = None
        self._handle_distraction(state, reason, now)
        self._handle_away(state, now)

        self.state_totals[state] = self.state_totals.get(state, 0) + dt
        self.storage.record(self.day, now, dt, state, self.selected, reason)
        if self._crossed_summary_time(prev, now):
            self.send_summary()
        self.status = Status(state, reason, self.selected, app, title, camera_state, cls.subject,
                             max(0.0, (self.break_until or now) - now), self.focus_secs)
        return self.status

    def _count(self, dt: float) -> None:
        subj = self.selected
        self.totals[subj] = self.totals.get(subj, 0) + dt
        self.focus_secs += dt

        target = self.targets.get(subj)
        if target and subj not in self.completed and self.totals[subj] >= target * 3600:
            self.completed.add(subj)
            nxt = self.planner.next_subject(self.day, self.totals)
            if nxt:
                self.notify(f"✅ ¡Completaste {self.planner.name(subj)}!",
                            f"Hiciste tus {target:g} h de hoy. Te toca {self.planner.name(nxt)} "
                            f"({self.targets[nxt]:g} h).")
                if self.auto_switch:
                    self.selected = nxt
            else:
                self.notify("🎉 ¡Día completo!", "Cumpliste todas las horas de hoy. Lo que sigas suma como extra.")

        if self.pomodoro and self.focus_secs >= self.focus_limit:
            self.start_break()
            self.notify("☕ Descanso", f"Llevás {self.focus_limit // 60} min. Tomate "
                        f"{self.break_len // 60} min (no cuentan como distracción).")

    def _handle_distraction(self, state: str, reason: str, now: float) -> None:
        if state != DISTRACTED:
            self._distracted_since = None
            return
        if self._distracted_since is None:
            self._distracted_since = now
        # un solo aviso por distracción larga, y como mucho uno cada `notify_repeat` segundos
        sustained = now - self._distracted_since >= self.notify_after
        if sustained and now - self._last_distraction_notice > self.notify_repeat:
            self.notify("⏸ Tiempo pausado", reason, sound="Funk")
            self._last_distraction_notice = now

    def _handle_away(self, state: str, now: float) -> None:
        # Una ausencia más larga que el descanso cuenta como descanso: se reinicia el pomodoro
        if state == AWAY_STATE:
            self._away_since = self._away_since or now
            if now - self._away_since >= self.break_len:
                self.focus_secs = 0
        else:
            self._away_since = None

    def _check_mismatch(self, detected: str | None, now: float) -> None:
        if not detected or detected == self.selected:
            self._mismatch_since = None
            return
        self._mismatch_since = self._mismatch_since or now
        if now - self._mismatch_since >= MISMATCH_AFTER and now - self._last_mismatch_notice > MISMATCH_REPEAT:
            self.notify("🔀 ¿Cambiaste de materia?",
                        f"Parece {self.planner.name(detected)}, pero la activa es "
                        f"{self.planner.name(self.selected)}. Cambiala desde el menú.")
            self._last_mismatch_notice = now
