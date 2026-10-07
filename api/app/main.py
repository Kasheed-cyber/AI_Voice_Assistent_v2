"""
FastAPI-приложение для прототипа ИИ-агента фиксации договорённостей.
Запуск: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import protocol, crm

app = FastAPI(
    title="AI Call Assistant API",
    description="ИИ-агент для фиксации договорённостей в телефонных разговорах",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(protocol.router)
app.include_router(crm.router)


@app.get("/")
def root():
    return {
        "service": "AI Call Assistant",
        "version": "0.1.0",
        "docs": "/docs",
        "endpoints": {
            "protocol": "/protocol/*",
            "integrations": "/integrations/*",
        }
    }


@app.get("/health")
def health():
    return {"status": "ok"}