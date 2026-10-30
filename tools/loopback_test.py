#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сквозной тест без живого голоса: проигрывает WAV в колонки, одновременно
слушает микрофон и прогоняет услышанное через тот же конвейер, что и приложение.

    python tools/loopback_test.py <файл.wav> [устройство ввода]
"""
import json
import queue
import sys
import time
import wave
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import app  # noqa: E402


def main() -> int:
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    wav_path = Path(sys.argv[1])
    device = int(sys.argv[2]) if len(sys.argv) > 2 else None

    with wave.open(str(wav_path), "rb") as wf:
        out_rate = wf.getframerate()
        audio = wf.readframes(wf.getnframes())

    cfg = app.load_config()
    matcher = app.Matcher(cfg["phrases"], cfg.get("threshold", 88.0), grammar_mode=True)
    model = Model(str(APP_DIR / cfg["model_dir"]))
    rate = 16000
    grammar = json.dumps(matcher.grammar_words() + ["[unk]"], ensure_ascii=False)
    rec = KaldiRecognizer(model, rate, grammar)

    q: "queue.Queue[bytes]" = queue.Queue()
    stream = sd.RawInputStream(
        samplerate=rate, blocksize=4000, device=device,
        dtype="int16", channels=1, callback=lambda i, f, t, s: q.put(bytes(i)),
    )

    finals = []
    with stream:
        time.sleep(0.3)
        import winsound

        winsound.PlaySound(str(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                data = q.get(timeout=0.3)
            except queue.Empty:
                continue
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "")
                if app.normalize(text):
                    finals.append(text)
        tail = json.loads(rec.FinalResult()).get("text", "")
        if app.normalize(tail):
            finals.append(tail)

    print(f"устройство ввода: {device if device is not None else 'по умолчанию'}")
    print(f"услышано: {finals}")
    for text in finals:
        name, score = matcher.find(text)
        if name:
            print(f"СРАБОТАЛО: {name} ({score:.1f}%)")
            return 0
    print("не сработало")
    return 1


if __name__ == "__main__":
    sys.exit(main())
