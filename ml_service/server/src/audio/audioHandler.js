const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// === НАСТРОЙКИ ===
const SAMPLE_RATE = 16000;
const CHUNK_SECONDS = 5;
const BUFFER_SIZE = SAMPLE_RATE * 2 * CHUNK_SECONDS;

// === ПУТИ ===
const ML_DIR = path.join(__dirname, '..', '..', '..');
const PROJECT_ROOT = path.join(ML_DIR, '..');
const PYTHON_PATH = path.join(ML_DIR, 'venv', 'Scripts', 'python.exe');
const SERVICE_SCRIPT = path.join(ML_DIR, 'service.py');
const OUTPUT_DIR = path.join(PROJECT_ROOT, 'data', 'protocols');

// === СОСТОЯНИЕ ===
const buffers = new Map();
const transcripts = new Map();
const finalizingCalls = new Set();
const audioSockets = new Map();
const conversationGroups = new Map();
const FINALIZE_JOIN_WINDOW_MS = 8000;

// === PYTHON SERVICE ===
let pyService = null;
let pyBuffer = '';
const pendingRequests = new Map();
let nextRequestId = 1;

function startPythonService() {
  console.log('[Python] Запуск сервиса...');
  console.log('[Python] Python:', PYTHON_PATH);
  console.log('[Python] Скрипт:', SERVICE_SCRIPT);

  pyService = spawn(PYTHON_PATH, [SERVICE_SCRIPT], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
  });

  pyService.stdout.on('data', (data) => {
    pyBuffer += data.toString('utf-8');
    const lines = pyBuffer.split('\n');
    pyBuffer = lines.pop();

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const result = JSON.parse(line);
        const id = result.id;
        if (id && pendingRequests.has(id)) {
          const { resolve } = pendingRequests.get(id);
          pendingRequests.delete(id);
          resolve(result);
        }
      } catch (e) {
        console.error('[Python] Ошибка парсинга:', e.message);
      }
    }
  });

  pyService.stderr.on('data', (data) => {
    console.error('[Python]', data.toString('utf-8').trim());
  });

  pyService.on('close', (code) => {
    console.error(`[Python] Сервис завершён (код ${code}), перезапуск через 3 сек...`);
    pyService = null;
    setTimeout(startPythonService, 3000);
  });

  pyService.on('error', (err) => {
    console.error('[Python] Ошибка:', err.message);
  });
}

function sendToPython(command) {
  return new Promise((resolve, reject) => {
    if (!pyService || pyService.killed) {
      reject(new Error('Python service not running'));
      return;
    }
    const id = nextRequestId++;
    command.id = id;
    pendingRequests.set(id, { resolve, reject });
    pyService.stdin.write(JSON.stringify(command) + '\n');

    setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error('Python timeout'));
      }
    }, 120000);
  });
}

// === PCM → WAV ===
function pcmToWavBuffer(pcmBuffer) {
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = SAMPLE_RATE * numChannels * bitsPerSample / 8;
  const blockAlign = numChannels * bitsPerSample / 8;
  const dataSize = pcmBuffer.length;
  const fileSize = 36 + dataSize;

  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(fileSize, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(numChannels, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write('data', 36);
  header.writeUInt32LE(dataSize, 40);

  return Buffer.concat([header, pcmBuffer]);
}

// === AI АНАЛИЗ ===
// Единая точка анализа — FastAPI /protocol/analyze. Это исключает расхождение
// промтов между Node и backend и позволяет применять один и тот же валидатор Qwen.
async function extractProtocol(text, callId='unknown') {
  if (!String(text || '').trim()) return null;
  try {
    const response = await fetch('http://localhost:8000/protocol/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({call_id: callId, text: String(text)})
    });
    if (!response.ok) {
      const body = await response.text();
      console.error('[Protocol] API error:', response.status, body.slice(0,300));
      return null;
    }
    const result = await response.json();
    return result?.protocol ? JSON.stringify(result.protocol) : null;
  } catch (e) {
    console.error('[Protocol] AI API error:', e.message);
    return null;
  }
}

// === CRM / КАЛЕНДАРЬ ===
async function persistProtocol(protocol, callId) {
  try {
    const parsed = JSON.parse(String(protocol).replace(/```json|```/g, '').trim());
    await fetch("http://localhost:8000/protocol/structured", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({call_id:callId, ...parsed})});
  } catch(e) { console.error("[Protocol API] Ошибка сохранения:", e.message); }
}

