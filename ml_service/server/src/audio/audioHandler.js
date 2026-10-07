const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// === НАСТРОЙКИ ===
const SAMPLE_RATE = 16000;
const CHUNK_SECONDS = 5;
const BUFFER_SIZE = SAMPLE_RATE * 2 * CHUNK_SECONDS;
const PROTOCOL_INTERVAL_CHUNKS = 3;

// === ПУТИ ===
const ML_DIR = path.join(__dirname, '..', '..', '..');
const PROJECT_ROOT = path.join(ML_DIR, '..');
const PYTHON_PATH = path.join(ML_DIR, 'venv', 'Scripts', 'python.exe');
const SERVICE_SCRIPT = path.join(ML_DIR, 'service.py');
const OUTPUT_DIR = path.join(PROJECT_ROOT, 'data', 'protocols');

// === СОСТОЯНИЕ ===
const buffers = new Map();
const transcripts = new Map();
const chunkCounters = new Map();
const sentTaskKeys = new Map();
const sentEventKeys = new Map();

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

// === LLM ===
const SYSTEM_PROMPT = `Ты — ассистент, который протоколирует деловые телефонные разговоры.
Извлеки:
1. Участников ("Абонент", "Собеседник", если имена не названы)
2. Договорённости (что решили)
3. Задачи (кто, что, к какому сроку)
4. Ключевые темы
5. Если в разговоре обсуждается несколько договоров или сделок, ОБЯЗАТЕЛЬНО раздели их. Не смешивай задачи разных договоров.

Формат ответа — строго JSON:
{
  "topic": "тема",
  "participants": ["роль1", "роль2"],
  "contracts": [{"contract_id":"15","contract_name":"Договор №15","agreements":["договорённость"],"tasks":[{"owner":"кто","task":"что","deadline":"когда","priority":"medium"}]}],
  "agreements": ["договорённость 1"],
  "tasks": [{"owner": "кто", "task": "что", "deadline": "когда"}],
  "key_points": ["мысль 1"]
}

Если поле не применимо — оставь пустым. Не выдумывай факты.
Отвечай ТОЛЬКО валидным JSON.`;

async function extractProtocol(text) {
  if (!text.trim()) return null;
  try {
    const response = await fetch('http://localhost:11434/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'qwen3:8b',
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: `Транскрипт:\n\n${text}` }
        ],
        stream: false,
        think: false,
        keep_alive: '0',
        options: { temperature: 0.2, num_ctx: 8192 }
      })
    });
    const data = await response.json();
    return data.message?.content || null;
  } catch (e) {
    console.error('[Protocol] Ollama error:', e.message);
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

async function sendTasksToCRM(protocol, callId) {
  if (!protocol) return;
  let data;
  try {
    const clean = String(protocol).replace(/```json|```/g, '').trim();
    data = JSON.parse(clean);
  } catch (e) {
    console.error('[CRM] Ошибка парсинга:', e.message);
    return;
  }
  const allTasks = [...(data.tasks || [])];
  for (const contract of data.contracts || []) {
    for (const task of contract.tasks || []) allTasks.push({...task, contract_id: contract.contract_id, contract_name: contract.contract_name});
  }
  if (!sentTaskKeys.has(callId)) sentTaskKeys.set(callId, new Set());
  for (const task of allTasks) {
    const key = [task.contract_id || '', task.owner || '', task.task || '', task.deadline || ''].join('|');
    if (sentTaskKeys.get(callId).has(key)) continue;
    sentTaskKeys.get(callId).add(key);
    try {
      const response = await fetch('http://localhost:8000/integrations/crm/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          call_id: callId,
          owner: task.owner || 'Не указано',
          task: task.task || '',
          deadline: task.deadline || null,
          contract_id: task.contract_id || null,
          contract_name: task.contract_name || null,
          priority: task.priority || 'medium' 
        })
      });
      const result = await response.json();
      console.log(`[CRM] Задача создана: "${task.task}" → ${result.status}`);
    } catch (e) {
      console.error('[CRM] Ошибка:', e.message);
    }
  }
}

