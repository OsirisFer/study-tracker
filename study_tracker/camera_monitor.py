"""Cámara: ¿estás, mirás la pantalla, tenés el celular?

Todo se procesa en memoria, cuadro a cuadro. Nunca se guarda ni se envía una imagen.

Modelos (MediaPipe, gratis, corren en la CPU):
  - FaceLandmarker: 478 puntos de la cara + matriz de rotación de la cabeza → ángulo "yaw"
  - ObjectDetector (EfficientDet-Lite0, entrenado con COCO): detecta "person" y "cell phone"

Mirar hacia ABAJO no cuenta como distracción (es escribir en papel). Si te agachás tanto que
no se ve la cara, alcanza con que se detecte una persona para seguir "presente".
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


def yaw_from_matrix(m) -> float:
    """Rotación de la cabeza alrededor del eje vertical, en grados (0 = de frente)."""
    return math.degrees(math.atan2(m[0][2], m[2][2]))


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
        for det in self._objects.detect(image).detections:
            cat = det.categories[0]
            if cat.category_name == "person" and cat.score >= 0.4:
                person = True
            elif cat.category_name == "cell phone" and cat.score >= self.cfg.get("phone_min_score", 0.5):
                phone = self.cfg.get("phone_detection", True)

        looking_away = face and abs(yaw) > self.cfg.get("yaw_threshold_deg", 35)
        absent = self._absent.update(not face and not person, now)
        on_phone = self._phone.update(phone, now)
        away = self._away.update(looking_away, now)

        state = ABSENT if absent else PHONE if on_phone else LOOKING_AWAY if away else OK
        self.status = CameraStatus(state, face, person, phone, yaw, now)
        return self.status

    def set_enabled(self, on: bool) -> None:
        if on:
            self.enabled.set()
        else:
            self.enabled.clear()
            self.status = CameraStatus(state=OFF)

    def run(self) -> None:
        cap = None
        interval = 1.0 / max(self.cfg.get("fps", 3), 1)
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
            time.sleep(max(0.0, interval - (time.time() - start)))


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
        s = monitor.process_frame(frame, time.time())
        yaw = f"{s.yaw:+.0f}°" if s.yaw is not None else "—"
        lines = [f"estado: {s.state}", f"cara: {s.face}  persona: {s.person}",
                 f"celular: {s.phone}  yaw: {yaw}"]
        color = (0, 200, 0) if s.state == OK else (0, 0, 255)
        for i, text in enumerate(lines):
            cv2.putText(frame, text, (12, 32 + 30 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.imshow("Study Tracker - prueba de camara", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
