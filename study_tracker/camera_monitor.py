"""Cámara: ¿estás, mirás la pantalla, tenés el celular?

Todo se procesa en memoria, cuadro a cuadro. Nunca se guarda ni se envía una imagen.

Modelos (MediaPipe, gratis, corren en la CPU):
  - FaceLandmarker: 478 puntos de la cara + matriz de rotación de la cabeza → ángulo "yaw"
  - ObjectDetector (EfficientDet-Lite0, entrenado con COCO): detecta "person" y "cell phone"

Mirar hacia ABAJO no cuenta como distracción (es escribir en papel). Si te agachás tanto que
no se ve la cara, alcanza con que se detecte una persona para seguir "presente".

Vista previa: solo mientras alguien la mira en el panel (`preview_jpeg`), se guarda el último
cuadro con sus detecciones y se dibuja encima. Si nadie mira, no se dibuja ni se codifica nada.
"""
import math
import threading
import time
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mpt
from mediapipe.tasks.python import vision

from .config import MODELS_DIR

OK, ABSENT, LOOKING_AWAY, PHONE = "ok", "absent", "looking_away", "phone"
OFF, UNAVAILABLE, STARTING = "off", "unavailable", "starting"

PREVIEW_TIMEOUT = 5.0   # segundos sin pedidos de vista previa → se deja de generar
PREVIEW_FPS = 8         # más fluido mientras la mirás
NOSE, LEFT_CHEEK, RIGHT_CHEEK = 1, 234, 454   # índices de puntos del FaceLandmarker

LABELS = {OK: "Atento", ABSENT: "AUSENTE", LOOKING_AWAY: "MIRANDO AL COSTADO", PHONE: "CELULAR",
          STARTING: "Iniciando...", UNAVAILABLE: "Camara no disponible", OFF: "Camara apagada"}


@dataclass
class CameraStatus:
    state: str = STARTING
    face: bool = False
    person: bool = False
    phone: bool = False
    yaw: float | None = None
    updated: float = field(default_factory=time.time)


class Debounce:
    """Activa una condición solo si se mantiene `on_after` segundos seguidos.
    Tolera huecos cortos (`gap` s) para que un cuadro malo no reinicie la cuenta."""

    def __init__(self, on_after: float, gap: float = 2.0):
        self.on_after, self.gap = on_after, gap
        self.since: float | None = None
        self.last_true = 0.0

    def update(self, raw: bool, now: float) -> bool:
        if raw:
            self.last_true = now
            if self.since is None:
                self.since = now
        elif self.since is not None and now - self.last_true > self.gap:
            self.since = None
        return self.since is not None and now - self.since >= self.on_after

    def elapsed(self, now: float) -> float:
        return 0.0 if self.since is None else now - self.since


def yaw_from_matrix(m) -> float:
    """Rotación de la cabeza alrededor del eje vertical, en grados (0 = de frente)."""
    return math.degrees(math.atan2(m[0][2], m[2][2]))


def _box(bb) -> tuple[int, int, int, int]:
    return bb.origin_x, bb.origin_y, bb.width, bb.height


