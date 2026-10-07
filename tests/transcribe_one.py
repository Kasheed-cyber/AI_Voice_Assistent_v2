r"""
Транскрибация одного WAV-файла через GigaAM v3.
Вывод — JSON в stdout.
"""
import sys
import json
import soundfile as sf
import onnx_asr

# Загружаем модель один раз (при первом вызове)
MODEL = None

def get_model():
    global MODEL
    if MODEL is None:
        print("[GigaAM] Загрузка модели...", file=sys.stderr)
        MODEL = onnx_asr.load_model("gigaam-v3-ctc")
        print("[GigaAM] Модель загружена", file=sys.stderr)
    return MODEL


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "no input file"}))
        sys.exit(1)

    audio_path = sys.argv[1]

    try:
        model = get_model()
        audio, sr = sf.read(audio_path)

        if sr != 16000:
            print(json.dumps({"error": f"expected 16kHz, got {sr}"}))
            sys.exit(1)

        text = model.recognize(audio)

        print(json.dumps({
            "text": text,
            "duration": len(audio) / sr
        }, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()