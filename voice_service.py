"""Servicio local de voz neuronal para Personajes.

No usa speechSynthesis ni servicios remotos. Los motores se cargan de forma
opcional y siempre se exponen únicamente en 127.0.0.1.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import inspect
import json
import logging
import os
import queue
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from compat_perth import ensure_pkg_resources_compat

VERSION = "1.4.0"
SCHEMA = "personajes.voice-catalog.v1"
MAX_TEXT_CHARS = 6000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_ORIGINS = {"null", "http://127.0.0.1:8764", "http://localhost:8764"}

# Token compartido. Vacío mientras el servicio solo escuche en 127.0.0.1, que es
# lo normal: ahí ya lo protege el sistema operativo. En cuanto escuche en otra
# interfaz —el proxy de RunPod, por ejemplo— pasa a ser obligatorio, y main() se
# niega a arrancar sin él. Sin esa regla bastaría una bandera mal puesta para
# dejar una GPU ajena abierta a cualquiera que dé con la dirección.
ACCESS_TOKEN = ""

# Rutas que no exigen token: comprobar que el servicio está vivo no revela nada
# y hace falta para diagnosticar desde fuera.
RUTAS_ABIERTAS = {"/health"}

# Carpeta con la aplicación, si se sirve desde aquí (--web). Vacía por defecto:
# el servicio es una API y solo pasa a servir páginas cuando se le pide.
WEB_ROOT: Path | None = None

TIPOS = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",   ".json": "application/json; charset=utf-8",
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".woff2": "font/woff2",
    ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
}


class VoiceError(RuntimeError):
    code = "voice_error"


class ConfigurationError(VoiceError):
    code = "configuration_error"


class EngineUnavailable(VoiceError):
    code = "engine_unavailable"


class InvalidRequest(VoiceError):
    code = "invalid_request"


class LocalWhisperEngine:
    """Spanish speech recognition kept on the local machine."""

    name = "faster-whisper-es"

    def __init__(self, root: Path, config: dict[str, Any]):
        self.root = root
        self.config = config
        self._model = None
        self._load_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self._warming = False
        try:
            from faster_whisper import WhisperModel  # type: ignore
            self._model_type = WhisperModel
            self._import_error = None
        except Exception as exc:
            self._model_type = None
            self._import_error = str(exc)

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self._import_error is None,
            "language": "es",
            "model": self.config.get("model", "small"),
            "model_loaded": self._model is not None,
            "warming": self._warming,
            "detail": self._import_error,
        }

    def _load(self) -> None:
        if self._import_error:
            raise EngineUnavailable(
                "El reconocimiento local no esta instalado. Ejecuta INSTALAR-RECONOCIMIENTO.cmd."
            )
        with self._load_lock:
            if self._model is not None:
                return
            self._warming = True
            try:
                device = self.config.get("device", "auto")
                if device == "auto":
                    try:
                        import torch  # type: ignore
                        device = "cuda" if torch.cuda.is_available() else "cpu"
                    except Exception:
                        device = "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
                self._model = self._model_type(
                    self.config.get("model", "small"),
                    device=device,
                    compute_type=compute_type,
                    download_root=str(self.root.parent / "models" / "whisper"),
                )
            except Exception as exc:
                # Some Windows CUDA installations lack the CTranslate2 runtime
                # DLLs. CPU int8 remains private and reliable as a fallback.
                logging.warning("Whisper no pudo cargar en GPU (%s); usando CPU int8", exc)
                self._model = self._model_type(
                    self.config.get("model", "small"),
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self.root.parent / "models" / "whisper"),
                )
            finally:
                self._warming = False

    def transcribe(self, raw: bytes) -> dict[str, Any]:
        self._load()
        temp_root = self.root / "transcription-temp"
        temp_root.mkdir(exist_ok=True)
        audio_path = temp_root / f"{uuid.uuid4().hex}.wav"
        audio_path.write_bytes(raw)
        started = time.monotonic()
        try:
            with self._transcribe_lock:
                segments, info = self._model.transcribe(
                    str(audio_path),
                    language="es",
                    task="transcribe",
                    beam_size=5,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
            if not text:
                raise InvalidRequest("No se detecto habla clara. Acercate al microfono e intentalo de nuevo.")
            return {
                "text": text,
                "language": getattr(info, "language", "es") or "es",
                "seconds": round(time.monotonic() - started, 3),
                "local": True,
            }
        finally:
            audio_path.unlink(missing_ok=True)


def clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def normalize_for_speech(text: str, speak_actions: bool = False) -> str:
    """Limpia marcas visuales sin reescribir el contenido del personaje."""
    text = str(text or "")
    if not speak_actions:
        text = re.sub(r"\*[^*\n]{1,500}\*", " ", text)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " enlace ", text)
    text = re.sub(r"[#_~>]", " ", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise InvalidRequest("El mensaje no contiene texto audible")
    return text


def sentence_chunks(text: str, limit: int = 420) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            parts = re.split(r"(?<=[,;:])\s+", sentence)
        else:
            parts = [sentence]
        for part in parts:
            candidate = (current + " " + part).strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def aplicar_titan(profile: dict[str, Any], salida: Path) -> None:
    """Aplica el carácter de máquina si el perfil lo pide, y si no, no toca nada.

    El bloque `titan` del perfil tiene dos mandos: `nobleza` mueve el cuerpo y
    la calma —una capa de la misma voz unos semitonos por debajo, más pecho—, y
    `filo` mueve el metal —modulación de anillo, saturación suave y presencia—.

    Existe porque para un personaje que no es una persona, clonar una garganta
    real es la decisión equivocada: siempre arrastra el acento de algún país, y
    Zinc es una conciencia reunida por magnetismo, no un señor de ningún sitio.
    Su base es sintética y sin región a propósito, y el carácter se lo da esto.

    Un fallo aquí no puede tumbar el turno: vale más el personaje hablando con
    su voz limpia que un error donde debería haber una respuesta.
    """
    conf = profile.get("titan")
    if not isinstance(conf, dict) or not conf:
        return
    try:
        import soundfile as sf
        import voz_titan
        datos, sr = sf.read(str(salida))
        if datos.ndim > 1:
            datos = datos.mean(axis=1)
        tratada = voz_titan.aplicar(
            datos, sr,
            nobleza=clamp(conf.get("nobleza"), 0.0, 1.0, 0.9),
            filo=clamp(conf.get("filo"), 0.0, 1.0, 0.5),
            capa_semitonos=clamp(conf.get("capa_semitonos"), -12.0, 0.0, -5.0),
            capa_mezcla=clamp(conf.get("capa_mezcla"), 0.0, 0.8, 0.44),
            banda=clamp(conf.get("banda"), 0.0, 1.0, 0.0),
            aire=clamp(conf.get("aire"), 0.0, 1.0, 0.0),
        )
        sf.write(str(salida), tratada, sr, subtype="FLOAT")
    except Exception:
        logging.exception("no se pudo aplicar el carácter de máquina; se deja la voz limpia")


class BaseEngine:
    name = "base"

    def __init__(self, root: Path, config: dict[str, Any]):
        self.root = root
        self.config = config

    def capabilities(self) -> dict[str, Any]:
        return {"name": self.name, "available": False}

    def synthesize(self, text: str, profile: dict[str, Any], output: Path, params: dict[str, Any]) -> None:
        raise NotImplementedError


class ChatterboxEngine(BaseEngine):
    name = "chatterbox-multilingual-v3"

    def __init__(self, root: Path, config: dict[str, Any]):
        super().__init__(root, config)
        self._model = None
        self._load_lock = threading.Lock()
        self._warming = False
        self._torch = None
        self._ta = None
        try:
            ensure_pkg_resources_compat()
            import torch  # type: ignore
            import torchaudio as ta  # type: ignore
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # type: ignore
            self._torch, self._ta, self._model_type = torch, ta, ChatterboxMultilingualTTS
            self._import_error = None
        except Exception as exc:  # paquete opcional
            self._import_error = str(exc)

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self._import_error is None,
            "device": self.config.get("device", "auto"),
            "languages": ["es"],
            "cloning": True,
            "model_loaded": self._model is not None,
            "warming": self._warming,
            "detail": self._import_error,
        }

    def _load(self) -> None:
        if self._import_error:
            raise EngineUnavailable("Chatterbox no está instalado: " + self._import_error)
        with self._load_lock:
            if self._model is not None:
                return
            self._warming = True
            try:
                device = self.config.get("device", "auto")
                if device == "auto":
                    device = "cuda" if self._torch.cuda.is_available() else "cpu"
                load_kwargs: dict[str, Any] = {"device": self._torch.device(device)}
                if "t3_model" in inspect.signature(self._model_type.from_pretrained).parameters:
                    load_kwargs["t3_model"] = "v3"
                self._model = self._model_type.from_pretrained(**load_kwargs)
            finally:
                self._warming = False

    def synthesize(self, text: str, profile: dict[str, Any], output: Path, params: dict[str, Any]) -> None:
        self._load()
        reference = profile.get("reference")
        kwargs: dict[str, Any] = {"language_id": "es"}
        if reference:
            ref_path = (self.root / reference).resolve()
            if self.root.resolve() not in ref_path.parents or not ref_path.is_file():
                raise ConfigurationError("La referencia de voz no existe o está fuera del paquete")
            kwargs["audio_prompt_path"] = str(ref_path)
        # Chatterbox acepta estos controles; se limitan para evitar caricaturas.
        acoustic = profile.get("acoustic") if isinstance(profile.get("acoustic"), dict) else {}
        # Los valores de params ya combinan identidad, intensidad y afinidad.
        # El perfil acústico fijo queda solo como compatibilidad para llamadas antiguas.
        kwargs["exaggeration"] = clamp(params.get("expressiveness", acoustic.get("expressiveness")), 0.25, 0.75, 0.5)
        kwargs["cfg_weight"] = clamp(params.get("cfg_weight", acoustic.get("cfg_weight")), 0.2, 0.65, 0.45)
        wav = self._model.generate(text, **kwargs)
        pitch = clamp(acoustic.get("pitch_semitones"), -5.0, 6.0, 0.0)
        if abs(pitch) >= 0.1:
            wav = self._ta.functional.pitch_shift(wav, self._model.sr, pitch)
        # Mismo pico fijo que KokoroEngine. Sin esto el volumen depende de lo
        # fuerte que hablara la persona de la referencia: Borges salía en 1,125
        # —recortado, con distorsión audible— y Ulises en 0,243, trece decibelios
        # más bajo. Quien conversa terminaría ajustando el volumen en cada
        # cambio de personaje.
        peak = float(wav.abs().max()) if wav.numel() else 0.0
        if peak > 0:
            wav = wav * (0.98 / peak)
        self._ta.save(str(output), wav, self._model.sr)


class KokoroEngine(BaseEngine):
    """TTS local ligero con voces nativas de mujer y hombre en español."""

    name = "kokoro-82m-es"

    def __init__(self, root: Path, config: dict[str, Any]):
        super().__init__(root, config)
        self._pipeline = None
        self._load_lock = threading.Lock()
        self._warming = False
        try:
            import torch  # type: ignore
            import torchaudio as ta  # type: ignore
            from kokoro import KPipeline  # type: ignore
            self._torch, self._ta, self._pipeline_type = torch, ta, KPipeline
            self._import_error = None
        except Exception as exc:
            self._import_error = str(exc)

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self._import_error is None,
            "device": self.config.get("device", "auto"),
            "languages": ["es"],
            "voices": ["ef_dora", "em_alex", "em_santa", "af_heart", "af_bella",
                       "af_nicole", "bf_emma", "am_fenrir", "am_michael", "am_puck",
                       "bm_fable", "bm_george", "bm_lewis"],
            "cloning": False,
            "model_loaded": self._pipeline is not None,
            "warming": self._warming,
            "detail": self._import_error,
        }

    def _load(self) -> None:
        if self._import_error:
            raise EngineUnavailable("Kokoro no está instalado: " + self._import_error)
        with self._load_lock:
            if self._pipeline is not None:
                return
            self._warming = True
            try:
                # La API oficial selecciona el dispositivo al construir el modelo.
                self._pipeline = self._pipeline_type(lang_code="e")
            finally:
                self._warming = False

    def synthesize(self, text: str, profile: dict[str, Any], output: Path, params: dict[str, Any]) -> None:
        self._load()
        voice: Any = str(profile.get("voice") or "ef_dora")
        mix = profile.get("voice_mix")
        if isinstance(mix, list) and mix:
            tensors = []
            weights = []
            for item in mix:
                if not isinstance(item, dict) or not item.get("voice"):
                    raise ConfigurationError("Mezcla Kokoro inválida")
                weight = clamp(item.get("weight"), 0.0, 1.0, 0.5)
                tensors.append(self._pipeline.load_voice(str(item["voice"])).float())
                weights.append(weight)
            total = sum(weights)
            if total <= 0:
                raise ConfigurationError("La mezcla Kokoro no tiene peso")
            voice = sum(t * (w / total) for t, w in zip(tensors, weights))
        acoustic = profile.get("acoustic") if isinstance(profile.get("acoustic"), dict) else {}
        speed = clamp(acoustic.get("speed"), 0.78, 1.08, 0.92)
        pieces = []
        for chunk in sentence_chunks(text, limit=300):
            for _graphemes, _phonemes, audio in self._pipeline(chunk, voice=voice, speed=speed):
                tensor = self._torch.as_tensor(audio, dtype=self._torch.float32).flatten().cpu()
                if tensor.numel():
                    pieces.append(tensor)
                    pieces.append(self._torch.zeros(2400, dtype=self._torch.float32))
        if not pieces:
            raise VoiceError("Kokoro no produjo audio para este texto")
        wav = self._torch.cat(pieces[:-1]).unsqueeze(0)
        # Diferenciación tímbrica local: modifica color y altura después de la
        # síntesis sin introducir voces donantes de otro idioma.
        pitch = clamp(acoustic.get("pitch_semitones"), -2.0, 2.0, 0.0)
        low_gain = clamp(acoustic.get("low_gain_db"), -4.0, 4.0, 0.0)
        high_gain = clamp(acoustic.get("high_gain_db"), -4.0, 4.0, 0.0)
        if abs(pitch) >= 0.1:
            wav = self._ta.functional.pitch_shift(wav, 24000, pitch)
        if abs(low_gain) >= 0.1:
            wav = self._ta.functional.equalizer_biquad(wav, 24000, 180.0, low_gain)
        if abs(high_gain) >= 0.1:
            wav = self._ta.functional.equalizer_biquad(wav, 24000, 3200.0, high_gain)
        peak = float(wav.abs().max()) if wav.numel() else 0.0
        if peak > 0.98:
            wav = wav * (0.98 / peak)
        self._ta.save(str(output), wav, 24000)


class OpenVoiceEngine(BaseEngine):
    """Adaptador seguro a un runner local de OpenVoice.

    OpenVoice necesita además un TTS base y checkpoints cuya disposición cambia
    entre instalaciones. El runner configurado recibe un JSON por stdin y debe
    escribir el WAV solicitado. Nunca recibe comandos construidos desde el texto.
    """
    name = "openvoice-v2"

    def capabilities(self) -> dict[str, Any]:
        runner = self.config.get("openvoice_runner")
        available = bool(runner and (self.root / runner).is_file())
        return {"name": self.name, "available": available, "languages": ["es"], "cloning": True,
                "detail": None if available else "Falta configurar openvoice_runner"}

    def synthesize(self, text: str, profile: dict[str, Any], output: Path, params: dict[str, Any]) -> None:
        import subprocess
        runner = self.config.get("openvoice_runner")
        if not runner:
            raise EngineUnavailable("OpenVoice no tiene un runner configurado")
        runner_path = (self.root / runner).resolve()
        if self.root.resolve() not in runner_path.parents or not runner_path.is_file():
            raise ConfigurationError("Runner OpenVoice inválido")
        payload = json.dumps({"text": text, "profile": profile, "params": params, "output": str(output)})
        proc = subprocess.run([sys.executable, str(runner_path)], input=payload, text=True,
                              capture_output=True, timeout=int(self.config.get("timeout_seconds", 180)))
        if proc.returncode != 0 or not output.is_file():
            raise EngineUnavailable("OpenVoice falló: " + (proc.stderr[-500:] or "sin audio"))


@dataclass
class Job:
    id: str
    character_id: str
    text: str
    params: dict[str, Any]
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    error: dict[str, str] | None = None
    audio_name: str | None = None
    cancel_requested: bool = False

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "character_id": self.character_id, "status": self.status,
                "created_at": self.created_at, "error": self.error,
                "audio_url": f"/v1/audio/{self.audio_name}" if self.audio_name else None}


class VoiceApplication:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._catalog_lock = threading.Lock()
        self.config = self._read_json(self.root / "config.json")
        self.catalog = self._read_json(self.root / "profiles" / "voices.json")
        if self.catalog.get("schema") != SCHEMA:
            raise ConfigurationError("Catálogo de voces incompatible")
        profiles = self.catalog.get("profiles")
        if not isinstance(profiles, list):
            raise ConfigurationError("Catálogo sin perfiles")
        self.profiles = {p["character_id"]: p for p in profiles if isinstance(p, dict) and p.get("character_id")}
        directions_path = self.root / "profiles" / "martinez-voice-directions.json"
        self._apply_voice_directions(directions_path)
        manifest_path = self.root / "references" / "manifest.json"
        try:
            manifest = self._read_json(manifest_path)
            self.generated_reference_hashes = {
                str(item.get("sha256")) for item in manifest.get("files", [])
                if isinstance(item, dict) and item.get("sha256")
            }
        except ConfigurationError:
            self.generated_reference_hashes = set()
        self._catalog_stamp = self._file_stamp(self.root / "profiles" / "voices.json")
        self._manifest_stamp = self._file_stamp(manifest_path)
        self._directions_stamp = self._file_stamp(directions_path)
        self.cache = self.root / "cache"
        self.cache.mkdir(exist_ok=True)
        engine_cfg = self.config.get("engine", {})
        self.engines: dict[str, BaseEngine] = {
            "kokoro-82m-es": KokoroEngine(self.root, engine_cfg),
            "chatterbox-multilingual-v3": ChatterboxEngine(self.root, engine_cfg),
            "openvoice-v2": OpenVoiceEngine(self.root, engine_cfg),
        }
        self.speech_recognition = LocalWhisperEngine(
            self.root, self.config.get("speech_recognition", {})
        )
        self.jobs: dict[str, Job] = {}
        self.jobs_lock = threading.Lock()
        self.work: queue.Queue[str] = queue.Queue(maxsize=50)
        self.worker = threading.Thread(target=self._worker, daemon=True, name="voice-worker")
        self.worker.start()
        kokoro = self.engines.get("kokoro-82m-es")
        if kokoro and self.config.get("engine", {}).get("prewarm", True):
            threading.Thread(target=self._prewarm, args=(kokoro,), daemon=True, name="voice-prewarm").start()

    @staticmethod
    def _file_stamp(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _reload_voice_files(self) -> None:
        """Reload profiles and generated-reference hashes after file changes."""
        catalog_path = self.root / "profiles" / "voices.json"
        manifest_path = self.root / "references" / "manifest.json"
        directions_path = self.root / "profiles" / "martinez-voice-directions.json"
        catalog_stamp = self._file_stamp(catalog_path)
        manifest_stamp = self._file_stamp(manifest_path)
        directions_stamp = self._file_stamp(directions_path)
        if (catalog_stamp == self._catalog_stamp and manifest_stamp == self._manifest_stamp
                and directions_stamp == self._directions_stamp):
            return
        with self._catalog_lock:
            catalog = self._read_json(catalog_path)
            if catalog.get("schema") != SCHEMA:
                raise ConfigurationError("Catalogo de voces incompatible")
            profiles = catalog.get("profiles")
            if not isinstance(profiles, list):
                raise ConfigurationError("Catalogo sin perfiles")
            self.catalog = catalog
            self.profiles = {
                p["character_id"]: p for p in profiles
                if isinstance(p, dict) and p.get("character_id")
            }
            self._apply_voice_directions(directions_path)
            try:
                manifest = self._read_json(manifest_path)
                self.generated_reference_hashes = {
                    str(item.get("sha256")) for item in manifest.get("files", [])
                    if isinstance(item, dict) and item.get("sha256")
                }
            except ConfigurationError:
                self.generated_reference_hashes = set()
            self._catalog_stamp = catalog_stamp
            self._manifest_stamp = manifest_stamp
            self._directions_stamp = directions_stamp

    def _apply_voice_directions(self, path: Path) -> None:
        """Attach the Martinez inner tension to acoustic delivery."""
        data = self._read_json(path)
        if data.get("schema") != "personajes.martinez-voice-directions.v1":
            raise ConfigurationError("Direcciones vocales Martinez incompatibles")
        directions = data.get("directions")
        if not isinstance(directions, dict):
            raise ConfigurationError("Direcciones vocales Martinez incompletas")
        for character_id, direction in directions.items():
            profile = self.profiles.get(character_id)
            if not profile or not isinstance(direction, dict):
                continue
            profile["martinez_voice"] = direction
            profile["profile_version"] = max(
                int(profile.get("profile_version", 0)),
                int(direction.get("profile_version", 0)),
            )
            acoustic = profile.setdefault("acoustic", {})
            acoustic["pitch_semitones"] = direction.get(
                "pitch_semitones", acoustic.get("pitch_semitones", 0.0)
            )

    @staticmethod
    def _prewarm(engine: BaseEngine) -> None:
        try:
            load = getattr(engine, "_load", None)
            if callable(load):
                load()
            logging.info("Modelo %s precargado", engine.name)
        except Exception:
            logging.exception("No se pudo precargar %s; se reintentará al sintetizar", engine.name)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigurationError(f"No se pudo leer {path.name}: {exc}") from exc

    def health(self) -> dict[str, Any]:
        self._reload_voice_files()
        return {"ok": True, "version": VERSION, "service": "personajes-local-voice",
                "profiles": len(self.profiles), "queue": self.work.qsize(),
                "engines": [e.capabilities() for e in self.engines.values()],
                "speech_recognition": self.speech_recognition.capabilities()}

    def voices(self) -> dict[str, Any]:
        self._reload_voice_files()
        public = []
        for p in self.profiles.values():
            public.append({k: p.get(k) for k in ("character_id", "display_name", "engine", "authenticity", "profile_version")})
        return {"schema": SCHEMA, "profiles": public}

    def submit(self, payload: dict[str, Any]) -> Job:
        self._reload_voice_files()
        character_id = str(payload.get("character_id") or "").strip()
        profile = self.profiles.get(character_id)
        if not profile:
            raise InvalidRequest("No existe un perfil de voz para ese personaje")
        raw = str(payload.get("text") or "")
        if not raw or len(raw) > MAX_TEXT_CHARS:
            raise InvalidRequest(f"El texto debe tener entre 1 y {MAX_TEXT_CHARS} caracteres")
        text = normalize_for_speech(raw, bool(payload.get("speak_actions")))
        intensity = clamp(payload.get("intensity"), 1, 10, 1)
        affinity = clamp(payload.get("affinity"), 1, 10, 3)
        # Martinez: intensidad presiona la mascara; afinidad permite una grieta.
        # Cada personaje responde de forma propia, sin convertir intensidad en grito.
        direction = profile.get("martinez_voice") if isinstance(profile.get("martinez_voice"), dict) else {}
        intensity_n = (intensity - 1.0) / 9.0
        affinity_n = (affinity - 1.0) / 9.0
        bounded_expression = (
            clamp(direction.get("base_expressiveness"), 0.25, 0.7, 0.36)
            + clamp(direction.get("intensity_gain"), -0.2, 0.3, 0.12) * intensity_n
            + clamp(direction.get("affinity_gain"), -0.1, 0.15, 0.04) * affinity_n
        )
        bounded_cfg = (
            clamp(direction.get("base_cfg_weight"), 0.25, 0.65, 0.5)
            + clamp(direction.get("cfg_intensity_gain"), -0.15, 0.1, -0.04) * intensity_n
        )
        params = {
            "intensity": intensity,
            "affinity": affinity,
            "expressiveness": clamp(payload.get("expressiveness"), 0.25, 0.75, bounded_expression),
            "cfg_weight": clamp(payload.get("cfg_weight"), 0.2, 0.65, bounded_cfg),
            "delivery": str(direction.get("delivery") or "natural")[:80],
        }
        job = Job(id=uuid.uuid4().hex, character_id=character_id, text=text, params=params)
        with self.jobs_lock:
            self.jobs[job.id] = job
        try:
            self.work.put_nowait(job.id)
        except queue.Full as exc:
            with self.jobs_lock:
                self.jobs.pop(job.id, None)
            raise VoiceError("La cola de voz está llena") from exc
        return job

    def transcribe(self, raw: bytes) -> dict[str, Any]:
        if not raw.startswith(b"RIFF") or raw[8:12] != b"WAVE":
            raise InvalidRequest("El dictado debe enviarse como audio WAV")
        return self.speech_recognition.transcribe(raw)

    def cancel(self, job_id: str) -> Job:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                raise InvalidRequest("Trabajo desconocido")
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
            return job

    def _worker(self) -> None:
        while True:
            job_id = self.work.get()
            try:
                with self.jobs_lock:
                    job = self.jobs.get(job_id)
                if not job or job.cancel_requested:
                    continue
                job.status = "running"
                self._reload_voice_files()
                profile = dict(self.profiles[job.character_id])
                engine_name = profile.get("engine") or self.config.get("engine", {}).get("default")
                if engine_name == "hybrid-local":
                    candidate = profile.get("reference") or profile.get("reference_candidate")
                    candidate_path = (self.root / str(candidate)).resolve() if candidate else None
                    candidate_hash = None
                    if candidate_path and candidate_path.is_file():
                        candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
                    if (candidate_path and self.root in candidate_path.parents
                            and candidate_path.is_file() and candidate_path.suffix.lower() == ".wav"
                            and candidate_hash not in self.generated_reference_hashes):
                        profile["reference"] = str(candidate)
                        # La referencia forma parte de la identidad de voz. Incluir su
                        # contenido en la clave evita servir un WAV antiguo si el
                        # usuario reemplaza la muestra conservando el mismo nombre.
                        profile["_reference_sha256"] = candidate_hash
                        engine_name = "chatterbox-multilingual-v3"
                    else:
                        engine_name = "kokoro-82m-es"
                engine = self.engines.get(engine_name)
                if not engine:
                    raise ConfigurationError("Motor desconocido: " + str(engine_name))
                key_material = json.dumps({"engine": engine_name, "profile": profile, "text": job.text,
                                           "params": job.params}, sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
                output = self.cache / f"{digest}.wav"
                if not output.is_file():
                    chunks = sentence_chunks(job.text)
                    if len(chunks) > 1:
                        # Mantener una sola inferencia por ahora evita uniones WAV corruptas.
                        # El motor conserva mejor la prosodia con el texto completo dentro del límite.
                        logging.info("Mensaje dividido lógicamente en %d segmentos", len(chunks))
                    engine.synthesize(job.text, profile, output, job.params)
                    # Carácter de máquina sobre la señal ya sintetizada. Va
                    # aquí, después del motor y no dentro de él, por dos
                    # razones: vale igual para Chatterbox y para Kokoro sin
                    # duplicar nada, y deja separado lo que el modelo produce
                    # de lo que le hacemos después — que es justo lo que
                    # permite ajustar un personaje sin volver a sintetizar.
                    #
                    # Solo se aplica a quien lo declare en su perfil. Un
                    # personaje histórico no tiene por qué enterarse de que
                    # esto existe.
                    aplicar_titan(profile, output)
                if job.cancel_requested:
                    job.status = "cancelled"
                else:
                    job.audio_name = output.name
                    job.status = "ready"
                    self._trim_cache()
            except Exception as exc:
                logging.error("Trabajo %s falló: %s\n%s", job_id, exc, traceback.format_exc())
                if job:
                    job.status = "error"
                    job.error = {"code": getattr(exc, "code", "synthesis_failed"), "message": str(exc)[:500]}
            finally:
                self.work.task_done()

    def _trim_cache(self) -> None:
        max_bytes = int(self.config.get("cache", {}).get("max_mb", 2048)) * 1024 * 1024
        files = sorted(self.cache.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= max_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except OSError:
                logging.warning("No se pudo limpiar %s", path)


class Handler(BaseHTTPRequestHandler):
    app: VoiceApplication
    server_version = "PersonajesVoice/1.0"

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "null")
        # Con token, quien autoriza es el token y no el origen: la página puede
        # estar servida desde cualquier sitio. Sin token el servicio es local, y
        # entonces la lista blanca sigue siendo la única defensa.
        if ACCESS_TOKEN or origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")

    def _autorizado(self) -> bool:
        """Comprueba el token, si lo hay. Sin token configurado, todo pasa."""
        if not ACCESS_TOKEN:
            return True
        ruta = urlparse(self.path).path
        if ruta in RUTAS_ABIERTAS:
            return True
        # Los archivos de la aplicación se sirven sin credencial: si la página
        # no puede cargarse, no hay dónde escribir el token. Lo que se protege
        # es la API —la que gasta GPU—, no el HTML que la usa.
        if WEB_ROOT is not None and not ruta.startswith(("/v1/", "/health")):
            return True
        cabecera = self.headers.get("Authorization", "")
        enviado = cabecera[7:] if cabecera.startswith("Bearer ") else self.headers.get("X-Voice-Token", "")
        # compare_digest en vez de ==: comparar cadena a cadena filtra el token
        # por el tiempo que tarda en fallar.
        return hmac.compare_digest(enviado.strip(), ACCESS_TOKEN)

    def _rechazar(self) -> None:
        self._json(401, {"error": {"code": "unauthorized",
                                   "message": "Falta o es inválido el token de voz."}})

    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise InvalidRequest("Content-Length inválido") from exc
        if length <= 0 or length > 100_000:
            raise InvalidRequest("Solicitud vacía o demasiado grande")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            raise InvalidRequest("JSON inválido") from exc
        if not isinstance(value, dict):
            raise InvalidRequest("Se esperaba un objeto JSON")
        return value

    def _audio_payload(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise InvalidRequest("Content-Length invalido") from exc
        if length <= 44 or length > MAX_AUDIO_BYTES:
            raise InvalidRequest("El audio esta vacio o es demasiado grande")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/wav", "audio/wave", "audio/x-wav"}:
            raise InvalidRequest("Formato de audio no compatible; se esperaba WAV")
        return self.rfile.read(length)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        # Sin Authorization aquí, el navegador ni siquiera intenta la petición
        # real cuando lleva token: el preflight la corta antes.
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Voice-Token")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._autorizado():
            self._rechazar()
            return
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._json(200, self.app.health())
            elif path == "/v1/capabilities":
                self._json(200, {"engines": [e.capabilities() for e in self.app.engines.values()],
                                 "speech_recognition": self.app.speech_recognition.capabilities()})
            elif path == "/v1/voices":
                self._json(200, self.app.voices())
            elif path.startswith("/v1/jobs/"):
                job = self.app.jobs.get(path.rsplit("/", 1)[-1])
                if not job:
                    raise InvalidRequest("Trabajo desconocido")
                self._json(200, job.public())
            elif path.startswith("/v1/audio/"):
                name = path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[a-f0-9]{64}\.wav", name):
                    raise InvalidRequest("Audio inválido")
                audio = self.app.cache / name
                if not audio.is_file():
                    raise InvalidRequest("Audio no encontrado")
                raw = audio.read_bytes()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif WEB_ROOT is not None and self._servir_estatico(path):
                pass
            else:
                self._json(404, {"error": {"code": "not_found", "message": "Ruta desconocida"}})
        except VoiceError as exc:
            self._json(400, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:
            logging.exception("Fallo en solicitud GET")
            self._json(500, {"error": {"code": "internal_error", "message": str(exc)[:500]}})

    def _servir_estatico(self, ruta: str) -> bool:
        """Entrega un archivo de la aplicación. Devuelve False si no lo hay."""
        rel = ruta.lstrip("/") or "Personajes.html"
        destino = (WEB_ROOT / rel).resolve()
        # Sin esto, «/../../etc/passwd» saldría de la carpeta. resolve() deshace
        # los .. y la comparación comprueba que sigue dentro.
        try:
            destino.relative_to(WEB_ROOT.resolve())
        except ValueError:
            return False
        if destino.is_dir():
            destino = destino / "Personajes.html"
        if not destino.is_file():
            return False
        raw = destino.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", TIPOS.get(destino.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        # La aplicación cambia cuando se actualiza el paquete; que el navegador
        # no se quede con una versión vieja que ya no habla con este servicio.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(raw)
        return True

    def do_POST(self) -> None:
        if not self._autorizado():
            self._rechazar()
            return
        try:
            path = urlparse(self.path).path
            if path == "/v1/transcriptions":
                self._json(200, self.app.transcribe(self._audio_payload()))
                return
            if path not in {"/v1/synthesis", "/v1/preview"}:
                self._json(404, {"error": {"code": "not_found", "message": "Ruta desconocida"}})
                return
            job = self.app.submit(self._payload())
            self._json(202, job.public())
        except VoiceError as exc:
            self._json(400, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:
            logging.exception("Fallo en solicitud POST")
            self._json(500, {"error": {"code": "internal_error", "message": str(exc)[:500]}})

    def do_DELETE(self) -> None:
        if not self._autorizado():
            self._rechazar()
            return
        try:
            path = urlparse(self.path).path
            if not path.startswith("/v1/jobs/"):
                self._json(404, {"error": {"code": "not_found", "message": "Ruta desconocida"}})
                return
            self._json(200, self.app.cancel(path.rsplit("/", 1)[-1]).public())
        except VoiceError as exc:
            self._json(400, {"error": {"code": exc.code, "message": str(exc)}})

    def log_message(self, fmt: str, *args: Any) -> None:
        # El token viaja en la URL la primera vez («/?voz=…»). La app lo borra
        # de la barra de direcciones al abrirse, pero el servidor registraría
        # la línea entera en cada visita: el secreto que se quita de un sitio
        # quedaría escrito en otro, y en un archivo que suele acabar pegado en
        # un chat cuando algo falla.
        texto = fmt % args
        texto = re.sub(r"([?&](?:voz|token)=)[^&\s\"]*", r"\1[oculto]", texto)
        logging.info("%s - %s", self.address_string(), texto)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--diagnose", action="store_true")
    # Por defecto sigue siendo solo localhost: nada cambia para quien ya lo usa
    # en su máquina. Exponerlo tiene que ser una decisión escrita, no un descuido.
    parser.add_argument("--host", default="127.0.0.1",
                        help="interfaz de escucha; 0.0.0.0 para exponerlo (exige --token)")
    parser.add_argument("--token", default=os.environ.get("VOICE_TOKEN", ""),
                        help="token compartido; también se puede pasar por VOICE_TOKEN")
    # Con esto el servicio deja de ser solo una API: sirve la aplicación en la
    # misma dirección. Se abre la URL y se conversa, sin repartir archivos.
    parser.add_argument("--web", type=Path, default=os.environ.get("VOICE_WEB") or None,
                        help="carpeta con Personajes.html, para servir la app aquí mismo")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = VoiceApplication(args.root)
    if args.diagnose:
        report = app.health()
        report.update({"python": sys.version, "platform": sys.platform, "disk_free_bytes": shutil.disk_usage(args.root).free,
                       "hostname": socket.gethostname()})
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    global ACCESS_TOKEN, WEB_ROOT
    ACCESS_TOKEN = str(args.token or "").strip()

    # La aplicación vive en web/, junto al servicio. No hay que indicarla: si
    # está, se sirve. --web queda para quien la tenga en otro sitio.
    candidata = Path(args.web).expanduser() if args.web else (args.root / "web")
    if args.web or candidata.is_dir():
        raiz = candidata.resolve()
        if not (raiz / "Personajes.html").is_file():
            if args.web:
                parser.error(f"--web {raiz} no contiene Personajes.html")
        else:
            WEB_ROOT = raiz

    # La regla que impide el accidente: escuchar fuera de localhost sin token
    # deja una GPU abierta a quien dé con la dirección, y la factura es de otro.
    # Mejor no arrancar que arrancar mal.
    if args.host not in ("127.0.0.1", "localhost", "::1") and not ACCESS_TOKEN:
        parser.error(
            f"--host {args.host} expone el servicio fuera de esta máquina y no hay token.\n"
            "  Genera uno y vuelve a intentarlo:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "    VOICE_TOKEN=<el token> python voice_service.py --host 0.0.0.0 --port 8765"
        )

    Handler.app = app
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if ACCESS_TOKEN:
        logging.info("Servicio de voz %s en http://%s:%d — con token", VERSION, args.host, args.port)
    else:
        logging.info("Servicio de voz %s en http://%s:%d", VERSION, args.host, args.port)
    if WEB_ROOT is not None:
        logging.info("Aplicación servida desde %s", WEB_ROOT)
        if ACCESS_TOKEN:
            # El enlace que hay que repartir: lleva el token puesto, la app lo
            # guarda al abrirse y lo borra de la barra de direcciones.
            logging.info("Enlace para conversar:  http://<esta-direccion>:%d/?voz=%s",
                         args.port, ACCESS_TOKEN)
    server.serve_forever()


if __name__ == "__main__":
    main()
