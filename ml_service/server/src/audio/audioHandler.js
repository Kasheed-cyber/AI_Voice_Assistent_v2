const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const crypto = require('crypto');

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
const DATA_KEY = crypto.createHash('sha256')
  .update(process.env.AI_DATA_KEY || `dev-${process.pid}-${Date.now()}`)
  .digest();

function encryptAtRest(text) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', DATA_KEY, iv);
  const encrypted = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return JSON.stringify({
    algorithm: 'aes-256-gcm',
    iv: iv.toString('base64'),
    tag: tag.toString('base64'),
    data: encrypted.toString('base64')
  });
}


// === СОСТОЯНИЕ ===
const buffers = new Map();
const transcripts = new Map();
const chunkCounters = new Map();
// Уже отправленные задачи/события. Храним сами задачи, чтобы финальная LLM-формулировка
// могла быть распознана как та же задача, даже если текст немного отличается.
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
Извлеки все договорённости и задачи. В одном звонке может обсуждаться несколько договоров/сделок.
НЕ смешивай их: группируй данные по contracts.

Формат строго JSON:
{
  "topic": "общая тема",
  "participants": [{"role": "клиент/менеджер", "name": "имя или null"}],
  "contracts": [
    {
      "contract_id": "номер договора или null",
      "contract_name": "название сделки или null",
      "agreements": ["договорённость"],
      "facts": ["сумма/цена/количество/условие оплаты"],
      "tasks": [{"owner":"кто","task":"что","deadline":"когда или null","status":"pending","priority":"medium"}]
    }
  ],
  "agreements": ["все договорённости"],
  "facts": ["все суммы, цены, количества и условия оплаты"],
  "tasks": [{"owner":"кто","task":"что","deadline":"когда или null","status":"pending","priority":"medium","contract_id":"номер или null"}],
  "key_points": ["ключевая мысль"]
}

