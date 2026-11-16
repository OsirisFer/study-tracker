"""Notificaciones nativas de macOS vía AppleScript (no requiere empaquetar la app)."""
import subprocess


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str, sound: str | None = "Glass") -> None:
    script = f'display notification "{_esc(message)}" with title "{_esc(title)}"'
    if sound:
        script += f' sound name "{sound}"'
    # Popen: no esperamos a que termine, así el cronómetro no se traba
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
