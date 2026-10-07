from fastapi import APIRouter, HTTPException
from app.schemas import Protocol, TextTranscriptRequest, AudioChunkRequest, Agreement, CallRecord, Settings
from app import storage

router = APIRouter(prefix='/protocol', tags=['protocol'])

@router.get('/agreements/all')
def list_agreements(): return {'agreements': storage.list_agreements()}

@router.post('/agreement')
def upsert_agreement(item: Agreement):
    saved = storage.save_agreement(item); storage.audit('agreement_saved', 'agreement', item.id, item.text)
    return {'status': 'saved', 'agreement': saved}

@router.delete('/agreement/{item_id}')
def remove_agreement(item_id: str):
    if not storage.delete_agreement(item_id): raise HTTPException(404, 'Agreement not found')
    storage.audit('agreement_deleted', 'agreement', item_id)
    return {'status': 'deleted', 'id': item_id}

@router.get('/calls/history')
def call_history():
    calls = storage.list_calls(); return {'calls': calls, 'total': len(calls)}

@router.post('/calls')
def save_call(call: CallRecord): return {'status': 'saved', 'call': storage.save_call(call)}

@router.delete('/calls/{call_id}')
def remove_call(call_id: str):
    if not storage.delete_call(call_id): raise HTTPException(404, 'Call not found')
    storage.audit('call_deleted', 'call', call_id); return {'status': 'deleted'}

@router.get('/settings')
def get_settings(): return storage.get_settings().model_dump(mode='json')

@router.put('/settings')
def put_settings(settings: Settings):
    saved = storage.save_settings(settings); storage.audit('settings_updated', 'settings', details='Настройки ИИ изменены'); return saved

@router.get('/audit')
def audit_log(): return {'events': storage.list_audit()}

@router.get('/', response_model=list[Protocol])
def list_all_protocols(): return storage.list_protocols()

@router.post('/start', response_model=Protocol)
def start_call(call_id: str):
    p = storage.get_protocol(call_id) or storage.create_protocol(call_id); storage.audit('call_started', 'call', call_id); return p

@router.post('/structured', response_model=Protocol)
def save_structured(payload: dict):
    call_id = payload.get('call_id')
    if not call_id: raise HTTPException(400, 'call_id is required')
    return storage.merge_protocol(call_id, payload)

@router.post('/text', response_model=Protocol)
def process_text(request: TextTranscriptRequest):
    from app.llm import extract_protocol
    extracted = extract_protocol(request.text); return storage.merge_protocol(request.call_id, extracted)

@router.post('/chunk')
def process_audio_chunk(request: AudioChunkRequest): return {'status':'ok','call_id':request.call_id,'chunk_index':request.chunk_index}

@router.post('/{call_id}/finalize', response_model=Protocol)
def finalize_call(call_id: str):
    p = storage.finalize_protocol(call_id)
    if not p: raise HTTPException(404, f'Protocol for {call_id} not found')
    storage.audit('call_finished', 'call', call_id); return p

@router.get('/{call_id}', response_model=Protocol)
def get_call_protocol(call_id: str):
    p = storage.get_protocol(call_id)
    if not p: raise HTTPException(404, f'Protocol for {call_id} not found')
    return p