class CameraMonitor(threading.Thread):
    def __init__(self, cfg: dict):
        super().__init__(daemon=True, name="camera")
        self.cfg = cfg
        self.status = CameraStatus(state=STARTING if cfg.get("enabled", True) else OFF)
        self.enabled = threading.Event()
        if cfg.get("enabled", True):
            self.enabled.set()
        self._absent = Debounce(cfg.get("absent_after", 30))
        self._away = Debounce(cfg.get("looking_away_after", 20))
        self._phone = Debounce(cfg.get("phone_after", 8))
        self._face = None
        self._objects = None
        self._preview_lock = threading.Lock()
        self._preview: tuple | None = None
        self._last_view = 0.0

    def _load_models(self) -> None:
        if self._face:
            return
        self._face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(MODELS_DIR / "face_landmarker.task")),
            output_facial_transformation_matrixes=True, num_faces=1))
        self._objects = vision.ObjectDetector.create_from_options(vision.ObjectDetectorOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(MODELS_DIR / "efficientdet_lite0.tflite")),
            score_threshold=0.3, category_allowlist=["person", "cell phone"]))

    def process_frame(self, frame_bgr, now: float) -> CameraStatus:
        self._load_models()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        faces = self._face.detect(image)
        face = bool(faces.face_landmarks)
        yaw = yaw_from_matrix(faces.facial_transformation_matrixes[0]) if face else None

        person = phone = False
        person_boxes, phone_boxes = [], []
        for det in self._objects.detect(image).detections:
            cat = det.categories[0]
            if cat.category_name == "person" and cat.score >= 0.4:
                person = True
                person_boxes.append((_box(det.bounding_box), cat.score))
            elif cat.category_name == "cell phone" and cat.score >= self.cfg.get("phone_min_score", 0.5):
                phone = self.cfg.get("phone_detection", True)
                phone_boxes.append((_box(det.bounding_box), cat.score))

        threshold = self.cfg.get("yaw_threshold_deg", 35)
        looking_away = face and abs(yaw) > threshold
        absent = self._absent.update(not face and not person, now)
        on_phone = self._phone.update(phone, now)
        away = self._away.update(looking_away, now)

        state = ABSENT if absent else PHONE if on_phone else LOOKING_AWAY if away else OK
        self.status = CameraStatus(state, face, person, phone, yaw, now)

        if self.wants_preview():
            info = {
                "state": state, "yaw": yaw, "threshold": threshold, "looking_away": looking_away,
                "landmarks": faces.face_landmarks[0] if face else None,
                "persons": person_boxes, "phones": phone_boxes,
                "progress": [
                    ("Ausente", self._absent.elapsed(now), self._absent.on_after),
                    ("Mirando al costado", self._away.elapsed(now), self._away.on_after),
                    ("Celular", self._phone.elapsed(now), self._phone.on_after),
                ],
            }
            with self._preview_lock:
                self._preview = (frame_bgr, info)
        return self.status

    # ── vista previa ─────────────────────────────────────────
    def wants_preview(self) -> bool:
        return time.time() - self._last_view < PREVIEW_TIMEOUT

    def preview_jpeg(self) -> bytes | None:
        """Último cuadro con las detecciones dibujadas (lo pide el panel)."""
        self._last_view = time.time()
        with self._preview_lock:
            data = self._preview
        if data is None:
            return None
        ok, jpg = cv2.imencode(".jpg", draw_overlay(*data), [cv2.IMWRITE_JPEG_QUALITY, 70])
        return jpg.tobytes() if ok else None

    def set_enabled(self, on: bool) -> None:
        if on:
            self.enabled.set()
        else:
            self.enabled.clear()
            self.status = CameraStatus(state=OFF)
            with self._preview_lock:
                self._preview = None

    def run(self) -> None:
        cap = None
        while True:
            if not self.enabled.is_set():
                if cap is not None:  # soltar la cámara (se apaga la lucecita)
                    cap.release()
                    cap = None
                self.enabled.wait(1.0)
                continue
            if cap is None:
                cap = cv2.VideoCapture(self.cfg.get("device_index", 0))
                if not cap.isOpened():
                    cap = None
                    self.status = CameraStatus(state=UNAVAILABLE)
                    time.sleep(5)
                    continue
            start = time.time()
            ok, frame = cap.read()
            if not ok:
                cap.release()
                cap = None
                self.status = CameraStatus(state=UNAVAILABLE)
                time.sleep(2)
                continue
            try:
                self.process_frame(frame, start)
            except Exception as e:  # un error en un cuadro no debe matar el hilo
                print(f"[cámara] error procesando cuadro: {e}")
            fps = PREVIEW_FPS if self.wants_preview() else max(self.cfg.get("fps", 3), 1)
            time.sleep(max(0.0, 1.0 / fps - (time.time() - start)))


