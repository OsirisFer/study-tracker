#!/bin/zsh
# Instalación completa (todo gratis). Uso: ./setup.sh
set -e
cd "$(dirname "$0")"

PY=$(command -v python3.12 || command -v python3)
echo "→ Creando entorno virtual con $PY"
$PY -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "→ Descargando modelos de MediaPipe"
mkdir -p models data
[ -f models/face_landmarker.task ] || curl -sL -o models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
[ -f models/efficientdet_lite0.tflite ] || curl -sL -o models/efficientdet_lite0.tflite \
  https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/latest/efficientdet_lite0.tflite

if command -v ollama >/dev/null; then
  echo "→ Descargando modelo local de Ollama (≈2 GB, una sola vez)"
  ollama pull qwen2.5:3b
else
  echo "⚠️  Ollama no está instalado: se usarán solo palabras clave. (brew install ollama)"
fi
echo "✅ Listo. Ejecutá ./run.sh"
