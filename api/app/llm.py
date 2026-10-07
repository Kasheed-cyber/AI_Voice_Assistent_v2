"""
Локальная LLM (Ollama + Qwen 3 8B) для извлечения только подтверждённых
деловых договорённостей из транскрипта.
"""
import json
import re
import ollama

MODEL = "qwen3:8b"
MIN_CONFIDENCE = 0.82

SYSTEM_PROMPT = r'''Ты — строгий AI-аналитик деловых телефонных разговоров.
Твоя задача — НЕ пересказывать разговор, а находить только реальные, подтверждённые ДЕЛОВЫЕ договорённости и поручения.

КРИТИЧЕСКОЕ ПРАВИЛО:
Если в разговоре нет делового содержания или нет явного принятого решения/обязательства — верни пустые массивы agreements, tasks и contracts.
Лучше пропустить сомнительную договорённость, чем создать ложное срабатывание.

Сначала определи тип разговора:
- business — деловой разговор;
- mixed — есть и бытовая, и деловая часть; извлекай только деловую;
- personal — бытовой/личный разговор; ничего не извлекай;
- unclear — недостаточно данных; ничего не извлекай.

ЧТО СЧИТАТЬ ДОГОВОРЁННОСТЬЮ:
1. Явное обязательство участника: «я отправлю КП», «мы подготовим документы», «я позвоню клиенту».
2. Явное поручение другому участнику: «пришлите счёт», «подтвердите количество», если поручение относится к деловой ситуации.
3. Подтверждённое совместное решение: «договорились отправить КП в пятницу», «подтверждаю встречу с клиентом во вторник».
4. Конкретная деловая встреча/звонок/поставка/оплата, если она действительно согласована, а не только предложена.

ЧТО НЕ СЧИТАТЬ ДОГОВОРЁННОСТЬЮ:
- приветствия, прощания, благодарности, бытовой разговор;
- шутки, мат, оскорбления, флирт, личные планы и разговоры;
- вопросы: «когда отправить?», «можешь ли ты?»;
- предложения без принятия: «давайте...», «можно...», «предлагаю...», «может быть...»;
- неопределённые фразы: «надо бы», «нужно», «хорошо бы», «потом решим», если нет принятого решения или поручения;
- пересказ чужих планов без принятия обязательства;
- общие цели и лозунги без конкретного результата: «выступить и победить», «сделаем всё», «надо постараться»;
- участие в хакатоне, конференции, прогулке, игре или другом мероприятии само по себе не является CRM-договорённостью; извлекай только конкретное деловое действие, которое кто-то обязан выполнить;
- фразы, которые выглядят как обязательство только из-за слов «договорились», «подтверждаю», «сделаем», но не содержат ясного делового действия;
- действия, которые относятся к личной жизни, даже если есть слова «договорились», «приду», «встретимся» и т.п.

ПРОВЕРКА КАЖДОЙ КАНДИДАТНОЙ ДОГОВОРЁННОСТИ:
Она проходит фильтр только если одновременно выполнены ВСЕ условия:
A) содержание явно деловое;
B) есть конкретное действие/решение/поручение;
C) действие принято или подтверждено, а не только предложено;
D) из транскрипта понятно, что именно нужно сделать;
E) можно указать точную цитату evidence из транскрипта.
Если хотя бы одно условие не выполнено — НЕ включай запись.

РАСПОЗНАВАНИЕ РЕЧИ МОЖЕТ БЫТЬ ОШИБОЧНЫМ:
Не достраивай смысл по одному искажённому слову. Не превращай похожие по звучанию слова в обязательства.
Если фраза непонятна — пропусти её.

ДОГОВОРЫ:
- contract_id указывай только если номер договора явно назван в транскрипте.
- Если номер произнесён словами, например «триста двадцать один», обязательно преобразуй его в цифры: «321».
- contract_name указывай только если название явно названо.
- Не наследуй номер договора на новую договорённость, если он не относится к ней явно.
- Никогда не придумывай номер договора.
- Если номер не назван, contract_id должен быть пустым, contract_name — «Договор без номера».

ДОКАЗАТЕЛЬСТВО:
Для каждой договорённости и задачи укажи evidence — точную непрерывную цитату из исходного транскрипта, на которой основан вывод. Не перефразируй evidence.
confidence — число от 0 до 1. Для сомнительных случаев ставь ниже 0.82 и такую запись НЕ включай в итоговые массивы.

ФОРМАТ — СТРОГО JSON, БЕЗ MARKDOWN:
{
  "topic": "краткая тема или пустая строка",
  "conversation_type": "business|mixed|personal|unclear",
  "participants": [{"role":"клиент/менеджер/собеседник","name":"имя или пусто"}],
  "contracts": [
    {
      "contract_id":"",
      "contract_name":"Договор без номера",
      "agreements":[
        {"text":"что конкретно решили","evidence":"точная цитата","confidence":0.95,"confirmed":true}
      ],
      "tasks":[
        {"owner":"кто","task":"что сделать","deadline":"когда или null","priority":"high|medium|low","status":"pending","evidence":"точная цитата","confidence":0.95,"confirmed":true}
      ]
    }
  ],
  "agreements": [
    {"text":"что конкретно решили","evidence":"точная цитата","confidence":0.95,"confirmed":true}
  ],
  "tasks": [
    {"owner":"кто","task":"что сделать","deadline":"когда или null","priority":"high|medium|low","status":"pending","evidence":"точная цитата","confidence":0.95,"confirmed":true}
  ],
  "key_points": ["только действительно важные деловые факты"]
}

Если договорённостей нет, обязательно верни:
"contracts": [], "agreements": [], "tasks": []

Никогда не используй номера 15, 18, 21 или любые другие номера из примеров, если их нет в самом транскрипте.'''


def clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def _valid_evidence(text: str, evidence: str) -> bool:
    if not evidence or not text:
        return False
    return evidence.strip().lower() in text.strip().lower()


def _valid_item(item, transcript: str):
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or item.get("agreement") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    try:
        confidence = float(item.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confirmed = item.get("confirmed") is True
    if not text or not evidence or not confirmed or confidence < MIN_CONFIDENCE:
        return None
    if not _valid_evidence(transcript, evidence):
        return None
    return {**item, "text": text, "evidence": evidence, "confidence": confidence, "confirmed": True}


def _valid_task(item, transcript: str):
    if not isinstance(item, dict):
        return None
    task = str(item.get("task") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    try:
        confidence = float(item.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confirmed = item.get("confirmed") is True
    if not task or not evidence or not confirmed or confidence < MIN_CONFIDENCE:
        return None
    if not _valid_evidence(transcript, evidence):
        return None
    return {**item, "task": task, "evidence": evidence, "confidence": confidence, "confirmed": True}



ONES = {
    'ноль':0,'один':1,'одна':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,
    'десять':10,'одиннадцать':11,'двенадцать':12,'тринадцать':13,'четырнадцать':14,'пятнадцать':15,'шестнадцать':16,'семнадцать':17,'восемнадцать':18,'девятнадцать':19,
}
TENS={'двадцать':20,'тридцать':30,'сорок':40,'пятьдесят':50,'шестьдесят':60,'семьдесят':70,'восемьдесят':80,'девяносто':90}
HUNDREDS={'сто':100,'двести':200,'триста':300,'четыреста':400,'пятьсот':500,'шестьсот':600,'семьсот':700,'восемьсот':800,'девятьсот':900}

def _ru_number_words_to_int(words):
    total=0
    for w in re.sub(r'[^а-яё -]',' ',words.lower()).split():
        if w in HUNDREDS: total += HUNDREDS[w]
        elif w in TENS: total += TENS[w]
        elif w in ONES: total += ONES[w]
        else: return None
    return total if total > 0 else None

def _contract_id_from_text(text):
    text=str(text or '')
    # First prefer explicit digits: «договор №482», «договор номер 482».
    m=re.search(r'\b(?:договор\w*|контракт\w*)[^\n,.]{0,30}?(?:№|\bномер\b|\bN\b|#)\s*([0-9][0-9-]*)', text, re.I)
    if m: return m.group(1)
    # ASR often produces "договоренности номер" instead of "по договору номер".
    m=re.search(r'\b(?:договор\w*|контракт\w*)[^\n,.]{0,40}?\bномер\s+([0-9][0-9-]*)', text, re.I)
    if m: return m.group(1)
    # Number written in Russian words, e.g. "номер триста тридцать два".
    m=re.search(r'\b(?:договор\w*|контракт\w*)[^\n,.]{0,45}?\bномер\s+([а-яё -]{2,60})', text, re.I)
    if m:
        words=m.group(1).strip()
        words=re.split(r'\b(?:мы|и|что|по|он|она|это|который|которая|завтра|встречаемся)\b',words,maxsplit=1,flags=re.I)[0].strip()
        value=_ru_number_words_to_int(words)
        if value is not None: return str(value)
    # ASR sometimes loses the word "номер": "по договору триста двадцать два".
    m=re.search(r'\b(?:договор\w*|контракт\w*)\s+([а-яё -]{2,45})', text, re.I)
    if m:
        words=m.group(1).strip()
        words=re.split(r'\b(?:завтра|сегодня|мы|и|что|по|в|на|у|как|будем|нужно|должны|встречаемся)\b',words,maxsplit=1,flags=re.I)[0].strip()
        value=_ru_number_words_to_int(words)
        if value is not None: return str(value)
    # Accept "номер 332" / "номер триста тридцать два" even if ASR
    # dropped the nearby word "договор". This is common in streaming ASR.
    m=re.search(r'\bномер\s*([0-9][0-9-]*)\b', text, re.I)
    if m: return m.group(1)
    m=re.search(r'\bномер\s+([а-яё -]{2,60})', text, re.I)
    if m:
        words=m.group(1).strip()
        words=re.split(r'\b(?:завтра|сегодня|мы|и|что|по|в|на|у|как|будем|нужно|должны|встречаемся|я|вы|хорошо|согласен|договорились)\b', words, maxsplit=1, flags=re.I)[0].strip()
        value=_ru_number_words_to_int(words)
        if value is not None: return str(value)
    return ''

def _apply_contract_ids(data, transcript):
    def item_id(item):
        if not isinstance(item,dict): return ''
        return _contract_id_from_text(item.get('evidence','')) or _contract_id_from_text(item.get('text',''))
    for c in data.get('contracts') or []:
        if not isinstance(c,dict): continue
        cid=str(c.get('contract_id') or '').strip()
        if not cid:
            for item in list(c.get('agreements') or [])+list(c.get('tasks') or []):
                cid=item_id(item)
                if cid: break
        if not cid: cid=_contract_id_from_text(c.get('contract_name',''))
        c['contract_id']=cid
        c['contract_name']=f'Договор №{cid}' if cid else 'Договор без номера'
    # Top-level items can also carry the contract number.
    for key in ('agreements','tasks'):
        for item in data.get(key) or []:
            if isinstance(item,dict) and not item.get('contract_id'):
                cid=item_id(item)
                if cid: item['contract_id']=cid; item['contract_name']=f'Договор №{cid}'
    return data

def extract_protocol(transcript_text: str) -> dict:
    print(f"[LLM] Отправка в {MODEL}...")
    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Исходный транскрипт разговора. Анализируй только этот текст:\n\n{transcript_text}"}
        ],
        think=False,
        options={"temperature": 0.1, "num_ctx": 8192}
    )
    raw = response["message"]["content"]
    cleaned = clean_json(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[LLM] Ошибка парсинга JSON: {e}")
        print(f"[LLM] Сырой ответ: {raw[:500]}")
        parsed = {}

    conversation_type = parsed.get("conversation_type") or "unclear"
    if conversation_type not in {"business", "mixed", "personal", "unclear"}:
        conversation_type = "unclear"
    if conversation_type in {"personal", "unclear"}:
        parsed["contracts"] = []
        parsed["agreements"] = []
        parsed["tasks"] = []

    agreements = []
    for a in parsed.get("agreements") or []:
        item = _valid_item(a, transcript_text)
        if item:
            agreements.append(item)

    tasks = []
    for t in parsed.get("tasks") or []:
        item = _valid_task(t, transcript_text)
        if item:
            tasks.append(item)

    contracts = []
    for c in parsed.get("contracts") or []:
        if not isinstance(c, dict):
            continue
        c_agreements = []
        for a in c.get("agreements") or []:
            item = _valid_item(a, transcript_text)
            if item:
                c_agreements.append(item)
        c_tasks = []
        for t in c.get("tasks") or []:
            item = _valid_task(t, transcript_text)
            if item:
                c_tasks.append(item)
        if c_agreements or c_tasks:
            contract_id = str(c.get("contract_id") or "").strip()
            contract_name = str(c.get("contract_name") or "Dоговор без номера").strip()
            contracts.append({
                "contract_id": contract_id,
                "contract_name": contract_name,
                "agreements": c_agreements,
                "tasks": c_tasks,
            })

    result = {
        "topic": parsed.get("topic") or None,
        "conversation_type": conversation_type,
        "participants": [p for p in (parsed.get("participants") or []) if isinstance(p, dict)],
        "agreements": agreements,
        "tasks": tasks,
        "contracts": contracts,
        "key_points": parsed.get("key_points") or [],
    }
    return _apply_contract_ids(result, transcript_text)


def check_ollama_available() -> bool:
    try:
        models = ollama.list()
        names = [m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "") for m in models.get("models", [])]
        return any(MODEL in name for name in names)
    except Exception as e:
        print(f"[LLM] Ollama недоступна: {e}")
        return False
