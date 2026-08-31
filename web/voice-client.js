(function(root){
  'use strict';
  // La dirección del servicio dejó de estar clavada: puede ser esta máquina,
  // un Pod remoto o cualquier servidor propio. Se lee en cada llamada, no una
  // vez al cargar, para que cambiarla en el panel surta efecto sin recargar.
  const BASE_LOCAL='http://127.0.0.1:8765';
  function vozDirecta(){
    try{
      const c=JSON.parse(localStorage.getItem('personajes_voz_directa')||'null');
      if(c&&c.url) return {url:String(c.url).replace(/\/+$/,''), token:String(c.token||'')};
    }catch(e){}
    return null;
  }
  function base(){ const d=vozDirecta(); return d?d.url:BASE_LOCAL; }

  // ── Configuración por enlace ─────────────────────────────────────────
  // Con «?voz=TOKEN» la página queda lista sin que nadie escriba nada. La
  // dirección no se pregunta: si la app se sirve desde el mismo sitio que la
  // voz —que es el caso para el que existe esto— la dirección es la de la
  // propia página. Con «?vozurl=» se puede indicar otra distinta.
  (function(){
    try{
      const p = new URLSearchParams(location.search);
      const tk = p.get('voz');
      if(!tk) return;
      const urlDada = (p.get('vozurl')||'').trim().replace(/\/+$/,'');
      const url = /^https?:\/\//.test(urlDada) ? urlDada : location.origin;
      // file:// no tiene origen utilizable: ahí el enlace no puede resolver la
      // dirección solo, y hay que darla con ?vozurl=.
      if(!/^https?:\/\//.test(url)) return;
      localStorage.setItem('personajes_voz_directa',
                           JSON.stringify({url:url, token:String(tk).trim()}));
      // Fuera de la barra de direcciones: el token deja de ser un secreto en
      // cuanto queda en el historial o en una captura de pantalla.
      p.delete('voz'); p.delete('vozurl');
      const limpia = location.pathname + (p.toString()?'?'+p.toString():'') + location.hash;
      history.replaceState(null, '', limpia);
    }catch(e){}
  })();
  // El token solo se manda si existe. Un servicio en localhost no lo pide, y
  // mandarlo de todos modos lo dejaría escrito en registros que no hacen falta.
  function cabecerasVoz(){
    const d=vozDirecta();
    return (d&&d.token)?{'Authorization':'Bearer '+d.token}:{};
  }
  const state={health:null,currentJob:null,cola:null,audio:null,autoplay:false,speakActions:false,queueMode:'interrupt',rate:0.86,lastError:null};
  try{
    const saved=JSON.parse(localStorage.getItem('personajes_voice_prefs')||'{}');
    state.autoplay=Boolean(saved.autoplay);state.speakActions=Boolean(saved.speakActions);
    state.queueMode=saved.queueMode==='queue'?'queue':'interrupt';
    const rate=Number(saved.rate);if(Number.isFinite(rate)&&rate>=0.72&&rate<=1.08)state.rate=rate;
  }catch(e){}
  const save=()=>localStorage.setItem('personajes_voice_prefs',JSON.stringify({autoplay:state.autoplay,speakActions:state.speakActions,queueMode:state.queueMode,rate:state.rate}));
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const msg=x=>String(x||'').replace(/[<>]/g,'');

  async function request(path,options){
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch(base()+path,{...(options||{}),signal:controller.signal,headers:{'Content-Type':'application/json',...cabecerasVoz(),...((options&&options.headers)||{})}});
      let data={};try{data=await response.json();}catch(e){}
      if(!response.ok)throw new Error(data.error&&data.error.message||`Error local ${response.status}`);
      return data;
    }finally{clearTimeout(timer);}
  }
  async function checkHealth(){
    try{state.health=await request('/health');state.lastError=null;}
    catch(e){state.health=null;state.lastError=e.message||'Servicio no disponible';}
    updateHeader();return state.health;
  }
  function updateHeader(){
    const b=document.getElementById('voice-settings-btn');if(!b)return;
    b.style.color=state.health?'#3B6D11':'#aaa';
    b.title=state.health?'Voces neuronales locales listas':'Voz local no disponible; abrir diagnóstico';
  }
  function characterFor(entry){
    if(entry&&entry.charId)return entry.charId;
    if(root.activeChar&&root.activeChar.id)return root.activeChar.id;
    return '';
  }
  function intensityFor(id){return typeof root.getExcitacion==='function'?root.getExcitacion(id):1;}
  async function cancel(){
    state.cola=null;   // corta el encadenado del turno anterior
    if(state.currentJob){try{await request('/v1/jobs/'+state.currentJob,{method:'DELETE'});}catch(e){}state.currentJob=null;}
    if(state.audio){state.audio.pause();state.audio.currentTime=0;state.audio=null;}
    document.querySelectorAll('[data-voice-playing="1"]').forEach(b=>{b.dataset.voicePlaying='0';b.textContent='▶';});
  }
  // ── Transporte: servicio local o RunPod ──────────────────────────────
  // La clave de RunPod vive en este navegador y en ningún otro sitio: no está
  // en el archivo que se reparte ni pasa por el servidor del Motor. Se
  // comprobó que api.runpod.ai admite llamadas desde el navegador
  // (access-control-allow-origin: *) y acepta la cabecera Authorization, así
  // que no hace falta intermediario — y meter uno significaría que la clave de
  // alguien viaje por una máquina ajena.
  function runpodConf(){
    try{
      const raw = localStorage.getItem('personajes_runpod');
      if(!raw) return null;
      const c = JSON.parse(raw);
      if(!c || !c.endpoint || !c.apiKey) return null;
      return {endpoint:String(c.endpoint).replace(/\/+$/,''), apiKey:String(c.apiKey)};
    }catch(e){ return null; }
  }
  function usaRunpod(){ return !!runpodConf(); }

  // Un trozo por llamada, igual que en local. RunPod cobra por segundo de
  // trabajador, así que el troceo aquí no solo adelanta el primer sonido:
  // también deja de pagar por un silencio largo antes de oír nada.
  async function runpodSintetizar(texto, id, voiceState){
    const c = runpodConf();
    const r = await fetch(c.endpoint + '/run', {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+c.apiKey},
      body: JSON.stringify({input:{character:id, text:texto,
        intensity:voiceState.intensity, affinity:voiceState.affinity}})
    });
    if(!r.ok) throw new Error('RunPod respondió HTTP '+r.status);
    const j = await r.json();
    if(!j.id) throw new Error('RunPod no devolvió un identificador de trabajo');
    return j.id;
  }

  async function runpodEsperar(jobId){
    const c = runpodConf();
    // El primer arranque en frío trae diez gigas de imagen y carga tres en la
    // tarjeta. Diez minutos, no cinco: con cinco, la primerísima petición de
    // quien acaba de montar el punto final se abortaba justo mientras RunPod
    // seguía levantando el trabajador —y se pagaba igual—, con lo que el
    // estreno parecía un fallo cuando era solo el arranque.
    for(let i=0;i<1200;i++){
      const r = await fetch(c.endpoint + '/status/' + jobId,
                            {headers:{'Authorization':'Bearer '+c.apiKey}});
      if(!r.ok) throw new Error('RunPod respondió HTTP '+r.status);
      const j = await r.json();
      if(j.status === 'COMPLETED'){
        const salida = j.output || {};
        if(salida.error) throw new Error(String(salida.error));
        if(!salida.audio_b64) throw new Error('RunPod no devolvió audio');
        return salida;
      }
      if(j.status === 'FAILED' || j.status === 'CANCELLED')
        throw new Error('RunPod: ' + (j.error || j.status));
      await wait(500);
    }
    throw new Error('La síntesis en RunPod agotó el tiempo de espera');
  }

  // El audio llega en base64 y se convierte en una URL de objeto, que es lo que
  // el reproductor espera. Se libera al terminar: sin eso, una conversación
  // larga deja decenas de megas retenidos en memoria.
  function urlDesdeBase64(b64){
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
    return URL.createObjectURL(new Blob([bytes], {type:'audio/wav'}));
  }

  // ── Troceo por frases ────────────────────────────────────────────────
  // El primer trozo va corto a propósito y el resto en bloques grandes. Es
  // asimétrico porque los dos motores lo son: Kokoro sintetiza a 0,07x tiempo
  // real y Chatterbox a 3,19x, medido. En Kokoro trocear sale gratis; en
  // Chatterbox cada llamada paga un coste fijo que en trozos cortos no se
  // amortiza, de modo que muchos pedazos empeoran el total aunque adelanten el
  // primer sonido. Un trozo corto al principio compra el arranque; bloques
  // grandes después evitan pagar ese coste una y otra vez.
  const PRIMER_TROZO = 140;   // caracteres: una frase, lo justo para arrancar
  const TROZO = 420;          // el resto, en bloques grandes

  function trocearParaVoz(texto){
    const t = String(texto || '').trim();
    if (t.length <= PRIMER_TROZO) return [t];
    // Se corta en final de oración; si no hay ninguno cerca, en una pausa; y
    // si tampoco, se deja entero antes que partir una palabra por la mitad.
    const cortar = (resto, tope) => {
      if (resto.length <= tope) return [resto, ''];
      // El último final de oración que quepa DENTRO del tope. Buscarlo en una
      // ventana más ancha y quedarse con el último hacía que un texto de 198
      // caracteres saliera entero en el primer trozo: el punto final estaba
      // dentro de la ventana, así que se lo llevaba todo y no había troceo.
      let i = -1;
      for (const m of resto.matchAll(/[.!?…](?=\s|$)/g)) {
        if (m.index <= tope) i = m.index; else break;
      }
      // Si el primer final de oración llega demasiado tarde, se corta antes en
      // una pausa: vale más empezar a sonar que respetar la oración entera.
      if (i < tope * 0.35) {
        let j = -1;
        for (const m of resto.matchAll(/[,;:](?=\s)/g)) {
          if (m.index <= tope) j = m.index; else break;
        }
        i = j;
      }
      // Sin puntuación util, se corta en un espacio. Nunca a mitad de palabra.
      if (i < 0) i = resto.lastIndexOf(' ', tope);
      if (i < 0) return [resto, ''];
      return [resto.slice(0, i + 1).trim(), resto.slice(i + 1).trim()];
    };
    const trozos = [];
    let [cabeza, resto] = cortar(t, PRIMER_TROZO);
    if (cabeza) trozos.push(cabeza);
    while (resto) {
      const [a, b] = cortar(resto, TROZO);
      if (!a) break;
      trozos.push(a);
      resto = b;
    }
    return trozos.filter(Boolean);
  }

  // Encola un trozo y devuelve su id sin esperar a que termine. Se piden todos
  // de una vez: el servicio tiene un solo trabajador y los procesa en orden, así
  // que esperar a que acabe uno para pedir el siguiente solo añadiría un viaje
  // de ida y vuelta por trozo.
  async function encolarTrozo(id, texto, voiceState){
    if(usaRunpod()) return {remoto:true, id: await runpodSintetizar(texto, id, voiceState)};
    const job = await request('/v1/synthesis', {method:'POST', body: JSON.stringify({
      character_id:id, text:texto, intensity:voiceState.intensity,
      affinity:voiceState.affinity, delivery:voiceState.delivery,
      speak_actions:state.speakActions})});
    return {remoto:false, id: job.id};
  }

  async function esperarTrozo(ref){
    if(ref.remoto){
      const salida = await runpodEsperar(ref.id);
      return {audio_url:null, url: urlDesdeBase64(salida.audio_b64), remoto:true};
    }
    for (let i = 0; i < 400; i++){
      const r = await request('/v1/jobs/' + ref.id);
      if (r.status === 'ready') return {audio_url:r.audio_url, url: base() + r.audio_url, remoto:false};
      if (r.status === 'error') throw new Error((r.error && r.error.message) || 'la síntesis falló');
      if (r.status === 'cancelled') return null;
      await wait(250);
    }
    throw new Error('La síntesis agotó el tiempo de espera');
  }

  function reproducir(url, velocidad, esObjeto){
    return new Promise((resolve, reject) => {
      const audio = new Audio(url);
      state.audio = audio;
      audio.playbackRate = velocidad;
      audio.preservesPitch = true;
      // Las URL de objeto se liberan al terminar: sin esto, una conversación
      // larga deja decenas de megas retenidos.
      const soltar = () => { if(esObjeto) URL.revokeObjectURL(url); state.audio = null; };
      audio.onended = () => { soltar(); resolve(); };
      audio.onerror = () => { soltar(); reject(new Error('El audio no pudo reproducirse.')); };
      audio.play().catch(e => { soltar(); reject(e); });
    });
  }

  async function speak(entry,button){
    const id=characterFor(entry);
    if(!id){notify('Este mensaje no conserva el autor de la voz.');return;}
    if(state.queueMode==='interrupt')await cancel();
    button=button||null;
    if(button){button.disabled=true;button.textContent='…';}
    try{
      // Con RunPod configurado no hay servicio local que comprobar. Y el
      // mensaje de antes mandaba a ejecutar un archivo que en la máquina de
      // quien recibe la app no existe: decirle que arranque algo imposible es
      // peor que no decirle nada.
      if(!usaRunpod() && !state.health && !await checkHealth())
        throw new Error('No hay voz configurada. Abre 🔊 y pega tu endpoint de RunPod, o inicia el servicio local.');
      const affinity=typeof root.getAfecto==='function'?root.getAfecto(id):3;
      const voiceState=root.MartinezV3?root.MartinezV3.outwardVoiceState({intensity:intensityFor(id),affinity},id):{intensity:intensityFor(id),affinity,rate:1};
      const trozos=trocearParaVoz(entry.content);
      const velocidad=Math.max(.72,Math.min(1.12,state.rate*Number(voiceState.rate||1)));
      // Se encolan todos antes de reproducir ninguno: el servicio los procesa
      // en orden con un solo trabajador, así que mientras suena el primero ya
      // se están sintetizando los siguientes.
      const ids=[];
      for(const t of trozos) ids.push(await encolarTrozo(id,t,voiceState));
      state.currentJob=ids[0] && ids[0].id;
      state.cola=ids;
      if(button){button.dataset.voicePlaying='1';button.textContent='■';button.disabled=false;}
      for(let i=0;i<ids.length;i++){
        state.currentJob=ids[i];
        const r=await esperarTrozo(ids[i]);
        if(!r) break;                       // cancelado: se deja de encadenar
        if(state.cola!==ids) break;         // otro turno tomó el relevo
        await reproducir(r.url,velocidad,r.remoto);
        if(state.cola!==ids) break;
      }
      state.currentJob=null;state.cola=null;
      if(button){button.dataset.voicePlaying='0';button.textContent='▶';}
    }catch(e){state.lastError=e.message;if(button){button.dataset.voicePlaying='0';button.textContent='▶';}notify(e.message);updateHeader();}
    finally{if(button)button.disabled=false;}
  }
  function notify(text){
    if(typeof root.toast==='function')root.toast('Voz: '+msg(text).slice(0,160));
    else console.warn('Voz:',text);
  }
  function attachButton(container,entry){
    if(!container||!entry||entry.role!=='assistant')return;
    const b=document.createElement('button');b.type='button';b.textContent='▶';b.title='Reproducir con voz neuronal local';
    b.style.cssText='background:none;border:none;cursor:pointer;font-size:12px;padding:2px 5px;opacity:0.45;color:#185FA5';
    b.onclick=()=>b.dataset.voicePlaying==='1'?cancel():speak(entry,b);container.appendChild(b);
  }
  function finalized(entry){if(state.autoplay&&entry&&entry.role==='assistant')speak(entry,null);}
  function invalidate(entry){if(entry)delete entry.audioRef;cancel();}
  function openSettings(){
    const rp=runpodConf();
    document.getElementById('voice-ov')?.remove();
    const vd=vozDirecta();
    const ov=document.createElement('div');ov.id='voice-ov';ov.className='overlay active';ov.onclick=e=>{if(e.target===ov)ov.remove();};
    const modal=document.createElement('div');modal.className='modal';
    const engine=state.health&&state.health.engines?state.health.engines.map(e=>`${e.name}: ${e.available?'disponible':'no instalado'}`).join(' · '):'servicio detenido';
    modal.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center"><div style="font-size:17px;font-weight:700">Voces neuronales locales</div><button id="voice-close" style="background:none;border:none;font-size:24px;cursor:pointer">×</button></div>
      <div style="margin:12px 0;padding:10px;border-radius:10px;background:${state.health?'#EAF3DE':'#FAECE7'};font-size:12px">${state.health?'Servicio listo con '+state.health.profiles+' perfiles.':'Servicio no disponible. Abre la aplicación con INICIAR-PERSONAJES.cmd.'}</div>
      <div style="font-size:11px;color:#777;line-height:1.5;margin-bottom:12px">${msg(engine)}</div>
      <label style="display:flex;gap:8px;align-items:center;margin:10px 0;font-size:13px"><input id="voice-auto" type="checkbox" ${state.autoplay?'checked':''} style="width:auto"> Reproducir automáticamente respuestas finales</label>
      <label style="display:flex;gap:8px;align-items:center;margin:10px 0;font-size:13px"><input id="voice-actions" type="checkbox" ${state.speakActions?'checked':''} style="width:auto"> Leer acciones entre asteriscos</label>
      <label style="display:block;margin:12px 0;font-size:13px">Velocidad: <strong id="voice-rate-label">${state.rate.toFixed(2)}×</strong><input id="voice-rate" type="range" min="0.72" max="1.08" step="0.02" value="${state.rate}" style="width:100%;margin-top:6px"></label>
      <div style="font-size:11px;color:#888;margin:12px 0">No existe fallback a speechSynthesis ni a voces del navegador.</div>
      <div style="display:flex;gap:8px"><button id="voice-test" class="btn-p">Probar personaje actual</button><button id="voice-stop" class="btn-g">Detener</button><button id="voice-check" class="btn-g">Diagnóstico</button></div>
      <div id="voice-detail" style="font-size:11px;color:#888;margin-top:10px"></div>
      <div style="margin-top:16px;padding-top:14px;border-top:1px solid #eee">
        <div style="font-size:13px;font-weight:600;color:#111;margin-bottom:4px">Servicio de voz remoto</div>
        <div style="font-size:11px;color:#888;line-height:1.5;margin-bottom:10px">
          Si el servicio corre en otra máquina —un Pod con GPU, por ejemplo— pon aquí su
          dirección y su token. Déjalo vacío para usar el de esta máquina.
        </div>
        <span class="lbl" style="font-size:11px;color:#aaa">Dirección</span>
        <input id="voice-dir-url" type="text" placeholder="https://xxxxx-8765.proxy.runpod.net"
               value="${(vd&&vd.url)||''}" style="width:100%;margin-bottom:8px">
        <span class="lbl" style="font-size:11px;color:#aaa">Token</span>
        <input id="voice-dir-token" type="password" placeholder="el que pusiste en VOICE_TOKEN"
               value="${(vd&&vd.token)||''}" style="width:100%">
        <div style="display:flex;gap:8px;margin-top:10px">
          <button id="voice-dir-save" class="btn-g" style="flex:1;font-size:12px">Guardar</button>
          <button id="voice-dir-test" class="btn-g" style="flex:1;font-size:12px">Probar</button>
          <button id="voice-dir-clear" class="btn-g" style="font-size:12px">Borrar</button>
        </div>
        <div id="voice-dir-estado" style="font-size:11px;color:#888;margin-top:8px;line-height:1.5"></div>
      </div>
      <div style="margin-top:16px;padding-top:14px;border-top:1px solid #eee">
        <div style="font-size:13px;font-weight:600;color:#111;margin-bottom:4px">Voces en la nube (RunPod Serverless)</div>
        <div style="font-size:11px;color:#888;line-height:1.5;margin-bottom:10px">
          Si tienes una cuenta de RunPod con el endpoint de voces desplegado, pega aquí sus datos
          y las voces sonarán sin instalar nada. Se guardan solo en este navegador.
        </div>
        <span class="lbl" style="font-size:11px;color:#aaa">Endpoint</span>
        <input id="voice-rp-url" type="text" placeholder="https://api.runpod.ai/v2/TU_ENDPOINT_ID"
               value="${(rp&&rp.endpoint)||''}" style="width:100%;margin-bottom:8px">
        <span class="lbl" style="font-size:11px;color:#aaa">Clave de API</span>
        <input id="voice-rp-key" type="password" placeholder="rpa_..."
               value="${(rp&&rp.apiKey)||''}" style="width:100%">
        <div style="display:flex;gap:8px;margin-top:10px">
          <button id="voice-rp-save" class="btn-g" style="flex:1;font-size:12px">Guardar</button>
          <button id="voice-rp-test" class="btn-g" style="flex:1;font-size:12px">Probar</button>
          <button id="voice-rp-clear" class="btn-g" style="font-size:12px">Borrar</button>
        </div>
        <div id="voice-rp-estado" style="font-size:11px;color:#888;margin-top:8px;line-height:1.5"></div>
      </div>
    `;
    ov.appendChild(modal);document.querySelector('.app').appendChild(ov);

    const guardarRp=()=>{
      const u=(modal.querySelector('#voice-rp-url').value||'').trim().replace(/\/+$/,'');
      const k=(modal.querySelector('#voice-rp-key').value||'').trim();
      const est=modal.querySelector('#voice-rp-estado');
      if(!u||!k){ est.textContent='Faltan el endpoint o la clave.'; return false; }
      if(!/^https?:\/\//.test(u)){ est.textContent='El endpoint tiene que empezar por https://'; return false; }
      localStorage.setItem('personajes_runpod', JSON.stringify({endpoint:u, apiKey:k}));
      est.textContent='Guardado. Las voces saldrán de RunPod.';
      return true;
    };
    const estadoDir=t=>{ const e=modal.querySelector('#voice-dir-estado'); if(e)e.textContent=t; };
    modal.querySelector('#voice-dir-save').addEventListener('click', ()=>{
      const u=(modal.querySelector('#voice-dir-url').value||'').trim().replace(/\/+$/,'');
      const k=(modal.querySelector('#voice-dir-token').value||'').trim();
      if(!u){ estadoDir('Pon una dirección, o pulsa Borrar para volver a esta máquina.'); return; }
      if(!/^https?:\/\//.test(u)){ estadoDir('La dirección tiene que empezar por http:// o https://'); return; }
      localStorage.setItem('personajes_voz_directa', JSON.stringify({url:u, token:k}));
      estadoDir('Guardado. La voz vendrá de ahí.');
    });
    modal.querySelector('#voice-dir-clear').addEventListener('click', ()=>{
      localStorage.removeItem('personajes_voz_directa');
      modal.querySelector('#voice-dir-url').value='';
      modal.querySelector('#voice-dir-token').value='';
      estadoDir('Borrado. Vuelve al servicio de esta máquina.');
    });
    modal.querySelector('#voice-dir-test').addEventListener('click', async ()=>{
      const u=(modal.querySelector('#voice-dir-url').value||'').trim().replace(/\/+$/,'');
      const k=(modal.querySelector('#voice-dir-token').value||'').trim();
      if(!u){ estadoDir('Pon una dirección primero.'); return; }
      estadoDir('Probando…');
      try{
        // /health no pide token a propósito: sirve para saber si el servicio
        // está vivo antes de discutir sobre credenciales.
        const s=await fetch(u+'/health');
        if(!s.ok) throw new Error('el servicio respondió '+s.status);
        const d=await s.json();
        // Y una ruta real, que sí lo pide: así se comprueba el token de verdad.
        const v=await fetch(u+'/v1/voices', k?{headers:{'Authorization':'Bearer '+k}}:undefined);
        if(v.status===401){ estadoDir('El servicio responde, pero el token no sirve.'); return; }
        if(!v.ok) throw new Error('respondió '+v.status);
        const motores=(d.engines||[]).filter(e=>e.available).map(e=>e.name).join(', ');
        estadoDir('Funciona. '+(d.profiles||'?')+' perfiles · '+(motores||'sin motores'));
      }catch(e){
        estadoDir('No respondió: '+(e&&e.message||e)+'. Revisa la dirección y que el servicio esté arriba.');
      }
    });
    modal.querySelector('#voice-rp-save').addEventListener('click', guardarRp);
    modal.querySelector('#voice-rp-clear').addEventListener('click', ()=>{
      localStorage.removeItem('personajes_runpod');
      modal.querySelector('#voice-rp-url').value='';
      modal.querySelector('#voice-rp-key').value='';
      modal.querySelector('#voice-rp-estado').textContent='Borrado. Vuelve al servicio local si lo tienes.';
    });
    modal.querySelector('#voice-rp-test').addEventListener('click', async ()=>{
      const est=modal.querySelector('#voice-rp-estado');
      if(!guardarRp()) return;
      est.textContent='Probando… el primer arranque puede tardar un minuto largo mientras carga el modelo.';
      const t0=Date.now();
      try{
        const ref=await encolarTrozo('zinc','Prueba de voz.',{intensity:1,affinity:3});
        const r=await esperarTrozo(ref);
        est.innerHTML='Funciona. Tardó '+Math.round((Date.now()-t0)/1000)+' s '+
          '<span style="opacity:.7">(el primer intento incluye el arranque en frío; los siguientes son mucho más rápidos)</span>';
        if(r&&r.remoto) URL.revokeObjectURL(r.url);
      }catch(e){
        est.textContent='No funcionó: '+String(e.message||e);
      }
    });
    modal.querySelector('#voice-close').onclick=()=>ov.remove();
    modal.querySelector('#voice-auto').onchange=e=>{state.autoplay=e.target.checked;save();};
    modal.querySelector('#voice-actions').onchange=e=>{state.speakActions=e.target.checked;save();};
    modal.querySelector('#voice-rate').oninput=e=>{state.rate=Number(e.target.value);modal.querySelector('#voice-rate-label').textContent=state.rate.toFixed(2)+'×';if(state.audio){state.audio.playbackRate=state.rate;state.audio.preservesPitch=true;}save();};
    modal.querySelector('#voice-stop').onclick=cancel;
    modal.querySelector('#voice-check').onclick=async()=>{const h=await checkHealth();modal.querySelector('#voice-detail').textContent=h?'Diagnóstico correcto: '+h.version:'Error: '+state.lastError;};
    modal.querySelector('#voice-test').onclick=()=>{
      const id=root.activeChar&&root.activeChar.id;if(!id){notify('Abre primero un personaje.');return;}
      speak({role:'assistant',charId:id,content:'Esta es una prueba breve de mi voz, en español neutral.'},modal.querySelector('#voice-test'));
    };
  }
  root.VoiceUI={state,checkHealth,speak,cancel,attachButton,finalized,invalidate,openSettings};
  document.addEventListener('DOMContentLoaded',()=>{checkHealth();setInterval(checkHealth,30000);});
})(window);
