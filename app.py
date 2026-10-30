#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Голосовой запуск World of Tanks.

Слушает микрофон, офлайн распознаёт речь (Vosk) и запускает игру,
когда слышит одну из кодовых фраз. Живёт в системном трее, ест ~1-2% CPU.

Как это работает:
    Распознаватель ограничен словарём из трёх фраз (режим «грамматики»).
    Это не мелочь: на тестах Vosk слышит «Йухан» как «краткое у хэн»,
    а с грамматикой — стабильно «юхан». Всё, что не похоже на фразу,
    помечается как [unk] и игнорируется.

Запуск:
    python app.py                     # с иконкой в трее
    python app.py --console           # без трея, всё в консоль (отладка)
    python app.py --simulate "юхан"   # проверить срабатывание без микрофона
    python app.py --list-mics         # посмотреть доступные микрофоны
    python app.py --calibrate         # подобрать словарь под свой голос
"""

from __future__ import annotations

import argparse
import array
import json
import logging
import logging.handlers
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "app.log"

# Не показывать чёрное окно у дочерних процессов на Windows.
CREATE_NO_WINDOW = 0x08000000

log = logging.getLogger("wot-voice")


# --------------------------------------------------------------------------
# Конфиг по умолчанию
# --------------------------------------------------------------------------
#
# grammar  — слова, которые распознаватель реально слышит (это его словарь);
# variants — как ещё это может выглядеть в тексте (для свободного режима
#            и на случай, если модель произнесёт слово иначе).

DEFAULT_CONFIG = {
    "mode": "grammar",
    "game_exe": r"E:\World_of_Tanks_EU\WorldOfTanks.exe",
    "process_name": "WorldOfTanks.exe",
    "cooldown_seconds": 10,
    "threshold": 88.0,
    "model_dir": "model\\vosk-model-small-ru-0.22",
    "sample_rate": 16000,
    "device": None,
    "beep": True,
    "phrases": [
        {
            "name": "Йухан",
            "grammar": ["юхан"],
            "variants": ["юхан", "йухан", "ю хан", "йу хан", "юхань"],
        },
        {
            "name": "Слет Парика",
            "grammar": ["след парика"],
            "variants": ["след парика", "слет парика", "слёт парика",
                         "след парик", "слет парик"],
        },
        {
            "name": "Витя-кислый",
            "grammar": ["ведь кислый"],
            "variants": ["ведь кислый", "витя кислый", "витя-кислый",
                         "витя скислый", "ведь кисло"],
        },
    ],
}


def load_config() -> dict:
    """Читает config.json; при отсутствии — создаёт его."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Создан новый конфиг: %s", CONFIG_PATH)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise SystemExit(f"Не могу прочитать config.json: {exc}")

    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, value)
    return cfg


# --------------------------------------------------------------------------
# Логирование
# --------------------------------------------------------------------------

def setup_logging(console: bool = False) -> None:
    log.setLevel(logging.DEBUG)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    if console and sys.stdout is not None:
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(logging.INFO)
        stream.setFormatter(fmt)
        log.addHandler(stream)


# --------------------------------------------------------------------------
# Нормализация и сопоставление
# --------------------------------------------------------------------------

_UNK = re.compile(r"\[[^\]]*\]")
_TRANSLIT = str.maketrans({"ё": "е", "Ё": "Е"})


def normalize(text: str) -> str:
    """Строчные, без ё, без [unk] и знаков препинания."""
    text = _UNK.sub(" ", text or "").lower().translate(_TRANSLIT)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return " ".join(text.split())