async function sendEventsToCalendar(protocol, callId) {
  if (!protocol) return;
  let data;
  try {
    const clean = String(protocol).replace(/```json|```/g, '').trim();
    data = JSON.parse(clean);
  } catch (e) {
    return;
  }
  const meetingKeywords = ['встреч', 'конференц', 'созвон', 'звонок'];
  const meetings = (data.agreements || []).filter(a =>
    meetingKeywords.some(kw => a.toLowerCase().includes(kw))
  );
  for (const meeting of meetings) {
    try {
      const response = await fetch('http://localhost:8000/integrations/calendar/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          call_id: callId,
          title: meeting,
          time: meeting,
          participants: (data.participants || []).map(p => typeof p === 'string' ? p : (p.name || p.role || '—'))
        })
      });
      const result = await response.json();
      console.log(`[Calendar] Событие: ${result.ics_file}`);
    } catch (e) {
      console.error('[Calendar] Ошибка:', e.message);
    }
  }
}

// === ОСНОВНОЙ ОБРАБОТЧИК ===
function setupAudioHandler(wss) {
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  startPythonService();

  wss.on('connection', (ws, req) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const callId = url.searchParams.get('call_id') || 'unknown';

    console.log(`[Audio] Клиент подключён: ${callId}`);
    buffers.set(callId, Buffer.alloc(0));
    transcripts.set(callId, '');
    chunkCounters.set(callId, 0);

    ws.on('message', async (data) => {
      if (!Buffer.isBuffer(data)) return;

      let buffer = buffers.get(callId) || Buffer.alloc(0);
      buffer = Buffer.concat([buffer, data]);
      buffers.set(callId, buffer);

      if (buffer.length >= BUFFER_SIZE) {
        const wavBuffer = pcmToWavBuffer(buffer);
        const wavBase64 = wavBuffer.toString('base64');
        buffers.set(callId, Buffer.alloc(0));

        try {
          const result = await sendToPython({
            action: 'transcribe',
            wav_base64: wavBase64
          });

          if (result && result.text) {
            const current = transcripts.get(callId) || '';
            const updated = current + ' ' + result.text;
            transcripts.set(callId, updated);

            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({
                type: 'transcript',
                text: result.text,
                duration: result.duration
              }));
            }
            console.log(`[GigaAM] ${callId}: "${result.text}"`);

            const cnt = (chunkCounters.get(callId) || 0) + 1;
            chunkCounters.set(callId, cnt);

            if (cnt % PROTOCOL_INTERVAL_CHUNKS === 0) {
              const protocol = await extractProtocol(updated);
              if (protocol && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                  type: 'protocol',
                  content: protocol,
                  final: false
                }));
                console.log(`[Protocol] ${callId}: обновление отправлено`);

                await persistProtocol(protocol, callId);
                await sendTasksToCRM(protocol, callId);
                await sendEventsToCalendar(protocol, callId);
              }
            }
          } else if (result && result.error) {
            console.error(`[GigaAM] Ошибка: ${result.error}`);
          }
        } catch (e) {
          console.error(`[GigaAM] Ошибка: ${e.message}`);
        }
      }
    });

    ws.on('close', async () => {
      console.log(`[Audio] Клиент отключён: ${callId}`);

      const transcript = transcripts.get(callId) || '';
      const protocol = await extractProtocol(transcript);
      if (protocol) {
        const outPath = path.join(OUTPUT_DIR, `${callId}_protocol.txt`);
        fs.writeFileSync(outPath, protocol, 'utf-8');
        console.log(`[Protocol] Финальный сохранён: ${outPath}`);

        await persistProtocol(protocol, callId);
        await sendTasksToCRM(protocol, callId);
        await sendEventsToCalendar(protocol, callId);
      }

      buffers.delete(callId);
      transcripts.delete(callId);
      chunkCounters.delete(callId);
      sentTaskKeys.delete(callId);
      sentEventKeys.delete(callId);
    });

    ws.on('error', (err) => {
      console.error(`[Audio] Ошибка ${callId}:`, err.message);
    });
  });

  console.log('[Audio] WebSocket-сервер для аудио готов');
}

module.exports = { setupAudioHandler };