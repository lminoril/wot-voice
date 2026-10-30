#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Самопроверка: прогоняет WAV-файлы через тот же конвейер, что и микрофон.

    python tools/selftest.py <папка с wav> [<папка 2> ...]

Ожидаемая фраза определяется по имени файла:
    01..07, p1  -> Йухан        08..10, p2 -> Слет Парика
    11..13, p3  -> Витя-кислый  остальное  -> ничего не должно срабатывать
"""
import json
import sys
import wave
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import app  # noqa: E402


def expected_for(stem: str) -> str | None:
    num = stem.split("_")[0]
    if num in {"p1"} or num.isdigit() and int(num) <= 7:
        return "Йухан"
    if num in {"p2"} or num.isdigit() and 8 <= int(num) <= 10:
        return "Слет Парика"
    if num in {"p3"} or num.isdigit() and 11 <= int(num) <= 13:
        return "Витя-кислый"
    return None


def main() -> int:
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    cfg = app.load_config()
    matcher = app.Matcher(cfg["phrases"], cfg.get("threshold", 88.0), grammar_mode=True)
    model = Model(str(APP_DIR / cfg["model_dir"]))
    rate = int(cfg.get("sample_rate", 16000))

    grammar = json.dumps(matcher.grammar_words() + ["[unk]"], ensure_ascii=False)
    print(f"словарь: {matcher.grammar_words()}\n")

    wavs = [p for folder in sys.argv[1:] for p in sorted(Path(folder).glob("*.wav"))]
    fails = 0
    for wav_path in wavs:
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())

        rec = KaldiRecognizer(model, rate, grammar)
        finals = []
        for start in range(0, len(frames), 8000):        # по 4000 сэмплов
            if rec.AcceptWaveform(frames[start:start + 8000]):
                text = json.loads(rec.Result()).get("text", "")
                if app.normalize(text):
                    finals.append(text)
        tail = json.loads(rec.FinalResult()).get("text", "")
        if app.normalize(tail):
            finals.append(tail)

        got = None
        for text in finals:
            name, _score = matcher.find(text)
            if name:
                got = name
                break

        want = expected_for(wav_path.stem)
        ok = got == want
        fails += 0 if ok else 1
        mark = "OK  " if ok else "FAIL"
        print(f"{mark} {wav_path.stem:<20} услышано={finals!s:<34} сработало={got!s:<14} ждали={want}")

    print(f"\nПровалов: {fails} из {len(wavs)}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