class Matcher:
    """Сопоставляет распознанный текст с кодовыми фразами."""

    def __init__(self, phrases: list[dict], threshold: float = 88.0, grammar_mode: bool = True):
        from rapidfuzz import fuzz

        self._fuzz = fuzz
        self.threshold = float(threshold)
        self.grammar_mode = grammar_mode
        self.phrases: list[dict] = []

        for phrase in phrases:
            name = (phrase.get("name") or "").strip()
            if not name:
                continue
            forms = phrase.get("variants") or []
            if grammar_mode:
                # В грамматическом режиме распознаватель может выдать только
                # эти слова — сравниваем в первую очередь с ними, но варианты
                # тоже держим: они не мешают, зато спасают, если модель
                # произнесёт слово чуть иначе.
                forms = (phrase.get("grammar") or []) + list(forms)
            normalized: list[str] = []
            for raw in forms:
                norm = normalize(raw)
                if norm and norm not in normalized:
                    normalized.append(norm)

            grammar_words: list[str] = []
            for raw in phrase.get("grammar", []):
                norm = normalize(raw)
                if norm and norm not in grammar_words:
                    grammar_words.append(norm)

            if normalized:
                self.phrases.append(
                    {"name": name, "forms": normalized, "grammar": grammar_words}
                )

    def grammar_words(self) -> list[str]:
        """Только те слова, из которых строится словарь распознавателя."""
        words: list[str] = []
        for phrase in self.phrases:
            words.extend(phrase["grammar"])
        return words

    def find(self, text: str) -> tuple[str | None, float]:
        """Возвращает (имя фразы, балл). Срабатывание только при полном совпадении."""
        norm = normalize(text)
        if not norm:
            return None, 0.0

        best_name, best_score = None, 0.0
        padded = f" {norm} "
        for phrase in self.phrases:
            for form in phrase["forms"]:
                if norm == form:
                    return phrase["name"], 100.0
                # Фразу могли повторить или разбавить лишним словом.
                if f" {form} " in padded:
                    return phrase["name"], 96.0
                score = float(self._fuzz.ratio(norm, form))
                if score > best_score:
                    best_name, best_score = phrase["name"], score
        if best_name and best_score >= self.threshold:
            return best_name, best_score
        return None, best_score


# --------------------------------------------------------------------------
# Запуск игры
# --------------------------------------------------------------------------

