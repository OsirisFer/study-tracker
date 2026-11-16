"""Registro en SQLite. Guarda "segmentos": tramos seguidos con el mismo estado/materia/motivo.

Un tick por segundo no genera una fila por segundo: si nada cambió, se alarga el segmento actual.
"""
import sqlite3
import threading
import time
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT NOT NULL,
    start   REAL NOT NULL,
    end     REAL NOT NULL,
    seconds REAL NOT NULL,
    state   TEXT NOT NULL,
    subject TEXT,
    reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_segments_day ON segments(day);
CREATE TABLE IF NOT EXISTS progress (
    subject TEXT NOT NULL,
    unit    TEXT NOT NULL,
    done_at REAL NOT NULL,
    PRIMARY KEY (subject, unit)
);
"""

MERGE_GAP = 5.0
FLUSH_EVERY = 10.0


class Storage:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.cur: dict | None = None
        self.last_flush = 0.0

    def record(self, day: date, ts: float, dt: float, state: str, subject: str | None, reason: str) -> None:
        key = (day.isoformat(), state, subject, reason)
        with self.lock:
            c = self.cur
            if c and c["key"] == key and ts - c["end"] < MERGE_GAP:
                c["end"] = ts
                c["seconds"] += dt
            else:
                self._flush()
                cursor = self.db.execute(
                    "INSERT INTO segments(day, start, end, seconds, state, subject, reason) VALUES (?,?,?,?,?,?,?)",
                    (*key[:1], ts - dt, ts, dt, state, subject, reason))
                self.cur = {"id": cursor.lastrowid, "key": key, "end": ts, "seconds": dt}
                self.db.commit()
            if ts - self.last_flush > FLUSH_EVERY:
                self._flush()

    def _flush(self) -> None:
        if self.cur:
            self.db.execute("UPDATE segments SET end=?, seconds=? WHERE id=?",
                            (self.cur["end"], self.cur["seconds"], self.cur["id"]))
            self.db.commit()
        self.last_flush = time.time()

    def flush(self) -> None:
        with self.lock:
            self._flush()

    def _query(self, sql: str, args=()) -> list:
        with self.lock:
            self._flush()
            return self.db.execute(sql, args).fetchall()

    def totals_for_day(self, day: date) -> dict[str, float]:
        rows = self._query("SELECT subject, SUM(seconds) FROM segments "
                           "WHERE day=? AND state='STUDYING' GROUP BY subject", (day.isoformat(),))
        return {s: secs for s, secs in rows if s}

    def daily_totals(self, since: date) -> list[tuple[str, str, float]]:
        return self._query("SELECT day, subject, SUM(seconds) FROM segments "
                           "WHERE day>=? AND state='STUDYING' GROUP BY day, subject ORDER BY day",
                           (since.isoformat(),))

    def subject_totals(self, since: date) -> dict[str, float]:
        rows = self._query("SELECT subject, SUM(seconds) FROM segments "
                           "WHERE day>=? AND state='STUDYING' GROUP BY subject", (since.isoformat(),))
        return {s: secs for s, secs in rows if s}

    def state_breakdown(self, day: date) -> dict[str, float]:
        rows = self._query("SELECT state, SUM(seconds) FROM segments WHERE day=? GROUP BY state",
                           (day.isoformat(),))
        return dict(rows)

    def top_distractions(self, since: date, limit: int = 10) -> list[tuple[str, float, int]]:
        return self._query("SELECT reason, SUM(seconds) AS s, COUNT(*) FROM segments "
                           "WHERE day>=? AND state='DISTRACTED' GROUP BY reason ORDER BY s DESC LIMIT ?",
                           (since.isoformat(), limit))

    # ── prácticos completados ────────────────────────────────
    def progress(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for subj, unit, ts in self._query("SELECT subject, unit, done_at FROM progress"):
            out.setdefault(subj, {})[unit] = ts
        return out

    def set_progress(self, subject: str, unit: str, done: bool) -> None:
        with self.lock:
            if done:
                self.db.execute("INSERT OR REPLACE INTO progress VALUES (?,?,?)", (subject, unit, time.time()))
            else:
                self.db.execute("DELETE FROM progress WHERE subject=? AND unit=?", (subject, unit))
            self.db.commit()