// === ОСНОВНОЙ ОБРАБОТЧИК ===
function setupAudioHandler(wss) {
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  startPythonService();

  // Объединяем аудиоканалы по реальному conversation_id, который выдаёт
  // signaling-сервер обоим участникам одного WebRTC-разговора. Таймер 8 с
  // остаётся только как защита от ситуации, когда второй участник не дошёл
  // до finalize.
  async function finalizeConversation(conversationId) {
    const group = conversationGroups.get(conversationId);
    conversationGroups.delete(conversationId);
    if (!group || !group.calls.size) return;

    const entries = [...group.calls.values()];
    const transcript = entries
      .map((x, i) => {
        const t = String(x.transcript || '').trim();
        return t ? `[Сторона ${i + 1}] ${t}` : '';
      })
      .filter(Boolean)
      .join('\n');

    if (!transcript.trim()) {
      console.warn(`[Protocol] conversation=${conversationId}: общий transcript пуст`);
      for (const x of entries) {
        if (x.ws?.readyState === WebSocket.OPEN) {
          x.ws.send(JSON.stringify({type:'final_protocol_error', message:'Не удалось получить текст разговора'}));
        }
      }
      return;
    }

    console.log(`[Protocol] Общий разговор: conversation=${conversationId}, каналов=${entries.length}, символов=${transcript.length}`);
    console.log(`[Protocol] Запуск единого AI-анализа для общего разговора`);

    const sharedCallId = entries[0].callId;
    const protocol = await extractProtocol(transcript, sharedCallId);

    if (!protocol) {
      for (const x of entries) {
        if (x.ws?.readyState === WebSocket.OPEN) {
          x.ws.send(JSON.stringify({type:'final_protocol_error', message:'Qwen не вернул результат анализа'}));
        }
      }
      return;
    }

    const outPath = path.join(OUTPUT_DIR, `${sharedCallId}_protocol.txt`);
    fs.writeFileSync(outPath, protocol, 'utf-8');
    console.log(`[Protocol] Финальный общий протокол сохранён: ${outPath}`);

    try {
      const parsed = JSON.parse(protocol);
      const agreementsCount = Array.isArray(parsed.agreements) ? parsed.agreements.length : 0;
      const tasksCount = Array.isArray(parsed.tasks) ? parsed.tasks.length : 0;
      const contractAgreements = Array.isArray(parsed.contracts)
        ? parsed.contracts.reduce((n,c)=>n+(Array.isArray(c.agreements)?c.agreements.length:0)+(Array.isArray(c.tasks)?c.tasks.length:0),0)
        : 0;
      const totalAgreements = agreementsCount + tasksCount + contractAgreements;
      const contractsCount = Array.isArray(parsed.contracts) ? parsed.contracts.length : 0;
      console.log(`[Protocol] AI-результат: договоров=${contractsCount}, договорённостей=${totalAgreements}`);
    } catch {}

    // Сохраняем один и тот же итог под каждым call_id обоих участников.
    // Это важно: каждый браузер знает только свой call_id, а AI-анализ общий.
    await persistProtocol(protocol, sharedCallId);
    for (const x of entries) {
      if (x.callId !== sharedCallId) await persistProtocol(protocol, x.callId);
    }

    for (const x of entries) {
      if (x.ws?.readyState === WebSocket.OPEN) {
        x.ws.send(JSON.stringify({
          type:'final_protocol',
          content:protocol,
          shared_call_id:sharedCallId,
          call_id:x.callId,
          conversation_id:conversationId
        }));
      }
    }

    for (const x of entries) {
      buffers.delete(x.callId);
      transcripts.delete(x.callId);
      finalizingCalls.delete(x.callId);
      audioSockets.delete(x.callId);
    }
  }

  function queueFinalize(callId, ws, conversationId) {
    const groupId = conversationId || `call:${callId}`;
    let group = conversationGroups.get(groupId);
    if (!group) {
      group = { createdAt: Date.now(), calls: new Map(), timer: null };
      conversationGroups.set(groupId, group);
    }

    group.calls.set(callId, { callId, ws, transcript: transcripts.get(callId) || '' });

    if (group.timer) clearTimeout(group.timer);
    group.timer = setTimeout(() => {
      finalizeConversation(groupId).catch(e => console.error('[Protocol] finalize error:', e.message));
    }, FINALIZE_JOIN_WINDOW_MS);

    console.log(`[Protocol] ${callId} добавлен в conversation=${groupId}; ждём второго участника до ${FINALIZE_JOIN_WINDOW_MS} мс`);
  }

  async function finishCurrentChannel(callId, ws) {
    let transcript = transcripts.get(callId) || '';
    const remaining = buffers.get(callId) || Buffer.alloc(0);
    if (remaining.length > 0) {
      try {
        const wavBuffer = pcmToWavBuffer(remaining);
        const result = await sendToPython({action:'transcribe', wav_base64:wavBuffer.toString('base64')});
        if (result?.text) {
          transcript = `${transcript} ${result.text}`.trim();
          transcripts.set(callId, transcript);
          console.log(`[GigaAM] ${callId}: "${result.text}" (финальный фрагмент)`);
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({type:'transcript', text:result.text, duration:result.duration, final:true}));
          }
        }
      } catch (e) {
        console.error(`[GigaAM] Финальный фрагмент не распознан: ${e.message}`);
      }
    }
    return transcript;
  }

  wss.on('connection', (ws, req) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const callId = url.searchParams.get('call_id') || 'unknown';
    const conversationId = url.searchParams.get('conversation_id') || `call:${callId}`;

    console.log(`[Audio] Клиент подключён: ${callId}, conversation=${conversationId}`);
    buffers.set(callId, Buffer.alloc(0));
    transcripts.set(callId, '');
    audioSockets.set(callId, ws);

    ws.on('message', async (data, isBinary) => {
      if (!isBinary) {
        try {
          const control = JSON.parse(String(data));
          if (control?.type === 'finalize') {
            if (finalizingCalls.has(callId)) return;
            finalizingCalls.add(callId);
            console.log(`[Protocol] Получена команда finalize: ${callId}`);

            await finishCurrentChannel(callId, ws);
            queueFinalize(callId, ws, conversationId);
            return;
          }
        } catch (_) {}
        return;
      }

      let buffer = buffers.get(callId) || Buffer.alloc(0);
      buffer = Buffer.concat([buffer, data]);
      buffers.set(callId, buffer);

      if (buffer.length >= BUFFER_SIZE) {
        const wavBuffer = pcmToWavBuffer(buffer);
        const wavBase64 = wavBuffer.toString('base64');
        buffers.set(callId, Buffer.alloc(0));

        try {
          const result = await sendToPython({action:'transcribe', wav_base64:wavBase64});
          if (result && result.text) {
            const current = transcripts.get(callId) || '';
            const updated = `${current} ${result.text}`.trim();
            transcripts.set(callId, updated);
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({type:'transcript', text:result.text, duration:result.duration}));
            }
            console.log(`[GigaAM] ${callId}: "${result.text}"`);
          } else if (result && result.error) {
            console.error(`[GigaAM] Ошибка: ${result.error}`);
          }
        } catch (e) {
          console.error(`[GigaAM] Ошибка: ${e.message}`);
        }
      }
    });

    ws.on('close', () => {
      console.log(`[Audio] Клиент отключён: ${callId}`);
      // Если finalize уже был отправлен, данные нужны группе общего анализа.
      // Не удаляем их здесь.
      if (!finalizingCalls.has(callId)) {
        buffers.delete(callId);
        transcripts.delete(callId);
        audioSockets.delete(callId);
      }
    });

    ws.on('error', (err) => {
      console.error(`[Audio] Ошибка ${callId}:`, err.message);
    });
  });

  console.log('[Audio] WebSocket-сервер для аудио готов');
}

module.exports = { setupAudioHandler };
