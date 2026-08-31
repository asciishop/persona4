"""Carácter de máquina para la voz, aplicado sobre la señal ya sintetizada.

Por qué esto vive aparte del motor de TTS: la decisión de diseño es que el
robot NO imite a una persona, sino que suene deliberadamente a máquina
simpática. Eso no se consigue eligiendo un TTS —todos los buenos apuntan a
lo contrario, a sonar humanos— sino procesando su salida. Y como el
procesamiento no depende de qué motor generó el audio, cambiar Piper por
XTTS o por lo que venga después no obliga a rehacer nada de acá.

Es la contrapartida de lo que se hizo en demo_navegador.html. Allá el
navegador no deja tocar la salida de speechSynthesis, así que el carácter
se construía por debajo con sonidos sueltos. Acá sí tenemos la onda, así
que el timbre se puede modelar de verdad.

Todo con numpy: sin dependencias de audio pesadas, y determinístico, que es
el mismo criterio del resto del proyecto (el motor mide, no adivina).
"""
from __future__ import annotations

import numpy as np

# Los valores por defecto salieron de escuchar; están expuestos para poder
# recalibrar el personaje sin tocar la lógica.
MODULACION_HZ = 62.0      # anillo: da el timbre metálico sin volverlo ininteligible
MODULACION_MEZCLA = 0.28  # cuánto del anillo se mezcla con la voz limpia
PASOS_CUANTIZACION = 512  # bitcrush suave: recuerda a un aparato, no a un error
FLUTTER_HZ = 5.5          # micro-vibrato: evita que suene a tono muerto
FLUTTER_PROFUNDIDAD = 0.0032


def _modulacion_anillo(senal: np.ndarray, sr: int, hz: float, mezcla: float) -> np.ndarray:
    """Multiplica la voz por una sinusoide grave. Es el efecto que asocia el
    oído a 'robot' desde los años 60, y funciona porque introduce bandas
    laterales inarmónicas: sonidos que ninguna garganta puede producir.
    Se mezcla con la señal original en vez de reemplazarla, si no la voz se
    vuelve muy difícil de entender en la calle, con ruido de fondo."""
    t = np.arange(len(senal), dtype=np.float32) / sr
    portadora = np.sin(2.0 * np.pi * hz * t).astype(np.float32)
    return (1.0 - mezcla) * senal + mezcla * (senal * portadora)


def _cuantizar(senal: np.ndarray, pasos: int) -> np.ndarray:
    """Reduce la resolución de amplitud. Con pasos altos (512) casi no se
    oye como distorsión, pero le saca a la señal la suavidad analógica de
    una voz grabada: el oído lo lee como 'esto salió de un circuito'."""
    if pasos <= 0:
        return senal
    return np.round(senal * pasos) / pasos


def _flutter(senal: np.ndarray, sr: int, hz: float, profundidad: float) -> np.ndarray:
    """Micro-variación de tono, por remuestreo con un desfase sinusoidal.
    Sin esto la voz procesada suena a tono muerto y resulta desagradable
    de escuchar más de dos frases; con esto suena a mecanismo vivo."""
    if profundidad <= 0:
        return senal
    n = len(senal)
    t = np.arange(n, dtype=np.float64)
    desfase = profundidad * sr / (2.0 * np.pi * hz) * np.sin(2.0 * np.pi * hz * t / sr)
    posiciones = np.clip(t + desfase, 0, n - 1)
    return np.interp(posiciones, t, senal).astype(np.float32)


def _normalizar(senal: np.ndarray, pico: float = 0.89) -> np.ndarray:
    """Deja el pico en un valor fijo. En la calle importa: el parlante va a
    estar al aire libre y una respuesta que sale más bajo que la anterior
    directamente no se escucha."""
    maximo = float(np.max(np.abs(senal))) if len(senal) else 0.0
    if maximo < 1e-6:
        return senal
    return (senal / maximo * pico).astype(np.float32)