Критически важно: любое конкретное обязательство стороны выполнить действие должно быть в tasks, даже если оно также указано в agreements/facts.
Например: «доставку планируем на пятницу» -> task «Доставить 50 кг зерна», deadline «пятница»; «счет скину вам на почту» -> task «Отправить счет на почту»; «оплачу счет завтра утром» -> task «Оплатить счет», deadline «завтра утром».
Не ставь deadline=null, если срок явно произнесён в транскрипте.
Не выдумывай факты. Если связь с договором неизвестна, используй null.
Отвечай ТОЛЬКО валидным JSON.`

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
function inferDeadlineFromTranscript(transcript, taskText) {
  const text = String(transcript || '').toLowerCase().replace(/ё/g, 'е');
  const task = String(taskText || '').toLowerCase().replace(/ё/g, 'е');
  if (!text || !task) return null;

  const hasTaskWord = (words) => words.some(w => task.includes(w));
  if (hasTaskWord(['оплат'])) {
    const m = text.match(/опла\w*[\s\S]{0,100}?\b(сегодня|завтра|послезавтра)(?:\s+(утром|днем|днём|вечером))?/i);
    if (m) return `${m[1]}${m[2] ? ' ' + m[2] : ''}`;
  }
  if (hasTaskWord(['счет', 'счёт', 'почт'])) {
    const m = text.match(/(?:счет|счёт|скину|отправлю|пришлю)[\s\S]{0,100}?\b(сегодня|завтра|послезавтра|в\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье))\b/i);
    if (m) return m[1];
  }
  if (hasTaskWord(['достав'])) {
    const m = text.match(/достав\w*[\s\S]{0,100}?\b(?:на\s+)?(сегодня|завтра|послезавтра|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)([^.!?]{0,40})/i);
    if (m) {
      const day = m[1];
      const nearbyTime = text.match(new RegExp(`(?:${day})[^.!?]{0,80}?(?:до|к|в)\\s+(?:\\d{1,2}(?::\\d{2})?|одного|два|двух|три|трех|четыре|четырех|пять|пяти|шесть|шести|семь|семи|восемь|восьми|девять|девяти|десять|десяти|одиннадцать|одиннадцати|двенадцать|двенадцати)(?:\\s+(?:час(?:а|ов)?|дня|вечера))?`, 'i'));
      if (nearbyTime) return nearbyTime[0].trim();
      return `${day}${m[2] || ''}`.trim();
    }
  }
  return null;
}

function repairProtocolData(protocol, transcript) {
  const data = parseProtocol(protocol);
  if (!data) return null;

  const allContracts = data.contracts || [];
  const allTasks = [];
  for (const contract of allContracts) {
    contract.tasks = contract.tasks || [];
    for (const task of contract.tasks) {
      if (!task.deadline) {
        const inferred = inferDeadlineFromTranscript(transcript, task.task);
        if (inferred) task.deadline = inferred;
      }
      allTasks.push(task);
    }
  }

  data.tasks = data.tasks || [];
  for (const task of data.tasks) {
    if (!task.deadline) {
      const inferred = inferDeadlineFromTranscript(transcript, task.task);
      if (inferred) task.deadline = inferred;
    }
    allTasks.push(task);
  }

  // Если LLM оставила поставку только в agreements, превращаем её в явную задачу.
  const deliveryText = [...(data.agreements || []), ...allContracts.flatMap(c => c.agreements || [])]
    .find(x => /достав\w*/i.test(String(x)) && /(пятниц|сред|четверг|завтра|сегодня|послезавтра)/i.test(String(x)));
  const hasDeliveryTask = allTasks.some(t => /достав\w*/i.test(String(t.task || '')));
  if (deliveryText && !hasDeliveryTask) {
    const match = String(deliveryText).match(/достав\w*[^.!?]*(?:пятниц\w*|сред\w*|четверг\w*|завтра|сегодня|послезавтра)[^.!?]*/i);
    const title = match ? match[0].replace(/\s+/g, ' ').trim() : String(deliveryText).trim();
    const deadline = inferDeadlineFromTranscript(transcript, 'доставить');
    const task = {
      owner: 'менеджер',
      task: /^достав/i.test(title) ? title : `Доставить товар: ${title}`,
      deadline: deadline || null,
      status: 'pending',
      priority: 'medium',
      contract_id: null,
      contract_name: null
    };
    if (allContracts.length) {
      allContracts[0].tasks = allContracts[0].tasks || [];
      task.contract_id = allContracts[0].contract_id || null;
      task.contract_name = allContracts[0].contract_name || null;
      allContracts[0].tasks.push(task);
    } else {
      data.tasks.push(task);
    }
  }

  data.contracts = allContracts;
  return JSON.stringify(data);
}

function parseProtocol(protocol) {
  try {
    return JSON.parse(String(protocol).replace(/```json|```/g, '').trim());
  } catch (e) {
    console.error('[Protocol] Ошибка парсинга:', e.message);
    return null;
  }
}

function normalizeTaskText(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[^а-яa-z0-9\s]/gi, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .filter(w => !new Set([
      'пожалуйста', 'сегодня', 'завтра', 'утром', 'вечером', 'конца', 'дня',
      'до', 'к', 'мне', 'тебе', 'вам', 'нам', 'тогда', 'потом', 'уже', 'все'
    ]).has(w));
}

function sameContract(a, b) {
  const aid = a.contract_id || a.contract_name || '';
  const bid = b.contract_id || b.contract_name || '';
  return !aid || !bid || aid === bid;
}

function taskLooksSame(a, b) {
  if (!sameContract(a, b)) return false;
  if (a.owner && b.owner && a.owner !== b.owner && a.owner !== 'не указано' && b.owner !== 'не указано') return false;
  const aa = normalizeTaskText(a.task);
  const bb = normalizeTaskText(b.task);
  if (!aa.length || !bb.length) return false;
  const setA = new Set(aa);
  const setB = new Set(bb);
  let common = 0;
  for (const word of setA) if (setB.has(word)) common++;
  const minSize = Math.min(setA.size, setB.size);
  const union = new Set([...setA, ...setB]).size;
  // Если одна формулировка практически является расширением другой
  // («Пришлите новые исходники сегодня» -> «Пришлите новые исходники»),
  // считаем это одной задачей.
  return (common / minSize >= 0.75) || (common / union >= 0.55);
}

function taskKey(callId, task) {
  return JSON.stringify([
    callId, task.contract_id || task.contract_name || '', task.owner || '',
    normalizeTaskText(task.task).join(' '), task.deadline || ''
  ]);
}

async function sendTasksToCRM(protocol, callId) {
  const data = parseProtocol(protocol);
  if (!data) return;

  if (!sentTaskKeys.has(callId)) sentTaskKeys.set(callId, []);
  const sent = sentTaskKeys.get(callId);

  // Поддерживаем и новый contracts, и старый tasks.
  const tasks = [];
  for (const contract of data.contracts || []) {
    for (const task of contract.tasks || []) {
      tasks.push({
        ...task,
        contract_id: task.contract_id ?? contract.contract_id ?? null,
        contract_name: task.contract_name ?? contract.contract_name ?? null
      });
    }
  }
  for (const task of data.tasks || []) {
    if (!tasks.some(x => taskKey(callId, x) === taskKey(callId, task))) tasks.push(task);
  }

  for (const task of tasks) {
    const key = taskKey(callId, task);
    if (sent.some(existing => taskLooksSame(existing.task, task))) continue;

    try {
      const response = await fetch('http://localhost:8000/integrations/crm/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          call_id: callId,
          owner: task.owner || 'Не указано',
          task: task.task || '',
          deadline: task.deadline || null,
          priority: task.priority || "medium",
          contract_id: task.contract_id || null,
          contract_name: task.contract_name || null
        })
      });
      const result = await response.json();
      if (response.ok) sent.push({ ...task, _key: key });
      console.log(`[CRM] Задача "${task.task}" (${task.contract_id || 'без договора'}) → ${result.status}`);
    } catch (e) {
      console.error('[CRM] Ошибка:', e.message);
    }
  }
}

async function sendEventsToCalendar(protocol, callId) {
  const data = parseProtocol(protocol);
  if (!data) return;

  if (!sentEventKeys.has(callId)) sentEventKeys.set(callId, new Set());
  const sent = sentEventKeys.get(callId);

  const meetingKeywords = ['встреч', 'конференц', 'созвон', 'звонок'];
  const candidates = [];

  for (const contract of data.contracts || []) {
    for (const agreement of contract.agreements || []) {
      if (meetingKeywords.some(kw => agreement.toLowerCase().includes(kw))) {
        candidates.push({ title: agreement, time: agreement, contract });
      }
    }
    for (const task of contract.tasks || []) {
      if (task.deadline) {
        candidates.push({ title: task.task || 'Задача по договорённости', time: task.deadline, contract });
      }
    }
  }

  for (const task of data.tasks || []) {
    if (task.deadline && !candidates.some(x => x.title === task.task && x.time === task.deadline)) {
      candidates.push({ title: task.task || 'Задача по договорённости', time: task.deadline, contract: {} });
    }
  }

  for (const agreement of data.agreements || []) {
    if (meetingKeywords.some(kw => agreement.toLowerCase().includes(kw)) &&
        !candidates.some(x => x.title === agreement)) {
      candidates.push({ title: agreement, time: agreement, contract: {} });
    }
  }

  for (const { title, time, contract } of candidates) {
    const eventKey = JSON.stringify([callId, contract.contract_id || contract.contract_name || '', title, time]);
    if (sent.has(eventKey)) continue;

    try {
      const response = await fetch('http://localhost:8000/integrations/calendar/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          call_id: callId,
          title,
          time,
          participants: (data.participants || []).map(p =>
            typeof p === 'string' ? p : (p.name || p.role || '—'))
        })
      });
      const result = await response.json();
      if (response.ok) sent.add(eventKey);
      console.log(`[Calendar] Событие (${contract.contract_id || 'без договора'}): ${result.ics_file}`);
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
    sentTaskKeys.set(callId, []);
    sentEventKeys.set(callId, new Set());

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
              let protocol = await extractProtocol(updated);
              protocol = repairProtocolData(protocol, updated) || protocol;
              if (protocol && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                  type: 'protocol',
                  content: protocol,
                  final: false
                }));
                console.log(`[Protocol] ${callId}: обновление отправлено`);

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
      let protocol = await extractProtocol(transcript);
      protocol = repairProtocolData(protocol, transcript) || protocol;
      if (protocol) {
        const outPath = path.join(OUTPUT_DIR, `${callId}_protocol.enc.json`);
        fs.writeFileSync(outPath, encryptAtRest(protocol), 'utf-8');
        console.log(`[Protocol] Финальный сохранён: ${outPath}`);

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

module.exports = { setupAudioHandler, _test: { normalizeTaskText, taskLooksSame, taskKey, inferDeadlineFromTranscript, repairProtocolData } };