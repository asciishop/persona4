# Voces de Personajes — RunPod Serverless
#
# Imagen con GPU, y no es una elección: siete de los trece personajes clonan su
# voz con Chatterbox —Borges y Einstein desde grabaciones históricas reales, los
# otros cinco desde referencias propias— y Chatterbox pesa 3 GB y no corre sin
# tarjeta. Los seis restantes van por Kokoro, que sí correría en CPU, pero la
# imagen es una sola y manda el más exigente.
#
# Medido en una RTX 2000 Ada: entre 1,2 y 1,6 veces el tiempo real, y veintiocho
# segundos de carga del modelo la primera vez.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# espeak-ng es el fonemizador que Kokoro usa por debajo para español;
# libsndfile lo necesita soundfile para leer y escribir WAV.
RUN apt-get update && apt-get install -y --no-install-recommends \
        espeak-ng libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
ENV PHONEMIZER_ESPEAK_LIBRARY=/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1

WORKDIR /app

# Las dependencias van antes que el código: un cambio en handler.py no invalida
# la capa pesada y el despliegue vuelve a subir en segundos.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El modelo y las voces se hornean en la imagen en vez de bajarse al arrancar.
# Son varios gigas más de imagen a cambio de que ningún arranque en frío espere
# una descarga de HuggingFace, que es donde se va el tiempo que se paga.
ENV HF_HOME=/app/models
COPY profiles/ ./profiles/
COPY references/ ./references/
COPY voice_service.py compat_perth.py config.json ./
COPY hornear_voces.py .
# SIN "|| true": si la descarga falla, la construcción tiene que fallar acá y no
# en producción. Una imagen que se construye "bien" y descarga tres gigas en
# cada arranque en frío es peor que una que no se construye.
RUN python hornear_voces.py

COPY handler.py .

ENV PRECALENTAR=1
CMD ["python", "-u", "handler.py"]
