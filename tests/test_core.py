"""Tests de la lógica (sin cámara ni pantalla reales). Correr: .venv/bin/python -m unittest -v"""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from study_tracker import camera_monitor as cam
from study_tracker.classifier import AWAY, DISTRACTION, NEUTRAL, STUDY, Classification, Classifier, clean_title
from study_tracker.config import load_config
from study_tracker.engine import AWAY_STATE, BREAK, DISTRACTED, IDLE, PAUSED, STUDYING, Engine, decide
from study_tracker.planner import Planner
from study_tracker.storage import Storage

CFG = load_config()


class FakeLLM:
    def __init__(self, answers):
        self.answers = answers

    def available(self):
        return True

    def classify(self, title):
        return self.answers.get(title)


class ClassifierTest(unittest.TestCase):
    def setUp(self):
        self.c = Classifier(CFG, FakeLLM({"Receta de pizza": "NONE", "Cómo resolver esto": "GAL1"}))

    def tab(self, url, title):
        return self.c.classify("Google Chrome", url, title)

    def test_pdf_in_chrome_is_study_and_detects_subject(self):
        r = self.tab("https://eva.fing.edu.uy/pluginfile.php/1/practico_autovalores.pdf", "practico_autovalores.pdf")
        self.assertEqual((r.kind, r.subject), (STUDY, "GAL1"))
        r = self.tab("file:///Users/x/Downloads/teorico.pdf", "teorico.pdf")
        self.assertEqual(r.kind, STUDY)

    def test_eva_is_study(self):
        self.assertEqual(self.tab("https://eva.fing.edu.uy/course/view.php?id=1", "Curso: Programación 3").subject, "PROG3")

    def test_distraction_domains(self):
        self.assertEqual(self.tab("https://www.instagram.com/", "Instagram").kind, DISTRACTION)

    def test_youtube_by_title(self):
        r = self.tab("https://www.youtube.com/watch?v=1", "(2) Diagonalización de matrices - YouTube")
        self.assertEqual((r.kind, r.subject), (STUDY, "GAL1"))
        self.assertEqual(self.tab("https://youtube.com/watch?v=2", "Receta de pizza - YouTube").kind, DISTRACTION)

    def test_chatgpt(self):
        r = self.tab("https://chatgpt.com/c/1", "Multiplicadores de Lagrange")
        self.assertEqual((r.kind, r.subject), (STUDY, "CDIVV"))
        self.assertEqual(self.tab("https://chatgpt.com/", "ChatGPT").kind, NEUTRAL)        # chat nuevo
        self.assertEqual(self.tab("https://chatgpt.com/c/2", "Cómo resolver esto").subject, "GAL1")  # vía LLM
        self.assertEqual(self.tab("https://chatgpt.com/c/3", "Receta de pizza").kind, DISTRACTION)

    def test_llm_pending_does_not_count(self):
        self.assertEqual(self.tab("https://chatgpt.com/c/4", "Algo sin respuesta aún").kind, DISTRACTION)

    def test_dashboard_is_neutral(self):
        self.assertEqual(self.tab("http://127.0.0.1:8765/", "Study Tracker").kind, NEUTRAL)

    def test_most_specific_domain_wins(self):
        # gemini.google.com es "distraction" por defecto; google.com sería "neutral"
        self.assertEqual(self.tab("https://gemini.google.com/app", "Receta de pizza").kind, DISTRACTION)
        self.assertEqual(self.tab("https://www.google.com/search?q=x", "Receta de pizza - Buscar con Google").kind, NEUTRAL)

    def test_keyword_needs_whole_word(self):
        self.assertIsNone(self.c.subject_from_text("Fotos de la galaxia"))
        self.assertEqual(self.c.subject_from_text("Práctico 3 GAL 1"), "GAL1")
        self.assertEqual(self.c.subject_from_text("metodo_gram-schmidt.pdf"), "GAL1")

    def test_apps(self):
        self.assertEqual(self.c.classify("Code").kind, STUDY)
        self.assertEqual(self.c.classify("WhatsApp").kind, DISTRACTION)
        self.assertEqual(self.c.classify("Finder").kind, NEUTRAL)
        self.assertEqual(self.c.classify("loginwindow").kind, AWAY)

    def test_without_llm_uses_fallback(self):
        c = Classifier(CFG, None)
        self.assertEqual(c.classify("Google Chrome", "https://youtube.com/watch", "Partido - YouTube").kind, DISTRACTION)

    def test_clean_title(self):
        self.assertEqual(clean_title("(3) Valores propios - YouTube"), "Valores propios")


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.p = Planner(CFG)

    def test_weekday_and_sunday(self):
        self.assertEqual(self.p.targets(date(2026, 11, 17)), {"GAL1": 4, "PROG3": 4, "CDIVV": 2})
        self.assertEqual(self.p.targets(date(2026, 11, 22)), {"GAL1": 2, "PROG3": 2, "CDIVV": 1})

    def test_no_targets_before_start(self):
        self.assertEqual(self.p.targets(date(2026, 11, 16)), {})

    def test_phase_change_after_exam(self):
        self.assertNotIn("GAL1", self.p.targets(date(2026, 12, 11)))
        self.assertEqual(list(self.p.targets(date(2026, 12, 11))), ["CDIVV", "PROG3"])
        self.assertEqual(self.p.targets(date(2026, 12, 18)), {"PROG3": 10})
        self.assertEqual(self.p.targets(date(2026, 12, 23)), {})

    def test_next_subject(self):
        d = date(2026, 11, 17)
        self.assertEqual(self.p.next_subject(d, {}), "GAL1")
        self.assertEqual(self.p.next_subject(d, {"GAL1": 4 * 3600}), "PROG3")
        self.assertIsNone(self.p.next_subject(d, {"GAL1": 4 * 3600, "PROG3": 4 * 3600, "CDIVV": 2 * 3600}))


