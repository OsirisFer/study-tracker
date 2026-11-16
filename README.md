# 📚 Study Tracker

App local para macOS que **cuenta solo el tiempo que realmente estudiás**. Para eso mira qué tenés en pantalla (app, pestaña de Chrome, PDF) y qué ve la cámara (si estás, si mirás la pantalla, si agarraste el celular). Además lleva tu plan de horas por materia y te avisa cuándo cambiar.

**100 % gratis y local**: no usa APIs pagas ni suscripciones, y nada sale de tu compu. Las imágenes de la cámara se procesan en memoria y nunca se guardan.

```
🟢 📐 2:13/4:00      ← barra de menú: estado · materia · hecho/objetivo
```

## Instalación

```bash
git clone https://github.com/OsirisFer/study-tracker.git
cd study-tracker
./setup.sh      # crea el entorno, instala dependencias y baja los modelos (gratis)
./run.sh        # arranca la app en la barra de menú
```

Requisitos: macOS, Python 3.12, Google Chrome y, opcionalmente, [Ollama](https://ollama.com) (`brew install ollama`).

### Permisos de macOS (se piden una sola vez)
| Permiso | Para qué | Dónde |
|---|---|---|
| Cámara | detectar presencia, mirada y celular | Ajustes → Privacidad y seguridad → Cámara → tu Terminal |
| Automatización → Google Chrome | leer la pestaña activa (respaldo si no está la extensión) | aparece la primera vez |

### Extensión de Chrome (recomendada)
Avisa al instante cuando cambiás de pestaña.
1. Abrí `chrome://extensions` y activá **Modo de desarrollador**.
2. **Cargar descomprimida** → elegí la carpeta `extension/` del proyecto.

(También está en el menú: *Instalar extensión de Chrome…*)

## Comandos

```bash
./run.sh               # app
./run.sh plan          # muestra el plan día por día hasta el último examen
./run.sh camera-test   # ventana para ver qué detecta la cámara (salir con q)
.venv/bin/python -m unittest -v   # tests
```

Panel de estadísticas: **http://127.0.0.1:8765/**. Muestra el progreso de hoy, la cuenta regresiva a los exámenes, las horas por día, el plan vs. lo real y tus distracciones más comunes.

## Cómo decide si estás estudiando

Cada segundo se combinan tres fuentes:

```
 Pantalla (app + pestaña)  ─┐
 Cámara (cara, celular)    ─┼─► motor ─► ESTUDIANDO / DISTRAÍDO / AUSENTE / DESCANSO / PAUSA
 Teclado/mouse (sin cámara)─┘
```

**Pantalla**
| Qué hay al frente | Resultado |
|---|---|
| PDF en Chrome, EVA, open.fing, GeoGebra, VS Code, Vista Previa… | ✅ cuenta |
| Instagram, X, Netflix, WhatsApp… | ❌ pausa |
| ChatGPT, Claude, YouTube, sitios desconocidos | se decide por el **título** |
| Finder, Google, pestaña nueva | cuenta solo si estuviste en material de estudio en los últimos 5 min |

Clasificación por título:
1. **Palabras clave** de cada materia (`config.yaml`). "Diagonalización de matrices - YouTube" → GAL 1.
2. Si no hay coincidencia, un **modelo local** (Ollama, `qwen2.5:3b`) responde "¿es de GAL, Prog 3, CDIVV o nada?". La respuesta queda en caché y nunca traba el cronómetro.
3. Un chat nuevo sin título ("ChatGPT") cuenta si venís del PDF o de EVA.

**Cámara** (MediaPipe, corre en la CPU)
| Detecta | Después de | Resultado |
|---|---|---|
| No hay cara ni persona | 30 s | AUSENTE |
| Cabeza girada más de 35° (al costado) | 20 s | DISTRAÍDO |
| Celular a la vista | 8 s | DISTRAÍDO |

Mirar **hacia abajo** no se considera distracción, porque es escribir en papel. Sin cámara, se usa la inactividad del teclado y el mouse (5 min).

**Plan**
- El reparto de horas por materia está en `phases` de `config.yaml`. El domingo se escala solo (5 h → 2/2/1) y el día de examen baja a 4 h.
- Al completar una materia te avisa ("✅ Completaste GAL 1, te toca Programación 3") y cambia sola.
- Pomodoro: 50 min de estudio y 10 de descanso (el descanso no cuenta como distracción).
- Si la materia que detecta en pantalla no coincide con la elegida, te pregunta si cambiaste.
- El día de estudio arranca a las 4 AM: estudiar a la 1 AM cuenta para el día anterior.

## Metas por práctico

`syllabus.yaml` tiene los prácticos de cada materia, sacados de la hoja de ruta del EVA. Cada uno pesa según cuánto tiempo le dedica el curso: semanas en GAL y CDIVV, y "Dedicación por capítulo" en Programación 3. La app reparte esos pesos en **tus** horas planificadas hasta el examen, dejando los últimos días para repasar, y le pone una **fecha meta** a cada práctico.

Marcás cada práctico cuando lo terminás, desde el panel (casillas) o desde el menú (*🎯 Metas por práctico*). Según el primer práctico que te falte, te dice si vas:
- **atrasado N días**: la meta de ese práctico ya pasó;
- **al día**: estás dentro del rango del plan;
- **adelantado N días**: según el plan, todavía no tocaba empezarlo.

La barra de cada materia muestra lo que completaste y una marca con lo que el plan espera al terminar el día de hoy.

## Estructura

```
study_tracker/
  __main__.py        punto de entrada (app / plan / camera-test)
  planner.py         horas objetivo por día y materia
  classifier.py      pantalla → estudio / distracción / neutral (+ Ollama)
  screen_monitor.py  app al frente, pestaña de Chrome, inactividad
  camera_monitor.py  MediaPipe: presencia, orientación de cabeza, celular
  engine.py          máquina de estados, conteo, pomodoro, avisos
  storage.py         SQLite (data/study.db): tiempo por estado y prácticos completados
  syllabus.py        metas por práctico (fechas y estado atrasado/al día/adelantado)
  server.py          servidor local: extensión + panel + API
  dashboard.html     panel de estadísticas
  menubar.py         ícono en la barra de menú (rumps)
extension/           extensión de Chrome (Manifest V3)
config.yaml          materias, exámenes, plan, reglas, umbrales
syllabus.yaml        prácticos de cada materia y su peso (hoja de ruta del EVA)
```

## Personalizar
Todo se ajusta en `config.yaml`: dominios y apps, palabras clave, umbrales de cámara, pomodoro y fases del plan. Después reiniciá la app.
