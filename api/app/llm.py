"""
Работа с локальной LLM (Ollama + Qwen 3 8B).
Извлекает договорённости с группировкой по нескольким договорам/сделкам.
"""
import json
import re
import ollama

MODEL = "qwen3:8b"

SYSTEM_PROMPT = """Ты — ассистент, который протоколирует деловые телефонные разговоры.
Извлеки ВСЕ договорённости, задачи и связанные с ними договоры/сделки.

КРИТИЧЕСКИ ВАЖНО:
- В одном разговоре может обсуждаться несколько договоров или сделок.
- НЕ смешивай их: создай отдельный объект contracts для каждого договора/сделки.
- Если номер договора назван, сохрани его в contract_id.
- Если номер не назван, но предмет сделки различается, используй contract_name.
- Каждую договорённость и задачу помести в тот договор/сделку, к которому она относится.
- Если связь с договором определить невозможно, используй contract_id=null.
- Не выдумывай номера, имена, даты и условия.
- В agreements фиксируй именно согласованные условия, а не превращай каждое условие в задачу.
- Если стороны согласовали цену, сумму, количество, способ оплаты или финансовый срок, обязательно добавь это в facts.
- В tasks помещай КАЖДОЕ конкретное обязательство, которое кто-то должен выполнить, включая поставку/доставку товара, отправку счёта, оплату, отправку документов, встречу и т.п.
- Если в разговоре сказано «доставку планируем на пятницу», «оплачу завтра утром», «счёт скину сегодня», это обязательно должно попасть в tasks с owner и deadline.
- Если срок относится к поставке, создай задачу с формулировкой вроде «Доставить 50 кг зерна», а не оставляй это только в facts/agreements.
- Не создавай задачу «уточнить цену», если цена уже согласована в разговоре.
- Относительные сроки («сегодня», «завтра утром», «до конца дня», «в пятницу к 14:00», «пятница до двух дня») сохраняй как deadline. Не ставь null, если срок явно есть в транскрипте.

Формат — СТРОГО JSON:
{
  "topic": "общая тема",
  "participants": [{"role": "клиент/менеджер", "name": "имя или null"}],
  "contracts": [
    {
      "contract_id": "№15 или null",
      "contract_name": "название или null",
      "agreements": ["договорённость"],
      "facts": ["важное условие: сумма/цена/количество/срок поставки и т.п."],
      "tasks": [{"owner": "кто", "task": "что", "deadline": "когда или null", "status": "pending"}]
    }
  ],
  "agreements": ["все договорённости для обратной совместимости"],
  "facts": ["все важные числовые и финансовые условия"],
  "tasks": [{"owner": "кто", "task": "что", "deadline": "когда или null",
             "status": "pending", "contract_id": "№15 или null"}],
  "key_points": ["ключевая мысль"]
}
Если договоров несколько — contracts должен содержать несколько объектов.
Отвечай ТОЛЬКО валидным JSON, без markdown."""


def clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def _task(t):
    if isinstance(t, dict):
        return {
            "owner": t.get("owner", "не указано"),
            "task": t.get("task", ""),
            "deadline": t.get("deadline"),
            "status": t.get("status", "pending"),
            "priority": t.get("priority", "medium"),
            "contract_id": t.get("contract_id"),
            "contract_name": t.get("contract_name"),
        }
    return {"owner": "не указано", "task": str(t), "deadline": None,
            "status": "pending", "contract_id": None, "contract_name": None}


def extract_protocol(transcript_text: str) -> dict:
    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Транскрипт разговора:\n\n{transcript_text}"}
        ],
        think=False,
        options={"temperature": 0.2, "num_ctx": 8192},
    )
    raw = response["message"]["content"]
    try:
        parsed = json.loads(clean_json(raw))
    except json.JSONDecodeError:
        parsed = {}

    contracts = []
    for c in parsed.get("contracts") or []:
        if isinstance(c, dict):
            contracts.append({
                "contract_id": c.get("contract_id"),
                "contract_name": c.get("contract_name"),
                "agreements": [str(x) for x in (c.get("agreements") or [])],
                "facts": [str(x) for x in (c.get("facts") or [])],
                "tasks": [_task(t) for t in (c.get("tasks") or [])],
            })

    return {
        "topic": parsed.get("topic") or None,
        "participants": [
            {"role": p.get("role", "не указано"), "name": p.get("name")}
            if isinstance(p, dict) else {"role": "не указано", "name": p}
            for p in (parsed.get("participants") or [])
        ],
        "agreements": [str(x) for x in (parsed.get("agreements") or [])],
        "facts": [str(x) for x in (parsed.get("facts") or [])],
        "tasks": [_task(t) for t in (parsed.get("tasks") or [])],
        "contracts": contracts,
        "key_points": [str(x) for x in (parsed.get("key_points") or [])],
    }


def check_ollama_available() -> bool:
    try:
        models = ollama.list()
        names = [m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "")
                 for m in models.get("models", [])]
        return any(MODEL in name for name in names)
    except Exception as e:
        print(f"[LLM] Ollama недоступна: {e}")
        return False
