# Correrlo en un Pod de RunPod, sin Docker

El camino para el que está hecho este paquete es el otro: RunPod construye la
imagen a partir del `Dockerfile` y no hace falta Docker en ninguna máquina. Está
en [LEEME.md](LEEME.md) y es más barato, porque con *workers activos en 0* no se
paga nada mientras nadie habla.

Esto es la alternativa: instalar a mano dentro de un **Pod** —una máquina con
GPU encendida por horas— sin construir ninguna imagen. Sirve para probar y para
tener control directo. Cuesta más si se deja puesto.

---

## La idea

Instalar el servicio en el Pod y llegar a él de una de tres formas, que se
explican más abajo. La que casi siempre quieres es la primera: **el servicio
sirve también la aplicación**, así que se reparte una URL y se conversa —desde
un ordenador, desde un teléfono— sin instalar nada del otro lado ni mantener
ninguna ventana abierta.

Por defecto el servicio escucha **solo en `127.0.0.1`**, y para exponerlo hay
que decirlo y poner un token; si no, se niega a arrancar. Esa dirección pública
no lleva contraseña por sí misma, y detrás hay una GPU que alguien paga.

---

## El atajo

Todo lo que sigue está en un script. Si no quieres leer los siete pasos, sube
esta carpeta al Pod y corre:

```bash
bash instalar.sh
```

Hace lo mismo en orden, se detiene en cuanto algo falla en vez de dejar una
instalación a medias, y al terminar dice si los dos motores cargan. No arranca
nada por su cuenta: imprime los comandos para hacerlo, con las rutas rellenadas.

Lo de abajo es ese mismo procedimiento explicado, por si algo falla y hay que
entender qué hace cada paso.

---

## En el Pod

Crea un Pod con GPU (una plantilla de PyTorch va bien) y sube **esta carpeta**
—una sola, con todo dentro— a `/workspace/personaje-ai`. Luego, dentro:

### 1. Dependencias del sistema

`espeak-ng` es el fonemizador que Kokoro usa por debajo para el español;
`libsndfile` lo necesita `soundfile` para leer y escribir WAV.

```bash
apt-get update && apt-get install -y --no-install-recommends espeak-ng libsndfile1 ffmpeg
```

### 2. El torch correcto

**Este es el paso que la gente se salta, y el que rompe todo.** Chatterbox exige
`torch==2.6.0` y `transformers==5.2.0`. Un Pod genérico trae otro torch, y
entonces `transformers` queda descolocado: Kokoro falla al importar con un
mensaje que no explica nada —«Could not import module 'AlbertModel'»— mientras
Chatterbox arranca tan tranquilo. Parece que todo va bien hasta que seis de los
trece personajes se quedan mudos.

Son unos 2,5 GB de descarga.

```bash
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
```

### 2b. Quitar torchvision

No es un capricho, y es el paso que más desconcierta porque nada de aquí usa
torchvision —ni Chatterbox, ni Kokoro, ni Whisper—. Pero el Pod lo trae, y
compilado contra su torch original.

`transformers` lo importa **solo si lo encuentra**:

```python
if is_torchvision_available():
    from torchvision.transforms import InterpolationMode
```

Si no está, lo salta y no pasa nada. Si está y no corresponde al torch, sus
operadores no existen y el import muere con `RuntimeError: operator
torchvision::nms does not exist` — que sube por toda la cadena hasta salir
disfrazado del inofensivo «Could not import module 'AlbertModel'».

La máquina donde esto se desarrolló no tiene torchvision instalado. Dejar el Pod
igual es lo más seguro:

```bash
pip uninstall -y torchvision
```

Si por otra razón hace falta tenerlo, la versión que corresponde a torch 2.6.0
es `torchvision==0.21.0`, del mismo índice `cu124`.

### 3. El resto

Las versiones van clavadas en `requirements.txt` y no conviene aflojarlas.

```bash
pip install -r requirements.txt
```

### 4. Las dos variables que ponía la imagen

`HF_HOME` apunta dentro de `/workspace` a propósito: ese disco sobrevive al
reinicio del Pod, así que los modelos no se vuelven a descargar cada vez.

```bash
export PHONEMIZER_ESPEAK_LIBRARY=/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1 && export HF_HOME=/workspace/personaje-ai/models
```

Si `espeak-ng` quedó en otra ruta, esto la encuentra:

```bash
find / -name "libespeak-ng.so*" 2>/dev/null | head -1
```

### 5. Bajar los modelos

Unos 3,8 GB entre Chatterbox, Kokoro y Whisper. El script falla a propósito si
falta una referencia de voz, en vez de dejar que un personaje suene con una voz
que no es la suya.

```bash
python hornear_voces.py
```

### 6. Comprobar antes de arrancar

Este es el paso que dice si funcionó. No lo saltes.

```bash
python voice_service.py --diagnose
```

Tiene que aparecer `"available": true` en **los dos** motores:

```
kokoro-82m-es                available: true
chatterbox-multilingual-v3   available: true
```

Si Kokoro sigue en `false`, algo quedó mal en el paso 2 o el 2b. Para ver el
error de verdad —`transformers` lo esconde detrás de un mensaje genérico—:

```bash
python -c "from transformers.models.albert.modeling_albert import AlbertModel; print('ok')"
```

