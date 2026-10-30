#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Прогон готового WAV-файла через Vosk — для калибровки фраз.

    python tools/asr_file.py path\\to\\file.wav
"""
import sys
import wave
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MODEL = APP_DIR / "model" / "vosk-model-small-ru-0.22"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: asr_file.py <file.wav>")
        return 1

    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    wav_path = Path(sys.argv[1])
    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    model = Model(str(MODEL))
    rec = KaldiRecognizer(model, rate)
    rec.AcceptWaveform(frames)
    import json

    print(f"{wav_path.name}: {json.loads(rec.FinalResult()).get('text', '')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
