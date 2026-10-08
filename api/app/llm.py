"""
Локальная LLM (Ollama + Qwen 3 8B) для извлечения только подтверждённых
деловых договорённостей из транскрипта.
"""
import json
import re
from datetime import date, datetime, timedelta
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
5. Деловая встреча или действие, которое уже было назначено ранее и в разговоре подтверждается: «встреча назначена на 11 октября», «я планирую встретиться... — да, я готов». Если собеседник принимает участие/подтверждает готовность, это договорённость даже без номера договора.

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

ДОГОВОРЫ — НОМЕР НЕ ОБЯЗАТЕЛЕН:
- Отсутствие номера договора НИКОГДА не является причиной пропускать реальную договорённость.
- contract_id указывай только если номер договора явно назван в транскрипте.
- Если номер произнесён словами, например «триста двадцать один», обязательно преобразуй его в цифры: «321».
- contract_name указывай только если название явно названо.
- Не наследуй номер договора на новую договорённость, если он не относится к ней явно.
- Никогда не придумывай номер договора.
- Если номер не назван, contract_id должен быть пустым, contract_name — «Договор без номера».
- ВАЖНО: «Договор без номера» — это нормальная категория результата, а НЕ причина вернуть пустые contracts/agreements/tasks.
- Пример: «обсудить нашу договорённость, назначенную на 11.10.2026, обсудить разработку мобильного приложения, вы готовы? — готов» — это подтверждённая деловая договорённость даже без номера договора.

СРОКИ И ДАТЫ — КРИТИЧЕСКИ ВАЖНО:
- Если в разговоре названа дата/время проведения согласованного действия, встречи, звонка, лекции, поставки, отправки или другого обязательства — обязательно запиши её в поле deadline соответствующей task.
- Это относится не только к словам «срок», «дедлайн», «до», но и к самой дате события: «встретимся 28 октября», «лекция двадцать восьмого числа десятого месяца 2026 года», «завтра в десять часов».
- Если дата выражена словами, преобразуй её в ISO-дату YYYY-MM-DD. Например: «двадцать восьмого числа десятого месяца две тысячи двадцать шестого года» → «2026-10-28».
- Если указано относительное время («завтра», «послезавтра», «в пятницу», «во вторник»), рассчитай конкретную дату относительно текущей даты, указанной в сообщении пользователя. Не придумывай дату, если относительная формулировка неоднозначна.
- Если указано время суток, сохрани его после даты в формате YYYY-MM-DD HH:MM.
- Если дата есть в транскрипте, но Qwen не уверен в формулировке, всё равно укажи её только если она однозначно относится к конкретной договорённости.
- Не оставляй deadline=null, если из evidence или контекста той же договорённости однозначно следует дата проведения/исполнения.

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