def aplicar(senal: np.ndarray, sr: int, intensidad: float = 1.0) -> np.ndarray:
    """Convierte una voz limpia en voz de máquina.

    intensidad: 0 devuelve la voz tal cual (útil para comparar), 1 es el
    carácter calibrado. Valores intermedios escalan los efectos.
    """
    x = np.asarray(senal, dtype=np.float32).copy()
    if intensidad <= 0 or not len(x):
        return _normalizar(x)
    x = _modulacion_anillo(x, sr, MODULACION_HZ, MODULACION_MEZCLA * intensidad)
    x = _flutter(x, sr, FLUTTER_HZ, FLUTTER_PROFUNDIDAD * intensidad)
    # La cuantización va última: aplicada antes, los efectos posteriores la
    # suavizan y se pierde el grano que se buscaba.
    pasos = int(PASOS_CUANTIZACION / max(intensidad, 0.05))
    x = _cuantizar(x, pasos)
    return _normalizar(x)


# Zumbido de espera: el equivalente audible de "estoy trabajando". Suena
# mientras el modelo piensa, que es entre 5 y 20 segundos de nada.
#
# Va no verbal a propósito. Una frase durante la espera promete una respuesta
# que todavía no existe, y además invita a que la persona conteste — y ahí
# hay dos turnos encimados. Un zumbido no promete ni pregunta: solo dice que
# el aparato está encendido y ocupado, que es exactamente lo que pasa.
ZUMBIDO_HZ = (110.0, 110.7)   # dos tonos casi iguales: el batido lo vuelve
ZUMBIDO_VOLUMEN = 0.035       # "mecanismo" en vez de "tono de prueba"


class GeneradorZumbido:
    """Genera el zumbido por trozos, guardando la fase entre llamadas.

    Con fase acumulada y no recalculada desde cero en cada bloque, los
    bordes no producen el clic que suena a error en vez de a máquina.
    """

    def __init__(self, sr: int, volumen: float = ZUMBIDO_VOLUMEN):
        self.sr = sr
        self.volumen = volumen
        self._fases = np.zeros(len(ZUMBIDO_HZ), dtype=np.float64)
        self._entrada = 0.0   # rampa de entrada, para que no arranque de golpe

    def siguiente(self, n: int) -> np.ndarray:
        salida = np.zeros(n, dtype=np.float32)
        for i, hz in enumerate(ZUMBIDO_HZ):
            paso = 2.0 * np.pi * hz / self.sr
            angulos = self._fases[i] + paso * np.arange(n)
            salida += np.sin(angulos).astype(np.float32)
            self._fases[i] = float((self._fases[i] + paso * n) % (2.0 * np.pi))
        salida *= self.volumen / len(ZUMBIDO_HZ)

        # Rampa de ~0,25 s al empezar: aparecer de golpe se oye como un
        # chasquido del parlante, no como que la máquina se puso a pensar.
        if self._entrada < 1.0:
            largo = max(1, int(self.sr * 0.25))
            rampa = np.clip(self._entrada + np.arange(n) / largo, 0.0, 1.0)
            salida *= rampa.astype(np.float32)
            self._entrada = float(rampa[-1])
        return salida


def chirp(sr: int, subiendo: bool = True) -> np.ndarray:
    """Dos notas cortas para marcar que empieza o termina de hablar.
    Ascendente al despertar, descendente al terminar: ascendente se lee como
    'se encendió y está de buen humor', descendente como 'se apagó'."""
    notas = [(520.0, 0.07), (780.0, 0.09)] if subiendo else [(660.0, 0.07), (430.0, 0.10)]
    partes = []
    for hz, dur in notas:
        t = np.arange(int(sr * dur), dtype=np.float32) / sr
        onda = np.sign(np.sin(2.0 * np.pi * hz * t)).astype(np.float32) * 0.16
        # Rampas en los extremos: cortar en seco produce un clic que suena a
        # error, no a robot.
        rampa = int(sr * 0.008)
        if len(onda) > 2 * rampa:
            onda[:rampa] *= np.linspace(0, 1, rampa, dtype=np.float32)
            onda[-rampa:] *= np.linspace(1, 0, rampa, dtype=np.float32)
        partes.append(onda)
    return np.concatenate(partes)
