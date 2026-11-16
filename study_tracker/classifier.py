"""Clasifica lo que hay en pantalla: estudio, distracción, neutral o ausencia.

Orden de decisión para una pestaña de Chrome:
  1. PDF                         → estudio
  2. dominio de estudio          → estudio
  3. dominio de distracción      → distracción
  4. ambiguo / desconocido       → se decide por el título:
       palabras clave → modelo local (Ollama) → valor por defecto del dominio
"""
import json
import re
import threading
import time
import unicodedata
import urllib.request
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

STUDY = "study"
DISTRACTION = "distraction"
NEUTRAL = "neutral"     # cuenta solo si venís de material de estudio hace poco
AWAY = "away"           # pantalla bloqueada / protector de pantalla


@dataclass
class Classification:
    kind: str
    subject: str | None
    reason: str


def normalize(text: str) -> str:
    """minúsculas y sin tildes: 'Diagonalización' → 'diagonalizacion'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def _words(text: str) -> str:
    """Separa nombres de archivo en palabras: 'practico_autovalores-3.pdf' → 'practico autovalores 3 pdf'."""
    return re.sub(r"[_\-.]+", " ", normalize(text))


def _keyword_pattern(keywords: list[str]) -> re.Pattern:
    # "diagonaliz*" = prefijo; el resto debe ser palabra completa ("gal" ≠ "galaxia")
    parts = []
    for kw in keywords:
        kw = _words(str(kw).rstrip("*")) + ("*" if str(kw).endswith("*") else "")
        if kw.endswith("*"):
            parts.append(r"\b" + re.escape(kw[:-1]))
        else:
            parts.append(r"\b" + re.escape(kw) + r"\b")
    return re.compile("|".join(parts))


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _match_domain(host: str, domains) -> str | None:
    """Devuelve el dominio más específico que coincide (gemini.google.com antes que google.com)."""
    hits = [d for d in domains if host == d or host.endswith("." + d)]
    return max(hits, key=len) if hits else None


_TITLE_SUFFIXES = re.compile(r"\s+[-–|]\s+(youtube|buscar con google|google search|chatgpt|claude)$", re.I)
_NOTIF_PREFIX = re.compile(r"^\(\d+\)\s*")


def clean_title(title: str) -> str:
    """'(3) Valores propios - YouTube' → 'Valores propios'."""
    title = _NOTIF_PREFIX.sub("", title or "").strip()
    return _TITLE_SUFFIXES.sub("", title).strip()


# Ejemplos para el modelo (few-shot): mejoran mucho la precisión de modelos chicos.
EXAMPLES = [
    ("Encontrar una base del subespacio generado", "GAL1"),
    ("Calcular la inversa de una matriz 3x3", "GAL1"),
    ("Camino más corto en un grafo con pesos", "PROG3"),
    ("Cuánto tarda este algoritmo recursivo", "PROG3"),
    ("Hallar máximos de f(x,y) con restricciones", "CDIVV"),
    ("Integral doble en coordenadas polares", "CDIVV"),
    ("Top 10 goles del mundial", "NONE"),
    ("Playlist para estudiar lofi", "NONE"),
]


class OllamaClassifier:
    """Le pregunta a un modelo local de qué materia es un título. Corre en segundo plano
    y cachea las respuestas, así nunca bloquea el cronómetro."""

    def __init__(self, cfg: dict, subjects: dict):
        self.enabled = cfg.get("enabled", False)
        self.model = cfg.get("model", "qwen2.5:3b")
        self.url = cfg.get("url", "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
        self.subjects = subjects
        self.cache: dict[str, str] = {}
        self.pending: set[str] = set()
        self.lock = threading.Lock()
        self.failures = 0
        self.disabled_until = 0.0

    def available(self) -> bool:
        return self.enabled and time.time() >= self.disabled_until

    def classify(self, title: str) -> str | None:
        """Código de materia, 'NONE', o None si todavía no hay respuesta."""
        key = normalize(title)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            if not self.enabled or time.time() < self.disabled_until or key in self.pending:
                return None
            self.pending.add(key)
        threading.Thread(target=self._run, args=(key, title), daemon=True).start()
        return None

    def _prompt(self, title: str) -> str:
        lines = [
            f"{code} = {s['name']}: {s.get('description') or ', '.join(map(str, s['keywords'][:15]))}"
            for code, s in self.subjects.items()
        ]
        examples = "\n".join(f'Título: "{t}"\nRespuesta: {a}' for t, a in EXAMPLES if a in self.subjects or a == "NONE")
        return (
            "Clasificá el título de una pestaña del navegador de un estudiante de ingeniería.\n"
            + "\n".join(lines) + "\n"
            "NONE = cualquier cosa que no sea estudiar esas materias (ocio, música, deportes, noticias, compras).\n\n"
            f"{examples}\nTítulo: \"{title}\"\nRespuesta:"
        )

    def _run(self, key: str, title: str) -> None:
        result = None
        try:
            body = json.dumps({
                "model": self.model, "prompt": self._prompt(title), "stream": False,
                "options": {"temperature": 0, "num_predict": 8},
            }).encode()
            req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                answer = json.loads(resp.read())["response"].upper()
            result = next((c for c in self.subjects if c.upper() in answer), "NONE")
            self.failures = 0
        except Exception:
            self.failures += 1
            if self.failures >= 3:  # Ollama apagado: reintentar en 5 minutos
                self.disabled_until = time.time() + 300
                self.failures = 0
        with self.lock:
            self.pending.discard(key)
            if result:
                self.cache[key] = result


class Classifier:
    def __init__(self, cfg: dict, llm: OllamaClassifier | None = None):
        rules = cfg["rules"]
        self.browsers = set(rules["browsers"])
        self.study_domains = rules["study_domains"]
        self.distraction_domains = rules["distraction_domains"]
        self.ambiguous = rules["ambiguous_domains"]
        self.unknown_fallback = rules.get("unknown_domain_fallback", DISTRACTION)
        self.generic_titles = {normalize(t) for t in rules["generic_titles"]}
        self.study_apps = set(rules["study_apps"])
        self.distraction_apps = set(rules["distraction_apps"])
        self.away_apps = set(rules["away_apps"])
        self.patterns = {c: _keyword_pattern(s["keywords"]) for c, s in cfg["subjects"].items()}
        self.llm = llm

    def subject_from_text(self, text: str) -> str | None:
        text = _words(text)
        scores = {c: len(p.findall(text)) for c, p in self.patterns.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else None

    def classify(self, app: str, url: str = "", title: str = "") -> Classification:
        if app in self.away_apps:
            return Classification(AWAY, None, "Pantalla bloqueada")
        if app in self.browsers:
            return self.classify_tab(url, title)
        if app in self.study_apps:
            return Classification(STUDY, None, app)
        if app in self.distraction_apps:
            return Classification(DISTRACTION, None, app)
        return Classification(NEUTRAL, None, app or "desconocido")

    def classify_tab(self, url: str, title: str) -> Classification:
        if not url:
            return Classification(NEUTRAL, None, "Chrome")
        title = title or ""
        if self.is_pdf(url, title):
            filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            return Classification(STUDY, self.subject_from_text(f"{title} {filename}"), f"PDF: {title or filename}")
        if url.startswith(("chrome://", "chrome-extension://", "about:", "edge://")):
            return Classification(NEUTRAL, None, "Pestaña nueva / ajustes")

        host = _host(url)
        if d := _match_domain(host, self.study_domains):
            return Classification(STUDY, self.subject_from_text(title), d)
        if d := _match_domain(host, self.distraction_domains):
            return Classification(DISTRACTION, None, d)

        amb = _match_domain(host, self.ambiguous)
        site = amb or host
        fallback = self.ambiguous[amb] if amb else self.unknown_fallback
        shown = clean_title(title)[:60]

        if subj := self.subject_from_text(title):
            return Classification(STUDY, subj, f"{site}: {shown}")
        if normalize(clean_title(title)) in self.generic_titles:
            return Classification(NEUTRAL, None, f"{site} (chat nuevo)")
        if not (self.llm and self.llm.available() and title):
            return Classification(fallback, None, f"{site}: {shown}")
        verdict = self.llm.classify(clean_title(title))
        if verdict is None:  # el modelo todavía está pensando
            return Classification(NEUTRAL, None, f"{site}: {shown} (analizando…)")
        if verdict != "NONE":
            return Classification(STUDY, verdict, f"{site}: {shown}")
        return Classification(fallback, None, f"{site}: {shown}")

    @staticmethod
    def is_pdf(url: str, title: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(".pdf") or "pluginfile.php" in url or title.lower().endswith(".pdf")
