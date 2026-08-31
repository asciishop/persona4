"""Descarga los dos modelos y las voces al construir la imagen.

Se ejecuta una sola vez, durante `docker build`. Si algo falla, falla acá y no
en producción: una imagen que se construye "bien" pero descarga tres gigas en
cada arranque en frío es peor que una que no se construye, porque el fallo
aparece tarde, intermitente y facturado.

Se hornean los dos motores porque el servicio usa los dos: Chatterbox para los
siete personajes con referencia clonable, Kokoro para los seis que caen al
respaldo. Las voces de Kokoro se leen de profiles/voices.json en vez de
escribirse a mano, porque una lista escrita a mano se queda corta apenas se
afine un personaje.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

perfiles = json.loads(Path("profiles/voices.json").read_text(encoding="utf-8"))["profiles"]

voces = set()
for p in perfiles:
    if p.get("voice"):
        voces.add(str(p["voice"]))
    for m in p.get("voice_mix") or []:
        if isinstance(m, dict) and m.get("voice"):
            voces.add(str(m["voice"]))

print(f"perfiles: {len(perfiles)} · voces de Kokoro a hornear: {len(voces)}")
print("  " + ", ".join(sorted(voces)))

faltan = []

from kokoro import KPipeline  # noqa: E402

pipe = KPipeline(lang_code="e")
print("Kokoro cargado")
for v in sorted(voces):
    try:
        t = pipe.load_voice(v)
        print(f"  ok   {v:14s} {tuple(t.shape)}")
    except Exception as err:
        faltan.append((v, f"{type(err).__name__}: {err}"))
        print(f"  FALLA {v:14s} {err}")

# Chatterbox: se descarga aquí para que no lo haga el primer arranque en frío.
try:
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    import inspect
    kwargs = {"device": torch.device("cpu")}   # al construir no hay GPU
    if "t3_model" in inspect.signature(ChatterboxMultilingualTTS.from_pretrained).parameters:
        kwargs["t3_model"] = "v3"
    ChatterboxMultilingualTTS.from_pretrained(**kwargs)
    print("Chatterbox descargado")
except Exception as err:
    faltan.append(("chatterbox", f"{type(err).__name__}: {err}"))
    print(f"  FALLA chatterbox: {err}")

# Whisper, para el dictado. Se hornea por lo mismo que los otros dos: que el
# primer uso no pague la descarga en un arranque en frío ya facturado.
try:
    from faster_whisper import WhisperModel
    WhisperModel("small", device="cpu", compute_type="int8")
    print("Whisper descargado")
except Exception as err:
    faltan.append(("whisper", f"{type(err).__name__}: {err}"))
    print(f"  FALLA whisper: {err}")

# Las referencias tienen que estar: sin ellas los siete que clonan caerían al
# respaldo sin avisar, y sonarían con una voz que no es la suya.
sin_referencia = []
for p in perfiles:
    cand = p.get("reference") or p.get("reference_candidate")
    if cand and not Path(str(cand)).is_file():
        sin_referencia.append(p["character_id"])
if sin_referencia:
    faltan.append(("referencias", "faltan: " + ", ".join(sin_referencia)))

if faltan:
    print("\nNo se pudo preparar la imagen:")
    for v, e in faltan:
        print(f"  {v}: {e}")
    raise SystemExit(1)

print("\nTodo horneado en la imagen.")
