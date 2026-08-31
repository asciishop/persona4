#!/usr/bin/env bash
#
# Instala el servicio de voz en un Pod de RunPod, sin Docker.
#
# Hace, en orden, todo lo que hay que hacer a mano, y se detiene en cuanto algo
# falla en vez de seguir y dejar una instalación a medias que parece correcta.
#
#   bash instalar.sh
#
# Al terminar deja el servicio listo para arrancar y dice si los dos motores
# cargan. No arranca nada por su cuenta: eso se decide después.

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$AQUI"

paso()  { printf '\n\033[1m── %s\033[0m\n' "$1"; }
bien()  { printf '   ok  %s\n' "$1"; }
mal()   { printf '\n   FALLO: %s\n\n' "$1" >&2; exit 1; }

[ -f voice_service.py ] || mal "hay que correrlo dentro de la carpeta del paquete (falta voice_service.py)"

# ── 1. Sistema ───────────────────────────────────────────────────────────
# espeak-ng es el fonemizador que Kokoro usa por debajo para el español;
# libsndfile lo necesita soundfile para leer y escribir WAV.
paso "1/6  Dependencias del sistema"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends espeak-ng libsndfile1 ffmpeg
  bien "espeak-ng, libsndfile1, ffmpeg"
else
  echo "   aviso: no hay apt-get; instálalos a mano si algo falla luego"
fi

# La ruta cambia entre imágenes, así que se busca. Pero se busca PRIMERO en las
# rutas del sistema: algunos paquetes de Python dejan una copia en /tmp, y esa
# desaparece al reiniciar el Pod. Apuntar ahí funciona hoy y falla mañana, sin
# decir por qué.
ESPEAK=""
for d in /usr/lib /usr/local/lib /lib; do
  [ -d "$d" ] || continue
  ESPEAK="$(find "$d" -name 'libespeak-ng.so*' 2>/dev/null | head -1 || true)"
  [ -n "$ESPEAK" ] && break
done
# Solo si no hay ninguna del sistema se acepta cualquier otra, avisando.
if [ -z "$ESPEAK" ]; then
  ESPEAK="$(find / -name 'libespeak-ng.so*' 2>/dev/null | grep -v '^/tmp/' | head -1 || true)"
  [ -n "$ESPEAK" ] && echo "   aviso: libespeak-ng fuera de las rutas del sistema"
fi
[ -n "$ESPEAK" ] || mal "no encuentro libespeak-ng; Kokoro no podrá fonemizar español"
export PHONEMIZER_ESPEAK_LIBRARY="$ESPEAK"
bien "libespeak-ng en $ESPEAK"

# ── 2. El torch que corresponde ──────────────────────────────────────────
# Chatterbox exige torch 2.6.0 exacto. Un Pod genérico trae otro, y entonces
# transformers queda descolocado.
paso "2/6  torch 2.6.0 + torchaudio (unos 2,5 GB)"
python -m pip install -q --root-user-action=ignore torch==2.6.0 torchaudio==2.6.0 \
  --index-url https://download.pytorch.org/whl/cu124
bien "torch $(python -c 'import torch; print(torch.__version__)')"

# ── 3. Fuera torchvision ─────────────────────────────────────────────────
# Nada de este proyecto lo usa. Pero transformers lo importa SI LO ENCUENTRA, y
# el del Pod está compilado contra otro torch: sus operadores no existen y el
# import muere con «operator torchvision::nms does not exist», que sube por toda
# la cadena hasta salir disfrazado de «Could not import module 'AlbertModel'».
# La máquina donde esto se desarrolló no lo tiene instalado.
paso "3/6  Quitar torchvision"
if python -c "import torchvision" >/dev/null 2>&1 || python -m pip show torchvision >/dev/null 2>&1; then
  python -m pip uninstall -y -q --root-user-action=ignore torchvision
  bien "desinstalado"
else
  bien "no estaba, que es como debe ser"
fi

# ── 4. El resto ──────────────────────────────────────────────────────────
paso "4/6  Requisitos del proyecto"
python -m pip install -q --root-user-action=ignore -r requirements.txt
bien "transformers $(python -c 'import transformers; print(transformers.__version__)')"

# Sin cargador perezoso de por medio: si esto pasa, Kokoro va a poder importar.
python -c "from transformers.models.albert.modeling_albert import AlbertModel" \
  || mal "AlbertModel sigue sin importar. Corre esto para ver la causa real:
     python -c \"from transformers.models.albert.modeling_albert import AlbertModel\""
bien "AlbertModel importa"

