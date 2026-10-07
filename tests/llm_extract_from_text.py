r"""
Извлечение договорённостей из ГОТОВОГО текста (без Whisper).
Запуск: python tests\llm_extract_from_text.py <text.txt>
"""
import sys
import json
import re
from pathlib import Path
import ollama

# === НАСТРОЙКИ ===
MODEL = "qwen3:8b"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# === ПРОМПТ ===
SYSTEM_PROMPT = """Ты — ассистент, который протоколирует деловые телефонные разговоры.
Твоя задача — извлечь из транскрипта:
1. Участников (кто говорил, если можно определить)
2. Договорённости (что решили)
3. Задачи (кто, что, к какому сроку)
4. Ключевые темы разговора

Отвечай СТРОГО в формате JSON со следующими полями:
{
  "topic": "краткая тема разговора",
  "participants": ["роль1", "роль2"],
  "agreements": ["договорённость 1", "договорённость 2"],
  "tasks": [
    {"owner": "кто", "task": "что сделать", "deadline": "когда"}
  ],
  "key_points": ["ключевая мысль 1", "ключевая мысль 2"]
}

Если поле не применимо — оставь пустой список или строку. Не выдумывай факты, которых нет в транскрипте.
Отвечай ТОЛЬКО валидным JSON, без пояснений и markdown-разметки."""


def load_text(txt_path: Path) -> str:
    """Загружает текст из txt-файла."""
    with open(txt_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def extract_protocol(transcript_text: str) -> str:
    """Отправляет текст в LLM, получает JSON-протокол."""
    print(f"Отправка в {MODEL}...")

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

    return response["message"]["content"]


def clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def main():
    if len(sys.argv) < 2:
        print("Использование: python tests\\llm_extract_from_text.py <text.txt>")
        sys.exit(1)

    txt_path = Path(sys.argv[1])
    if not txt_path.exists():
        print(f"❌ Файл не найден: {txt_path}")
        sys.exit(1)

    print(f"Загрузка текста: {txt_path.name}")
    transcript = load_text(txt_path)
    print(f"Длина текста: {len(transcript)} символов")
    print(f"Текст:\n{transcript}\n")

    # Отправка в LLM
    raw_protocol = extract_protocol(transcript)

    # Очистка и валидация
    cleaned = clean_json(raw_protocol)
    try:
        parsed = json.loads(cleaned)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError as e:
        print(f"⚠️ LLM вернул невалидный JSON: {e}")
        pretty = cleaned

    # Сохранение
    out_path = OUTPUT_DIR / (txt_path.stem + "_protocol.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty)

    print(f"\n{'=' * 60}")
    print("РЕЗУЛЬТАТ:")
    print(f"{'=' * 60}")
    print(pretty)
    print(f"\n✅ Сохранено: {out_path}")


if __name__ == "__main__":
    main()