#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Прогон WAV через Vosk в режиме ограниченной грамматики.

    python tools/asr_grammar.py <папка> [отчёт] [--unk]
"""
import json
import sys
import wave
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MODEL = APP_DIR / "model" / "vosk-model-small-ru-0.22"

GRAMMAR = ["юхан", "след парика", "ведь кислый"]


def main() -> int:
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    folder = Path(args[0])
    report = Path(args[1]) if len(args) > 1 else folder / "grammar_report.txt"

    grammar = GRAMMAR + (["[unk]"] if "--unk" in sys.argv else [])
    model = Model(str(MODEL))

    lines = [f"грамматика: {grammar}"]
    for wav_path in sorted(folder.glob("*.wav")):
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        rec = KaldiRecognizer(model, rate, json.dumps(grammar, ensure_ascii=False))
        rec.AcceptWaveform(frames)
        text = json.loads(rec.FinalResult()).get("text", "")
        lines.append(f"{wav_path.stem:<28} -> {text!r}")

    report.write_text("\n".join(lines), encoding="utf-8")
    print("готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
