# Fuentes y autenticidad de las voces

## Estado de los diez perfiles

Los WAV creados por `GENERAR-WAV-PERSONAJES.cmd` son **recreaciones
sintéticas**, no grabaciones históricas. Parten de las voces españolas abiertas
de `hexgrad/Kokoro-82M`: `ef_dora` (femenina), `em_alex` y `em_santa`
(masculinas). Se aplican variaciones moderadas de ritmo y registro para separar
los personajes, siempre desde una base del género correcto.

Cleopatra, Juana de Arco y Julio César murieron siglos antes de la grabación de
sonido. Medea, Ulises y Sherlock Holmes son personajes míticos o ficticios.
Nikola Tesla no dispone aquí de una grabación de voz propia verificada. La voz
atribuida públicamente a Frida Kahlo no se adopta sin una acreditación sólida.

## Grabaciones históricas verificadas para una sustitución futura

- **Albert Einstein:** “03 ALBERT EINSTEIN.ogg”, voz grabada en 1943 y
  conservada por Radio Universidad Nacional de La Plata. Wikimedia Commons,
  CC BY-SA 3.0:
  https://commons.wikimedia.org/wiki/File:03_ALBERT_EINSTEIN.ogg
- **Jorge Luis Borges:** “08 JORGE LUIS BORGES.ogg”, Radio Universidad
  Nacional de La Plata. Wikimedia Commons, CC BY-SA 3.0 con permiso VRT:
  https://commons.wikimedia.org/wiki/File:08_JORGE_LUIS_BORGES.ogg

Si se importan y limpian esas fuentes, deben reemplazar únicamente
`einstein.wav` y `borges.wav`. La atribución y la licencia deben conservarse.

## Modelo de recreación

- Kokoro-82M: https://huggingface.co/hexgrad/Kokoro-82M
- Licencia de pesos: Apache 2.0.
- Catálogo de voces: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
