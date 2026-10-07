r"""
Тест GigaAM 0.2.0 на одном аудио.
Запуск: python tests\test_gigaam.py <audio.wav>
"""
import sys
import time
from pathlib import Path
import gigaam


def main():
    if len(sys.argv) < 2:
        print("Использование: python tests\\test_gigaam.py <audio.wav>")
        sys.exit(1)

    audio_path = str(Path(sys.argv[1]).resolve())
    if not Path(audio_path).exists():
        print(f"❌ Файл не найден: {audio_path}")
        sys.exit(1)

    print("Загрузка модели GigaAM (CTC)...")
    model = gigaam.load_model("ctc")

    print(f"Транскрибация: {audio_path}")
    start = time.time()
    text = model.transcribe(audio_path)
    elapsed = time.time() - start

    # Длительность аудио (приблизительно)
    import wave
    with wave.open(audio_path, "rb") as w:
        duration = w.getnframes() / w.getframerate()

    rtf = elapsed / duration if duration > 0 else 0

    print(f"\n{'=' * 60}")
    print("ТРАНСКРИПТ:")
    print(f"{'=' * 60}")
    print(text)
    print(f"\nДлительность аудио: {duration:.2f} сек")
    print(f"Время обработки: {elapsed:.2f} сек")
    print(f"RTF: {rtf:.3f}")


if __name__ == "__main__":
    main()