# ---------- Даты и сроки ----------
_CARDINAL_BY_ORDINAL = {
    'первого':'один','первое':'один','первый':'один','первому':'один','первым':'один',
    'второго':'два','второе':'два','второй':'два','второму':'два','вторым':'два',
    'третьего':'три','третье':'три','третий':'три','третьему':'три','третьим':'три',
    'четвёртого':'четыре','четвертого':'четыре','четвёртое':'четыре','четвертое':'четыре','четвёртый':'четыре','четвертый':'четыре',
    'пятого':'пять','пятое':'пять','пятый':'пять','пятому':'пять','пятым':'пять',
    'шестого':'шесть','шестое':'шесть','шестой':'шесть','шестому':'шесть','шестым':'шесть',
    'седьмого':'семь','седьмое':'семь','седьмой':'семь','седьмому':'семь','седьмым':'семь',
    'восьмого':'восемь','восьмое':'восемь','восьмой':'восемь','восьмому':'восемь','восьмым':'восемь',
    'девятого':'девять','девятое':'девять','девятый':'девять','девятому':'девять','девятым':'девять',
    'десятого':'десять','десятое':'десять','десятый':'десять','десятому':'десять','десятым':'десять',
    'одиннадцатого':'одиннадцать','одиннадцатое':'одиннадцать','одиннадцатый':'одиннадцать',
    'двенадцатого':'двенадцать','двенадцатое':'двенадцать','двенадцатый':'двенадцать',
    'тринадцатого':'тринадцать','тринадцатое':'тринадцать','тринадцатый':'тринадцать',
    'четырнадцатого':'четырнадцать','четырнадцатое':'четырнадцать','четырнадцатый':'четырнадцать',
    'пятнадцатого':'пятнадцать','пятнадцатое':'пятнадцать','пятнадцатый':'пятнадцать',
    'шестнадцатого':'шестнадцать','шестнадцатое':'шестнадцать','шестнадцатый':'шестнадцать',
    'семнадцатого':'семнадцать','семнадцатое':'семнадцать','семнадцатый':'семнадцать',
    'восемнадцатого':'восемнадцать','восемнадцатое':'восемнадцать','восемнадцатый':'восемнадцать',
    'девятнадцатого':'девятнадцать','девятнадцатое':'девятнадцать','девятнадцатый':'девятнадцать',
    'двадцатого':'двадцать','двадцатое':'двадцать','двадцатый':'двадцать',
    'тридцатого':'тридцать','тридцатое':'тридцать','тридцатый':'тридцать',
}
_MONTHS = {
    'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,
    'июля':7,'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12,
}
_WEEKDAYS = {'понедельник':0,'вторник':1,'среду':2,'среда':2,'четверг':3,'пятницу':4,'пятница':4,'субботу':5,'суббота':5,'воскресенье':6,'воскресенья':6}

def _normalize_num_token(token):
    token=token.lower().strip(' ,.')
    return _CARDINAL_BY_ORDINAL.get(token, token)

def _ru_number_words_to_int_flexible(words):
    normalized=' '.join(_normalize_num_token(w) for w in re.sub(r'[^а-яё -]',' ',str(words).lower()).split())
    return _ru_number_words_to_int(normalized)

def _ru_year_to_int(words):
    words=re.sub(r'\bгода?\b',' ',str(words).lower())
    # «две тысячи двадцать шестого» / «две тысячи двадцать шестого года».
    m=re.search(r'((?:одна|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+тысяч[аиу]?\s+[а-яё -]+)', words)
    if m:
        part=m.group(1)
        mt=re.search(r'\bтысяч[аиу]?\b',part)
        before=part[:mt.start()].strip()
        after=part[mt.end():].strip()
        a=_ru_number_words_to_int_flexible(before)
        b=_ru_number_words_to_int_flexible(after)
        if a is not None and b is not None:
            return a*1000+b
    m=re.search(r'\b(20\d{2}|19\d{2}|[12]\d{3})\b', words)
    if m: return int(m.group(1))
    return _ru_number_words_to_int_flexible(words)

def _parse_time(text):
    t=str(text).lower()
    m=re.search(r'\b(?:в|к|около)\s*(\d{1,2})(?::|\.)?(\d{2})?\s*(?:час(?:а|ов)?|ч)?\s*(утра|дня|вечера|ночи)?',t)
    if m:
        hour=int(m.group(1)); minute=int(m.group(2) or 0); part=m.group(3) or ''
        if part in ('вечера','ночи') and hour<12: hour+=12
        if part=='дня' and 1<=hour<12: hour+=12
        if 0<=hour<=23 and 0<=minute<=59:return f'{hour:02d}:{minute:02d}'
    m=re.search(r'\b(?:в|к)\s+([а-яё -]+?)\s+час(?:а|ов)?',t)
    if m:
        hour=_ru_number_words_to_int_flexible(m.group(1))
        if hour is not None and 0<=hour<=23:return f'{hour:02d}:00'
    return ''

def _safe_iso(y,m,d):
    try:return date(int(y),int(m),int(d)).isoformat()
    except (TypeError,ValueError):return ''

