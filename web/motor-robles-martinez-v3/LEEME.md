# Motor Robles–Martínez v3

Versión desacoplada de afinidad, intensidad y consentimiento. Selecciona entre una y tres lentes según el contexto y un presupuesto de profundidad. Una conversación cotidiana no está obligada a exhibir contradicciones ni dramatismo.

API pública:

- `normalizeState(state)`
- `selectModes(state, context)`
- `validateMoralField(character)`
- `buildInternalBlock(character, state, context)`
- `outwardVoiceState(state, characterId)`

La salida vocal contiene solo estado expresivo observable; nunca incluye razonamiento interno.