Esa traza sí dice la causa, al final del todo. Las dos que aparecen en la
práctica:

| Lo que dice al final | Qué pasa |
|---|---|
| `operator torchvision::nms does not exist` | falta el paso **2b** |
| algo sobre `torch` o CUDA | falta el paso **2** |

### 7. Arrancar

En `tmux`, para que siga vivo cuando se corte la sesión.

```bash
tmux new -s voz "python voice_service.py --port 8765"
```

---

## Tres formas de llegar al servicio

La primera es la que casi siempre quieres.

### A. Una sola URL — sin túnel, sin repartir archivos

El servicio sirve **también la aplicación**, en la misma dirección y el mismo
puerto. Se abre una URL y se conversa. Nada que instalar del otro lado, y
funciona desde un teléfono.

La aplicación viene dentro, en `web/`. No hay que indicarla ni subir nada
aparte: si esa carpeta está, el servicio la sirve.

```bash
VOICE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" python voice_service.py --host 0.0.0.0 --port 8765
```

Anota el token que imprime el arranque —sale en la línea «Enlace para
conversar»— y reparte esa dirección:

```
https://<TU-POD>-8765.proxy.runpod.net/?voz=EL_TOKEN
```

Quien la abra queda configurado sin escribir nada: el token se guarda en su
navegador y **desaparece de la barra de direcciones**, porque un token en el
historial o en una captura de pantalla deja de ser un secreto.

Lo que protege qué: la **página** se sirve sin credencial —si no, no habría
dónde escribir el token— y la **API de voz**, que es la que gasta GPU, la exige
siempre. Sin token se puede ver la aplicación, pero no hacerla hablar.

Tres cosas que conviene tener presentes:

**El registro del servidor oculta el token** en cada petición (`/?voz=[oculto]`),
porque si no, el secreto que se quita de la barra de direcciones quedaría escrito
en un archivo. Pero **la línea del arranque sí lo lleva entero** —es cómo lo lees
tú—, así que no pegues el arranque completo en un sitio público.

**No hay límite de peticiones.** Lo único que separa esa dirección de una GPU
gratis para cualquiera es el token. Si se filtra, cámbialo: basta reiniciar con
otro `VOICE_TOKEN` y repartir el enlace nuevo. Los anteriores dejan de servir.

**Quien abra el enlace conversa de verdad**, y cada turno queda registrado en el
observatorio del Motor. Van anonimizados —se guarda la medición y la respuesta,
no lo que la persona escribió— pero son indistinguibles de los tuyos. Si el
enlace circula más de lo previsto, tus datos de investigación se mezclan con
conversaciones de desconocidos.

### B. Túnel SSH — nada expuesto

El servicio escucha solo en `127.0.0.1` y se trae por SSH. Nadie más puede
alcanzarlo, no hace falta token, y la app funciona sin configurar nada. La pega
es que hay que mantener una ventana abierta.

### C. La URL pública, configurada a mano

RunPod expone los puertos en `https://<id-del-pod>-8765.proxy.runpod.net`. Para
que llegue ahí, el servicio tiene que escuchar en `0.0.0.0` — y eso lo deja al
alcance de cualquiera que dé con la dirección, que **son públicas**. Por eso el
servicio se niega a arrancar así sin un token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Guarda lo que imprima y arranca con él:

```bash
VOICE_TOKEN="<el token>" python voice_service.py --host 0.0.0.0 --port 8765
```

Después, en la app: botón **🔊**, sección **Servicio de voz remoto**. Pega la
URL del proxy y el token, **Guardar**, **Probar**. La prueba comprueba dos
cosas por separado: que el servicio esté vivo y que el token sirva, así que si
falla sabes cuál de las dos es.

Con esto la app funciona desde cualquier sitio, incluido un teléfono, sin
túnel ni terminal abierta.

> `/health` responde siempre sin token, a propósito: sirve para saber si el
> servicio está en pie antes de discutir sobre credenciales. No revela nada.

---

## Si elegiste el túnel (B)

RunPod da el comando SSH del Pod. Añádele el túnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 root@IP_DEL_POD -p PUERTO -i ~/.ssh/id_ed25519
```

Se queda abierto sin devolver el prompt. Eso está bien: mientras siga ahí, el
túnel está en pie. Si lo cierras, se acaba la voz.

---

## Abrir la aplicación

Doble clic en `Personajes.html`, en la otra carpeta.

**No configures nada en el panel 🔊**: esos campos son para RunPod Serverless. Si
los dejas vacíos, la app habla con `127.0.0.1:8765`, que ahora es el Pod.

La primera vez que hablen Borges, Einstein, Cleopatra, Frida, Julio César, Medea
o Ulises habrá una espera larga: es Chatterbox cargando tres gigas. Después va
rápido. Los otros seis responden enseguida desde el principio.

---

## Lo que se gana y lo que se pierde

|  | Pod (esto) | Serverless (LEEME.md) |
|---|---|---|
| Docker | no hace falta | tampoco: lo construye RunPod |
| Costo en reposo | se paga por hora | no se paga nada |
| Exposición | ninguna, va por SSH | endpoint con clave |
| Túnel abierto | obligatorio | no hace falta |
| Arranque en frío | ninguno, está encendido | de uno a varios minutos |

Para probar y trastear, el Pod es cómodo. Para dejarlo funcionando, Serverless
sale mucho más barato.
