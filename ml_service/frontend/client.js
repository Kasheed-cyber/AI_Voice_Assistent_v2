// === НАСТРОЙКИ ===
const HOST = window.location.host;
const SIGNALING_URL = 'ws://' + HOST + '/ws/signaling';
const AUDIO_URL = 'ws://' + HOST + '/ws/audio';

const ICE_CONFIG = {
  iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
};

// === СОСТОЯНИЕ ===
let signalingWs = null;
let audioWs = null;
let pc = null;
let localStream = null;
let audioContext = null;
let processor = null;
let myId = null;
let mode = null;
let audioProcessCalls = 0;
let audioBytesSent = 0;
let mixedAudioStream = null;
let micGainNode = null;
let mixDestination = null;
let complianceAudioBuffer = null;
let complianceAnnouncementStarted = false;
const COMPLIANCE_AUDIO_URL = 'compliance_warning.wav';

// === ЭЛЕМЕНТЫ UI ===
const statusEl = document.getElementById('status');
const logEl = document.getElementById('log');
const callerBtn = document.getElementById('callerBtn');
const listenerBtn = document.getElementById('listenerBtn');
const stopBtn = document.getElementById('stopBtn');
const remoteAudio = document.getElementById('remoteAudio');

// === ВСПОМОГАТЕЛЬНЫЕ ===
function log(msg) {
  const time = new Date().toLocaleTimeString();
  logEl.textContent += `[${time}] ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
  console.log(msg);
}

function setStatus(text, cls = '') {
  statusEl.textContent = text;
  statusEl.className = 'status ' + cls;
}

function setButtonsDisabled(callerDisabled, listenerDisabled, stopDisabled) {
  callerBtn.disabled = callerDisabled;
  listenerBtn.disabled = listenerDisabled;
  stopBtn.disabled = stopDisabled;
}

// === UI: ТРАНСКРИПТ ===
function appendTranscript(text) {
  let el = document.getElementById('transcript');
  if (!el) {
    el = document.createElement('div');
    el.id = 'transcript';
    el.className = 'panel';
    document.body.appendChild(el);
  }
  const line = document.createElement('div');
  line.textContent = '📝 ' + text;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

// === UI: ПРОТОКОЛ ===
function renderProtocol(jsonString, isFinal) {
  let el = document.getElementById('protocol');
  if (!el) {
    el = document.createElement('div');
    el.id = 'protocol';
    el.className = 'panel';
    document.body.appendChild(el);
  }
  try {
    const clean = String(jsonString).replace(/```json|```/g, '').trim();
    const data = JSON.parse(clean);
    const contracts = data.contracts || [];
    const contractsHtml = contracts.length
      ? contracts.map((c, i) => {
          const agreements = (c.agreements || []).map(a => `<li>${a}</li>`).join('') || '<li>—</li>';
          const facts = (c.facts || []).map(f => `<li>${f}</li>`).join('');
          const tasks = (c.tasks || []).map(t =>
            `<li><b>${t.owner || '—'}</b>: ${t.task || '—'} <i>(${t.deadline || 'без срока'})</i></li>`
          ).join('') || '<li>—</li>';
          return `
            <div class="contract">
              <h4>📑 Договор/сделка ${i + 1}: ${c.contract_id || c.contract_name || 'без идентификатора'}</h4>
              ${c.contract_name && c.contract_id ? `<p><b>Название:</b> ${c.contract_name}</p>` : ''}
              <p><b>Договорённости:</b></p><ul>${agreements}</ul>
              ${facts ? `<p><b>Условия / суммы:</b></p><ul>${facts}</ul>` : ''}
              <p><b>Задачи:</b></p><ul>${tasks}</ul>
            </div>`;
        }).join('')
      : '<p>Договоры/сделки отдельно не определены.</p>';

    const agreements = (data.agreements || []).map(a => `<li>${a}</li>`).join('') || '<li>—</li>';
    const facts = (data.facts || []).map(f => `<li>${f}</li>`).join('') || '<li>—</li>';
    const tasks = (data.tasks || [])
      .map(t => `<li><b>${t.owner || '—'}</b>: ${t.task || '—'} <i>(${t.deadline || 'без срока'})</i> ${t.contract_id ? `[${t.contract_id}]` : ''}</li>`)
      .join('') || '<li>—</li>';
    const keyPoints = (data.key_points || []).map(k => `<li>${k}</li>`).join('') || '<li>—</li>';

    el.innerHTML = `
      <h3>${isFinal ? '📄 Финальный протокол' : '📄 Протокол (обновляется)'}</h3>
      <p><b>Тема:</b> ${data.topic || '—'}</p>
      <p><b>Участники:</b> ${(data.participants || []).map(p => typeof p === 'string' ? p : (p.name || p.role || '—')).join(', ') || '—'}</p>
      <h4>📚 Договоры и сделки</h4>
      ${contractsHtml}
      <details><summary>Все договорённости</summary><ul>${agreements}</ul></details>
      <details><summary>Условия, суммы и количества</summary><ul>${facts}</ul></details>
      <details><summary>Все задачи</summary><ul>${tasks}</ul></details>
      <p><b>Ключевые моменты:</b></p>
      <ul>${keyPoints}</ul>
    `;
  } catch (e) {
    el.innerHTML = `<h3>📄 Протокол</h3><pre>${jsonString}</pre>`;
  }
}

// === УВЕДОМЛЕНИЕ ОБ ИИ-ОБРАБОТКЕ ===
function isAiServiceEnabled() {
  return document.getElementById('agentToggle')?.checked === true;
}

const AI_NOTICE_TEXT = 'Внимание! Разговор записывается и обрабатывается искусственным интеллектом для распознавания речи и автоматической фиксации договорённостей.';

async function prepareOutgoingAudio() {
  audioContext = new AudioContext({ sampleRate: 16000 });
  const source = audioContext.createMediaStreamSource(localStream);
  const destination = audioContext.createMediaStreamDestination();
  mixDestination = destination;
  micGainNode = audioContext.createGain();
  micGainNode.gain.value = isAiServiceEnabled() ? 0 : 1;
  source.connect(micGainNode);
  micGainNode.connect(destination);
  mixedAudioStream = destination.stream;

  if (!isAiServiceEnabled()) return;

  try {
    const response = await fetch(COMPLIANCE_AUDIO_URL, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const buffer = await response.arrayBuffer();
    complianceAudioBuffer = await audioContext.decodeAudioData(buffer);
    log('⚖️ Голосовое предупреждение загружено и готово для WebRTC');
  } catch (err) {
    log('⚠️ Не удалось загрузить голосовое предупреждение: ' + err.message);
    micGainNode.gain.value = 1;
  }
}

function playComplianceAnnouncement() {
  if (!isAiServiceEnabled() || complianceAnnouncementStarted || !complianceAudioBuffer || !audioContext || !micGainNode) {
    if (micGainNode && !isAiServiceEnabled()) micGainNode.gain.value = 1;
    return;
  }

  complianceAnnouncementStarted = true;
  const announcement = audioContext.createBufferSource();
  const announcementGain = audioContext.createGain();
  announcementGain.gain.value = 1;
  announcement.buffer = complianceAudioBuffer;
  announcement.connect(announcementGain);
  announcementGain.connect(audioContext.destination);

  // Главное: это же объявление отправляется в MediaStreamDestination,
  // поэтому его слышит второй участник, а не только браузер звонящего.
  const outgoingAnnouncement = audioContext.createBufferSource();
  const outgoingGain = audioContext.createGain();
  outgoingGain.gain.value = 1;
  outgoingAnnouncement.buffer = complianceAudioBuffer;
  outgoingAnnouncement.connect(outgoingGain);
  outgoingGain.connect(mixDestination);

  const startAt = audioContext.currentTime + 0.15;
  announcement.start(startAt);
  outgoingAnnouncement.start(startAt);

  outgoingAnnouncement.onended = () => {
    if (micGainNode) {
      micGainNode.gain.setTargetAtTime(1, audioContext.currentTime, 0.02);
    }
    log('🔊 Голосовое предупреждение передано второму участнику через WebRTC');
    showToast('Второй участник услышал уведомление об ИИ-обработке');
  };

  signalingWs?.send(JSON.stringify({ type: 'ai_notice', callId: myId, text: AI_NOTICE_TEXT }));
  log('⚖️ Compliance: уведомление отправлено и воспроизводится в звонке');
}

function showAiProcessingNotice() {
  if (!isAiServiceEnabled()) return true;
  return window.confirm(
    AI_NOTICE_TEXT + '\n\nПродолжить звонок? Второй участник автоматически услышит это уведомление.'
  );
}

// === СТАРТ ЗВОНКА ===
async function startCall(selectedMode) {
  if (isAiServiceEnabled() && !showAiProcessingNotice()) {
    log('Звонок отменён: пользователь не подтвердил уведомление об ИИ-обработке');
    setStatus('Ожидание подтверждения', 'error');
    return;
  }

  mode = selectedMode;
  setButtonsDisabled(true, true, false);

  try {
    if (mode === 'caller') {
      setStatus('Запрос доступа к микрофону...');
      localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });
      await prepareOutgoingAudio();
      log('Микрофон получен (режим: звонящий)');
    } else {
      setStatus('Подключение в режиме слушателя...');
      localStream = new MediaStream();
      log('Режим слушателя: камера отключена, ожидаем голосовой канал');
    }

    connectSignaling();

  } catch (err) {
    log('Ошибка доступа к медиа: ' + err.message);
    setStatus('Ошибка: ' + err.message, 'error');
    setButtonsDisabled(false, false, true);
  }
}

// === СИГНАЛИЗАЦИЯ ===
function connectSignaling() {
  signalingWs = new WebSocket(SIGNALING_URL);

  signalingWs.onopen = () => {
    log('Signaling: подключено');
    setStatus('Ожидание второго участника...');
  };

  signalingWs.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    log('Signaling ← ' + msg.type);

    if (msg.type === 'init') {
      myId = msg.id;
      log('Мой ID: ' + myId);

      if (mode === 'caller') connectAudio();
      await createPeerConnection();

    } else if (msg.type === 'offer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      signalingWs.send(JSON.stringify({ type: 'answer', sdp: pc.localDescription }));

    } else if (msg.type === 'answer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));

    } else if (msg.type === 'candidate') {
      try {
        await pc.addIceCandidate(new RTCIceCandidate(msg.candidate));
      } catch (e) {
        log('Ошибка добавления ICE: ' + e.message);
      }

    } else if (msg.type === 'ai_notice') {
      const notice = msg.text || 'Внимание! Разговор записывается и обрабатывается искусственным интеллектом.';
      log('⚖️ Уведомление: ' + notice);
      showToast('ИИ-обработка активна: уведомление прозвучит в звонке');
      setStatus('ИИ-обработка активна', 'connected');
    } else if (msg.type === 'peer-left') {
      log('Второй участник отключился');
    }
  };

  signalingWs.onerror = () => {
    log('Signaling ошибка');
    setStatus('Ошибка сигнализации', 'error');
  };

  signalingWs.onclose = () => {
    log('Signaling закрыто');
  };
}

// === WebRTC ===
async function createPeerConnection() {
  pc = new RTCPeerConnection(ICE_CONFIG);

  if (mode === 'caller' && mixedAudioStream) {
    mixedAudioStream.getTracks().forEach(track => pc.addTrack(track, mixedAudioStream));
  } else {
    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
  }

  pc.onicecandidate = (event) => {
    if (event.candidate && signalingWs.readyState === WebSocket.OPEN) {
      signalingWs.send(JSON.stringify({
        type: 'candidate',
        candidate: event.candidate
      }));
    }
  };

  pc.ontrack = (event) => {
    log('Получен удалённый трек: ' + event.track.kind);
    if (event.track.kind === 'audio' && event.streams[0] && remoteAudio) {
      remoteAudio.srcObject = event.streams[0];
      remoteAudio.play().catch(() => log('ℹ️ Автовоспроизведение аудио ожидает действие пользователя'));
    }
  };

  pc.onconnectionstatechange = () => {
    log('WebRTC состояние: ' + pc.connectionState);
    if (pc.connectionState === 'connected') {
      setStatus('Соединение установлено (' + mode + ')', 'connected');
      if (mode === 'caller') playComplianceAnnouncement();
    }
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  signalingWs.send(JSON.stringify({ type: 'offer', sdp: pc.localDescription }));

  if (mode === 'caller') {
    startAudioCapture();
  }
}

function getAgentSettings() {
  return {
    defaultPriority: document.getElementById('defaultPriority')?.value || localStorage.getItem('aiDefaultPriority') || 'medium',
    triggerPhrases: document.getElementById('triggerPhrases')?.value ?? localStorage.getItem('aiTriggerPhrases') ?? 'пришлю, отправлю, согласуем, подтвердим, доставим',
    taskTemplate: document.getElementById('taskTemplate')?.value ?? localStorage.getItem('aiTaskTemplate') ?? 'Действие + срок'
  };
}

function saveAgentSettings() {
  const settings = getAgentSettings();
  localStorage.setItem('aiDefaultPriority', settings.defaultPriority);
  localStorage.setItem('aiTriggerPhrases', settings.triggerPhrases);
  localStorage.setItem('aiTaskTemplate', settings.taskTemplate);
  showToast('Настройки ИИ сохранены');
}

function loadAgentSettings() {
  const priority = document.getElementById('defaultPriority');
  const triggers = document.getElementById('triggerPhrases');
  const template = document.getElementById('taskTemplate');
  if (priority) priority.value = localStorage.getItem('aiDefaultPriority') || 'medium';
  if (triggers) triggers.value = localStorage.getItem('aiTriggerPhrases') || 'пришлю, отправлю, согласуем, подтвердим, доставим';
  if (template) template.value = localStorage.getItem('aiTaskTemplate') || 'Действие + срок';
}

// === АУДИО-КАНАЛ ===
function connectAudio() {
  const url = `${AUDIO_URL}?call_id=${myId}`;
  audioWs = new WebSocket(url);
  audioWs.binaryType = 'arraybuffer';   // ⚠️ важно для отправки бинарных данных

  audioWs.onopen = () => {
    log('Audio: подключено к серверу (отправка чанков)');
    const settings = getAgentSettings();
    audioWs.send(JSON.stringify({ type: 'config', ...settings }));
    log('⚙️ Настройки правил переданы ИИ');
  };

  audioWs.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'transcript') {
        log(`📝 ${msg.text}`);
        appendTranscript(msg.text);
      } else if (msg.type === 'protocol') {
        log(`📄 Протокол обновлён (final=${msg.final})`);
        renderProtocol(msg.content, msg.final);
      }
    } catch (e) {
      log('Audio msg parse error: ' + e.message);
    }
  };

  audioWs.onerror = (e) => {
    log('Audio: ошибка ' + (e.message || ''));
  };

  audioWs.onclose = () => {
    log('Audio: закрыто');
  };
}

// === ЗАХВАТ И ОТПРАВКА PCM ===
function startAudioCapture() {
  if (!audioContext) audioContext = new AudioContext({ sampleRate: 16000 });
  log(`AudioContext: state=${audioContext.state}, sampleRate=${audioContext.sampleRate}`);

  const source = audioContext.createMediaStreamSource(localStream);
  processor = audioContext.createScriptProcessor(4096, 1, 1);

  processor.onaudioprocess = (event) => {
    audioProcessCalls++;

    if (!audioWs) {
      if (audioProcessCalls % 40 === 0) log('⚠️ audioWs = null');
      return;
    }
    if (audioWs.readyState !== WebSocket.OPEN) {
      if (audioProcessCalls % 40 === 0) log(`⚠️ audioWs.state=${audioWs.readyState}`);
      return;
    }

    try {
      const inputData = event.inputBuffer.getChannelData(0);
      const pcm16 = float32ToInt16(inputData);

      if (audioProcessCalls % 10 === 0) {
        log(`📤 Sending ${pcm16.byteLength} bytes (#${audioProcessCalls})`);
      }

      audioWs.send(pcm16);
      audioBytesSent += pcm16.byteLength;

      if (audioProcessCalls % 10 === 0) {
        log(`✅ Total sent: ${audioBytesSent} bytes`);
      }
    } catch (e) {
      log(`❌ send error: ${e.message}`);
    }
  };

  source.connect(processor);
  processor.connect(audioContext.destination);

  audioContext.resume().then(() => {
    log(`✅ AudioContext возобновлён: state=${audioContext.state}`);
  }).catch(err => {
    log(`❌ AudioContext resume failed: ${err.message}`);
  });

  log('Захват аудио запущен');
}

