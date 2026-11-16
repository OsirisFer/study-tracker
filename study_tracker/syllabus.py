"""Metas por práctico.

Idea: el curso le dedica X semanas a cada práctico (hoja de ruta del EVA). Esos pesos se
reparten en TUS horas planificadas hasta el examen (sin contar los días de repaso), y cada
práctico recibe una fecha meta: el día en que, siguiendo el plan, ya deberías haberlo terminado.

Estado de una materia según el primer práctico que te falta:
  - su meta ya pasó                        → atrasado N días
  - todavía no "empezó" según el plan      → adelantado N días
  - si no                                  → al día
"""
from dataclasses import dataclass
from datetime import date, timedelta

import yaml

from .config import ROOT

SYLLABUS_PATH = ROOT / "syllabus.yaml"


@dataclass
class Unit:
    id: str
    name: str
    weight: float
    start: date      # día en que, según el plan, empezás este práctico
    due: date        # día en que ya deberías tenerlo terminado
    done_at: float | None = None


class Syllabus:
    def __init__(self, data: dict, planner):
        self.planner = planner
        self.review_days = data.get("review_days", {})
        self.courses = {c: v for c, v in data.items() if c in planner.subjects}

    @classmethod
    def load(cls, planner, path=SYLLABUS_PATH) -> "Syllabus":
        if not path.exists():
            return cls({}, planner)
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {}, planner)

    def study_days(self, code: str) -> list[tuple[date, float]]:
        """(día, horas planificadas) desde el inicio hasta el examen, sin los días de repaso."""
        exam = self.planner.exams.get(code)
        if not exam:
            return []
        days, d = [], self.planner.start_date
        while d < exam:
            h = self.planner.targets(d).get(code, 0)
            if h > 0:
                days.append((d, h))
            d += timedelta(days=1)
        review = self.review_days.get(code, 0)
        return days[:-review] if review and len(days) > review else days

    def review_start(self, code: str) -> date | None:
        days = self.study_days(code)
        return days[-1][0] + timedelta(days=1) if days else None

    def units(self, code: str, done: dict[str, float]) -> list[Unit]:
        course = self.courses.get(code)
        days = self.study_days(code)
        if not course or not days:
            return []
        total_h = sum(h for _, h in days)
        total_w = sum(u["weeks"] for u in course["units"])

        # horas acumuladas al final de cada día
        cum, acc = [], 0.0
        for d, h in days:
            acc += h
            cum.append((d, acc))

        def day_reaching(frac: float) -> date:
            need = frac * total_h - 1e-6
            return next((d for d, a in cum if a >= need), days[-1][0])

        out, acc_w = [], 0.0
        for u in course["units"]:
            start = day_reaching(acc_w / total_w) if acc_w else days[0][0]
            acc_w += u["weeks"]
            out.append(Unit(str(u["id"]), u["name"], u["weeks"] / total_w, start,
                            day_reaching(acc_w / total_w), done.get(str(u["id"]))))
        return out

    def summary(self, code: str, done: dict[str, float], today: date) -> dict | None:
        units = self.units(code, done)
        if not units:
            return None
        completed = sum(u.weight for u in units if u.done_at)
        days = self.study_days(code)
        total_h = sum(h for _, h in days)
        planned_h = sum(h for d, h in days if d <= today)
        pending = next((u for u in units if not u.done_at), None)

        if pending is None:
            status, delta, text = "done", 0, "¡Todos los prácticos completos! A repasar."
        elif pending.due < today:
            delta = (today - pending.due).days
            status, text = "behind", f"Atrasado {delta} día{'s' if delta != 1 else ''}: {pending.id} era para el {pending.due:%d/%m}"
        elif pending.start > today:
            delta = (pending.start - today).days
            status, text = "ahead", f"Adelantado {delta} día{'s' if delta != 1 else ''}"
        else:
            delta = 0
            when = "hoy" if pending.due == today else f"el {pending.due:%d/%m}"
            status, text = "on_track", f"Al día: {pending.id} para {when}"

        return {
            "code": code,
            "name": self.planner.name(code),
            "source": self.courses[code].get("source"),
            "status": status,
            "delta_days": delta,
            "text": text,
            "completed_pct": round(completed * 100),
            "expected_pct": round(min(1.0, planned_h / total_h) * 100) if total_h else 0,
            "review_start": (rs.isoformat() if (rs := self.review_start(code)) else None),
            "exam": self.planner.exams[code].isoformat(),
            "next": pending and {"id": pending.id, "name": pending.name, "due": pending.due.isoformat()},
            "units": [{"id": u.id, "name": u.name, "start": u.start.isoformat(), "due": u.due.isoformat(),
                       "done": u.done_at is not None, "weight_pct": round(u.weight * 100, 1)} for u in units],
        }
