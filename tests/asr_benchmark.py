"""
ASR Benchmark: Whisper Large-v3 vs Whisper Turbo
Запуск: python tests\asr_benchmark.py
"""
import os
import sys
import time
import json
import pandas as pd
from pathlib import Path
from faster_whisper import WhisperModel

# === ПУТИ ===
# Скрипт лежит в tests/, данные в data/
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
INPUT_DIR = PROJECT_ROOT / "data" / "input"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# === МОДЕЛИ ДЛЯ ТЕСТА ===
MODELS = {
    "whisper_large_v3": {
        "size": "large-v3",
        "device": "cuda",
        "compute_type": "int8_float16"
    },
    "whisper_turbo": {
        "size": "turbo",
        "device": "cuda",
        "compute_type": "int8_float16"
    }
}

# === ФУНКЦИЯ ТРАНСКРИБАЦИИ ===
def transcribe(model, audio_path):
    start = time.time()
    segments, info = model.transcribe(
        str(audio_path),
        language="ru",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )
    # segments — генератор, нужен list() чтобы материализовать
    seg_list = [
        {
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip()
        }
        for seg in segments
    ]
    elapsed = time.time() - start
    return seg_list, elapsed, info.duration


def main():
    # Найти все WAV
    audio_files = sorted(INPUT_DIR.glob("*.wav"))
    if not audio_files:
        print(f"❌ Нет WAV-файлов в {INPUT_DIR}")
        sys.exit(1)

    print(f"Найдено файлов: {len(audio_files)}")
    for f in audio_files:
        print(f"  - {f.name}")
    print()

    all_results = []

    for model_key, config in MODELS.items():
        print(f"\n{'=' * 60}")
        print(f"МОДЕЛЬ: {model_key} ({config['size']})")
        print(f"{'=' * 60}")

        # Загружаем модель один раз
        try:
            model = WhisperModel(
                config["size"],
                device=config["device"],
                compute_type=config["compute_type"]
            )
        except Exception as e:
            print(f"❌ Не удалось загрузить модель: {e}")
            continue

        for audio_file in audio_files:
            print(f"\n  Файл: {audio_file.name}")
            try:
                seg_list, elapsed, duration = transcribe(model, audio_file)

                # Сохраняем транскрипт
                out_file = OUTPUT_DIR / f"{model_key}_{audio_file.stem}.json"
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(
                        {"model": model_key, "file": audio_file.name,
                         "duration": duration, "segments": seg_list},
                        f, ensure_ascii=False, indent=2
                    )

                # Метрики
                rtf = elapsed / duration if duration else 0
                print(f"    Длительность аудио: {duration:.2f} сек")
                print(f"    Время обработки:    {elapsed:.2f} сек")
                print(f"    RTF (real-time factor): {rtf:.3f}")
                print(f"    Сегментов: {len(seg_list)}")
                print(f"    Полный текст: {' '.join(s['text'] for s in seg_list)}")

                all_results.append({
                    "model": model_key,
                    "file": audio_file.name,
                    "duration_sec": round(duration, 2),
                    "time_sec": round(elapsed, 2),
                    "rtf": round(rtf, 3),
                    "segments": len(seg_list)
                })

            except Exception as e:
                print(f"    ❌ Ошибка: {e}")
                all_results.append({
                    "model": model_key,
                    "file": audio_file.name,
                    "error": str(e)
                })

        # Выгружаем модель из памяти
        del model
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass

    # Сохраняем сводную таблицу
    df = pd.DataFrame(all_results)
    csv_path = OUTPUT_DIR / "asr_benchmark.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"РЕЗУЛЬТАТЫ")
    print(f"{'=' * 60}")
    print(df.to_string(index=False))
    print(f"\n✅ Сохранено: {csv_path}")


if __name__ == "__main__":
    main()