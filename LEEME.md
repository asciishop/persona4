# Voces de Personajes en RunPod

Este paquete pone las voces de los personajes en la nube. Quien lo despliega lo
hace con **su propia cuenta de RunPod**: paga lo que use, el audio no pasa por
ningún servidor ajeno, y la clave no sale de su navegador.

No hace falta Docker ni saber nada del proyecto. RunPod construye la imagen a
partir de un repositorio de GitHub.

> Si prefieres no construir ninguna imagen y trabajar dentro de un **Pod** con
> GPU, instalando a mano, está en [LEEME-SIN-DOCKER.md](LEEME-SIN-DOCKER.md).
> Es más cómodo para trastear y más caro para dejarlo puesto: un Pod se paga
> por hora, y este camino no cobra nada mientras nadie habla.

---

## Qué es esto

`Personajes.html` es una aplicación de un solo archivo para conversar con trece
personajes: diez históricos o de ficción —Einstein, Borges, Frida Kahlo,
Cleopatra, Julio César, Juana de Arco, Medea, Ulises, Sherlock Holmes, Tesla— y
tres guardianes originales: Zinc, VA 91 y Ucron.

El texto lo genera un servidor que ya está en marcha y no hay que tocar. Lo que
este paquete despliega es **la voz**: que cada personaje suene con la suya.

## Por qué hace falta una GPU

Siete de los trece personajes no usan una voz sintética genérica: **clonan una
grabación**. Borges y Einstein clonan grabaciones históricas reales, de Radio
Universidad Nacional de La Plata, con licencia CC BY-SA. Los otros cinco clonan
referencias propias.

Clonar exige Chatterbox, que pesa 3 GB y no funciona sin tarjeta gráfica. Los
seis restantes van por Kokoro, que sí correría en CPU — pero la imagen es una
sola y manda el más exigente.

Medido en una RTX 2000 Ada de portátil: entre 1,2 y 3,2 veces el tiempo real
según el largo de la frase. Una GPU de centro de datos es bastante más rápida.

---

## Desplegar, paso a paso

### 1. Subir esta carpeta a un repositorio de GitHub

Puede ser privado. RunPod pide acceso al conectarlo.

```bash
cd Personajes-runpod
git init
git add .
git commit -m "Voces de Personajes"
git remote add origin https://github.com/TU_USUARIO/personajes-voz.git
git push -u origin main
```

### 2. Crear el punto final en RunPod

En **runpod.io → Serverless → New Endpoint → Import Git Repository**:

| Campo | Valor |
|---|---|
| Repositorio | el que acabas de subir |
| Dockerfile | `Dockerfile` (está en la raíz) |
| Tipo de trabajador | **GPU** |
| GPU | 24 GB basta. Una L4 o una A4000 van sobradas |
| Workers activos | 0 |
| Workers máximos | 1 o 2 |
| Idle timeout | 60 segundos |
| Container disk | 25 GB |

**Workers activos en 0** es lo que hace que esto sea barato: no se paga nada
mientras nadie habla. El precio es que la primera petición después de un rato
sin uso levanta el trabajador desde cero.

La primera construcción tarda: la imagen ronda los 8 GB porque lleva los
modelos horneados dentro. Es a propósito — descargarlos en cada arranque en frío
costaría más, y se pagaría cada vez.

### 3. Copiar los dos datos que hacen falta

- **El endpoint**: en la página del endpoint aparece como
  `https://api.runpod.ai/v2/XXXXXXXXXXXX`
- **La clave**: en **Settings → API Keys**, crea una y cópiala. Empieza por `rpa_`

### 4. Conectarlos en la aplicación

Abre `Personajes.html`, entra a cualquier personaje y pulsa **🔊** en la barra de
arriba. Al final del panel, en «Voces en la nube (RunPod)»:

1. Pega el endpoint y la clave
2. **Guardar**
3. **Probar**

La primera prueba puede tardar un minuto largo: está levantando tres gigas de
modelo. Las siguientes son mucho más rápidas.

Los dos datos se guardan **solo en ese navegador**. No están dentro del archivo
ni se envían a ningún otro sitio.

---

## Qué queda funcionando

**Las voces.** Cada personaje con la suya. El primer sonido llega en torno a un
segundo porque la respuesta se trocea: la primera frase se sintetiza y suena
mientras el resto se prepara detrás.

**El dictado.** El botón del micrófono transcribe con Whisper en el mismo punto
final. El audio se queda en tu cuenta de RunPod; lo que sale de ahí es el texto,
que aparece en el campo para que lo revises antes de enviarlo.

---

## Qué esperar del costo

Solo se paga el tiempo de trabajador. Con los workers activos en 0, una
conversación de veinte turnos son unos pocos minutos de GPU. Consulta el precio
por segundo de la tarjeta que elijas en la página de RunPod: cambia con el
tiempo y con la región, y cualquier número que pusiera aquí envejecería mal.

Dos cosas que sí afectan bastante:

- **El idle timeout.** Sesenta segundos mantiene el trabajador caliente entre
  turnos seguidos sin cobrar de más por una pausa larga.
- **Los workers máximos.** Con 1 basta para una persona conversando. Subirlo
  solo tiene sentido si van a hablar varias a la vez.

---

## Si algo no funciona

**«RunPod respondió HTTP 401».** La clave está mal copiada o es de otra cuenta.
Revisa que empiece por `rpa_` y que no se haya colado un espacio.

**«RunPod respondió HTTP 404».** El endpoint está mal. Tiene que ser la URL
completa hasta el identificador, sin barra al final ni `/run`.

**La prueba se queda esperando mucho.** Es normal la primera vez. Si pasa de dos
minutos, mira los *logs* del endpoint en RunPod: si dice que no encuentra la
GPU, el trabajador se creó como CPU y hay que rehacerlo.

**Suena un personaje pero otro no.** Los siete que clonan necesitan su archivo
de referencia dentro de la imagen. Si la construcción terminó bien, están: el
paso de horneado falla a propósito si falta alguno, en vez de dejar que el
personaje suene con una voz que no es la suya.

**No suena nada y no hay error.** Comprueba que la reproducción automática esté
encendida en el mismo panel 🔊, o pulsa el ▶ de un mensaje.

---

## Qué hay dentro

```
handler.py         lo que RunPod ejecuta. No reimplementa la síntesis:
                   importa voice_service.py y usa su entrada pública, para
                   que la nube y una instalación local no puedan divergir
voice_service.py   el servicio de voz completo
voz_titan.py       el carácter de máquina de los tres guardianes
voz_maquina.py     modulación de anillo, flutter y cuantización
profiles/          los trece perfiles de voz y las direcciones de actuación
references/        las grabaciones que clonan los siete personajes
hornear_voces.py   descarga los modelos al construir, para que ningún
                   arranque en frío espere una descarga ya facturada
Dockerfile         imagen con CUDA
```

Las referencias de Borges y Einstein provienen de grabaciones históricas con
licencia CC BY-SA y permiso VRT documentado; su procedencia está en
`references/FUENTES-Y-AUTENTICIDAD.md` y debe conservarse.
