r"""
Извлечение договорённостей из транскрипта через локальную LLM (Ollama + Qwen 3.5 4B).
Запуск: python tests\llm_extract.py <transcript.json>
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


def load_transcript(json_path: Path) -> str:
    """Загружает транскрипт Whisper и объединяет сегменты в один текст."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    full_text = " ".join(seg["text"] for seg in data["segments"])
    return full_text


def extract_protocol(transcript_text: str) -> str:
    """Отправляет транскрипт в LLM, получает JSON-протокол."""
    print(f"Отправка в {MODEL}...")

    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Транскрипт разговора:\n\n{transcript_text}"}
        ],
        think=False,  # отключаем режим размышлений у Qwen 3.5
        options={
            "temperature": 0.2,
            "num_ctx": 8192,
        }
    )

    return response["message"]["content"]


def clean_json(raw: str) -> str:
    """Убирает markdown-обёртку ```json ... ``` и лишние пробелы."""
    # Убираем ```json и ```
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def save_protocol(content: str, source_file: str) -> Path:
    """Сохраняет ответ LLM в txt-файл."""
    out_name = Path(source_file).stem + "_protocol.txt"
    out_path = OUTPUT_DIR / out_name

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return out_path


def main():
    if len(sys.argv) < 2:
        print("Использование: python tests\\llm_extract.py <transcript.json>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"❌ Файл не найден: {json_path}")
        sys.exit(1)

    print(f"Загрузка транскрипта: {json_path.name}")
    transcript = load_transcript(json_path)
    print(f"Длина текста: {len(transcript)} символов")
    print(f"Текст: {transcript}\n")

    # Отправка в LLM
    raw_protocol = extract_protocol(transcript)

    # Очистка от markdown
    cleaned = clean_json(raw_protocol)

    # Попытка распарсить JSON
    try:
        parsed = json.loads(cleaned)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError as e:
        print(f"⚠️ LLM вернул невалидный JSON: {e}")
        pretty = cleaned

    # Сохранение
    out_path = save_protocol(pretty, json_path.name)

    print(f"\n{'=' * 60}")
    print("РЕЗУЛЬТАТ:")
    print(f"{'=' * 60}")
    print(pretty)
    print(f"\n✅ Сохранено: {out_path}")


if __name__ == "__main__":
    main()