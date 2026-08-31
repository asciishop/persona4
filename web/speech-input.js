(function(){
  'use strict';

  const API_LOCAL='http://127.0.0.1:8765';
  // El dictado sigue al mismo servicio que la voz: si una está en un Pod
  // remoto, la otra también. Se comparte la configuración en vez de tener dos.
  function vozDirecta(){
    try{
      const c=JSON.parse(localStorage.getItem('personajes_voz_directa')||'null');
      if(c&&c.url) return {url:String(c.url).replace(/\/+$/,''), token:String(c.token||'')};
    }catch(e){}
    return null;
  }
  const API=(()=>{ const d=vozDirecta(); return d?d.url:API_LOCAL; })();
  const MAX_SECONDS=60;
  let recording=false;
  let stream=null;
  let context=null;
  let source=null;
  let processor=null;
  let chunks=[];
  let startedAt=0;
  let timer=null;

  function notify(message){
    if(typeof window.toast==='function')window.toast(message);
    else console.info(message);
  }

  function button(){return document.getElementById('mic-btn');}

  function setState(state){
    const btn=button();
    if(!btn)return;
    if(state==='recording'){
      btn.style.background='#FDE8E7';
      btn.style.color='#C62828';
      btn.style.opacity='1';
      btn.title='Detener y transcribir';
      btn.setAttribute('aria-label','Detener dictado');
    }else if(state==='working'){
      btn.style.background='#FFF3D6';
      btn.style.color='#8A5A00';
      btn.style.opacity='1';
      btn.disabled=true;
      btn.title='Transcribiendo localmente...';
    }else{
      btn.style.background='none';
      btn.style.color='#555';
      btn.style.opacity='0.65';
      btn.disabled=false;
      btn.title='Dictar mensaje con Whisper local';
      btn.setAttribute('aria-label','Iniciar dictado');
    }
  }

  function merge(parts){
    let length=0;
    parts.forEach(p=>length+=p.length);
    const merged=new Float32Array(length);
    let offset=0;
    parts.forEach(p=>{merged.set(p,offset);offset+=p.length;});
    return merged;
  }

  function downsample(samples,inputRate,outputRate){
    if(inputRate===outputRate)return samples;
    const ratio=inputRate/outputRate;
    const length=Math.round(samples.length/ratio);
    const result=new Float32Array(length);
    for(let i=0;i<length;i++){
      const start=Math.floor(i*ratio);
      const end=Math.min(Math.floor((i+1)*ratio),samples.length);
      let sum=0;
      for(let j=start;j<end;j++)sum+=samples[j];
      result[i]=sum/Math.max(1,end-start);
    }
    return result;
  }

  function wavBlob(samples,sampleRate){
    const buffer=new ArrayBuffer(44+samples.length*2);
    const view=new DataView(buffer);
    const write=(offset,text)=>{for(let i=0;i<text.length;i++)view.setUint8(offset+i,text.charCodeAt(i));};
    write(0,'RIFF');view.setUint32(4,36+samples.length*2,true);write(8,'WAVE');
    write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);
    view.setUint16(22,1,true);view.setUint32(24,sampleRate,true);
    view.setUint32(28,sampleRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
    write(36,'data');view.setUint32(40,samples.length*2,true);
    let offset=44;
    for(let i=0;i<samples.length;i++,offset+=2){
      const value=Math.max(-1,Math.min(1,samples[i]));
      view.setInt16(offset,value<0?value*0x8000:value*0x7fff,true);
    }
    return new Blob([view],{type:'audio/wav'});
  }

  async function start(){
    if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
      notify('Este navegador no permite capturar el micrófono.');
      return;
    }
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      const AudioCtx=window.AudioContext||window.webkitAudioContext;
      context=new AudioCtx();
      source=context.createMediaStreamSource(stream);
      processor=context.createScriptProcessor(4096,1,1);
      const mute=context.createGain();mute.gain.value=0;
      chunks=[];
      processor.onaudioprocess=e=>chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      source.connect(processor);processor.connect(mute);mute.connect(context.destination);
      recording=true;startedAt=Date.now();setState('recording');
      timer=setTimeout(()=>stop(),MAX_SECONDS*1000);
      notify('● Grabando. Pulsa el micrófono para transcribir.');
    }catch(error){
      setState('idle');
      notify(error&&error.name==='NotAllowedError'?'Debes permitir el acceso al micrófono.':'No se pudo abrir el micrófono.');
    }
  }

  async function stop(){
    if(!recording)return;
    recording=false;clearTimeout(timer);timer=null;setState('working');
    const duration=(Date.now()-startedAt)/1000;
    const inputRate=context?context.sampleRate:48000;
    try{
      if(processor){processor.disconnect();processor.onaudioprocess=null;}
      if(source)source.disconnect();
      if(stream)stream.getTracks().forEach(track=>track.stop());
      if(context)await context.close();
      if(duration<0.35||!chunks.length)throw new Error('La grabación fue demasiado corta.');
      const samples=downsample(merge(chunks),inputRate,16000);
      // Si hay RunPod configurado, el dictado va por ahí: quien recibe la app
      // no tiene servicio local y el botón del micrófono no haría nada. El
      // audio se queda en su cuenta; lo que sale es el texto.
      const rp=(()=>{ try{
        const c=JSON.parse(localStorage.getItem('personajes_runpod')||'null');
        return (c&&c.endpoint&&c.apiKey)?{endpoint:String(c.endpoint).replace(/\/+$/,''),apiKey:String(c.apiKey)}:null;
      }catch(e){ return null; } })();

      let texto;
      if(rp){
        texto=await transcribirEnRunpod(rp, wavBlob(samples,16000));
      }else{
        const _d=vozDirecta();
        const response=await fetch(API+'/v1/transcriptions',{
          method:'POST',
          headers:Object.assign({'Content-Type':'audio/wav'},
                                (_d&&_d.token)?{'Authorization':'Bearer '+_d.token}:{}),
          body:wavBlob(samples,16000)
        });
        const data=await response.json().catch(()=>({}));
        if(!response.ok)throw new Error(data.error&&data.error.message||'No se pudo transcribir el audio.');
        texto=String(data.text||'');
      }
      const data={text:texto};
      const input=document.getElementById('chat-in');
      if(input){
        const previous=input.value.trim();
        input.value=(previous?previous+' ':'')+String(data.text||'').trim();
        input.dispatchEvent(new Event('input',{bubbles:true}));
        input.focus();
        // Queda anotado que este trozo se habló. La app lo compara luego con
        // lo que finalmente se envíe, porque entre esto y el envío puede
        // haber corrección. Va protegido: speech-input.js tiene que seguir
        // funcionando aunque se cargue sin la aplicación alrededor.
        try{
          if(window.RegistroEntrada)
            window.RegistroEntrada.anotarDictado(String(data.text||''), rp?'runpod':'local');
        }catch(e){}
      }
      notify('✓ Dictado transcrito'+(rp?'':' localmente')+'. Revísalo y pulsa enviar.');
    }catch(error){
      notify(error&&error.message?error.message:'Falló el reconocimiento de voz.');
    }finally{
      chunks=[];stream=context=source=processor=null;setState('idle');
    }
  }

  // El audio viaja en base64 al mismo punto final que sintetiza las voces, así
  // que dictar aprovecha el trabajador que ya está caliente y no paga otro
  // arranque en frío.
  async function transcribirEnRunpod(rp, blob){
    const b64 = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(',')[1] || '');
      fr.onerror = () => rej(new Error('No se pudo leer la grabación.'));
      fr.readAsDataURL(blob);
    });
    const r = await fetch(rp.endpoint + '/run', {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+rp.apiKey},
      body: JSON.stringify({input:{accion:'transcribir', audio_b64:b64}})
    });
    if(!r.ok) throw new Error('RunPod respondió HTTP '+r.status);
    const j = await r.json();
    if(!j.id) throw new Error('RunPod no devolvió un identificador de trabajo');
    // Mismo techo que la síntesis y por lo mismo: dictar usa el mismo punto
    // final, así que si el dictado es lo primero que se prueba paga el mismo
    // arranque en frío. Diez minutos.
    for(let i=0;i<1200;i++){
      const s = await fetch(rp.endpoint+'/status/'+j.id, {headers:{'Authorization':'Bearer '+rp.apiKey}});
      if(!s.ok) throw new Error('RunPod respondió HTTP '+s.status);
      const e = await s.json();
      if(e.status === 'COMPLETED'){
        const o = e.output || {};
        if(o.error) throw new Error(String(o.error));
        return String(o.texto || '');
      }
      if(e.status === 'FAILED' || e.status === 'CANCELLED')
        throw new Error('RunPod: ' + (e.error || e.status));
      await new Promise(r2=>setTimeout(r2,500));
    }
    throw new Error('La transcripción agotó el tiempo de espera.');
  }

  window.SpeechInput={toggle:function(){return recording?stop():start();},stop:stop};
})();