# ── 5. Modelos ───────────────────────────────────────────────────────────
# HF_HOME dentro de la carpeta a propósito: en un Pod, /workspace sobrevive al
# reinicio y el caché por defecto no. Sin esto se vuelven a bajar 3,8 GB.
paso "5/6  Modelos (unos 3,8 GB)"
export HF_HOME="$AQUI/models"
mkdir -p "$HF_HOME"
python hornear_voces.py
bien "en $HF_HOME"

# ── 6. Comprobación ──────────────────────────────────────────────────────
paso "6/6  Comprobación"
python voice_service.py --diagnose > /tmp/diagnostico.txt || mal "el diagnóstico no corrió"
python - <<'PY'
import json

# El informe sale por la salida estándar, pero Kokoro y HuggingFace escriben
# sus avisos por ahí también, antes y después. No sirve buscar «la línea que
# es un {»: el informe trae un array de motores y cada uno empieza con un {
# suelto en su propia línea.
#
# Así que no se adivina dónde empieza: se prueba a decodificar desde cada { y
# se acepta el primero que produzca un objeto válido. raw_decode ignora lo que
# venga después, así que el ruido de cualquier lado deja de importar.
crudo = open('/tmp/diagnostico.txt', encoding='utf-8', errors='replace').read()
dec = json.JSONDecoder()
d = None
for i, ch in enumerate(crudo):
    if ch != '{':
        continue
    try:
        candidato, _ = dec.raw_decode(crudo[i:])
    except ValueError:
        continue
    # El informe es el objeto que trae los motores; un { de un aviso, no.
    if isinstance(candidato, dict) and 'engines' in candidato:
        d = candidato
        break

if d is None:
    print("   No pude leer el informe. Esto es lo que salió:\n")
    print("   " + "\n   ".join(crudo.splitlines()[-25:]))
    raise SystemExit(1)
motores = {e['name']: e.get('available') for e in d.get('engines', [])}

# Solo dos hacen falta: kokoro da voz a seis personajes y chatterbox clona los
# siete restantes. El servicio puede listar otros —openvoice, por ejemplo— que
# este proyecto no usa; que no carguen no es un problema y no debe hacer fallar
# la instalación.
NECESARIOS = ('kokoro', 'chatterbox')
def necesario(nombre):
    return any(k in nombre for k in NECESARIOS)

faltan = []
for n, ok in motores.items():
    if necesario(n):
        print(f"   {'ok ' if ok else 'MAL'} {n}")
        if not ok:
            faltan.append(n)
    elif not ok:
        print(f"   --  {n} (no carga, pero este proyecto no lo usa)")

print()
if faltan:
    print("   Falta cargar: " + ", ".join(faltan))
    if any('chatterbox' in n for n in faltan):
        print("   Sin chatterbox no hablan los siete que clonan: Borges,")
        print("   Einstein, Cleopatra, Frida, Julio César, Medea, Ulises.")
    if any('kokoro' in n for n in faltan):
        print("   Sin kokoro no hablan los otros seis: Juana, Tesla,")
        print("   Sherlock, Zinc, VA 91, Ucron.")
    raise SystemExit(1)
print(f"   Los dos motores que hacen falta cargan. {d.get('profiles','?')} perfiles listos.")
PY

# El token se genera aquí y no se pide a mano: uno inventado a mano suele ser
# corto, y esta dirección es pública.
TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

cat <<EOF

── Listo ──────────────────────────────────────────────────────────────

  A) Una sola URL, sin túnel ni archivos  ← lo más cómodo

     El servicio sirve también la aplicación —está en web/, aquí
     mismo—, así que se abre una dirección y se conversa.

       export PHONEMIZER_ESPEAK_LIBRARY="$ESPEAK"
       export HF_HOME="$AQUI/models"
       export VOICE_TOKEN="$TOKEN"
       tmux new -s voz "python voice_service.py --host 0.0.0.0 --port 8765"

     Y el enlace que hay que repartir —RunPod da la dirección pública
     del puerto 8765 en la pestaña Connect—:

       https://<TU-POD>-8765.proxy.runpod.net/?voz=$TOKEN

     El token queda guardado en el navegador de quien lo abra y
     desaparece de la barra de direcciones. Sin él, la voz no responde.

  B) Túnel SSH, sin exponer nada

       export PHONEMIZER_ESPEAK_LIBRARY="$ESPEAK"
       export HF_HOME="$AQUI/models"
       tmux new -s voz "python voice_service.py --port 8765"

     Y desde tu máquina:

       ssh -N -L 8765:127.0.0.1:8765 root@IP_DEL_POD -p PUERTO -i ~/.ssh/id_ed25519

     Después abre Personajes.html desde el disco. No hace falta token,
     pero sí mantener esa ventana abierta.

EOF