def _extract_date_candidates(text, reference_date=None):
    text=str(text or '')
    ref=reference_date or date.today()
    out=[]
    def add(ds,start,end,source):
        if not ds:return
        tm=_parse_time(text[max(0,start-25):min(len(text),end+45)])
        value=ds + (f' {tm}' if tm else '')
        if not any(x['date']==ds and abs(x['start']-start)<8 for x in out):out.append({'date':ds,'value':value,'start':start,'end':end,'source':source})
    # 28.10.2026 / 28-10-2026 / 28/10/2026
    for m in re.finditer(r'\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b',text): add(_safe_iso(m.group(3),m.group(2),m.group(1)),m.start(),m.end(),m.group(0))
    # 28 октября 2026 года
    month_alt='|'.join(_MONTHS)
    for m in re.finditer(rf'\b(\d{{1,2}})\s+({month_alt})(?:\s+(\d{{4}}))?(?:\s*г(?:ода|\.)?)?',text.lower()):
        y=int(m.group(3)) if m.group(3) else ref.year
        add(_safe_iso(y,_MONTHS[m.group(2)],m.group(1)),m.start(),m.end(),m.group(0))
    # «пятнадцатого одиннадцатого две тысячи двадцать седьмого года»
    # Частая форма после ASR: день и месяц произнесены числительными без слов «числа/месяца».
    numeric_words = r'([а-яё]+(?:\s+[а-яё]+){0,2})\s+([а-яё]+)(?:\s+([а-яё]+(?:\s+[а-яё]+){0,5}))?\s+года?'
    for m in re.finditer(r'\b'+numeric_words, text.lower()):
        day=_ru_number_words_to_int_flexible(m.group(1)); month=_ru_number_words_to_int_flexible(m.group(2)); year=_ru_year_to_int(m.group(3) or '')
        if day and 1 <= day <= 31 and month and 1 <= month <= 12 and year and 1900 <= year <= 2200:
            add(_safe_iso(year,month,day),m.start(),m.end(),m.group(0))

    # «двадцать восьмого числа десятого месяца две тысячи двадцать шестого года»
    pat=r'\b([а-яё]+(?:\s+[а-яё]+){0,1})\s+числа\s+([а-яё]+)\s+месяца\s+([а-яё]+(?:\s+[а-яё]+){0,4})(?:\s+года)?'
    for m in re.finditer(pat,text.lower()):
        day=_ru_number_words_to_int_flexible(m.group(1)); month=_ru_number_words_to_int_flexible(m.group(2)); year=_ru_year_to_int(m.group(3))
        if day and month and year:
            add(_safe_iso(year,month,day),m.start(),m.end(),m.group(0))
    # «двадцать восьмого октября две тысячи двадцать шестого года»
    for m in re.finditer(rf'\b([а-яё]+(?:\s+[а-яё]+){{0,2}})\s+({month_alt})(?:\s+([а-яё]+(?:\s+[а-яё]+){{0,5}}))?(?:\s+года)?',text.lower()):
        day=_ru_number_words_to_int_flexible(m.group(1)); month=_MONTHS[m.group(2)]; year=_ru_year_to_int(m.group(3) or '')
        if day and year:add(_safe_iso(year,month,day),m.start(),m.end(),m.group(0))
    # Relative dates.
    for m in re.finditer(r'\b(сегодня|завтра|послезавтра)\b',text.lower()):
        delta={'сегодня':0,'завтра':1,'послезавтра':2}[m.group(1)]
        add((ref+timedelta(days=delta)).isoformat(),m.start(),m.end(),m.group(1))
    for m in re.finditer(r'\b(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье|воскресенья)\b',text.lower()):
        wd=_WEEKDAYS[m.group(1)]; delta=(wd-ref.weekday())%7
        if delta==0: delta=7
        add((ref+timedelta(days=delta)).isoformat(),m.start(),m.end(),m.group(1))
    return sorted(out,key=lambda x:x['start'])

def _normalize_deadline(value, reference_date=None):
    if not value:return ''
    candidates=_extract_date_candidates(str(value),reference_date)
    if candidates:return candidates[0]['value']
    return str(value).strip()