def draw_overlay(frame_bgr, info: dict):
    """Dibuja sobre una copia en espejo (como un selfie). Textos sin tildes: OpenCV no las soporta."""
    img = cv2.flip(frame_bgr, 1)
    h, w = img.shape[:2]
    scale = 640 / w
    if scale < 1:
        img = cv2.resize(img, (640, int(h * scale)))
        h, w = img.shape[:2]
    else:
        scale = 1.0
    green, orange, red, gray, white = (80, 200, 80), (0, 165, 255), (60, 60, 230), (170, 170, 170), (255, 255, 255)

    def mirror_box(box):
        x, y, bw, bh = (int(v * scale) for v in box)
        return w - x - bw, y, bw, bh

    for box, score in info["persons"]:
        x, y, bw, bh = mirror_box(box)
        cv2.rectangle(img, (x, y), (x + bw, y + bh), gray, 1)
        cv2.putText(img, f"persona {score:.0%}", (x + 4, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, gray, 1)
    for box, score in info["phones"]:
        x, y, bw, bh = mirror_box(box)
        cv2.rectangle(img, (x, y), (x + bw, y + bh), red, 3)
        cv2.putText(img, f"celular {score:.0%}", (x + 4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, red, 2)

    lm = info["landmarks"]
    if lm:
        px = lambda p: (int((1 - p.x) * w), int(p.y * h))  # espejo
        xs = [int((1 - p.x) * w) for p in lm]
        ys = [int(p.y * h) for p in lm]
        color = red if info["state"] == LOOKING_AWAY else orange if info["looking_away"] else green
        cv2.rectangle(img, (min(xs), min(ys)), (max(xs), max(ys)), color, 2)
        # Flecha: del centro de la cara hacia la nariz, exagerada → hacia dónde apunta la cabeza
        nose = px(lm[NOSE])
        mid = ((px(lm[LEFT_CHEEK])[0] + px(lm[RIGHT_CHEEK])[0]) // 2, nose[1])
        tip = (mid[0] + 4 * (nose[0] - mid[0]), nose[1])
        cv2.arrowedLine(img, mid, tip, color, 3, tipLength=0.3)
        cv2.circle(img, nose, 4, color, -1)
        cv2.putText(img, f"giro {abs(info['yaw']):.0f} / {info['threshold']} grados",
                    (min(xs), max(ys) + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    # Panel superior: estado + barras de cuánto falta para disparar cada condición
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, 118), (0, 0, 0), -1)
    img = cv2.addWeighted(overlay, 0.45, img, 0.55, 0)
    state_color = green if info["state"] == OK else red
    cv2.putText(img, LABELS.get(info["state"], info["state"]), (12, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.85, state_color, 2)
    for i, (label, elapsed, limit) in enumerate(info["progress"]):
        y = 52 + i * 22
        frac = min(1.0, elapsed / limit) if limit else 0
        cv2.putText(img, label, (12, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, white, 1)
        x0, x1 = 190, w - 90
        cv2.rectangle(img, (x0, y), (x1, y + 14), (90, 90, 90), -1)
        if frac:
            cv2.rectangle(img, (x0, y), (x0 + int((x1 - x0) * frac), y + 14), red if frac >= 1 else orange, -1)
        cv2.putText(img, f"{elapsed:.0f}/{limit:.0f}s", (x1 + 8, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, white, 1)
    return img


def camera_test(cfg: dict) -> None:
    """Ventana de prueba: muestra lo que detecta la cámara. Salir con 'q'."""
    monitor = CameraMonitor(cfg)
    cap = cv2.VideoCapture(cfg.get("device_index", 0))
    if not cap.isOpened():
        print("No pude abrir la cámara. Revisá Ajustes → Privacidad → Cámara para tu terminal.")
        return
    print("Probando cámara… apretá 'q' en la ventana para salir.")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        monitor._last_view = time.time()
        monitor.process_frame(frame, time.time())
        cv2.imshow("Study Tracker - prueba de camara", draw_overlay(*monitor._preview))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
