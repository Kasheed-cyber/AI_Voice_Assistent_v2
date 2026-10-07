"""
Эндпоинты для интеграции с CRM и календарём.
Пока это мок-реализация. Позже заменим на Bitrix24/amoCRM/Google Calendar.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.schemas import CRMTaskRequest, CalendarEventRequest
from app import storage
from pathlib import Path
import json
from datetime import datetime, timedelta
import re

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Папка для сохранения мок-данных
MOCK_DIR = Path(__file__).parent.parent.parent.parent / "data" / "mock"
MOCK_DIR.mkdir(parents=True, exist_ok=True)


def _task_tokens(text: str):
    stop = {
        "пожалуйста", "сегодня", "завтра", "утром", "вечером", "конца", "дня",
        "до", "к", "мне", "тебе", "вам", "нам", "тогда", "потом", "уже", "все"
    }
    return {
        w for w in str(text or "").lower().replace("ё", "е").split()
        if w and w.strip(".,!?;:()[]{}«»—-\"'") not in stop
    }


def _same_task(existing: dict, request: CRMTaskRequest) -> bool:
    if existing.get("call_id") != request.call_id:
        return False
    existing_contract = existing.get("contract_id") or existing.get("contract_name")
    request_contract = request.contract_id or request.contract_name
    if existing_contract and request_contract and existing_contract != request_contract:
        return False
    a = _task_tokens(existing.get("task"))
    b = _task_tokens(request.task)
    if not a or not b:
        return False
    common = len(a & b)
    return common / min(len(a), len(b)) >= 0.75 or common / len(a | b) >= 0.55


@router.post("/crm/task")
def create_crm_task(request: CRMTaskRequest):
    """
    Создать задачу в мок-CRM.
    Сохраняет в data/mock/crm_tasks.json
    """
    tasks_file = MOCK_DIR / "crm_tasks.json"
    tasks = []
    if tasks_file.exists():
        try:
            tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tasks = []

    # Защита второго уровня: даже после перезапуска Node одинаковая задача
    # из промежуточного и финального протокола не создаётся повторно.
    for existing in tasks:
        if _same_task(existing, request):
            return {"status": "already_exists", "task": existing, "total_tasks": len(tasks)}

    new_task = {
        "call_id": request.call_id,
        "owner": request.owner,
        "task": request.task,
        "deadline": request.deadline,
        "priority": request.priority,
        "contract_id": request.contract_id,
        "contract_name": request.contract_name,
        "created_at": datetime.now().isoformat(),
        "status": "pending"
    }
    tasks.append(new_task)
    tasks_file.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return {"status": "created", "task": new_task, "total_tasks": len(tasks)}


@router.get("/crm/tasks")
def list_crm_tasks():
    """Список всех задач в мок-CRM."""
    tasks_file = MOCK_DIR / "crm_tasks.json"
    if not tasks_file.exists():
        return {"tasks": [], "total": 0}
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    return {"tasks": tasks, "total": len(tasks)}


RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RU_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
}

def _parse_event_datetime(value: str):
    text = str(value or "").lower().strip()
    now = datetime.now()
    dt = None
    if "послезавтра" in text:
        dt = now + timedelta(days=2)
    elif "завтра" in text:
        dt = now + timedelta(days=1)
    elif "сегодня" in text:
        dt = now

    if dt is None:
        m = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", text)
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else now.year
            if year < 100: year += 2000
            try: dt = datetime(year, month, day)
            except ValueError: dt = None

    if dt is None:
        m = re.search(r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b", text)
        if m:
            try:
                dt = datetime(now.year, RU_MONTHS[m.group(2)], int(m.group(1)))
                if dt.date() < now.date(): dt = dt.replace(year=now.year + 1)
            except ValueError: dt = None

    if dt is None:
        for name, weekday in RU_WEEKDAYS.items():
            if name in text:
                days = (weekday - now.weekday()) % 7 or 7
                dt = now + timedelta(days=days)
                break

    if dt is None:
        return None

    # Поддерживаем естественные русские формулировки времени:
    # «до двух», «до двух дня», «к двум», «в два дня».
    tm = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if tm:
        hour, minute = int(tm.group(1)), int(tm.group(2))
    else:
        hour, minute = None, 0
        hour_words = {
            "один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3,
            "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6,
            "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8,
            "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
            "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
        }
        for word, value in hour_words.items():
            if re.search(rf"\b(?:до|к|в)\s+{word}\b", text):
                hour = value
                break
        if hour is None:
            tm2 = re.search(r"\b(?:до|к|в)\s+(\d{1,2})\s*(?:час(?:а|ов)?|ч)?\b", text)
            if tm2:
                hour = int(tm2.group(1))
        if hour is not None and re.search(r"(?:дня|день)", text) and 1 <= hour <= 7:
            hour += 12
    if hour is not None:
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.replace(hour=9, minute=0, second=0, microsecond=0)

def _ics_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")

@router.post("/calendar/event")
def create_calendar_event(request: CalendarEventRequest):
    """Создать событие/срок в формате .ics для импорта в календарь."""
    event_file = MOCK_DIR / f"event_{request.call_id}.ics"
    dt = _parse_event_datetime(request.time)
    participants_str = ", ".join(request.participants) if request.participants else "—"

    if dt:
        dt_start = dt.strftime("%Y%m%dT%H%M%S")
        dt_end = (dt + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%S")
        date_fields = f"DTSTART:{dt_start}\r\nDTEND:{dt_end}\r\n"
        time_note = dt.isoformat(timespec="minutes")
    else:
        date_fields = ""
        time_note = request.time

    description = f"Срок/время: {time_note}\nУчастники: {participants_str}"
    uid = f"{request.call_id}-{abs(hash(request.title + request.time))}@ai-assistant.local"
    ics_content = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "PRODID:-//AI Call Assistant//RU\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"SUMMARY:{_ics_escape(request.title)}\r\n"
        f"DESCRIPTION:{_ics_escape(description)}\r\n"
        f"{date_fields}END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    event_file.write_text(ics_content, encoding="utf-8")
    return {
        "status": "created",
        "parsed_datetime": dt.isoformat() if dt else None,
        "ics_file": str(event_file),
        "download_url": f"/integrations/calendar/download/{request.call_id}"
    }

@router.get("/calendar/download/{call_id}")
def download_ics(call_id: str):
    """Скачать .ics-файл события."""
    event_file = MOCK_DIR / f"event_{call_id}.ics"
    if not event_file.exists():
        raise HTTPException(404, f"Event for {call_id} not found")
    return FileResponse(
        path=event_file,
        media_type="text/calendar",
        filename=f"event_{call_id}.ics"
    )