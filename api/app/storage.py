"""
Временное in-memory хранилище протоколов.
Для прототипа достаточно. Позже заменим на SQLite/PostgreSQL.
"""
from typing import Dict, List
from datetime import datetime
from app.schemas import Protocol, ContractProtocol

_protocols: Dict[str, Protocol] = {}


def create_protocol(call_id: str) -> Protocol:
    """Создаёт пустой протокол для нового звонка."""
    protocol = Protocol(call_id=call_id)
    _protocols[call_id] = protocol
    return protocol


def get_protocol(call_id: str):
    return _protocols.get(call_id)


def update_protocol(call_id: str, **kwargs):
    """Обновляет поля протокола."""
    protocol = _protocols.get(call_id)
    if not protocol:
        return None
    for key, value in kwargs.items():
        if hasattr(protocol, key):
            setattr(protocol, key, value)
    protocol.updated_at = datetime.now()
    return protocol


def delete_protocol(call_id: str) -> bool:
    if call_id in _protocols:
        del _protocols[call_id]
        return True
    return False


def list_protocols() -> List[Protocol]:
    return list(_protocols.values())

def merge_protocol(call_id: str, extracted: dict):
    """Сливает промежуточный результат LLM, не теряя уже найденные записи."""
    protocol = _protocols.get(call_id) or create_protocol(call_id)
    for field in ("topic", "participants"):
        if extracted.get(field):
            setattr(protocol, field, extracted[field])

    for field in ("agreements", "facts", "key_points"):
        current = list(getattr(protocol, field) or [])
        for value in extracted.get(field) or []:
            if value not in current:
                current.append(value)
        setattr(protocol, field, current)

    current_tasks = list(protocol.tasks or [])
    task_keys = {(t.get("owner"), t.get("task"), t.get("deadline"), t.get("contract_id"))
                 for t in current_tasks}
    for task in extracted.get("tasks") or []:
        key = (task.get("owner"), task.get("task"), task.get("deadline"), task.get("contract_id"))
        if key not in task_keys:
            current_tasks.append(task)
            task_keys.add(key)
    protocol.tasks = current_tasks

    current = {}
    for c in protocol.contracts or []:
        d = c.model_dump() if hasattr(c, "model_dump") else dict(c)
        current[d.get("contract_id") or f"name:{d.get('contract_name')}"] = d

    for incoming in extracted.get("contracts") or []:
        key = incoming.get("contract_id") or f"name:{incoming.get('contract_name')}"
        c = current.setdefault(key, {
            "contract_id": incoming.get("contract_id"),
            "contract_name": incoming.get("contract_name"),
            "agreements": [],
            "facts": [],
            "tasks": [],
        })
        for ag in incoming.get("agreements") or []:
            if ag not in c["agreements"]:
                c["agreements"].append(ag)
        for fact in incoming.get("facts") or []:
            if fact not in c["facts"]:
                c["facts"].append(fact)
        existing = {(t.get("owner"), t.get("task"), t.get("deadline")) for t in c["tasks"]}
        for task in incoming.get("tasks") or []:
            tk = (task.get("owner"), task.get("task"), task.get("deadline"))
            if tk not in existing:
                c["tasks"].append(task)
                existing.add(tk)

    protocol.contracts = [ContractProtocol(**c) for c in current.values()]
    protocol.updated_at = datetime.now()
    return protocol
