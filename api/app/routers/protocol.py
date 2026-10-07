"""
Эндпоинты для работы с протоколами разговоров.
"""
from fastapi import APIRouter, HTTPException
from app.schemas import Protocol, TextTranscriptRequest, AudioChunkRequest
from app import storage
from app.llm import extract_protocol

router = APIRouter(prefix="/protocol", tags=["protocol"])


@router.post("/start", response_model=Protocol)
def start_call(call_id: str):
    """Начать новый звонок — создать пустой протокол."""
    existing = storage.get_protocol(call_id)
    if existing:
        return existing
    return storage.create_protocol(call_id)


@router.get("/{call_id}", response_model=Protocol)
def get_call_protocol(call_id: str):
    """Получить текущий протокол."""
    protocol = storage.get_protocol(call_id)
    if not protocol:
        raise HTTPException(404, f"Protocol for {call_id} not found")
    return protocol


@router.post("/text", response_model=Protocol)
def process_text(request: TextTranscriptRequest):
    """
    Обработать готовый текст через LLM (Ollama + Qwen 3 8B).
    Извлекает договорённости, задачи, участников.
    """
    protocol = storage.get_protocol(request.call_id)
    if not protocol:
        protocol = storage.create_protocol(request.call_id)

    # Вызов LLM
    extracted = extract_protocol(request.text)

    # Обновляем протокол
    updated = storage.merge_protocol(request.call_id, extracted)
    return updated or protocol


@router.post("/chunk")
def process_audio_chunk(request: AudioChunkRequest):
    """
    Принять аудио-чанк от WebRTC (для real-time).
    Заглушка: пока ничего не делает.
    """
    # TODO: передать чанк в Whisper, накопить транскрипт
    return {"status": "ok", "call_id": request.call_id, "chunk_index": request.chunk_index}


@router.post("/{call_id}/finalize", response_model=Protocol)
def finalize_call(call_id: str):
    """Финализировать разговор — сохранить итоговый протокол."""
    protocol = storage.get_protocol(call_id)
    if not protocol:
        raise HTTPException(404, f"Protocol for {call_id} not found")
    # TODO: финальный вызов LLM на полном транскрипте
    return protocol


@router.get("/", response_model=list[Protocol])
def list_all_protocols():
    """Список всех протоколов (для отладки)."""
    return storage.list_protocols()