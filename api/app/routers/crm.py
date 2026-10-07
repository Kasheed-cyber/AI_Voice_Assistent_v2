from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.schemas import CRMTaskRequest, CalendarEventRequest
from app import storage
from pathlib import Path
import json
from datetime import datetime

router = APIRouter(prefix='/integrations', tags=['integrations'])
MOCK_DIR = Path(__file__).resolve().parents[3] / 'data' / 'mock'
MOCK_DIR.mkdir(parents=True, exist_ok=True)
TASKS_FILE = MOCK_DIR / 'crm_tasks.json'
EVENTS_FILE = MOCK_DIR / 'calendar_events.json'

def read(path):
    if not path.exists(): return []
    try: return json.loads(path.read_text(encoding='utf-8'))
    except: return []
def write(path, data): path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

@router.post('/crm/task')
def create_crm_task(request: CRMTaskRequest):
    tasks = read(TASKS_FILE)
    task = {'id': f"task_{int(datetime.now().timestamp()*1000)}", 'call_id': request.call_id, 'agreement_id': request.agreement_id, 'owner': request.owner, 'task': request.task, 'deadline': request.deadline, 'contract_id': request.contract_id, 'contract_name': request.contract_name, 'priority': request.priority, 'created_at': datetime.now().isoformat(), 'status': 'pending'}
    tasks.insert(0, task); write(TASKS_FILE, tasks); storage.audit('crm_task_created', 'crm_task', task['id'], task['task'])
    return {'status': 'created', 'task': task, 'total_tasks': len(tasks)}

@router.get('/crm/tasks')
def list_crm_tasks():
    tasks = read(TASKS_FILE); return {'tasks': tasks, 'total': len(tasks)}

@router.delete('/crm/tasks')
def clear_crm_tasks():
    write(TASKS_FILE, [])
    storage.audit('crm_tasks_cleared', 'crm')
    return {'status': 'cleared', 'total': 0}

@router.patch('/crm/task/{task_id}')
def update_crm_task(task_id: str, payload: dict):
    tasks = read(TASKS_FILE)
    for task in tasks:
        if task.get('id') == task_id:
            task.update({k: v for k, v in payload.items() if k in {'status','deadline','owner','priority','task'}})
            write(TASKS_FILE, tasks); storage.audit('crm_task_updated', 'crm_task', task_id); return {'status': 'updated', 'task': task}
    raise HTTPException(404, 'CRM task not found')

@router.delete('/crm/task/{task_id}')
def delete_crm_task(task_id: str):
    tasks = read(TASKS_FILE); new = [x for x in tasks if x.get('id') != task_id]
    if len(new) == len(tasks): raise HTTPException(404, 'CRM task not found')
    write(TASKS_FILE, new); storage.audit('crm_task_deleted', 'crm_task', task_id); return {'status': 'deleted'}

@router.post('/calendar/event')
def create_calendar_event(request: CalendarEventRequest):
    events = read(EVENTS_FILE)
    event = {'id': f"event_{int(datetime.now().timestamp()*1000)}", 'call_id': request.call_id, 'agreement_id': request.agreement_id, 'title': request.title, 'time': request.time, 'participants': request.participants, 'created_at': datetime.now().isoformat()}
    events.insert(0, event); write(EVENTS_FILE, events)
    event_file = MOCK_DIR / f"event_{event['id']}.ics"
    participants_str = ', '.join(request.participants) if request.participants else '—'
    ics_content = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//AI Voice Assistant//RU\r\nBEGIN:VEVENT\r\n' + f"UID:{event['id']}@ai-assistant.local\r\n" + f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%SZ')}\r\n" + f"SUMMARY:{request.title}\r\nDESCRIPTION:Время: {request.time}\\nУчастники: {participants_str}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    event_file.write_text(ics_content, encoding='utf-8'); storage.audit('calendar_event_created', 'calendar', event['id'], event['title'])
    return {'status': 'created', 'event': event, 'download_url': f"/integrations/calendar/download/{event['id']}"}

@router.get('/calendar/events')
def list_calendar_events():
    events = read(EVENTS_FILE); return {'events': events, 'total': len(events)}

@router.delete('/calendar/events')
def clear_calendar_events():
    write(EVENTS_FILE, [])
    for f in MOCK_DIR.glob('event_event_*.ics'):
        try: f.unlink()
        except OSError: pass
    storage.audit('calendar_events_cleared', 'calendar')
    return {'status': 'cleared', 'total': 0}

@router.delete('/calendar/event/{event_id}')
def delete_calendar_event(event_id: str):
    events = read(EVENTS_FILE); new = [x for x in events if x.get('id') != event_id]
    if len(new) == len(events): raise HTTPException(404, 'Calendar event not found')
    write(EVENTS_FILE, new); return {'status': 'deleted'}

@router.get('/calendar/download/{event_id}')
def download_ics(event_id: str):
    event_file = MOCK_DIR / f'event_{event_id}.ics'
    if not event_file.exists(): raise HTTPException(404, f'Event {event_id} not found')
    return FileResponse(path=event_file, media_type='text/calendar', filename=f'{event_id}.ics')
