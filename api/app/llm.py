"""
Модуль для работы с локальной LLM (Ollama + Qwen 3 8B).
Извлекает договорённости из транскрипта разговора.
"""
import json
import re
import ollama

MODEL = "qwen3:8b"

SYSTEM_PROMPT = """Ты — ассистент, который протоколирует деловые телефонные разговоры.
Твоя задача — извлечь из транскрипта ВСЕ договорённости и задачи.

Особое внимание:
- Даты, сроки, дедлайны (числа и слова: "15 ноября", "четверг", "до среды")
- Условия оплаты (предоплата, отсрочка, суммы)
- Гарантии и сервисные условия
- Встречи, презентации, звонки (с указанием времени)
- Любые обязательства сторон ("я отправлю", "вы подготовьте")

Формат ответа — СТРОГО JSON:
{
  "topic": "краткая тема разговора",
  "participants": [{"role": "роль (клиент/поставщик/менеджер)", "name": "имя"}],
  "contracts": [{"contract_id":"15","contract_name":"Договор №15","agreements":["что решили"],"tasks":[{"owner":"кто","task":"что сделать","deadline":"когда","priority":"medium","status":"pending"}]}],
  "agreements": ["договорённость 1", "договорённость 2"],
  "tasks": [
    {"owner": "кто", "task": "что сделать", "deadline": "когда", "status": "pending"}
  ],
  "key_points": ["ключевая мысль 1", "ключевая мысль 2"]
}

В agreements указывай ВСЕ достигнутые договорённости — обычно их 4–8 в деловом разговоре.
Не выдумывай факты, которых нет в транскрипте. Отвечай ТОЛЬКО валидным JSON, без markdown-разметки."""

def clean_json(raw: str) -> str:
    """Убирает markdown-обёртку ```json ... ``` и лишние пробелы."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def extract_protocol(transcript_text: str) -> dict:
    """
    Отправляет текст в LLM, получает структурированный протокол.
    Возвращает dict с полями: topic, participants, agreements, tasks, key_points.
    """
    print(f"[LLM] Отправка в {MODEL}...")

    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Транскрипт разговора:\n\n{transcript_text}"}
        ],
        think=False,
        options={
            "temperature": 0.2,
            "num_ctx": 8192,
        }
    )

    raw = response["message"]["content"]
    cleaned = clean_json(raw)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[LLM] Ошибка парсинга JSON: {e}")
        print(f"[LLM] Сырой ответ: {raw[:500]}")
        parsed = {}

    # Нормализация: LLM может вернуть None вместо списка, или строки вместо dict
    result = {
        "topic": parsed.get("topic") or None,
        "participants": [],
        "agreements": parsed.get("agreements") or [],
        "tasks": [],
        "contracts": [],
        "key_points": parsed.get("key_points") or [],
    }

    # Нормализация participants
    for p in parsed.get("participants") or []:
        if isinstance(p, dict):
            result["participants"].append({
                "role": p.get("role", "не указано"),
                "name": p.get("name"),
            })
        elif isinstance(p, str):
            result["participants"].append({"role": "не указано", "name": p})

    # Нормализация отдельных договоров
    for c in parsed.get("contracts") or []:
        if isinstance(c, dict):
            result["contracts"].append({"contract_id": str(c.get("contract_id") or len(result["contracts"])+1), "contract_name": c.get("contract_name") or "Договор без номера", "agreements": c.get("agreements") or [], "tasks": c.get("tasks") or []})

    # Нормализация tasks
    for t in parsed.get("tasks") or []:
        if isinstance(t, dict):
            result["tasks"].append({
                "owner": t.get("owner", "не указано"),
                "task": t.get("task", ""),
                "deadline": t.get("deadline"),
                "status": t.get("status", "pending"),
            })
        elif isinstance(t, str):
            result["tasks"].append({
                "owner": "не указано",
                "task": t,
                "deadline": None,
                "status": "pending",
            })

    return result


def check_ollama_available() -> bool:
    """Проверяет, доступна ли Ollama и модель."""
    try:
        models = ollama.list()
        names = [m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "")
                 for m in models.get("models", [])]
        return any(MODEL in name for name in names)
    except Exception as e:
        print(f"[LLM] Ollama недоступна: {e}")
        return False