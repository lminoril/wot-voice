#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пакетный прогон WAV-файлов через Vosk (для калибровки фраз).

    python tools/asr_dir.py <папка с wav> [файл-отчёта]
"""
import json
import sys
import wave
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MODEL = APP_DIR / "model" / "vosk-model-small-ru-0.22"


def main() -> int:
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    folder = Path(sys.argv[1])
    report = Path(sys.argv[2]) if len(sys.argv) > 2 else folder / "report.txt"

    model = Model(str(MODEL))
    lines = []
    for wav_path in sorted(folder.glob("*.wav")):
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        rec = KaldiRecognizer(model, rate)
        rec.AcceptWaveform(frames)
        text = json.loads(rec.FinalResult()).get("text", "")
        lines.append(f"{wav_path.stem:<28} -> {text}")

    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"готово: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
