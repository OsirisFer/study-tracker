"""Planificador: cuántas horas le tocan a cada materia cada día."""
from datetime import date, datetime, timedelta


class Planner:
    def __init__(self, cfg: dict):
        self.subjects = cfg["subjects"]
        self.exams: dict[str, date] = cfg["exams"]
        self.hours = cfg["daily_hours"]
        self.phases = sorted(cfg["phases"], key=lambda p: p["until"])
        self.day_start_hour = cfg.get("day_start_hour", 4)
        self.start_date: date = cfg.get("start_date", date.today())

    def name(self, code: str | None) -> str:
        if not code:
            return "—"
        return self.subjects.get(code, {}).get("name", code)

    def emoji(self, code: str | None) -> str:
        if not code:
            return "📚"
        return self.subjects.get(code, {}).get("emoji", "📚")

    def study_day(self, now: datetime | None = None) -> date:
        """El día de estudio empieza a las `day_start_hour` (por defecto 4 AM)."""
        now = now or datetime.now()
        return (now - timedelta(hours=self.day_start_hour)).date()

    def phase_for(self, day: date) -> dict | None:
        for phase in self.phases:
            if day < phase["until"]:
                return phase
        return None

    def day_hours(self, day: date) -> float:
        if day in self.exams.values():
            return self.hours.get("exam_day", self.hours["sunday"])
        if day.weekday() == 6:
            return self.hours["sunday"]
        return self.hours["weekday"]

    def targets(self, day: date) -> dict[str, float]:
        """Horas objetivo por materia, escalando el reparto de la fase al total del día."""
        phase = self.phase_for(day)
        if not phase or day < self.start_date:  # antes del inicio: día libre, sin metas
            return {}
        split = phase["split"]
        total = sum(split.values())
        scale = self.day_hours(day) / total if total else 0
        return {s: round(h * scale, 2) for s, h in split.items()}  # respeta el orden del YAML

    def next_subject(self, day: date, totals_sec: dict[str, float]) -> str | None:
        """Primera materia (en el orden de la fase) que todavía no cumplió su objetivo de hoy."""
        for code, hours in self.targets(day).items():
            if totals_sec.get(code, 0) < hours * 3600:
                return code
        return None

    def upcoming_exams(self, day: date) -> list[tuple[str, date, int]]:
        items = [(c, d, (d - day).days) for c, d in self.exams.items() if d >= day]
        return sorted(items, key=lambda x: x[1])

    def planned_cumulative(self, until: date) -> dict[str, float]:
        """Horas planificadas por materia desde start_date hasta `until` inclusive."""
        out: dict[str, float] = {}
        day = self.start_date
        while day <= until:
            for code, h in self.targets(day).items():
                out[code] = out.get(code, 0) + h
            day += timedelta(days=1)
        return out