def _enrich_deadlines(data, transcript):
    """Заполняет deadline из фактических дат разговора, если Qwen оставил его null."""
    ref=date.today()
    candidates=_extract_date_candidates(transcript,ref)
    def evidence_pos(item):
        ev=str(item.get('evidence') or '')
        if ev:
            p=transcript.lower().find(ev.lower())
            if p>=0:return p
        txt=str(item.get('task') or item.get('text') or '')
        p=transcript.lower().find(txt.lower()) if txt else -1
        return p
    def best_for(item, local_candidates=None):
        # Дата из фактической цитаты разговора имеет приоритет над тем,
        # что придумал/неверно распознал LLM. Это критично для форм
        # «восемнадцатого одиннадцатого две тысячи двадцать седьмого года».
        ev=str(item.get('evidence') or '')
        txt=str(item.get('task') or item.get('text') or '')
        local=_extract_date_candidates(ev,ref)
        if local:
            return local[0]['value']
        local=_extract_date_candidates(ev+' '+txt,ref)
        if local:
            return local[0]['value']
        # Сначала доверяем датам, которые реально есть в транскрипте.
        # Qwen иногда ошибочно нормализует «до пятницы» в произвольную дату.
        pool=local_candidates or candidates
        pos=evidence_pos(item)
        if pos>=0 and pool:
            return min(pool,key=lambda x:abs(x['start']-pos))['value']
        if len(pool)==1:return pool[0]['value']
        # Только если в исходной речи нет подходящей даты, используем
        # уже нормализованный deadline от LLM.
        current=_normalize_deadline(item.get('deadline'),ref)
        if current:return current
        return ''
    for key in ('tasks','agreements'):
        for item in data.get(key) or []:
            if isinstance(item,dict):
                dl=best_for(item)
                if dl:item['deadline']=dl
    for c in data.get('contracts') or []:
        if not isinstance(c,dict):continue
        local_text=' '.join(str(x.get('evidence') or x.get('text') or x.get('task') or '') for x in (c.get('agreements') or [])+(c.get('tasks') or []))
        local_candidates=_extract_date_candidates(local_text,ref) or candidates
        for item in c.get('tasks') or []:
            if isinstance(item,dict):
                dl=best_for(item,local_candidates)
                if dl:item['deadline']=dl
        for idx,item in enumerate(c.get('agreements') or []):
            if isinstance(item,dict):
                dl=best_for(item,local_candidates)
                if not dl and idx < len(c.get('tasks') or []):
                    task=c.get('tasks')[idx]
                    if isinstance(task,dict):dl=task.get('deadline') or ''
                if dl:item['deadline']=dl
    return data

def _apply_contract_ids(data, transcript):
    def item_id(item):
        if not isinstance(item,dict): return ''
        return _contract_id_from_text(item.get('evidence','')) or _contract_id_from_text(item.get('text',''))
    for c in data.get('contracts') or []:
        if not isinstance(c,dict): continue
        cid=str(c.get('contract_id') or '').strip()
        if cid and not re.fullmatch(r'\d[\d-]*', cid): cid=''
        if not cid:
            for item in list(c.get('agreements') or [])+list(c.get('tasks') or []):
                cid=item_id(item)
                if cid: break
        if not cid: cid=_contract_id_from_text(c.get('contract_name',''))
        if cid and not re.fullmatch(r'\d[\d-]*', cid): cid=''
        c['contract_id']=cid
        c['contract_name']=f'Договор №{cid}' if cid else 'Договор без номера'
    # Top-level items can also carry the contract number.
    for key in ('agreements','tasks'):
        for item in data.get(key) or []:
            if isinstance(item,dict):
                cid=str(item.get('contract_id') or '').strip()
                if cid and not re.fullmatch(r'\d[\d-]*', cid): cid=''
                if not cid: cid=item_id(item)
                if cid and re.fullmatch(r'\d[\d-]*', cid):
                    item['contract_id']=cid; item['contract_name']=f'Договор №{cid}'
                elif not item.get('contract_id'):
                    item['contract_name']='Договор без номера'
    return data

