const HOST=window.location.host;
const API_BASE=(location.port==='8080'||location.port==='')?`http://${location.hostname}:8000`:'';
const SIGNALING_URL=`${location.protocol==='https:'?'wss':'ws'}://${HOST}/ws/signaling`;
const AUDIO_URL=`${location.protocol==='https:'?'wss':'ws'}://${HOST}/ws/audio`;
const STORAGE_KEY='ai_voice_agreements_v3', CALLS_KEY='ai_voice_calls_v3', SETTINGS_KEY='ai_voice_settings_v3';
const ICE_CONFIG={iceServers:[{urls:'stun:stun.l.google.com:19302'}]};
let signalingWs=null,audioWs=null,pc=null,localStream=null,audioContext=null,processor=null,myId=null,mode=null;
let timerId=null,timerSeconds=0,activeCallId=null,demoTimers=[],lastProtocol=null,lastTranscript='',currentCallStarted=null,currentDetected=null,lastLiveContract=null,liveAnalysisTimer=null,settings=loadSettings(),audioFinalizePromise=null,audioFinalizeResolve=null,audioFinalizeReject=null;
const $=id=>document.getElementById(id);
const els={status:$('status'),dot:$('statusDot'),log:$('log'),caller:$('callerBtn'),listener:$('listenerBtn'),demo:$('demoBtn'),stop:$('stopBtn'),timer:$('timer'),callState:$('callState'),callHint:$('callHint'),badge:$('callBadge'),stt:$('sttState'),ai:$('aiState'),found:$('foundState'),transcript:$('transcript'),protocol:$('protocol'),count:$('agreementCount'),list:$('agreementList'),empty:$('emptyAgreements'),modal:$('modal')};
const labels={high:'Высокий',medium:'Средний',low:'Низкий',pending:'В работе',done:'Выполнено',cancelled:'Отменено'};
function log(m){const line=`[${new Date().toLocaleTimeString()}] ${m}`;els.log.textContent+=`\n${line}`;els.log.scrollTop=els.log.scrollHeight;console.log(m)}
function toast(message,kind='ok'){let t=document.getElementById('appToast');if(!t){t=document.createElement('div');t.id='appToast';document.body.appendChild(t)}t.textContent=message;t.dataset.kind=kind;t.classList.add('show');clearTimeout(window.__toastTimer);window.__toastTimer=setTimeout(()=>t.classList.remove('show'),2600)}
function status(text,active=false){els.status.textContent=text;els.dot.style.background=active?'#55c78b':'#999'}
function uid(prefix='id'){return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,7)}`}
function esc(s=''){return String(s).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function read(key, fallback){try{return JSON.parse(localStorage.getItem(key)||JSON.stringify(fallback))}catch{return fallback}}
function write(key,val){localStorage.setItem(key,JSON.stringify(val))}
function getData(){return read(STORAGE_KEY,[])}
function getCalls(){return read(CALLS_KEY,[])}
function loadSettings(){return {...{autoCreateTasks:true,autoCreateCalendar:true,askConfirmation:true,autoPriority:true,triggerPhrases:['отправлю','подготовлю','согласуем','встречаемся','пришлю']},...read(SETTINGS_KEY,{})}}
function saveSettingsLocal(v){settings=v;write(SETTINGS_KEY,v)}
function normalizeAgreement(a={},i=0){let contractId=String(a.contractId||a.contract_id||'').trim();let detectedName=String(a.contractName||a.contract_name||'').trim();if(!contractId&&detectedName){const m=detectedName.match(/(?:договор|сделка)\s*(?:№|N|#)?\s*(\d[\d-]*)/i);if(m)contractId=m[1]}if(!detectedName&&contractId)detectedName=`Договор №${contractId}`;const contractName=detectedName||'Договор без номера';return{id:a.id||uid('agr'),contractName,client:a.client||a.clientName||'Клиент',text:a.text||a.agreement||a.agreement_text||'',owner:a.owner||'Менеджер',deadline:a.deadline||'',priority:a.priority||'medium',status:a.status||'pending',details:a.details||'',callId:a.callId||a.call_id||activeCallId||'',contractId,createdAt:a.createdAt||new Date().toISOString(),updatedAt:a.updatedAt||new Date().toISOString()}}
function saveData(data, sync=true){write(STORAGE_KEY,data);renderAgreements();if(sync)data.forEach(x=>apiSync(x))}
async function api(path,opts={}){if(!API_BASE)throw new Error('API недоступен');const r=await fetch(API_BASE+path,{headers:{'Content-Type':'application/json',...(opts.headers||{})},...opts});if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);return r.status===204?null:r.json()}
async function apiSync(item){try{await api('/protocol/agreement',{method:'POST',body:JSON.stringify(item)})}catch(e){log('API sync: '+e.message)}}
function extractContractRef(text=''){const m=String(text).match(/(?:договор(?:а|у|ом)?|сделк(?:а|и|у|ой))\s*(?:№|N|#)?\s*([A-Za-zА-Яа-я0-9][\w-]*)/i);return m?String(m[1]):''}
function protocolToAgreements(data){const result=[],contracts=Array.isArray(data.contracts)?data.contracts:[];contracts.forEach(c=>{const rawName=c.contract_name||c.contractName||'';const rawId=String(c.contract_id||c.contractId||extractContractRef(rawName)).trim();const contractName=rawName|| (rawId?`Договор №${rawId}`:'Договор без номера');(c.agreements||[]).forEach((a,i)=>{const task=(c.tasks||[])[i]||{};result.push(normalizeAgreement({contractName,contractId:rawId,text:typeof a==='string'?a:a.text,details:c.details,owner:task.owner,deadline:task.deadline,priority:task.priority||'medium'},result.length))})});if(!contracts.length)(data.agreements||[]).forEach((a,i)=>{const task=(data.tasks||[])[i]||{};const text=typeof a==='string'?a:a.text||'';const rawName=task.contract_name||task.contractName||'';const rawId=String(task.contract_id||task.contractId||extractContractRef(rawName)||extractContractRef(text)).trim();result.push(normalizeAgreement({contractName:rawName||(rawId?`Договор №${rawId}`:'Договор без номера'),contractId:rawId,text,owner:task.owner,deadline:task.deadline,priority:task.priority},i))});return result}
function switchPage(id){document.querySelectorAll('.page').forEach(p=>p.classList.remove('active-page'));$(id).classList.add('active-page');document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.page===id));if(id==='agreementsPage')renderAgreements()}
function isOverdue(a){return a.status==='pending'&&a.deadline&&new Date(a.deadline+'T23:59:59')<new Date()}
function renderAgreements(){const all=getData();els.count.textContent=all.length;const q=($('searchInput')?.value||'').toLowerCase(),sf=$('statusFilter')?.value||'all',pf=$('priorityFilter')?.value||'all';const filtered=all.filter(a=>(sf==='all'||a.status===sf)&&(pf==='all'||a.priority===pf)&&(!q||[a.contractName,a.client,a.text,a.owner,a.details].join(' ').toLowerCase().includes(q)));$('totalStat').textContent=all.length;$('pendingStat').textContent=all.filter(a=>a.status==='pending').length;$('doneStat').textContent=all.filter(a=>a.status==='done').length;$('overdueStat').textContent=all.filter(isOverdue).length;const overdue=all.filter(isOverdue);const ns=$('notificationStrip');if(overdue.length){ns.hidden=false;ns.innerHTML=`⚠ <b>${overdue.length}</b> ${overdue.length===1?'договорённость требует':'договорённостей требуют'} внимания: есть просроченные сроки.`}else ns.hidden=true;els.list.innerHTML=filtered.map(a=>`<article class="agreement-card ${isOverdue(a)?'overdue':''}"><div class="agreement-main"><h3>${esc(a.contractName)}</h3><div class="client">${esc(a.client||'Клиент не указан')} · ${esc(a.owner)}</div><div class="agreement-text">${esc(a.text)}</div><div class="meta"><span class="pill ${a.priority}">${labels[a.priority]}</span><span class="pill ${a.status}">${labels[a.status]}</span>${a.deadline?`<span class="pill ${isOverdue(a)?'high':''}">Срок: ${esc(formatDate(a.deadline))}</span>`:''}${a.callId?`<span class="pill">Звонок: ${esc(a.callId.slice(-8))}</span>`:''}</div>${a.details?`<p class="client details">${esc(a.details)}</p>`:''}</div><div class="agreement-actions"><button class="icon-btn" onclick="editAgreement('${a.id}')">Изменить</button>${a.status==='pending'?`<button class="icon-btn" onclick="completeAgreement('${a.id}')">✓ Выполнить</button>`:''}<button class="icon-btn delete" onclick="deleteAgreement('${a.id}')">Удалить</button></div></article>`).join('');els.empty.hidden=filtered.length!==0;if(!filtered.length&&all.length)els.empty.hidden=true}
function formatDate(d){const x=new Date(d);return isNaN(x)?d:x.toLocaleDateString('ru-RU')}
function openModal(item=null){$('agreementForm').reset();$('editId').value=item?.id||'';$('modalTitle').textContent=item?'Изменить договорённость':'Новая договорённость';$('contractId').value=item?.contractId||extractContractRef(item?.contractName||'');$('contractName').value=item?.contractName||'';$('clientName').value=item?.client||'';$('agreementText').value=item?.text||'';$('owner').value=item?.owner||'Менеджер';$('deadline').value=item?.deadline||'';$('priority').value=item?.priority||'medium';$('agreementStatus').value=item?.status||'pending';$('details').value=item?.details||'';els.modal.hidden=false}
function closeModal(){$('modal').hidden=true}
window.editAgreement=id=>{const a=getData().find(x=>x.id===id);if(a)openModal(a)};
window.completeAgreement=async id=>{const all=getData(),a=all.find(x=>x.id===id);if(!a)return;a.status='done';a.updatedAt=new Date().toISOString();write(STORAGE_KEY,all);renderAgreements();await apiSync(a);log(`Договорённость выполнена: ${a.text}`);toast('Договорённость отмечена как выполненная')};
window.deleteAgreement=async id=>{const a=getData().find(x=>x.id===id);if(!a||!confirm(`Удалить договорённость «${a.text}»?`))return;write(STORAGE_KEY,getData().filter(x=>x.id!==id));renderAgreements();try{await api(`/protocol/agreement/${encodeURIComponent(id)}`,{method:'DELETE'})}catch{}log('Договорённость удалена')};
$('agreementForm').addEventListener('submit',async e=>{e.preventDefault();const data=getData(),id=$('editId').value,contractId=$('contractId').value.trim(),contractName=$('contractName').value.trim()||(contractId?`Договор №${contractId}`:'Договор без номера');const item=normalizeAgreement({id:id||uid('agr'),contractName,contractId,client:$('clientName').value,text:$('agreementText').value,owner:$('owner').value,deadline:$('deadline').value,priority:$('priority').value,status:$('agreementStatus').value,details:$('details').value,callId:activeCallId});const next=id?data.map(x=>x.id===id?item:x):[item,...data];write(STORAGE_KEY,next);renderAgreements();closeModal();await apiSync(item);if(!id&&settings.autoCreateTasks)await createCRMTask(item);if(!id&&settings.autoCreateCalendar&&/встреч|созвон|звонок/i.test(item.text))await createCalendarEvent(item);log(id?'Договорённость изменена':'Новая договорённость сохранена');toast(id?'Договорённость изменена':'Договорённость сохранена')});
function renderProtocol(data){lastProtocol=data;const contracts=data.contracts?.length?data.contracts:[{contract_name:'Общий разговор',agreements:data.agreements||[],tasks:data.tasks||[]}];els.protocol.classList.remove('empty-state');els.protocol.innerHTML=contracts.map((c,i)=>`<div class="contract-preview"><h3>${esc(c.contract_name||`Договор №${i+1}`)}</h3>${(c.agreements||[]).map(a=>`<p>• ${esc(typeof a==='string'?a:a.text||'')}</p>`).join('')}${(c.tasks||[]).map(t=>`<span class="task-chip">${esc(t.owner||'—')}: ${esc(t.task||'—')}${t.deadline?' · '+esc(t.deadline):''}</span>`).join('')}</div>`).join('');els.found.textContent=`${protocolToAgreements(data).length} договорённостей`;const summary=data.summary||buildSummary(data);$('summary').textContent=summary}
function buildSummary(data){const n=protocolToAgreements(data).length,c=data.contracts?.length||0;return `Обсуждены ${c||'несколько'} ${c===1?'договор':'договоров'}. Зафиксировано ${n} договорённост${n===1?'ь':'и'}. По результатам разговора сформированы задачи и сроки.`}
function appendTranscript(text,who='Собеседник'){if(els.transcript.classList.contains('transcript-empty'))els.transcript.innerHTML='';const div=document.createElement('div');div.className='transcript-line';div.innerHTML=`<strong>${esc(who)}:</strong>${esc(text)}`;els.transcript.appendChild(div);els.transcript.scrollTop=els.transcript.scrollHeight;lastTranscript+=(lastTranscript?' ':'')+text}
function startTimer(){clearInterval(timerId);timerSeconds=0;els.timer.textContent='00:00';timerId=setInterval(()=>{timerSeconds++;els.timer.textContent=`${String(Math.floor(timerSeconds/60)).padStart(2,'0')}:${String(timerSeconds%60).padStart(2,'0')}`},1000)}
function stopTimer(){clearInterval(timerId);timerId=null}
function setCallActive(active,label='Идёт звонок'){els.stop.disabled=!active;els.caller.disabled=active;els.listener.disabled=active;els.demo.disabled=active;els.badge.textContent=active?'Активен':'Не активен';els.badge.classList.toggle('active',active);els.callState.textContent=label;status(active?'Звонок активен':'Готов к работе',active)}
async function startCall(selected){if(!$('consentBtn').dataset.accepted){alert('Сначала подтвердите уведомление о записи разговора.');return}mode=selected;activeCallId=uid('call');currentCallStarted=new Date();lastTranscript='';lastLiveContract=null;setCallActive(true,selected==='caller'?'Идёт реальный звонок':'Слушатель подключается');startTimer();els.stt.textContent='Запуск';els.ai.textContent='Ожидание';els.callHint.textContent=selected==='caller'?'Микрофон захватывается и отправляется в GigaAM. Демо-режим не включается автоматически.':'Режим слушателя: ожидается входящий аудиопоток.';try{try{await api(`/protocol/start?call_id=${encodeURIComponent(activeCallId)}`,{method:'POST'});log('Протокол звонка создан')}catch(e){log('API протокола недоступно, продолжаю локальную обработку: '+e.message)}if(!navigator.mediaDevices?.getUserMedia)throw new Error('Браузер не дал доступ к микрофону');localStream=await navigator.mediaDevices.getUserMedia({audio:selected==='caller',video:false});connectAudio();startAudioCapture();connectSignaling()}catch(e){log('Реальный звонок не запущен: '+e.message);status('Ошибка запуска',false);els.callState.textContent='Не удалось начать звонок';els.stt.textContent='Ошибка';els.ai.textContent='Не запущен';stopCall(false)}}
function connectSignaling(){try{signalingWs=new WebSocket(SIGNALING_URL);signalingWs.onopen=()=>{log('Signaling подключён');status('Звонок активен',true)};signalingWs.onmessage=async e=>{const m=JSON.parse(e.data);if(m.type==='init'){myId=m.id;await createPeerConnection()}else if(m.type==='offer'){if(!pc)await createPeerConnection();await pc.setRemoteDescription(m.sdp);const a=await pc.createAnswer();await pc.setLocalDescription(a);signalingWs.send(JSON.stringify({type:'answer',sdp:pc.localDescription}))}else if(m.type==='answer'&&pc)await pc.setRemoteDescription(m.sdp);else if(m.type==='candidate'&&pc){try{await pc.addIceCandidate(m.candidate)}catch{}}};signalingWs.onerror=()=>log('Сигнализация недоступна: аудиораспознавание продолжит работать без второго WebRTC-клиента') }catch(e){log('Signaling недоступен: '+e.message)}}
async function createPeerConnection(){pc=new RTCPeerConnection(ICE_CONFIG);localStream?.getTracks().forEach(t=>pc.addTrack(t,localStream));pc.onicecandidate=e=>{if(e.candidate&&signalingWs?.readyState===1)signalingWs.send(JSON.stringify({type:'candidate',candidate:e.candidate}))};pc.onconnectionstatechange=()=>log('WebRTC: '+pc.connectionState);const offer=await pc.createOffer();await pc.setLocalDescription(offer);signalingWs.send(JSON.stringify({type:'offer',sdp:pc.localDescription}))}
function connectAudio(){audioWs=new WebSocket(`${AUDIO_URL}?call_id=${activeCallId}`);audioWs.binaryType='arraybuffer';audioWs.onopen=()=>{els.stt.textContent='Работает';log('Аудио-канал подключён к текущему звонку')};audioWs.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type==='transcript'){appendTranscript(m.text);els.ai.textContent='Речь распознана'}else if(m.type==='final_protocol'){const data=parseJSON(m.content);if(data){lastProtocol=data;renderProtocol(data);els.ai.textContent='Готов';log(`AI: сервер прислал итоговый анализ (${protocolToAgreements(data).length} договорённостей)`);if(audioFinalizeResolve){audioFinalizeResolve(data);audioFinalizeResolve=null;audioFinalizeReject=null;audioFinalizePromise=null;}}}else if(m.type==='final_protocol_error'){const err=new Error(m.message||'Не удалось выполнить итоговый анализ');if(audioFinalizeReject){audioFinalizeReject(err);audioFinalizeResolve=null;audioFinalizeReject=null;audioFinalizePromise=null;}log('AI-анализ: '+err.message)}}catch(err){log('Ошибка ответа: '+err.message)}};audioWs.onerror=()=>{els.stt.textContent='Ошибка';log('Аудио WebSocket недоступен');if(audioFinalizeReject){audioFinalizeReject(new Error('Аудио-канал недоступен'));audioFinalizeResolve=null;audioFinalizeReject=null;audioFinalizePromise=null;}};audioWs.onclose=()=>{if(audioFinalizeReject){audioFinalizeReject(new Error('Аудио-канал закрыт до получения анализа'));audioFinalizeResolve=null;audioFinalizeReject=null;audioFinalizePromise=null;}}} 
function requestFinalAnalysis(){if(!audioWs||audioWs.readyState!==WebSocket.OPEN)return Promise.reject(new Error('Аудио-канал уже закрыт'));if(audioFinalizePromise)return audioFinalizePromise;audioFinalizePromise=new Promise((resolve,reject)=>{audioFinalizeResolve=resolve;audioFinalizeReject=reject;});audioWs.send(JSON.stringify({type:'finalize'}));return Promise.race([audioFinalizePromise,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Истекло время ожидания AI-анализа')),120000))]);}
function startAudioCapture(){if(!localStream?.getAudioTracks?.().length){log('У этого режима нет исходящего микрофона');return}audioContext=new AudioContext({sampleRate:16000});const source=audioContext.createMediaStreamSource(localStream);processor=audioContext.createScriptProcessor(4096,1,1);processor.onaudioprocess=e=>{if(audioWs?.readyState===1)audioWs.send(float32ToInt16(e.inputBuffer.getChannelData(0)))};source.connect(processor);processor.connect(audioContext.destination);audioContext.resume()}
function float32ToInt16(a){const b=new Int16Array(a.length);for(let i=0;i<a.length;i++){const s=Math.max(-1,Math.min(1,a[i]));b[i]=s<0?s*32768:s*32767}return b.buffer}
function parseJSON(s){try{return JSON.parse(String(s).replace(/```json|```/g,'').trim())}catch{return null}}
function mergeUnique(items,existing){const keys=new Set(existing.map(x=>`${x.contractName}|${x.text}`));return [...items.filter(x=>{const k=`${x.contractName}|${x.text}`;if(keys.has(k))return false;keys.add(k);return true}),...existing]}
function detectLiveAgreement(){/* В новой логике анализ показывается только после завершения звонка. */}

async function saveDetected(){
  if(!currentDetected)return false;
  const pending=Array.isArray(currentDetected)?currentDetected:[currentDetected];
  let added=0;
  for(const detected of pending){
    const item=normalizeAgreement({...detected,id:uid('agr'),callId:activeCallId});
    const before=getData();
    const next=mergeUnique([item],before);
    if(next.length===before.length)continue;
    write(STORAGE_KEY,next);renderAgreements();
    await apiSync(item);
    if(settings.autoCreateTasks)await createCRMTask(item);
    if(settings.autoCreateCalendar&&/встреч|созвон|звонок/i.test(item.text))await createCalendarEvent(item);
    added++;
  }
  currentDetected=null;$('liveAgreementBox').hidden=true;
  if(added){log(`Договорённости подтверждены: ${added}`);toast(added===1?'Договорённость подтверждена и сохранена':`Подтверждено договорённостей: ${added}`);}
  else{toast('Все найденные договорённости уже сохранены','error');}
  return added>0;
}
function editDetected(){if(!currentDetected)return;const first=Array.isArray(currentDetected)?currentDetected[0]:currentDetected;openModal(normalizeAgreement({...first,id:''}));currentDetected=null;$('liveAgreementBox').hidden=true}
async function showAnalysisResult(protocol, callId){
  lastProtocol=protocol;
  renderProtocol(protocol);
  const incoming=protocolToAgreements(protocol).map(x=>({...x,callId}));
  const existing=getData();
  const fresh=incoming.filter(x=>!existing.some(e=>e.contractId===x.contractId&&e.contractId&&e.text===x.text || (!x.contractId&&!e.contractId&&e.text===x.text)));
  if(!fresh.length){$('liveAgreementBox').hidden=true;currentDetected=null;els.ai.textContent='Готов';return}
  if(settings.askConfirmation){
    currentDetected=fresh;
    $('liveAgreementTitle').textContent=fresh.length===1?'Обнаружена договорённость':`Обнаружено договорённостей: ${fresh.length}`;
    $('liveAgreementText').textContent=fresh.length===1?`${fresh[0].contractName}: ${fresh[0].text}`:fresh.map(x=>`• ${x.contractName}: ${x.text}`).join('\n');
    $('liveAgreementBox').hidden=false;
    log(`ИИ: после завершения звонка найдено ${fresh.length} новых договорённостей — требуется подтверждение`);
  }else{
    currentDetected=fresh;
    await saveDetected();
    log(`ИИ: ${fresh.length} договорённостей сохранены автоматически — подтверждение отключено в настройках`);
  }
  els.ai.textContent='Готов';
}
async function createCRMTask(a,status=null){if(!settings.autoCreateTasks)return;try{const r=await api('/integrations/crm/task',{method:'POST',body:JSON.stringify({call_id:a.callId||activeCallId||'manual',agreement_id:a.id,owner:a.owner,task:a.text,deadline:a.deadline||null,contract_id:a.contractId||null,contract_name:a.contractName,priority:a.priority})});if(status)await api(`/integrations/crm/task/${r.task.id}`,{method:'PATCH',body:JSON.stringify({status})});log('CRM: задача создана')}catch(e){log('CRM: '+e.message)}}
async function createCalendarEvent(a){try{await api('/integrations/calendar/event',{method:'POST',body:JSON.stringify({call_id:a.callId||activeCallId||'manual',agreement_id:a.id,title:a.text,time:a.deadline||'По договорённости',participants:[a.owner]})});log('Календарь: событие создано')}catch(e){log('Календарь: '+e.message)}}
function demoProtocol(){return{topic:'Обсуждение трёх договоров',participants:[{role:'Менеджер'},{role:'Клиент'}],summary:'Клиент подтвердил дальнейшие действия по трём договорам: отправка КП, подтверждение количества оборудования и встреча.',contracts:[{contract_id:'15',contract_name:'Договор №15',agreements:['Отправить коммерческое предложение до пятницы'],tasks:[{owner:'Менеджер',task:'Отправить КП',deadline:'2026-10-09',priority:'high'}]},{contract_id:'18',contract_name:'Договор №18',agreements:['Подтвердить количество мониторов до 20 ноября'],tasks:[{owner:'Клиент',task:'Подтвердить количество мониторов',deadline:'2026-11-20',priority:'medium'}]},{contract_id:'21',contract_name:'Договор №21',agreements:['Провести встречу во вторник в 14:00'],tasks:[{owner:'Менеджер',task:'Провести встречу',deadline:'2026-10-13',priority:'high'}]}]}}
async function runDemo(asCall=true){demoTimers.forEach(clearTimeout);demoTimers=[];activeCallId=uid('call');currentCallStarted=new Date();lastTranscript='';currentDetected=null;$('liveAgreementBox').hidden=true;setCallActive(true,asCall?'Демо-звонок':'Демо-режим');startTimer();els.stt.textContent='Работает';els.ai.textContent='Ожидание';els.transcript.className='';els.transcript.innerHTML='';els.protocol.innerHTML='';$('summary').textContent='После завершения демо будет выполнен AI-анализ…';const lines=[['Менеджер','По договору №15 я отправлю коммерческое предложение до пятницы.'],['Клиент','По договору №18 мы подтвердим количество мониторов до 20 ноября.'],['Менеджер','И ещё по договору №21 давайте встретимся во вторник в 14:00.'],['Клиент','Договорились, встречу подтверждаю.']];lines.forEach((x,i)=>demoTimers.push(setTimeout(()=>appendTranscript(x[1],x[0]),i*850)));demoTimers.push(setTimeout(async()=>{const data=demoProtocol();renderProtocol(data);await apiSaveProtocol(data);els.stt.textContent='Готово';await finishCallRecord(data);stopCall(false);showAnalysisResult(data,activeCallId);log('Демо завершено: выполнен финальный анализ. CRM/календарь создаются только после подтверждения или при отключённом подтверждении.');},4000))}
async function apiSaveProtocol(data){try{await api('/protocol/structured',{method:'POST',body:JSON.stringify({...data,call_id:activeCallId})})}catch(e){log('Протокол API: '+e.message)}}
async function finishCallRecord(protocol){const call={id:activeCallId,client:'Тестовый клиент',startedAt:currentCallStarted?.toISOString()||new Date().toISOString(),finishedAt:new Date().toISOString(),duration:timerSeconds,status:'completed',summary:protocol.summary||buildSummary(protocol),transcription:lastTranscript,agreementIds:protocolToAgreements(protocol).map(x=>x.id),protocol};const calls=getCalls();write(CALLS_KEY,[call,...calls.filter(x=>x.id!==call.id)].slice(0,100));try{await api('/protocol/calls',{method:'POST',body:JSON.stringify(call)});await api(`/protocol/${encodeURIComponent(activeCallId)}/finalize`,{method:'POST'})}catch(e){log('История API: '+e.message)}}
async function stopCall(save=true){
  demoTimers.forEach(clearTimeout);demoTimers=[];clearTimeout(liveAnalysisTimer);
  const callId=activeCallId;
  processor?.disconnect();processor=null;
  try{await audioContext?.close()}catch{}
  audioContext=null;
  pc?.close();pc=null;
  signalingWs?.close();signalingWs=null;
  localStream?.getTracks()?.forEach(t=>t.stop());localStream=null;
  stopTimer();setCallActive(false,'Готов к звонку');els.timer.textContent='00:00';els.callHint.textContent='Звонок завершён. Выполняется итоговый анализ разговора.';
  let finalProtocol=null;
  if(save&&callId){
    els.ai.textContent='Анализ…';
    try{
      // Главный путь: сервер сначала дописывает последний аудиофрагмент,
      // затем запускает Qwen и присылает готовый протокол по тому же WebSocket.
      if(audioWs?.readyState===WebSocket.OPEN){
        finalProtocol=await requestFinalAnalysis();
        log('AI-анализ завершён: финальный протокол получен от сервера');
      }
    }catch(e){
      log('Ожидание финального протокола: '+e.message);
    }
    try{audioWs?.close()}catch{}
    audioWs=null;

    // Запасной путь: если WebSocket не ответил, читаем сохранённый протокол API.
    if(!finalProtocol){
      for(let attempt=0;attempt<30&&!finalProtocol;attempt++){
        try{const p=await api(`/protocol/${encodeURIComponent(callId)}`);if(p?.updated_at||p?.finished_at){finalProtocol=p;}}catch(e){}
        if(!finalProtocol)await new Promise(r=>setTimeout(r,1000));
      }
    }
    if(finalProtocol){
      if(!finalProtocol.summary)finalProtocol.summary=buildSummary(finalProtocol);
      lastProtocol=finalProtocol;
      await apiSaveProtocol(finalProtocol);
      try{await api(`/protocol/${encodeURIComponent(callId)}/finalize`,{method:'POST'})}catch(e){log('Финализация API: '+e.message)}
      await finishCallRecord(finalProtocol);
      showAnalysisResult(finalProtocol,callId);
      els.ai.textContent='Готов';
    }else{
      log('Финальный протокол не получен. Проверьте Ollama/Qwen и журнал сервера.');
      els.ai.textContent='Ошибка анализа';
      toast('Не удалось получить результат AI-анализа','error');
    }
  }else{try{audioWs?.close()}catch{}audioWs=null;}
  log('Звонок завершён');
}
function showPanel(title,eyebrow,html){$('panelTitle').textContent=title;$('panelEyebrow').textContent=eyebrow;$('panelContent').innerHTML=html;$('panelModal').hidden=false}
function closePanel(){$('panelModal').hidden=true}
async function showHistory(){let calls=getCalls();try{const r=await api('/protocol/calls/history');calls=r.calls||calls;write(CALLS_KEY,calls)}catch{}const html=calls.length?calls.map(c=>`<div class="history-item"><div><b>${esc(c.client||'Клиент')}</b><p>${esc(new Date(c.startedAt).toLocaleString('ru-RU'))} · ${Math.floor((c.duration||0)/60)}:${String((c.duration||0)%60).padStart(2,'0')}</p><span class="pill">${(c.agreementIds||[]).length} договорённостей</span></div><div class="history-actions"><button class="btn btn-outline btn-small" onclick="viewCall('${c.id}')">Открыть</button><button class="icon-btn delete" onclick="deleteCall('${c.id}')">Удалить</button></div></div>`).join(''):'<div class="empty-state">История пока пуста. Проведите демо-звонок.</div>';showPanel('История звонков','ИСТОРИЯ',html)}
window.viewCall=id=>{const c=getCalls().find(x=>x.id===id);if(!c)return;showPanel('Звонок '+id.slice(-8),'ПРОСМОТР',`<div class="call-detail"><b>${esc(c.client)}</b><p>${esc(new Date(c.startedAt).toLocaleString('ru-RU'))}</p><h3>Резюме</h3><p>${esc(c.summary)}</p><h3>Транскрипция</h3><div class="transcript-history">${esc(c.transcription||'Нет транскрипции')}</div><h3>Договорённости</h3><p>${(c.agreementIds||[]).length} записей сохранено.</p></div>`)};
window.deleteCall=async id=>{write(CALLS_KEY,getCalls().filter(x=>x.id!==id));try{await api(`/protocol/calls/${encodeURIComponent(id)}`,{method:'DELETE'})}catch{}showHistory()};
async function showAnalytics(){const a=getData(),calls=getCalls(),done=a.filter(x=>x.status==='done').length,over=a.filter(isOverdue).length;let tasks=[],events=[];try{tasks=(await api('/integrations/crm/tasks')).tasks||[];events=(await api('/integrations/calendar/events')).events||[]}catch{}const byPriority=['high','medium','low'].map(p=>`<div class="bar-row"><span>${labels[p]}</span><div class="bar"><i style="width:${a.length?Math.round(a.filter(x=>x.priority===p).length/a.length*100):0}%"></i></div><b>${a.filter(x=>x.priority===p).length}</b></div>`).join('');showPanel('Аналитика и KPI','АНАЛИТИКА',`<div class="analytics-grid"><div><span>Звонков</span><b>${calls.length}</b></div><div><span>Договорённостей</span><b>${a.length}</b></div><div><span>Выполнено</span><b>${done}</b></div><div><span>Просрочено</span><b>${over}</b></div><div><span>CRM задач</span><b>${tasks.length}</b></div><div><span>Событий календаря</span><b>${events.length}</b></div></div><h3>Приоритеты</h3>${byPriority}<h3>Доля выполнения</h3><div class="big-kpi">${a.length?Math.round(done/a.length*100):0}%</div><p class="muted">Метрики рассчитываются по данным текущего прототипа.</p>`)}
async function showSettings(){
  let s=settings;try{s=await api('/protocol/settings');saveSettingsLocal(s)}catch{}
  showPanel('Настройки автоматизации','НАСТРОЙКИ',`<form id="settingsForm" class="settings-form">
    <label><input type="checkbox" id="sTasks" ${s.autoCreateTasks?'checked':''}> Автоматически создавать задачи CRM после подтверждения</label>
    <label><input type="checkbox" id="sCalendar" ${s.autoCreateCalendar?'checked':''}> Создавать события календаря после подтверждения</label>
    <label><input type="checkbox" id="sConfirm" ${s.askConfirmation?'checked':''}> Спрашивать подтверждение найденной договорённости</label>
    <label><input type="checkbox" id="sPriority" ${s.autoPriority?'checked':''}> Автоматически определять приоритет</label>
    <label>Дополнительные слова-контексты<textarea id="sPhrases" rows="3">${esc((s.triggerPhrases||[]).join(', '))}</textarea></label>
    <button class="btn btn-primary" type="submit">Сохранить настройки</button>
    <div id="settingsSaved" class="settings-saved" hidden>✓ Настройки сохранены и применяются к новым действиям.</div>
    <hr>
    <div class="settings-danger"><b>Очистка тестовых данных</b><p>Удаляет накопленные тестовые задачи CRM и события календаря. Договорённости и историю звонков не удаляет.</p><div class="settings-danger-actions"><button class="btn btn-outline" type="button" id="clearCrmBtn">Очистить CRM</button><button class="btn btn-outline" type="button" id="clearCalendarBtn">Очистить календарь</button></div></div>
  </form>`);
  $('settingsForm').onsubmit=async e=>{
    e.preventDefault();
    const next={autoCreateTasks:$('sTasks').checked,autoCreateCalendar:$('sCalendar').checked,askConfirmation:$('sConfirm').checked,autoPriority:$('sPriority').checked,triggerPhrases:$('sPhrases').value.split(',').map(x=>x.trim()).filter(Boolean)};
    saveSettingsLocal(next);
    try{await api('/protocol/settings',{method:'PUT',body:JSON.stringify(next)})}catch(e){toast('Не удалось сохранить настройки в API','error');log('Настройки API: '+e.message);return}
    $('settingsSaved').hidden=false;log('Настройки сохранены');toast('Настройки сохранены');
  };
  $('clearCrmBtn').onclick=async()=>{if(!confirm('Очистить все накопленные тестовые CRM-задачи?'))return;try{await api('/integrations/crm/tasks',{method:'DELETE'});toast('CRM очищена');showAnalytics()}catch(e){toast('Не удалось очистить CRM','error')}};
  $('clearCalendarBtn').onclick=async()=>{if(!confirm('Очистить все накопленные тестовые события календаря?'))return;try{await api('/integrations/calendar/events',{method:'DELETE'});toast('Календарь очищен');showAnalytics()}catch(e){toast('Не удалось очистить календарь','error')}};
}
async function showAudit(){try{const r=await api('/protocol/audit');showPanel('Журнал действий','БЕЗОПАСНОСТЬ',(r.events||[]).map(e=>`<div class="audit-item"><b>${esc(e.action)}</b><span>${esc(new Date(e.timestamp).toLocaleString('ru-RU'))}</span><p>${esc(e.details||e.entity+' '+e.entity_id)}</p></div>`).join('')||'<div class="empty-state">Аудит пока пуст.</div>')}catch{showPanel('Журнал действий','БЕЗОПАСНОСТЬ','API недоступен. Запустите FastAPI, чтобы посмотреть аудит.')}}
$('historyBtn').onclick=showHistory;$('analyticsBtn').onclick=showAnalytics;$('settingsBtn').onclick=showSettings;
$('callerBtn').onclick=()=>startCall('caller');$('listenerBtn').onclick=()=>startCall('listener');$('demoBtn').onclick=()=>runDemo(true);$('stopBtn').onclick=()=>stopCall(true);$('clearTranscriptBtn').onclick=()=>{els.transcript.className='transcript-empty';els.transcript.textContent='Здесь появится текст разговора…';lastTranscript=''};$('clearLogBtn').onclick=()=>els.log.textContent='Журнал очищен.';$('openAgreementsBtn').onclick=()=>switchPage('agreementsPage');$('addAgreementBtn').onclick=()=>openModal();$('emptyAddBtn').onclick=()=>openModal();$('consentBtn').onclick=()=>{$('consentBtn').dataset.accepted='1';$('consentBtn').textContent='✓ Согласие учтено';$('consentBanner').classList.add('accepted')};$('confirmDetectedBtn').onclick=saveDetected;$('editDetectedBtn').onclick=editDetected;$('copySummaryBtn').onclick=async()=>{await navigator.clipboard?.writeText($('summary').textContent);log('Резюме скопировано')};document.querySelectorAll('[data-close-modal]').forEach(x=>x.onclick=closeModal);document.querySelectorAll('[data-close-panel]').forEach(x=>x.onclick=closePanel);$('searchInput').oninput=renderAgreements;$('statusFilter').onchange=renderAgreements;$('priorityFilter').onchange=renderAgreements;document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>switchPage(t.dataset.page));$('exportBtn').onclick=()=>{const blob=new Blob([JSON.stringify(getData(),null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ai_voice_agreements.json';a.click();URL.revokeObjectURL(a.href);log('Экспорт выполнен')};window.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();closePanel()}});
renderAgreements();
(async()=>{try{const remote=await api('/protocol/settings');saveSettingsLocal(remote);log(`Настройки загружены: подтверждение ${remote.askConfirmation?'включено':'выключено'}`)}catch(e){log('Настройки API недоступны, используются локальные настройки')}})();
log('Интерфейс готов. Анализ договорённостей выполняется после завершения звонка.');