class DecideTest(unittest.TestCase):
    base = dict(context_recent=False, paused=False, on_break=False, has_subject=True, idle_s=0, idle_limit=300)

    def d(self, kind, camera=cam.OK, **kw):
        return decide(Classification(kind, None, "x"), camera, **{**self.base, **kw})[0]

    def test_priorities(self):
        self.assertEqual(self.d(STUDY), STUDYING)
        self.assertEqual(self.d(STUDY, paused=True), PAUSED)
        self.assertEqual(self.d(STUDY, on_break=True), BREAK)
        self.assertEqual(self.d(STUDY, has_subject=False), IDLE)
        self.assertEqual(self.d(STUDY, cam.ABSENT), AWAY_STATE)
        self.assertEqual(self.d(STUDY, cam.PHONE), DISTRACTED)
        self.assertEqual(self.d(STUDY, cam.LOOKING_AWAY), DISTRACTED)
        self.assertEqual(self.d(DISTRACTION), DISTRACTED)
        self.assertEqual(self.d(AWAY), AWAY_STATE)

    def test_neutral_depends_on_context(self):
        self.assertEqual(self.d(NEUTRAL, context_recent=True), STUDYING)
        self.assertEqual(self.d(NEUTRAL), DISTRACTED)

    def test_idle_only_matters_without_camera(self):
        self.assertEqual(self.d(STUDY, cam.OK, idle_s=900), STUDYING)  # escribiendo en papel
        self.assertEqual(self.d(STUDY, cam.OFF, idle_s=900), AWAY_STATE)


class DebounceTest(unittest.TestCase):
    def test_needs_sustained_condition_and_tolerates_gaps(self):
        db = cam.Debounce(on_after=10, gap=2)
        self.assertFalse(db.update(True, 0))
        self.assertFalse(db.update(False, 1))   # un cuadro malo no reinicia
        self.assertTrue(db.update(True, 10))
        self.assertFalse(db.update(False, 13))  # 3 s sin condición → se apaga


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "t.db")
        self.screen = SimpleNamespace(snapshot=lambda: ("Google Chrome", "https://eva.fing.edu.uy/x", "GAL 1"),
                                      source="test")
        self.camera = SimpleNamespace(status=cam.CameraStatus(state=cam.OK))
        self.notes = []
        cfg = {**CFG, "pomodoro": {"enabled": True, "focus_minutes": 1, "break_minutes": 1}}
        planner = Planner(cfg)
        self.clock = [1_000_000.0]
        with mock.patch("study_tracker.engine.time.time", lambda: self.clock[0]):
            mock_day = mock.patch.object(planner, "study_day", lambda now=None: date(2026, 11, 17))
            mock_day.start()
            self.addCleanup(mock_day.stop)
            self.engine = Engine(cfg, planner, Classifier(cfg, None), self.screen, self.camera,
                                 self.storage, lambda t, m, sound=None: self.notes.append(t), lambda: 0)

    def tearDown(self):
        self.tmp.cleanup()

    def run_secs(self, n):
        with mock.patch("study_tracker.engine.time.time", lambda: self.clock[0]):
            for _ in range(n):
                self.clock[0] += 1
                self.engine.tick()

    def test_counts_study_and_triggers_break(self):
        self.assertEqual(self.engine.selected, "GAL1")
        self.run_secs(30)
        self.assertAlmostEqual(self.engine.totals["GAL1"], 30)
        self.run_secs(31)
        self.assertEqual(self.engine.status.state, BREAK)
        self.assertTrue(any("Descanso" in n for n in self.notes))
        before = self.engine.totals["GAL1"]
        self.run_secs(30)
        self.assertEqual(self.engine.totals["GAL1"], before)  # el descanso no cuenta

    def test_camera_phone_pauses(self):
        self.camera.status = cam.CameraStatus(state=cam.PHONE)
        self.run_secs(20)
        self.assertEqual(self.engine.totals.get("GAL1", 0), 0)
        self.assertAlmostEqual(self.engine.state_totals[DISTRACTED], 20)  # se suma el tiempo distraído
        self.assertTrue(any("pausado" in n for n in self.notes))

    def test_goal_switches_subject(self):
        self.engine.pomodoro = False
        self.engine.totals["GAL1"] = 4 * 3600 - 2
        self.run_secs(3)
        self.assertEqual(self.engine.selected, "PROG3")
        self.assertTrue(any("Completaste GAL 1" in n for n in self.notes))

    def test_persisted_totals(self):
        self.run_secs(10)
        self.assertAlmostEqual(self.storage.totals_for_day(date(2026, 11, 17))["GAL1"], 10)