function float32ToInt16(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16.buffer;
}

// === ЗАВЕРШЕНИЕ ===
function stopCall() {
  if (processor) processor.disconnect();
  if (audioContext) audioContext.close();
  if (pc) pc.close();
  if (signalingWs) signalingWs.close();
  if (audioWs) audioWs.close();
  if (localStream) localStream.getTracks().forEach(t => t.stop());

  if (remoteAudio) remoteAudio.srcObject = null;
  mixedAudioStream = null;
  micGainNode = null;
  mixDestination = null;
  complianceAudioBuffer = null;
  complianceAnnouncementStarted = false;

  setButtonsDisabled(false, false, true);
  setStatus('Звонок завершён');
  log('Звонок завершён');
}

// === ОБРАБОТЧИКИ ===
callerBtn.addEventListener('click', () => startCall('caller'));
listenerBtn.addEventListener('click', () => startCall('listener'));
stopBtn.addEventListener('click', stopCall);



// === ДЕМО-ОБОЛОЧКА МОБИЛЬНОГО ПРИЛОЖЕНИЯ ===
const tabs = document.querySelectorAll('.tab');
const pages = document.querySelectorAll('.tab-page');
const toast = document.getElementById('toast');
const agentToggle = document.getElementById('agentToggle');
const demoBtn = document.getElementById('demoBtn');
const agreementBadge = document.getElementById('agreementBadge');

