"""
Pydantic-схемы для API.
Определяют формат запросов и ответов.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class Participant(BaseModel):
    role: str = Field(..., description="Роль: 'Абонент', 'Собеседник', 'Менеджер'")
    name: Optional[str] = Field(None, description="Имя, если известно")


class Task(BaseModel):
    owner: str = Field(..., description="Кто выполняет")
    task: str = Field(..., description="Что сделать")
    deadline: Optional[str] = Field(None, description="Срок (текстом: 'четверг', 'до 15.11')")
    status: str = Field("pending", description="Статус: pending / done / cancelled")
    priority: str = Field("medium", description="Приоритет: low / medium / high")


class ContractProtocol(BaseModel):
    """Группа договорённостей, относящихся к одному договору/сделке."""
    contract_id: Optional[str] = Field(None, description="Номер/идентификатор договора, если назван")
    contract_name: Optional[str] = Field(None, description="Название договора/сделки, если названо")
    agreements: List[str] = Field(default_factory=list)
    # Факты/условия сделки: суммы, количества, цены и другие числовые условия.
    facts: List[str] = Field(default_factory=list)
    tasks: List[dict] = Field(default_factory=list)


class Protocol(BaseModel):
    call_id: str
    topic: Optional[str] = None
    participants: List[dict] = Field(default_factory=list)
    # Обратная совместимость: общий список договорённостей.
    agreements: List[str] = Field(default_factory=list)
    # Структурированные условия, чтобы не терять суммы/количества/цены.
    facts: List[str] = Field(default_factory=list)
    tasks: List[dict] = Field(default_factory=list)
    # Новая структура: один звонок -> несколько договоров/сделок.
    contracts: List[ContractProtocol] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

class TextTranscriptRequest(BaseModel):
    call_id: str
    text: str


class AudioChunkRequest(BaseModel):
    call_id: str
    chunk_index: int
    is_final: bool = False
    audio_base64: Optional[str] = None


class CRMTaskRequest(BaseModel):
    call_id: str
    owner: str
    task: str
    deadline: Optional[str] = None
    priority: str = "medium"
    contract_id: Optional[str] = None
    contract_name: Optional[str] = None


class CalendarEventRequest(BaseModel):
    call_id: str
    title: str
    time: str
    participants: List[str] = []
