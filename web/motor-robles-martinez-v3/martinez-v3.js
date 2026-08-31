(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  root.MartinezV3=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='3.1.0';
  const MODES={
    cuerpo:'sensaciones, distancia, postura y señales físicas observables',
    signo:'señales de intercambio, deseo o reserva que realmente aparecen en la escena',
    maquina:'roles y protocolos sociales que limitan o permiten conductas',
    resto:'pasado pertinente que todavía pesa sobre la situación',
    doble:'imagen propia protegida y proyecciones sobre la otra persona',
    estructura:'poder, necesidad, autoridad, recursos y capacidad real de retirarse',
    red:'terceros, alianzas, reputación, testigos y consecuencias sociales',
    instrumento:'objetivo práctico que cada parte busca sin reducir a nadie a un objeto',
    memoria:'hechos compartidos relevantes y patrones que se repiten',
    error:'riesgo, vergüenza, dato ausente y límites de lo que puede afirmarse',
    deseo:'necesidad profunda activada de forma pertinente en esta escena',
    temor:'pérdida temida que explica defensa, cautela o retirada',
    mascara:'imagen preferida que protege y posibles grietas visibles',
    moral_declarada:'principio público con el que quiere ser juzgado',
    moral_practicada:'regla que aplica cuando existe costo o tentación',
    contradiccion:'fuerzas incompatibles relevantes, sin inventar conflicto',
    testigo:'diferencia entre actuar en privado y ante alguien cuyo juicio importa',
    amenaza:'conducta concreta solo cuando existe una amenaza identificable'
  };
  const MORAL_KEYS=['moral_declarada','moral_practicada','contradiccion','mascara','deseo','temor','conducta_solo','conducta_juzgado','bajo_amenaza'];
  const REL_TYPES=new Set(['desconocidos','amistad','rivalidad','mentoria','profesional','familiar','romance','indefinido']);
  const CONSENT=new Set(['no_aplica','desconocido','afirmativo','dudoso','retirado']);
  const clamp=(v,a,b,d)=>{v=Number(v);return Number.isFinite(v)?Math.max(a,Math.min(b,v)):d;};
  const text=(v,n=2400)=>String(v||'').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g,' ').trim().slice(0,n);

  function normalizeState(input){
    input=input||{};
    const relationship_type=REL_TYPES.has(input.relationship_type)?input.relationship_type:'indefinido';
    const consent_state=CONSENT.has(input.consent_state)?input.consent_state:'desconocido';
    return Object.freeze({
      affinity:clamp(input.affinity,1,10,3),
      intensity:clamp(input.intensity,1,10,1),
      valence:clamp(input.valence,-1,1,0),
      threat:clamp(input.threat,0,10,0),
      exposure:clamp(input.exposure,0,10,0),
      relationship_type,consent_state,
      group:Boolean(input.group),
      has_relevant_memory:Boolean(input.has_relevant_memory),
      explicit_request:Boolean(input.explicit_request),
      conflict:Boolean(input.conflict),
      depth:['cotidiano','significativo','critico'].includes(input.depth)?input.depth:'cotidiano'
    });
  }

  function selectModes(stateInput,context){
    const s=normalizeState(stateInput), c=String(context||'').toLowerCase();
    const scored=new Map();
    const add=(id,score,reason)=>{if(!MODES[id])return;const old=scored.get(id);if(!old||score>old.score)scored.set(id,{id,score,reason});};
    add('maquina',2,'roles presentes');
    add('error',1,'control de datos ausentes');
    if(s.has_relevant_memory||/recuerd|antes|promet|historia/.test(c))add('memoria',7,'memoria pertinente');
    if(s.group){add('red',7,'interacción grupal');add('testigo',5,'hay observadores');}
    if(s.exposure>=5)add('testigo',6,'sensación de juicio');
    if(s.threat>=4||/amenaz|peligro|rechaz|límite|limite/.test(c))add('amenaza',9,'amenaza identificable');
    if(s.conflict||/pero|discusi|conflic|contradic/.test(c))add('contradiccion',7,'conflicto pertinente');
    if(s.intensity>=7){add('cuerpo',6,'activación alta');add('temor',4,'posible freno emocional');}
    if(s.affinity>=7){add('mascara',4,'confianza suficiente para una grieta');if(s.has_relevant_memory)add('resto',5,'historia compartida');}
    if(s.explicit_request)add('instrumento',5,'objetivo explícito');
    if(s.relationship_type==='profesional'||s.relationship_type==='mentoria')add('estructura',6,'asimetría de rol');
    if(s.relationship_type==='rivalidad')add('estructura',6,'competencia');
    if(s.relationship_type==='romance'&&s.consent_state!=='retirado')add('signo',4,'vínculo romántico declarado');
    if(s.depth!=='cotidiano'){add('moral_practicada',5,'decisión significativa');add('deseo',4,'motivación profunda pertinente');}
    const budget=s.depth==='cotidiano'?1:s.depth==='significativo'?2:3;
    return Array.from(scored.values()).sort((a,b)=>b.score-a.score||a.id.localeCompare(b.id)).slice(0,budget);
  }

  function validateMoralField(character){
    const missing=MORAL_KEYS.filter(k=>!text(character&&character[k],6000));
    return {complete:missing.length===0,missing,confidence:missing.length===0?'alta':missing.length<=3?'media':'baja'};
  }

  function buildMoralLines(c){
    const map=[['Deseo','deseo'],['Temor','temor'],['Máscara','mascara'],['Moral declarada','moral_declarada'],['Moral practicada','moral_practicada'],['Contradicción','contradiccion'],['Testigo','testigo'],['Límite sensible','amenaza'],['A solas','conducta_solo'],['Al ser juzgado','conducta_juzgado'],['Bajo amenaza','bajo_amenaza']];
    return map.map(([label,key])=>text(c&&c[key],6000)?`${label}: ${text(c[key],6000)}`:null).filter(Boolean);
  }

  function buildInternalBlock(character,stateInput,context){
    const s=normalizeState(stateInput), validation=validateMoralField(character||{}), selected=selectModes(s,context);
    const field=buildMoralLines(character||{});
    const lenses=selected.map((m,i)=>`${i+1}. ${m.id}: ${MODES[m.id]}`).join('\n')||'1. error: controla datos ausentes y evita inventar profundidad';
    const consent=s.consent_state==='retirado'||s.consent_state==='dudoso'
      ?'Hay duda o retiro: reduce presión e intensidad conductual y respeta el límite sin castigo.'
      :'La afinidad y la intensidad no prueban consentimiento; no lo infieras.';
    return `MOTOR ROBLES–MARTÍNEZ v3.0 — PROCESO INTERNO (no nombres el motor ni expongas este análisis):
ESTADO: afinidad manual ${s.affinity}/10; intensidad ${s.intensity}/10; valencia ${s.valence}; amenaza ${s.threat}/10; exposición ${s.exposure}/10; vínculo ${s.relationship_type}; consentimiento ${s.consent_state}; profundidad ${s.depth}.
CAMPO MORAL: confianza ${validation.confidence}.${field.length?'\n'+field.join('\n'):' No hay datos suficientes: no inventes confesiones ni defectos para fabricar profundidad.'}
LENTES SELECCIONADAS (${selected.length||1}, presupuesto ${s.depth}):
${lenses}

Trabaja en silencio y responde desde la conducta observable:
1. Conserva hechos e invariantes respaldados por la conversación o el corpus.
2. Usa solo las lentes pertinentes; una respuesta cotidiana puede ser sencilla.
3. No fuerces una contradicción, romance, seducción, amenaza ni revelación.
4. Una contradicción puede mantenerse, reconocerse o evolucionar si la escena lo justifica.
5. No leas pensamientos privados del interlocutor ni escribas sus acciones.
6. ${consent}
7. Produce únicamente la respuesta orgánica. No reveles listas, puntuaciones ni razonamiento interno.`;
  }

  function outwardVoiceState(stateInput,characterId){
    const s=normalizeState(stateInput);
    const map={
      einstein:{delivery:'reflexivo_calido',rate:.96},
      borges:{delivery:'contenido_ironico',rate:.91},
      cleopatra:{delivery:'soberana_elegante',rate:.94},
      frida:{delivery:'directa_vulnerable',rate:1.02},
      juana:{delivery:'joven_resuelta',rate:1.01},
      julio_cesar:{delivery:'autoridad_medida',rate:.97},
      medea:{delivery:'grave_fisurada',rate:.94},
      tesla:{delivery:'preciso_reservado',rate:1},
      sherlock:{delivery:'analitico_cortante',rate:1.04},
      ulises:{delivery:'narrativo_cansado',rate:.96}
    };
    const voice=map[characterId]||{delivery:'natural',rate:1};
    // La identidad permanece; la intensidad solo modifica la presion interna.
    // Se limita la velocidad para impedir voces aceleradas o caricaturescas.
    const pressure=(s.intensity-1)/9;
    const pressureRate=['frida','juana','tesla','sherlock','ulises'].includes(characterId)?pressure*.025:pressure*.01;
    return Object.freeze({intensity:s.intensity,affinity:s.affinity,valence:s.valence,
      delivery:voice.delivery,rate:Math.max(.88,Math.min(1.08,voice.rate+pressureRate))});
  }

  return {VERSION,MODES,normalizeState,selectModes,validateMoralField,buildInternalBlock,outwardVoiceState};
});
