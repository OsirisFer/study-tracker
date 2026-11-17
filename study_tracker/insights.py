"""Patrones de foco: cuánto tardás en volver, a qué hora te distraés, mejor bloque y racha.

Todo sale de los segmentos guardados. Las funciones de cálculo son puras (fáciles de testear):
reciben segmentos (inicio, fin, segundos, estado) ordenados por inicio.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

STUDYING, DISTRACTED, AWAY = "STUDYING", "DISTRACTED", "AWAY"

BLOCK_TOLERANCE = 60   # un corte más corto (otra pestaña, mirar el celular un toque) no corta el bloque
MIN_EPISODE = 5        # distracciones más cortas son ruido (un clic en otra pestaña)
MAX_HOLE = 300         # si entre dos segmentos hay un hueco así (compu dormida), se corta todo


def episodes(segs) -> list[tuple[float, float]]:
    """Distracciones de las que volviste: tramos DISTRACTED entre dos tramos STUDYING.
    Devuelve (inicio, segundos distraído). Si en el medio te fuiste o hubo descanso, no cuenta."""
    out, run_start, run_secs, prev, last_end = [], None, 0.0, None, None
    for start, end, secs, state in segs:
        if last_end is not None and start - last_end > MAX_HOLE:
            run_start, prev = None, None
        if state == DISTRACTED:
            if run_start is None and prev == STUDYING:
                run_start, run_secs = start, 0.0
            if run_start is not None:
                run_secs += secs
        elif state == STUDYING:
            if run_start is not None and run_secs >= MIN_EPISODE:
                out.append((run_start, run_secs))
            run_start = None
        else:
            run_start = None
        prev, last_end = state, end
    return out


def best_block(segs) -> float:
    """Segundos de estudio del bloque más largo. Cortes de menos de BLOCK_TOLERANCE no lo cortan;
    un descanso o una pausa sí."""
    best = cur = gap = 0.0
    last_end = None
    for start, end, secs, state in segs:
        if last_end is not None and start - last_end > BLOCK_TOLERANCE:
            cur = gap = 0.0
        if state == STUDYING:
            cur += secs
            gap = 0.0
            best = max(best, cur)
        elif state in (DISTRACTED, AWAY):
            gap += secs
            if gap > BLOCK_TOLERANCE:
                cur = 0.0
        else:
            cur = gap = 0.0
        last_end = end
    return best


def by_hour(segs) -> tuple[list[float], list[float]]:
    """Segundos estudiados y distraídos por hora del día (0–23), partiendo segmentos entre horas."""
    study, dist = [0.0] * 24, [0.0] * 24
    for start, end, secs, state in segs:
        if state not in (STUDYING, DISTRACTED) or end <= start:
            continue
        bucket, scale, t = (study if state == STUDYING else dist), secs / (end - start), start
        while t < end:
            dt = datetime.fromtimestamp(t)
            nxt = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
            piece = min(end, nxt) - t
            bucket[dt.hour] += piece * scale
            t += piece
    return study, dist


def streaks(studied: dict[date, float], planner, today: date) -> tuple[int, int]:
    """(racha actual, mejor racha): días seguidos cumpliendo las horas del plan.
    Hoy suma si ya cumpliste, pero no corta la racha mientras el día no terminó."""
    run = best = 0
    d = planner.start_date
    while d <= today:
        target = sum(planner.targets(d).values()) * 3600
        if target > 0:
            if studied.get(d, 0) >= target:
                run += 1
            elif d < today:
                run = 0
        best = max(best, run)
        d += timedelta(days=1)
    return run, best


def focus_pct(segs) -> int | None:
    study = sum(s[2] for s in segs if s[3] == STUDYING)
    dist = sum(s[2] for s in segs if s[3] == DISTRACTED)
    return round(study / (study + dist) * 100) if study + dist else None


def day_metrics(d: date, segs) -> dict:
    eps = [secs for _, secs in episodes(segs)]
    return {"day": d.isoformat(), "episodes": len(eps),
            "recovery": round(median(eps)) if eps else None,
            "best_block": round(best_block(segs)), "focus": focus_pct(segs),
            "distracted": round(sum(x[2] for x in segs if x[3] == DISTRACTED)),
            "away": round(sum(x[2] for x in segs if x[3] == AWAY))}


def history(planner, storage, day: date) -> dict:
    """Un renglón por día desde el inicio del plan: cuánto estudiaste, contra el objetivo,
    y el balance acumulado (+ adelantado / − atrasado). Hoy no entra al balance hasta que termine."""
    start = min(planner.start_date, day)
    per_day = defaultdict(list)
    for d, s_, e, secs, state in storage.segments(start, day):
        per_day[d].append((s_, e, secs, state))
    by_subject: dict[str, dict[str, float]] = defaultdict(dict)
    for d, subj, secs in storage.daily_totals(start):
        if subj:
            by_subject[d][subj] = secs

    rows, balance, met, planned_days, cursor = [], 0.0, 0, 0, start
    while cursor <= day:
        iso = cursor.isoformat()
        targets = planner.targets(cursor)
        target, studied = sum(targets.values()) * 3600, sum(by_subject[iso].values())
        row = day_metrics(cursor, per_day.get(iso, []))
        if cursor < day and target:
            balance += studied - target
            planned_days += 1
            met += studied >= target
        row.update(target=round(target), studied=round(studied), targets=targets,
                   subjects={c: round(v) for c, v in by_subject[iso].items()},
                   pct=round(studied / target * 100) if target else None,
                   balance=round(balance), today=cursor == day)
        rows.append(row)
        cursor += timedelta(days=1)

    past = [r for r in rows if not r["today"] and r["target"]]
    focus = [r["focus"] for r in past if r["focus"] is not None]
    return {
        "rows": rows, "balance": round(balance), "met": met, "planned_days": planned_days,
        "avg_studied": round(sum(r["studied"] for r in past) / len(past)) if past else None,
        "avg_focus": round(sum(focus) / len(focus)) if focus else None,
    }


def build(planner, storage, day: date, days_back: int = 13) -> dict:
    """Todo lo que muestran el panel y el resumen del día."""
    since = min(day, max(planner.start_date, day - timedelta(days=days_back)))
    per_day = defaultdict(list)
    for d, start, end, secs, state in storage.segments(since, day):
        per_day[d].append((start, end, secs, state))

    days, cursor = [], since
    while cursor <= day:
        days.append(day_metrics(cursor, per_day.get(cursor.isoformat(), [])))
        cursor += timedelta(days=1)

    week = [s for d, segs in per_day.items() if d >= (day - timedelta(days=6)).isoformat() for s in segs]
    study_h, dist_h = by_hour(week)
    hours = [{"hour": h, "study_min": round(study_h[h] / 60), "dist_min": round(dist_h[h] / 60),
              "pct": round(dist_h[h] / (study_h[h] + dist_h[h]) * 100)}
             for h in sorted(range(24), key=lambda h: (h - planner.day_start_hour) % 24)  # 4 AM primero
             if study_h[h] + dist_h[h] >= 300]
    worst = max((h for h in hours if h["study_min"] + h["dist_min"] >= 30), key=lambda h: h["pct"], default=None)

    studied: dict[date, float] = defaultdict(float)
    for d, _subj, secs in storage.daily_totals(planner.start_date):
        studied[date.fromisoformat(d)] += secs
    current, best = streaks(studied, planner, day)

    today = days[-1]
    earlier = [d["recovery"] for d in days[:-1] if d["recovery"] is not None]
    return {
        "today": today,
        "recovery_before": round(median(earlier)) if earlier else None,
        "best_block_record": max(d["best_block"] for d in days),
        "streak": current, "best_streak": best,
        "days": days, "hours": hours, "worst_hour": worst,
    }


def mmss(secs: float | None) -> str:
    if secs is None:
        return "—"
    secs = round(secs)
    return f"{secs // 60}:{secs % 60:02d}"


def summary_text(ins: dict, studied_secs: float, target_h: float) -> str:
    t = ins["today"]
    parts = [f"Estudiaste {int(studied_secs // 3600)}:{int(studied_secs % 3600 // 60):02d}"
             + (f" ({round(studied_secs / (target_h * 3600) * 100)} % del plan)" if target_h else "")]
    if t["focus"] is not None:
        parts.append(f"foco {t['focus']} %")
    if t["best_block"]:
        parts.append(f"mejor bloque {t['best_block'] // 60} min")
    if t["episodes"]:
        parts.append(f"{t['episodes']} distracciones, volviste en {mmss(t['recovery'])} (mediana)")
    if ins["streak"]:
        parts.append(f"🔥 racha {ins['streak']} {'día' if ins['streak'] == 1 else 'días'}")
    return " · ".join(parts)
