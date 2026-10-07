r"""
Постоянный сервис транскрибации через GigaAM v3.
Читает JSON-команды из stdin, пишет JSON-ответы в stdout.
Запускается один раз из Node.js.
"""
import sys
import json
import base64
import io
import numpy as np
import soundfile as sf
import onnx_asr
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# === Загрузка модели ОДИН РАЗ ===
print("[Service] Загрузка GigaAM v3 (ONNX)...", file=sys.stderr, flush=True)
MODEL = onnx_asr.load_model("gigaam-v3-ctc")
print("[Service] Модель загружена. Готов к работе.", file=sys.stderr, flush=True)


def transcribe_wav_bytes(wav_bytes: bytes) -> dict:
    """Транскрибирует WAV из байтов через GigaAM."""
    try:
        # Читаем как float32
        audio, sr = sf.read(io.BytesIO(wav_bytes), dtype='float32')

        if sr != 16000:
            return {"error": f"Ожидалось 16 kHz, получено {sr}"}

        # Если стерео — микшируем в моно
        if len(audio.shape) > 1:
            audio = audio.mean(axis=1)

        # Приводим к float32 (на случай, если sf вернул что-то другое)
        audio = np.asarray(audio, dtype=np.float32)

        text = MODEL.recognize(audio)
        return {
            "text": text,
            "duration": len(audio) / sr
        }
    except Exception as e:
        return {"error": str(e)}


def process_command(cmd: dict) -> dict:
    """Обрабатывает одну команду."""
    req_id = cmd.get("id")
    action = cmd.get("action")

    if action == "transcribe":
        wav_b64 = cmd.get("wav_base64", "")
        try:
            wav_bytes = base64.b64decode(wav_b64)
        except Exception as e:
            return {"id": req_id, "error": f"base64 decode: {e}"}
        return {"id": req_id, **transcribe_wav_bytes(wav_bytes)}

    elif action == "ping":
        return {"id": req_id, "pong": True}

    else:
        return {"id": req_id, "error": f"Unknown action: {action}"}


def main():
    """Основной цикл: читаем команды, отвечаем."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            result = process_command(cmd)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"JSON: {e}"}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    main()