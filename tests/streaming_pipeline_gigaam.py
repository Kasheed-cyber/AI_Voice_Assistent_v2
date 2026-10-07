r"""
Real-time pipeline с GigaAM (ASR на CPU) + Qwen 3 8B (LLM на GPU).
Запуск: python tests\streaming_pipeline_gigaam.py <audio.wav>
"""
import sys
import json
import time
import wave
import re
from pathlib import Path
import numpy as np
import gigaam
import ollama

# === НАСТРОЙКИ ===
CHUNK_DURATION = 5.0
SAMPLE_RATE = 16000
LLM_MODEL = "qwen3:8b"
PROTOCOL_INTERVAL = 15.0

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = """Ты — ассистент, который протоколирует деловые телефонные разговоры.
Извлеки из транскрипта:
1. Участников (используй "Абонент" и "Собеседник", если имена не названы)
2. Договорённости (что решили)
3. Задачи (кто, что, к какому сроку)
4. Ключевые темы

Формат ответа — строго JSON:
{
  "topic": "тема",
  "participants": ["роль1", "роль2"],
  "agreements": ["договорённость 1"],
  "tasks": [{"owner": "кто", "task": "что", "deadline": "когда"}],
  "key_points": ["мысль 1"]
}

Если поле не применимо — оставь пустым. Не выдумывай факты.
Отвечай ТОЛЬКО валидным JSON."""


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def split_into_chunks(audio: np.ndarray, chunk_sec: float) -> list:
    chunk_size = int(chunk_sec * SAMPLE_RATE)
    return [audio[i:i + chunk_size] for i in range(0, len(audio), chunk_size)
            if len(audio[i:i + chunk_size]) >= SAMPLE_RATE]


def save_chunk_to_temp(chunk: np.ndarray, path: Path):
    """GigaAM требует путь к файлу, поэтому сохраняем чанк во временный WAV."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((chunk * 32768).astype(np.int16).tobytes())


def transcribe_chunk(model, chunk: np.ndarray, temp_path: Path) -> str:
    """Транскрибирует чанк через GigaAM. Обрабатывает оба типа результата."""
    save_chunk_to_temp(chunk, temp_path)
    try:
        result = model.transcribe(str(temp_path))

        # GigaAM 0.2.0 может вернуть строку ИЛИ объект TranscriptionResult
        if isinstance(result, str):
            return result.strip()

        # Если это объект — ищем атрибут с текстом
        if hasattr(result, "text"):
            return str(result.text).strip()
        elif hasattr(result, "transcription"):
            return str(result.transcription).strip()
        elif hasattr(result, "transcript"):
            return str(result.transcript).strip()
        else:
            # Последний шанс — превращаем в строку
            return str(result).strip()
    except Exception as e:
        print(f"  [ошибка ASR: {e}]")
        return ""


def extract_protocol(text: str) -> str:
    try:
        response = ollama.chat(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Транскрипт:\n\n{text}"}
            ],
            think=False,
            keep_alive="0",
            options={"temperature": 0.2, "num_ctx": 8192}
        )
        return response["message"]["content"]
    except Exception as e:
        return f'{{"error": "{e}"}}'


def clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def main():
    if len(sys.argv) < 2:
        print("Использование: python tests\\streaming_pipeline_gigaam.py <audio.wav>")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    if not audio_path.exists():
        print(f"❌ Файл не найден: {audio_path}")
        sys.exit(1)

    print(f"Загрузка аудио: {audio_path.name}")
    audio = load_wav(audio_path)
    duration = len(audio) / SAMPLE_RATE
    print(f"Длительность: {duration:.2f} сек")

    chunks = split_into_chunks(audio, CHUNK_DURATION)
    print(f"Чанков: {len(chunks)} (по {CHUNK_DURATION} сек)\n")

    print("Загрузка GigaAM (CTC)...")
    asr = gigaam.load_model("ctc")

    temp_wav = OUTPUT_DIR / "_temp_chunk.wav"

    full_transcript = []
    last_protocol_time = 0.0
    start_time = time.time()

    for i, chunk in enumerate(chunks, 1):
        chunk_start = time.time()
        text = transcribe_chunk(asr, chunk, temp_wav)
        elapsed = time.time() - chunk_start

        if text:
            full_transcript.append(text)

        print(f"[{i}/{len(chunks)}] ({elapsed:.2f} сек) {text}")

        total_audio_processed = i * CHUNK_DURATION
        if total_audio_processed - last_protocol_time >= PROTOCOL_INTERVAL:
            last_protocol_time = total_audio_processed
            accumulated = " ".join(full_transcript)
            print(f"\n{'─' * 60}")
            print(f"📄 ПРОМЕЖУТОЧНЫЙ ПРОТОКОЛ (обработано {total_audio_processed:.0f} сек)")
            print(f"{'─' * 60}")
            print(clean_json(extract_protocol(accumulated)))
            print()

    total_time = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ ОБРАБОТКА ЗАВЕРШЕНА за {total_time:.2f} сек")
    print(f"RTF: {total_time / duration:.3f}")
    print(f"{'=' * 60}")

    final_text = " ".join(full_transcript)
    final_protocol = clean_json(extract_protocol(final_text))

    try:
        parsed = json.loads(final_protocol)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        pretty = final_protocol

    out_path = OUTPUT_DIR / f"{audio_path.stem}_gigaam_protocol.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty)

    print(f"\nФИНАЛЬНЫЙ ПРОТОКОЛ:\n{pretty}")
    print(f"\n✅ Сохранено: {out_path}")

    # Удаляем временный файл
    if temp_wav.exists():
        temp_wav.unlink()


if __name__ == "__main__":
    main()