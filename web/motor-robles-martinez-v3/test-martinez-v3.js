const assert=require('assert');
const M=require('./martinez-v3.js');

assert.equal(M.VERSION,'3.1.0');
assert.deepEqual(M.normalizeState({affinity:99,intensity:-2}).affinity,10);
assert.equal(M.normalizeState({consent_state:'inventado'}).consent_state,'desconocido');

const everyday=M.selectModes({depth:'cotidiano',affinity:9,intensity:1},'Hola, ¿cómo estuvo tu día?');
assert.equal(everyday.length,1,'una escena cotidiana no debe cargar muchas lentes');
assert.ok(!everyday.some(x=>x.id==='contradiccion'),'no debe inventar contradicción');

const groupThreat=M.selectModes({depth:'critico',group:true,threat:9,exposure:8},'Alguien cruzó el límite delante del grupo');
assert.ok(groupThreat.some(x=>x.id==='amenaza'));
assert.ok(groupThreat.some(x=>x.id==='testigo'||x.id==='red'));

const withdrawn=M.buildInternalBlock({}, {consent_state:'retirado'}, 'no');
assert.match(withdrawn,/reduce presión/);
assert.match(withdrawn,/no inventes confesiones/);

const normal=M.buildInternalBlock({moral_declarada:'No mentir'}, {depth:'cotidiano'}, 'saludo');
assert.doesNotMatch(normal,/UN PERSONAJE MORALMENTE PERFECTO/);
const fridaLow=M.outwardVoiceState({affinity:3,intensity:1},'frida');
const fridaHigh=M.outwardVoiceState({affinity:8,intensity:10},'frida');
const cesar=M.outwardVoiceState({affinity:3,intensity:1},'julio_cesar');
const ulises=M.outwardVoiceState({affinity:3,intensity:1},'ulises');
assert.equal(fridaLow.delivery,'directa_vulnerable');
assert.ok(fridaHigh.rate>fridaLow.rate,'Frida debe ganar urgencia sin perder su identidad');
assert.notEqual(cesar.delivery,ulises.delivery,'Julio Cesar y Ulises no pueden compartir direccion vocal');
console.log('Martinez v3: todas las pruebas pasaron');
