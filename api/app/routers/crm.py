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
from datetime import datetime

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


@router.post("/calendar/event")
def create_calendar_event(request: CalendarEventRequest):
    """
    Создать событие в мок-календаре.
    Возвращает .ics-файл для импорта в Google Calendar.
    """
    event_file = MOCK_DIR / f"event_{request.call_id}.ics"

    participants_str = ", ".join(request.participants) if request.participants else "—"
    description = f"Время: {request.time}\\nУчастники: {participants_str}"

    ics_content = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Antifraud Assistant//RU\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{request.call_id}@ai-assistant.local\r\n"
        f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"SUMMARY:{request.title}\r\n"
        f"DESCRIPTION:{description}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    event_file.write_text(ics_content, encoding="utf-8")

    return {
        "status": "created",
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