function showTab(name) {
  tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === name));
  pages.forEach(page => page.classList.toggle('active', page.id === `tab-${name}`));
}

tabs.forEach(tab => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
document.querySelectorAll('[data-open]').forEach(btn => btn.addEventListener('click', () => showTab(btn.dataset.open)));

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 2600);
}

const profileAgentToggle = document.getElementById('profileAgentToggle');
profileAgentToggle?.addEventListener('change', () => {
  if (agentToggle) agentToggle.checked = profileAgentToggle.checked;
  agentToggle?.dispatchEvent(new Event('change'));
});

agentToggle?.addEventListener('change', () => {
  const enabled = agentToggle.checked;
  const serviceStatus = document.getElementById('serviceStatus');
  serviceStatus.innerHTML = enabled
    ? '<span class="dot"></span> Сервис подключён'
    : '<span class="dot" style="background:#9aa2ad"></span> Сервис выключен';
  showToast(enabled ? 'ИИ-агент договорённостей подключён' : 'ИИ-агент договорённостей выключен');
});

async function runRealDemo() {
  const apiBase = `${window.location.protocol}//${window.location.hostname}:8000`;
  const callId = `demo-${Date.now()}`;
  const sampleTranscript = `Менеджер: По договору номер 15 отправим коммерческое предложение до 10 октября.
Клиент: Хорошо. Встречу по этому вопросу проведём 12 октября в 15:00.
Менеджер: По договору 18 клиент подтвердит объём поставки до 20 октября.`;

  try {
    const response = await fetch(`${apiBase}/protocol/text`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({call_id: callId, text: sampleTranscript})
    });
    if (!response.ok) throw new Error(`API ${response.status}`);
    const protocol = await response.json();
    renderProtocol(JSON.stringify(protocol), true);
    agreementBadge.textContent = String((protocol.tasks || []).length);
    demoBtn.textContent = '✓ Открыть результат звонка';
    demoBtn.dataset.resultReady = 'true';
    showToast(`Реальный LLM-пайплайн нашёл ${(protocol.tasks || []).length} задач`);
    showTab('agreements');
  } catch (e) {
    demoBtn.textContent = '✓ Открыть результат звонка';
    demoBtn.dataset.resultReady = 'true';
    agreementBadge.textContent = '4';
    showToast('API/LLM недоступны — показан резервный сценарий прототипа');
    showTab('agreements');
  } finally {
    demoBtn.disabled = false;
  }
}

demoBtn?.addEventListener('click', async () => {
  if (demoBtn.dataset.resultReady === 'true') {
    showTab('agreements');
    return;
  }
  if (!agentToggle.checked) {
    showToast('Сначала включите ИИ-агента');
    return;
  }
  demoBtn.textContent = '● ИИ обрабатывает разговор…';
  demoBtn.disabled = true;
  showToast('Отправляем тестовый разговор в реальный LLM-пайплайн');
  await runRealDemo();
});

document.getElementById('transcriptBtn')?.addEventListener('click', () => {
  const panel = document.getElementById('transcript');
  panel?.scrollIntoView({behavior:'smooth'});
  document.querySelector('.technical-panel')?.setAttribute('open','');
  showToast('Расшифровка доступна в техническом режиме прототипа');
});

document.getElementById('editBtn')?.addEventListener('click', () => {
  showToast('Режим редактирования: здесь оператор сможет изменить задачу или срок');
});

document.getElementById('saveSettingsBtn')?.addEventListener('click', saveAgentSettings);
loadAgentSettings();
log('Клиент готов. Мобильный режим прототипа активен.');