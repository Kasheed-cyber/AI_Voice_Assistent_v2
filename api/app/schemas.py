from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class ContractProtocol(BaseModel):
    contract_id: str = ''
    contract_name: str
    agreements: List[Any] = Field(default_factory=list)
    tasks: List[dict] = Field(default_factory=list)

class Protocol(BaseModel):
    call_id: str
    topic: Optional[str] = None
    participants: List[dict] = Field(default_factory=list)
    contracts: List[ContractProtocol] = Field(default_factory=list)
    agreements: List[Any] = Field(default_factory=list)
    tasks: List[dict] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    summary: str = ''
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
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
    contract_id: Optional[str] = None
    contract_name: Optional[str] = None
    priority: str = 'medium'
    agreement_id: Optional[str] = None

class CalendarEventRequest(BaseModel):
    call_id: str
    title: str
    time: str
    participants: List[str] = Field(default_factory=list)
    agreement_id: Optional[str] = None

class Agreement(BaseModel):
    id: str
    contractName: str
    client: str = ''
    text: str
    owner: str
    deadline: str = ''
    priority: str = 'medium'
    status: str = 'pending'
    details: str = ''
    callId: str = ''
    contractId: str = ''
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class CallRecord(BaseModel):
    id: str
    client: str = ''
    startedAt: str = ''
    finishedAt: str = ''
    duration: int = 0
    status: str = 'completed'
    summary: str = ''
    transcription: str = ''
    agreementIds: List[str] = Field(default_factory=list)
    protocol: Optional[Dict[str, Any]] = None

class Settings(BaseModel):
    autoCreateTasks: bool = True
    autoCreateCalendar: bool = True
    askConfirmation: bool = True
    autoPriority: bool = True
    triggerPhrases: List[str] = Field(default_factory=lambda: ['отправлю', 'подготовлю', 'согласуем', 'встречаемся', 'пришлю'])
