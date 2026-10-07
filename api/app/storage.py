import json
from pathlib import Path
from typing import Dict
from datetime import datetime
from app.schemas import Protocol, Agreement, CallRecord, Settings

DATA_DIR = Path(__file__).resolve().parents[2] / 'data' / 'app'
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE = DATA_DIR / 'protocols.json'
AGREEMENTS_FILE = DATA_DIR / 'agreements.json'
CALLS_FILE = DATA_DIR / 'calls.json'
SETTINGS_FILE = DATA_DIR / 'settings.json'
AUDIT_FILE = DATA_DIR / 'audit.json'
_protocols: Dict[str, Protocol] = {}

def _read_json(path, default):
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default

def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def _load():
    global _protocols
    raw = _read_json(FILE, {})
    _protocols = {}
    for k, v in raw.items():
        try: _protocols[k] = Protocol(**v)
        except Exception: pass
_load()

def _save_protocols(): _write_json(FILE, {k: v.model_dump(mode='json') for k, v in _protocols.items()})

def create_protocol(call_id):
    p = Protocol(call_id=call_id, started_at=datetime.now())
    _protocols[call_id] = p; _save_protocols(); return p

def get_protocol(call_id): return _protocols.get(call_id)

def update_protocol(call_id, **kwargs):
    p = _protocols.get(call_id)
    if not p: return None
    for k, v in kwargs.items():
        if hasattr(p, k): setattr(p, k, v)
    p.updated_at = datetime.now(); _save_protocols(); return p

def merge_protocol(call_id, incoming):
    p = _protocols.get(call_id) or create_protocol(call_id)
    for k in ('topic','participants','contracts','agreements','tasks','key_points','summary'):
        v = incoming.get(k)
        if v is None: continue
        if isinstance(v, list):
            old = getattr(p, k, []) or []
            merged = old.copy()
            for item in v:
                if item not in merged: merged.append(item)
            setattr(p, k, merged)
        else: setattr(p, k, v)
    p.updated_at = datetime.now(); _save_protocols(); return p

def finalize_protocol(call_id):
    p = _protocols.get(call_id)
    if not p: return None
    p.finished_at = datetime.now(); p.updated_at = datetime.now(); _save_protocols(); return p

def delete_protocol(call_id):
    ok = call_id in _protocols
    if ok: del _protocols[call_id]; _save_protocols()
    return ok

def list_protocols(): return list(_protocols.values())

# Agreements

def list_agreements(): return _read_json(AGREEMENTS_FILE, [])
def save_agreement(item):
    items = list_agreements(); data = item.model_dump(mode='json') if hasattr(item, 'model_dump') else item
    data['updatedAt'] = datetime.now().isoformat()
    if not data.get('createdAt'): data['createdAt'] = data['updatedAt']
    for i, x in enumerate(items):
        if x.get('id') == data.get('id'): items[i] = data; break
    else: items.insert(0, data)
    _write_json(AGREEMENTS_FILE, items); return data

def delete_agreement(item_id):
    items = list_agreements(); new = [x for x in items if x.get('id') != item_id]
    changed = len(new) != len(items); _write_json(AGREEMENTS_FILE, new); return changed

# Calls

def list_calls(): return _read_json(CALLS_FILE, [])
def save_call(call):
    data = call.model_dump(mode='json') if hasattr(call, 'model_dump') else call
    items = list_calls()
    for i, x in enumerate(items):
        if x.get('id') == data.get('id'): items[i] = data; break
    else: items.insert(0, data)
    _write_json(CALLS_FILE, items); return data

def get_call(call_id): return next((x for x in list_calls() if x.get('id') == call_id), None)

def delete_call(call_id):
    items = list_calls(); new = [x for x in items if x.get('id') != call_id]
    _write_json(CALLS_FILE, new); return len(new) != len(items)

# Settings

def get_settings():
    return Settings(**_read_json(SETTINGS_FILE, Settings().model_dump()))
def save_settings(settings):
    data = settings.model_dump(mode='json') if hasattr(settings, 'model_dump') else settings
    _write_json(SETTINGS_FILE, data); return data

# Audit

def audit(action, entity='', entity_id='', details=''):
    items = _read_json(AUDIT_FILE, [])
    items.insert(0, {'timestamp': datetime.now().isoformat(), 'action': action, 'entity': entity, 'entity_id': entity_id, 'details': details})
    _write_json(AUDIT_FILE, items[:500])
    return items[0]

def list_audit(): return _read_json(AUDIT_FILE, [])