def is_game_running(process_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
        out = result.stdout.decode("cp866", errors="ignore").lower()
        return process_name.lower() in out
    except Exception as exc:
        log.warning("Не удалось проверить процессы: %s", exc)
        return False


def launch_game(cfg: dict) -> bool:
    exe = Path(cfg["game_exe"])
    if not exe.exists():
        log.error("Игра не найдена: %s", exe)
        return False
    try:
        subprocess.Popen(
            [str(exe)], cwd=str(exe.parent), close_fds=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        log.error("Не удалось запустить игру: %s", exc)
        return False
    log.info("Игра запущена: %s", exe)
    return True


def beep(kind: str = "ok") -> None:
    try:
        import winsound

        winsound.MessageBeep(
            winsound.MB_ICONASTERISK if kind == "ok" else winsound.MB_ICONHAND
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Слушатель микрофона
# --------------------------------------------------------------------------

def resample_linear(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Линейная передискретизация int16-моно (без numpy)."""
    if src_rate == dst_rate or not data:
        return data

    count = len(data) // 2
    src = array.array("h")
    src.frombytes(data[: count * 2])

    out_len = max(1, int(count * dst_rate / src_rate))
    out = array.array("h", bytes(2 * out_len))
    step = src_rate / dst_rate

    for i in range(out_len):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = src[j] if j < count else 0
        b = src[j + 1] if j + 1 < count else a
        value = int(a + (b - a) * frac)
        out[i] = max(-32768, min(32767, value))

    return out.tobytes()


class Listener(threading.Thread):
    """Фоновый поток: микрофон → Vosk → сопоставление с фразами."""

    def __init__(self, cfg: dict, matcher: Matcher, on_hit, mode: str = "grammar"):
        super().__init__(name="voice-listener", daemon=True)
        self.cfg = cfg
        self.matcher = matcher
        self.on_hit = on_hit
        self.mode = mode

        self.paused = threading.Event()
        self.stopped = threading.Event()
        self.ready = threading.Event()          # модель загружена
        self.last_hit_at = 0.0
        self.last_text = ""
        self.load_error: str | None = None

    def stop(self) -> None:
        self.stopped.set()

    def pause(self) -> None:
        self.paused.set()

    def resume(self) -> None:
        self.paused.clear()

    def run(self) -> None:
        attempt = 0
        while not self.stopped.is_set():
            try:
                self._listen_once()
                attempt = 0
            except Exception as exc:
                attempt += 1
                self.load_error = str(exc)
                log.error("Сбой слушателя (попытка %s): %s", attempt, exc)
                if self.stopped.is_set():
                    break
                self.stopped.wait(min(30, 3 * attempt))

    def _make_recognizer(self, model, rate: int):
        from vosk import KaldiRecognizer

        if self.mode == "grammar":
            words = self.matcher.grammar_words()
            if not words:
                raise RuntimeError("Пустой список фраз в config.json")
            grammar = json.dumps(words + ["[unk]"], ensure_ascii=False)
            log.info("Режим грамматики, словарь: %s", ", ".join(words))
            return KaldiRecognizer(model, rate, grammar)
        return KaldiRecognizer(model, rate)

    def _listen_once(self) -> None:
        import sounddevice as sd
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)

        model_dir = APP_DIR / self.cfg["model_dir"]
        if not model_dir.exists():
            raise RuntimeError(
                f"Нет модели Vosk по пути {model_dir}. Запусти setup.ps1."
            )

        log.info("Загружаю модель %s ...", model_dir.name)
        model = Model(str(model_dir))
        rate = int(self.cfg.get("sample_rate", 16000))
        recognizer = self._make_recognizer(model, rate)
        log.info("Модель загружена, слушаю микрофон")
        self.ready.set()

        audio_queue: "queue.Queue[bytes]" = queue.Queue()
        device = self.cfg.get("device")
        stream, src_rate = self._open_stream(sd, rate, device, audio_queue)
        with stream:
            log.info("Микрофон открыт: %s (%s Гц)", self._device_name(sd, device), src_rate)
            while not self.stopped.is_set():
                try:
                    data = audio_queue.get(timeout=0.4)
                except queue.Empty:
                    continue

                if self.paused.is_set():
                    continue

                if src_rate != rate:
                    data = resample_linear(data, src_rate, rate)

                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "")
                    self._process(text)
                else:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "")
                    if partial and partial != self.last_text:
                        self.last_text = partial
                        log.debug("… %s", partial)

    @staticmethod
    def _open_stream(sd, rate: int, device, audio_queue):
        """Открывает поток; если устройство не умеет 16 кГц — берём его частоту."""

        def callback(indata, frames, time_info, status) -> None:
            if status:
                log.debug("Статус аудиопотока: %s", status)
            audio_queue.put(bytes(indata))

        try:
            stream = sd.RawInputStream(
                samplerate=rate, blocksize=4000, device=device,
                dtype="int16", channels=1, callback=callback,
            )
            return stream, rate
        except Exception as exc:
            native = int(sd.query_devices(device, "input")["default_samplerate"])
            log.warning("16 кГц не открылись (%s), беру %s Гц с пересчётом", exc, native)
            stream = sd.RawInputStream(
                samplerate=native, blocksize=4000, device=device,
                dtype="int16", channels=1, callback=callback,
            )
            return stream, native

    @staticmethod
    def _device_name(sd, device) -> str:
        try:
            return sd.query_devices(device, "input")["name"]
        except Exception:
            return str(device)

    def _process(self, text: str) -> None:
        clean = normalize(text)
        if not clean:
            return

        log.debug("распознано: %r", text)
        name, score = self.matcher.find(text)
        if not name:
            if score > 60:
                log.debug("похоже, но мимо: %.1f", score)
            return

        now = time.time()
        gap = float(self.cfg.get("cooldown_seconds", 10))
        if now - self.last_hit_at < gap:
            log.info("«%s» повторно, но ещё кулдаун — пропускаю", name)
            return

        self.last_hit_at = now
        log.info("🎯 Фраза «%s» (совпадение %.1f%%)", name, score)

        if self.cfg.get("beep", True):
            beep("ok")

        try:
            self.on_hit(name, text)
        except Exception as exc:
            log.exception("Ошибка обработчика: %s", exc)


# --------------------------------------------------------------------------
# Трей
# --------------------------------------------------------------------------

def make_icon_image(active: bool):
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = (46, 125, 50, 255) if active else (110, 110, 110, 255)
    draw.ellipse((2, 2, size - 3, size - 3), fill=bg,
                 outline=(255, 255, 255, 255), width=2)
    draw.rectangle((14, 38, 50, 47), fill=(255, 255, 255, 255))   # гусеницы
    draw.rectangle((21, 27, 43, 38), fill=(255, 255, 255, 255))   # корпус
    draw.rectangle((41, 30, 57, 34), fill=(255, 255, 255, 255))   # ствол
    return img


class TrayApp:
    def __init__(self, cfg: dict, listener: Listener):
        self.cfg = cfg
        self.listener = listener
        self.icon = None
        self._paused = False

    def _toggle(self, _item=None) -> None:
        self._paused = not self._paused
        if self._paused:
            self.listener.pause()
            log.info("Пауза")
        else:
            self.listener.resume()
            log.info("Слушаю дальше")
        self.icon.icon = make_icon_image(not self._paused)
        self.icon.update_menu()

    def _launch_now(self, _item=None) -> None:
        threading.Thread(target=self._launch_now_worker, daemon=True).start()

    def _launch_now_worker(self) -> None:
        if is_game_running(self.cfg["process_name"]):
            log.info("Игра уже запущена")
            self._notify("World of Tanks уже запущен")
            return
        if launch_game(self.cfg):
            self._notify("Запускаю World of Tanks")

    def _notify(self, message: str) -> None:
        try:
            if self.icon:
                self.icon.notify(message, "WoT: голосовой запуск")
        except Exception:
            pass

    def _open_log(self, _item=None) -> None:
        try:
            os.startfile(LOG_PATH)  # noqa: S606
        except Exception as exc:
            log.warning("Не открыть журнал: %s", exc)

    def _quit(self, _item=None) -> None:
        log.info("Выход")
        self.listener.stop()
        if self.icon:
            self.icon.stop()

    def run(self) -> None:
        import pystray

        status = pystray.MenuItem(
            lambda _item: "Пауза" if self._paused else "Слушаю микрофон",
            None, enabled=False,
        )
        menu = pystray.Menu(
            status,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Пауза / продолжить", self._toggle),
            pystray.MenuItem("Запустить игру сейчас", self._launch_now),
            pystray.MenuItem("Открыть журнал", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._quit),
        )
        self.icon = pystray.Icon(
            "wot-voice", make_icon_image(True), "WoT: голосовой запуск", menu
        )
        self.icon.run()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Голосовой запуск World of Tanks")
    parser.add_argument("--console", action="store_true", help="без трея, вывод в консоль")
    parser.add_argument("--simulate", metavar="ТЕКСТ", help="проверить фразу без микрофона")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны")
    parser.add_argument("--launch", action="store_true", help="просто запустить игру и выйти")
    parser.add_argument("--calibrate", type=int, nargs="?", const=25, metavar="СЕК",
                        help="слушать в свободном режиме и показать, что слышит Vosk")
    return parser.parse_args()


def run_calibration(cfg: dict, seconds: int) -> int:
    """Свободное распознавание: показывает, какими словами Vosk слышит твой голос."""
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    model_dir = APP_DIR / cfg["model_dir"]
    if not model_dir.exists():
        print(f"Нет модели: {model_dir}")
        return 1

    print("Загружаю модель…")
    model = Model(str(model_dir))
    rate = int(cfg.get("sample_rate", 16000))
    rec = KaldiRecognizer(model, rate)

    q: "queue.Queue[bytes]" = queue.Queue()

    def cb(indata, frames, time_info, status):
        q.put(bytes(indata))

    print(f"Говорите {seconds} секунд. Ctrl+C — выйти раньше.")
    heard: list[str] = []
    with sd.RawInputStream(samplerate=rate, blocksize=4000, device=cfg.get("device"),
                           dtype="int16", channels=1, callback=cb):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                data = q.get(timeout=0.4)
            except queue.Empty:
                continue
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "")
                if text:
                    heard.append(normalize(text))
                    print(f"  слышу: {text}")

    out = APP_DIR / "calibrate.txt"
    out.write_text("\n".join(heard), encoding="utf-8")
    print(f"\nЗаписано в {out}")
    print("Если в тексте есть слова, которые Vosk понимает стабильно,")
    print("впиши их в поле grammar нужной фразы в config.json.")
    return 0


def main() -> int:
    args = parse_args()
    setup_logging(console=args.console or args.simulate is not None or args.list_mics)

    if args.list_mics:
        import sounddevice as sd

        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                print(f"{index:>3}  {dev['name']}  ({dev['default_samplerate']:.0f} Гц)")
        return 0

    cfg = load_config()
    mode = cfg.get("mode", "grammar")

    if args.launch:
        if is_game_running(cfg["process_name"]):
            print("Игра уже запущена")
            return 0
        return 0 if launch_game(cfg) else 1

    if args.calibrate is not None:
        return run_calibration(cfg, args.calibrate)

    matcher = Matcher(cfg["phrases"], cfg.get("threshold", 88.0),
                      grammar_mode=(mode == "grammar"))

    if args.simulate is not None:
        name, score = matcher.find(args.simulate)
        print(f"Текст: {args.simulate!r}")
        print(f"Итог:  {name or '—'}  (лучшее совпадение {score:.1f}%)")
        return 0 if name else 2

    tray: TrayApp | None = None

    def on_hit(name: str, _text: str) -> None:
        if is_game_running(cfg["process_name"]):
            log.info("«%s» — но игра уже запущена, ничего не делаю", name)
            if tray:
                tray._notify("World of Tanks уже запущен")
            return
        if launch_game(cfg) and tray:
            tray._notify(f"Кодовое слово «{name}» — запускаю")

    listener = Listener(cfg, matcher, on_hit, mode=mode)
    listener.start()

    if args.console:
        print("Слушаю… Ctrl+C для выхода.", file=sys.stderr)
        try:
            while listener.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nВыход", file=sys.stderr)
            listener.stop()
        return 0

    tray = TrayApp(cfg, listener)
    tray.run()
    listener.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
