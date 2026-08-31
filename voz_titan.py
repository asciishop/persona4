"""Carácter de titán para una voz ya sintetizada: cuerpo noble y filo metálico.

No imita a ningún actor ni clona ninguna interpretación. Reconstruye el
**diseño de sonido** que produce esa clase de voz, que es público y bien
conocido: una garganta grave, doblada consigo misma unos semitonos por debajo,
con resonancia de pecho y una modulación que introduce bandas inarmónicas.

Se separa en dos mandos, porque las dos referencias del encargo tiran hacia
lados distintos:

    nobleza   cuerpo, pecho, calma. La capa grave pesa y el filo se retira.
    filo      metal, grano, amenaza. Anillo más agudo y saturación suave.

**La capa grave es lo que más importa.** Una sola garganta, por procesada que
esté, sigue sonando a una persona con un efecto encima. Dos copias de la misma
voz separadas por unos semitonos dejan de sonar a persona sin dejar de sonar a
alguien: es lo que el oído lee como «esto es más grande que un cuerpo».

Se apoya en `voz_maquina.py` del robot de la calle para el grano de circuito
—modulación de anillo, flutter, cuantización— en vez de reescribirlo. Aquello
está calibrado para un parlante en la calle; esto añade la escala.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# voz_maquina.py viaja al lado, no se importa desde el proyecto del robot: este
# paquete tiene que poder copiarse entero a la imagen de RunPod sin arrastrar
# rutas de esta máquina.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import voz_maquina


def _pitch(x: np.ndarray, sr: int, semitonos: float) -> np.ndarray:
    """Desplaza el tono conservando la duración."""
    import librosa
    return librosa.effects.pitch_shift(y=x.astype(np.float32), sr=sr, n_steps=semitonos)


def _eq(x: np.ndarray, sr: int, hz: float, ganancia_db: float, q: float = 0.9) -> np.ndarray:
    """Campana de ecualización. Con ganancia 0 devuelve la señal intacta."""
    if abs(ganancia_db) < 0.05:
        return x
    import torch
    import torchaudio.functional as AF
    t = torch.as_tensor(x, dtype=torch.float32).unsqueeze(0)
    t = AF.equalizer_biquad(t, sr, hz, ganancia_db, Q=q)
    return t.squeeze(0).numpy()


def _saturar(x: np.ndarray, cantidad: float) -> np.ndarray:
    """Saturación suave por tangente hiperbólica.

    Añade armónicos impares, que es lo que el oído lee como «metal» y no como
    «distorsión rota», siempre que la cantidad se mantenga baja. Se compensa la
    ganancia para que no suba el volumen y parezca mejor solo por eso.
    """
    if cantidad <= 0:
        return x
    k = 1.0 + 6.0 * cantidad
    return (np.tanh(x * k) / np.tanh(k)).astype(np.float32)


def _normalizar(x: np.ndarray, pico: float = 0.95) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return x if m < 1e-6 else (x / m * pico).astype(np.float32)


def _banda(x: np.ndarray, sr: int, cantidad: float) -> np.ndarray:
    """Estrecha la señal a una banda de transmisión.

    Quita el cuerpo por abajo y el brillo por arriba, que es lo que hace que
    algo suene «llegado por un aparato» y no «dicho aquí al lado». Para VA 91
    no es un efecto: es lo que es. Vive en el 3005, hecho de señales que nadie
    recibió, y no tiene cuerpo del que salga un pecho.
    """
    if cantidad <= 0:
        return x
    grave = 180.0 + 170.0 * cantidad     # hasta ~350 Hz de corte inferior
    agudo = 5200.0 - 1600.0 * cantidad   # hasta ~3,6 kHz de corte superior
    import torch
    import torchaudio.functional as AF
    t = torch.as_tensor(x, dtype=torch.float32).unsqueeze(0)
    t = AF.highpass_biquad(t, sr, grave)
    t = AF.lowpass_biquad(t, sr, agudo)
    y = t.squeeze(0).numpy()
    # Una resonancia suave en mitad de la banda: es lo que le da el carácter de
    # bocina y no de simple filtro.
    return _eq(y, sr, 1700.0, 3.0 * cantidad, q=1.4)


def _aire(x: np.ndarray, sr: int, cantidad: float) -> np.ndarray:
    """Difunde la señal: copias muy cercanas, sin llegar a eco audible.

    No es reverberación de sala —eso pondría a Ucron en un lugar, y su asunto
    es no tener uno—. Son retardos de entre cinco y veinticinco milisegundos,
    por debajo del umbral en que el oído los separa: lo que se percibe no es
    repetición sino que la voz deja de tener un punto de origen.
    """
    if cantidad <= 0:
        return x
    y = x.copy()
    for ms, nivel in ((7.0, 0.22), (13.0, 0.16), (23.0, 0.11)):
        d = int(sr * ms / 1000.0)
        if d >= len(x):
            continue
        eco = np.zeros_like(x)
        eco[d:] = x[:-d]
        y = y + eco * (nivel * cantidad)
    y = _eq(y, sr, 6500.0, 3.5 * cantidad, q=0.7)   # brillo, no metal
    return (y / (1.0 + 0.30 * cantidad)).astype(np.float32)


def aplicar(senal: np.ndarray, sr: int, nobleza: float = 1.0, filo: float = 0.5,
            capa_semitonos: float = -5.0, capa_mezcla: float = 0.42,
            banda: float = 0.0, aire: float = 0.0) -> np.ndarray:
    """Convierte una voz limpia en voz de entidad.

    Los cuatro mandos son un espacio, no cuatro efectos sueltos: cada guardián
    es un punto distinto del mismo procedimiento.

    nobleza  0..1  cuerpo y pecho: la capa grave y los graves de la principal
    filo     0..1  metal y grano: anillo agudo, saturación y presencia
    banda    0..1  transmisión: recorta arriba y abajo hasta dejar una bocina
    aire     0..1  difusión: copias muy cercanas, sin punto de origen
    """
    x = np.asarray(senal, dtype=np.float32).copy()
    if not len(x):
        return x

    # El orden es el diseño. La primera versión ponía el pecho antes del filo y
    # medía 80% de energía bajo 250 Hz con MENOS metal que la voz limpia: los
    # graves tapaban justo lo que se buscaba. Ahora el metal se genera primero,
    # sobre la señal todavía clara, y el cuerpo se añade después sin comérselo.

    # 1. Filo, sobre la voz limpia. La saturación fabrica armónicos impares a
    #    partir de lo que hay; si antes se ha inflado el grave, fabrica barro.
    if filo > 0:
        t = np.arange(len(x), dtype=np.float32) / sr
        portadora = np.sin(2.0 * np.pi * 118.0 * t).astype(np.float32)
        mezcla = 0.20 * filo
        x = (1.0 - mezcla) * x + mezcla * (x * portadora)
        x = _saturar(x, 0.35 * filo)
        x = _eq(x, sr, 3200.0, 7.0 * filo, q=1.1)

    # 2. La capa grave. Es lo que deja de sonar a una sola persona.
    if capa_mezcla > 0 and nobleza > 0:
        grave = _pitch(x, sr, capa_semitonos)
        n = min(len(x), len(grave))
        x = x[:n].copy()
        # Va por debajo, no al lado. Se le quita un poco de agudo para que
        # aporte cuerpo y no una segunda voz audible, pero no tanto como antes:
        # cortarla en -6 dB apagaba el filo recién ganado.
        grave = _eq(grave[:n], sr, 3200.0, -3.0, q=0.7)
        x = (1.0 - capa_mezcla * nobleza) * x + (capa_mezcla * nobleza) * grave

    # 3. Pecho, con mano ligera. El tamaño lo da la capa, no el ecualizador; el
    #    realce solo lo asienta. Y se limpia el retumbe de debajo de 70 Hz, que
    #    no aporta cuerpo audible y se come el margen de volumen.
    x = _eq(x, sr, 70.0, -6.0, q=0.7)
    x = _eq(x, sr, 150.0, 2.5 * nobleza, q=0.9)
    x = _eq(x, sr, 400.0, -2.0 * nobleza, q=1.1)   # se despeja el barro del medio-grave

    # 4. Banda y aire, que son mutuamente contrarios y por eso van al final:
    #    uno cierra la señal y el otro la abre. Un personaje usa uno u otro,
    #    no los dos, y si alguien pide ambos manda el que esté más alto.
    if banda > 0:
        x = _banda(x, sr, banda)
    if aire > 0:
        x = _aire(x, sr, aire)

    # 5. El grano de circuito, del módulo que ya existe. Con intensidad baja:
    #    su anillo está en 62 Hz y a fuerza alta vuelve a llenar de grave lo que
    #    se acaba de despejar.
    intensidad = 0.18 + 0.30 * filo
    x = voz_maquina.aplicar(x, sr, intensidad=intensidad)

    return _normalizar(x)