class SyllabusTest(unittest.TestCase):
    def setUp(self):
        from study_tracker.syllabus import Syllabus
        self.s = Syllabus.load(Planner(CFG))

    def test_units_cover_plan_and_end_before_review(self):
        units = self.s.units("GAL1", {})
        self.assertEqual(len(units), 12)
        self.assertAlmostEqual(sum(u.weight for u in units), 1.0)
        self.assertEqual(units[0].start, date(2026, 11, 17))
        self.assertLess(units[-1].due, self.s.review_start("GAL1"))
        self.assertEqual(self.s.review_start("GAL1"), date(2026, 12, 7))
        self.assertTrue(all(a.due <= b.due for a, b in zip(units, units[1:])))

    def test_status(self):
        units = self.s.units("GAL1", {})
        late = units[2].due + __import__("datetime").timedelta(days=2)
        done = {u.id: 1.0 for u in units[:2]}
        self.assertEqual(self.s.summary("GAL1", done, late)["status"], "behind")
        self.assertEqual(self.s.summary("GAL1", done, late)["delta_days"], 2)
        all_but_last = {u.id: 1.0 for u in units[:-1]}
        self.assertEqual(self.s.summary("GAL1", all_but_last, date(2026, 11, 20))["status"], "ahead")
        self.assertEqual(self.s.summary("GAL1", {u.id: 1.0 for u in units}, date(2026, 11, 20))["status"], "done")
        self.assertEqual(self.s.summary("GAL1", {}, date(2026, 11, 17))["status"], "on_track")


class InsightsTest(unittest.TestCase):
    @staticmethod
    def segs(*parts, t0=1_800_000_000.0):
        """Arma segmentos seguidos a partir de (estado, segundos)."""
        out, t = [], t0
        for state, secs in parts:
            out.append((t, t + secs, secs, state))
            t += secs
        return out

    def test_episodes_only_count_returns_to_study(self):
        from study_tracker.insights import episodes
        segs = self.segs((STUDYING, 600), (DISTRACTED, 90), (DISTRACTED, 30), (STUDYING, 300),
                         (DISTRACTED, 2), (STUDYING, 100),        # muy corta: ruido
                         (DISTRACTED, 60), (AWAY_STATE, 600), (STUDYING, 100),  # se fue: no cuenta
                         (DISTRACTED, 40), (BREAK, 600))           # terminó en descanso
        self.assertEqual([d for _, d in episodes(segs)], [120])

    def test_best_block_tolerates_short_cuts(self):
        from study_tracker.insights import best_block
        segs = self.segs((STUDYING, 1200), (DISTRACTED, 30), (STUDYING, 600),   # 30 s no corta
                         (DISTRACTED, 120), (STUDYING, 900),                      # 2 min sí
                         (BREAK, 600), (STUDYING, 300))
        self.assertEqual(best_block(segs), 1800)

    def test_by_hour_splits_segments(self):
        from datetime import datetime
        from study_tracker.insights import by_hour
        t0 = datetime(2026, 11, 17, 21, 30).timestamp()
        study, dist = by_hour(self.segs((STUDYING, 3600), (DISTRACTED, 600), t0=t0))
        self.assertAlmostEqual(study[21], 1800)
        self.assertAlmostEqual(study[22], 1800)
        self.assertAlmostEqual(dist[22], 600)

    def test_streaks(self):
        from study_tracker.insights import streaks
        p = Planner(CFG)
        full = lambda d: sum(p.targets(d).values()) * 3600
        d = [date(2026, 11, 17 + i) for i in range(5)]
        studied = {d[0]: full(d[0]), d[1]: 100, d[2]: full(d[2]), d[3]: full(d[3])}
        self.assertEqual(streaks(studied, p, d[4]), (2, 2))   # hoy (d4) sin cumplir no corta
        studied[d[4]] = full(d[4])
        self.assertEqual(streaks(studied, p, d[4]), (3, 3))


if __name__ == "__main__":
    unittest.main()