def _business_retry_prompt(transcript_text: str) -> str:
    return f"""Ты — второй, более внимательный проверяющий деловых договорённостей.

Верни строго JSON того же формата, что требуется основному анализатору.
Главное правило: НОМЕР ДОГОВОРА НЕ ОБЯЗАТЕЛЕН. Если в разговоре есть реальная деловая договорённость, создай её даже при contract_id=\"\" и contract_name=\"Договор без номера\".

Особенно внимательно ищи:
- уже назначенные деловые встречи/мероприятия;
- даты проведения, сроки и время;
- подтверждение готовности: «готов», «я готов», «согласен», «подтверждаю», «договорились»;
- конкретное деловое действие, которое следует из контекста разговора.

Не создавай запись для бытовых разговоров и предложений без принятия.
Evidence обязано быть непрерывной точной цитатой из транскрипта.
Confidence ставь не ниже 0.90 только для действительно подтверждённых случаев.

Пример:
Транскрипт: «обговорить нашу договоренность которая есть которая назначена на одиннадцатое десятое две тысячи двадцать шестого года ... обсудить разработку мобильного приложения для МТС ... вы готовы ... готов»
Результат: одна договорённость без номера договора, с deadline=2026-10-11, связанная с встречей/обсуждением разработки мобильного приложения.

Текущий транскрипт:
{transcript_text}
"""

def extract_protocol(transcript_text: str) -> dict:
    print(f"[LLM] Отправка в {MODEL}...")
    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Текущая дата сервера: {date.today().isoformat()}. Используй её только для расчёта относительных дат вроде «завтра» или «во вторник».\n\nИсходный транскрипт разговора. Анализируй только этот текст:\n\n{transcript_text}"}
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

    # Если основной Qwen слишком строго отфильтровал разговор, но в тексте явно
    # есть деловые сигналы, делаем один повторный анализ с акцентом на то, что
    # номер договора необязателен. Это защищает сценарий «Договор без номера».
    total_found = len(agreements) + len(tasks) + sum(len(c.get("agreements") or []) + len(c.get("tasks") or []) for c in contracts)
    business_markers = re.search(
        r"\b(договорённост|договоренност|встреч|совещан|лекци|разработк|проект|приложен|клиент|заказ|поставк|согласен|готов|подтверждаю|договорились)\w*\b",
        transcript_text.lower(),
    )
    if total_found == 0 and business_markers:
        try:
            retry = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": _business_retry_prompt(transcript_text)}],
                think=False,
                options={"temperature": 0.0, "num_ctx": 8192},
            )
            retry_raw = retry["message"]["content"]
            retry_parsed = json.loads(clean_json(retry_raw))
            retry_agreements = []
            for a in retry_parsed.get("agreements") or []:
                item = _valid_item(a, transcript_text)
                if item: retry_agreements.append(item)
            retry_tasks = []
            for t in retry_parsed.get("tasks") or []:
                item = _valid_task(t, transcript_text)
                if item: retry_tasks.append(item)
            retry_contracts = []
            for c in retry_parsed.get("contracts") or []:
                if not isinstance(c, dict): continue
                ca = []
                for a in c.get("agreements") or []:
                    item = _valid_item(a, transcript_text)
                    if item: ca.append(item)
                ct = []
                for t in c.get("tasks") or []:
                    item = _valid_task(t, transcript_text)
                    if item: ct.append(item)
                if ca or ct:
                    retry_contracts.append({
                        "contract_id": str(c.get("contract_id") or "").strip(),
                        "contract_name": str(c.get("contract_name") or "Договор без номера").strip(),
                        "agreements": ca, "tasks": ct,
                    })
            if retry_agreements or retry_tasks or retry_contracts:
                print("[LLM] Повторный анализ: найдены договорённости без обязательного номера договора")
                agreements, tasks, contracts = retry_agreements, retry_tasks, retry_contracts
        except Exception as e:
            print(f"[LLM] Повторный анализ не выполнен: {e}")

    result = {
        "topic": parsed.get("topic") or None,
        "conversation_type": conversation_type,
        "participants": [p for p in (parsed.get("participants") or []) if isinstance(p, dict)],
        "agreements": agreements,
        "tasks": tasks,
        "contracts": contracts,
        "key_points": parsed.get("key_points") or [],
    }
    result = _apply_contract_ids(result, transcript_text)
    return _enrich_deadlines(result, transcript_text)


def check_ollama_available() -> bool:
    try:
        models = ollama.list()
        names = [m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "") for m in models.get("models", [])]
        return any(MODEL in name for name in names)
    except Exception as e:
        print(f"[LLM] Ollama недоступна: {e}")
        return False
