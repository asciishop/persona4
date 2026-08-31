"""Voces de Personajes en RunPod Serverless.

Sintetiza la respuesta de un personaje y devuelve el WAV en base64.

No reimplementa la síntesis: importa `voice_service.py` —el mismo archivo que
corre en la máquina local— y usa su entrada pública, `submit()`. Por ahí pasan
las direcciones vocales Martínez, la elección entre Chatterbox y Kokoro según
exista o no una referencia clonable, el troceo por frases, los silencios entre
ellas y la normalización de pico. Si mañana cambia el servicio local, cambia
esto con él; no hay dos versiones que sincronizar.

Reimplementar ese camino ya salió mal una vez: un script propio llamó a Kokoro
con `voice_mix` —que es el respaldo, no la voz— y produjo el plan B de cada
personaje presentándolo como su voz. Por eso acá no se toca la síntesis.

Entrada:
    {"input": {"character": "borges", "text": "…",
               "intensity": 1..10, "affinity": 1..10}}

Salida:
    {"audio_b64": "…", "sample_rate": 24000, "character": "borges",
     "engine": "chatterbox-multilingual-v3", "cached": false, "seconds": 7.8}
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import runpod

RAIZ = Path(__file__).parent
sys.path.insert(0, str(RAIZ))

_app = None
_candado = threading.Lock()
_cache: "dict[str, tuple[str, int, float, str]]" = {}
CACHE_MAX = 200
ESPERA_MAX = 180.0


def cargar():
    """Carga perezosa con candado.

    En serverless pueden entrar dos peticiones al mismo trabajador; construir
    dos veces la aplicación cargaría el modelo dos veces y tumbaría el
    contenedor por memoria.
    """
    global _app
    if _app is not None:
        return _app
    with _candado:
        if _app is None:
            import voice_service as vs
            _app = vs.VoiceApplication(RAIZ)
    return _app


def sintetizar(cid: str, texto: str, intensidad, afinidad):
    app = cargar()
    trabajo = app.submit({
        "character_id": cid,
        "text": texto,
        "intensity": intensidad,
        "affinity": afinidad,
    })
    limite = time.monotonic() + ESPERA_MAX
    while trabajo.status in ("queued", "running"):
        if time.monotonic() > limite:
            raise RuntimeError("la síntesis agotó el tiempo de espera")
        time.sleep(0.1)
    if trabajo.status != "ready":
        detalle = (trabajo.error or {}).get("message") if isinstance(trabajo.error, dict) else trabajo.error
        raise RuntimeError(str(detalle or trabajo.status))

    salida = app.cache / trabajo.audio_name
    crudo = salida.read_bytes()

    import soundfile as sf
    info = sf.info(str(salida))
    motor = "chatterbox-multilingual-v3" if _clona(app, cid) else "kokoro-82m-es"
    return base64.b64encode(crudo).decode("ascii"), int(info.samplerate), float(info.duration), motor


def _clona(app, cid: str) -> bool:
    """Si este personaje se clona o cae al respaldo. Es informativo para el
    cliente; la decisión real la toma el servicio, no esto."""
    p = app.profiles.get(cid) or {}
    cand = p.get("reference") or p.get("reference_candidate")
    if not cand:
        return False
    ruta = (app.root / str(cand)).resolve()
    if not ruta.is_file():
        return False
    h = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return h not in app.generated_reference_hashes


def transcribir(audio_b64: str) -> dict:
    """Pasa una grabación a texto, con el Whisper que el servicio ya trae.

    Existe para que quien recibe la aplicación pueda dictar sin instalar nada.
    El audio se queda en la cuenta de RunPod de quien conversa; lo único que
    sale de aquí es el texto, que es lo que después se mide y se guarda como
    traza. Mandar el audio al servidor de otra persona habría significado que
    esa persona acumule grabaciones de voz ajenas, que es peor en todo:
    en ancho de banda, en costo y en lo que se guarda de quién.
    """
    app = cargar()
    crudo = base64.b64decode(audio_b64)
    return app.transcribe(crudo)


def handler(job):
    entrada = job.get("input") or {}

    # Dos trabajos en el mismo punto final: sintetizar y transcribir. Comparten
    # el trabajador ya caliente, así que dictar no paga otro arranque en frío.
    if str(entrada.get("accion") or "").strip() == "transcribir":
        audio = entrada.get("audio_b64")
        if not audio:
            return {"error": "falta 'audio_b64'"}
        try:
            return {"texto": (transcribir(audio) or {}).get("text", "")}
        except Exception as err:
            return {"error": f"{type(err).__name__}: {err}"}

    cid = str(entrada.get("character") or "").strip()
    if not cid:
        return {"error": "falta 'character'"}
    texto = str(entrada.get("text") or "")
    if not texto.strip():
        return {"error": "falta 'text'"}
    intensidad = entrada.get("intensity", 1)
    afinidad = entrada.get("affinity", 3)

    # La misma frase del mismo personaje con el mismo estado no se sintetiza dos
    # veces: en una conversación se repiten saludos y muletillas, y en
    # serverless cada síntesis se paga por segundo.
    clave = hashlib.sha256(f"{cid}|{intensidad}|{afinidad}|{texto}".encode()).hexdigest()
    if clave in _cache:
        b64, sr, seg, motor = _cache[clave]
        return {"audio_b64": b64, "sample_rate": sr, "seconds": seg,
                "character": cid, "engine": motor, "cached": True}

    try:
        b64, sr, seg, motor = sintetizar(cid, texto, intensidad, afinidad)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[clave] = (b64, sr, seg, motor)
    return {"audio_b64": b64, "sample_rate": sr, "seconds": seg,
            "character": cid, "engine": motor, "cached": False}


if __name__ == "__main__":
    # Precalentar en el arranque paga la carga del modelo una vez, mientras el
    # trabajador todavía no atiende a nadie. En serverless el arranque en frío
    # es lo único que de verdad se nota: acá, con el modelo ya descargado,
    # fueron veintiocho segundos.
    if os.environ.get("PRECALENTAR", "1") == "1":
        try:
            app = cargar()
            app.submit({"character_id": "borges", "text": "Listo.",
                        "intensity": 1, "affinity": 3})
            print("modelo precalentado", flush=True)
        except Exception as e:
            print("no se pudo precalentar:", e, flush=True)
    runpod.serverless.start({"handler": handler})
