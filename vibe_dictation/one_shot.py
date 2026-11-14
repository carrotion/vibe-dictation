"""One-shot dictation from the command line (no tray, no hotkey)."""
from __future__ import annotations

import argparse

from .audio import record_utterance
from .clipboard import copy_and_paste
from .config import load_config
from .stt import WhisperStt


def main() -> int:
    parser = argparse.ArgumentParser(description="Record one utterance and paste it.")
    parser.add_argument("--no-paste", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--wait-s", type=float, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.model:
        config.model = args.model
    wait_s = args.wait_s if args.wait_s is not None else max(2.0, float(config.wait_s))
    audio_config = config.audio()
    print("Speak now. Stops after silence.")
    rec = record_utterance(audio_config, max_wait_s=wait_s)
    if rec.reason != "ok" or rec.audio is None:
        print(f"No usable speech: {rec.reason}")
        return 1
    print("Transcribing...")
    result = WhisperStt(config).transcribe(rec.audio)
    text = result.text.strip()
    if not text:
        print("Could not transcribe any words.")
        return 1
    copy_and_paste(text, paste=not args.no_paste)
    print(text)
    if not args.no_paste:
        print("Pasted into the active input. Edit it, then press Enter yourself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
