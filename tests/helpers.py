"""테스트용 헬퍼: 합성 전사와 합성 오디오."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from voicecut.transcript import Transcript, Utterance, Word


def utt(text: str, start: float, wdur: float = 0.3, gap: float = 0.05, pauses: dict[int, float] | None = None) -> Utterance:
    """단어를 일정 간격으로 배치. pauses={단어 인덱스: 그 단어 뒤 추가 멈춤(초)}"""
    words = []
    t = start
    for i, tok in enumerate(text.split()):
        words.append(Word(tok, round(t, 3), round(t + wdur, 3)))
        t += wdur + gap + (pauses or {}).get(i, 0.0)
    return Utterance(words[0].start, words[-1].end, words)


def transcript(*utts: Utterance) -> Transcript:
    return Transcript(list(utts), backend="test")


def write_tone_wav(path: Path, pattern: list[tuple[float, bool]], sr: int = 16000) -> Path:
    """pattern: [(초, 소리 있음?)] 순서대로 440Hz 톤 또는 무음을 쓴다."""
    frames = bytearray()
    for secs, tone in pattern:
        n = int(secs * sr)
        for i in range(n):
            v = int(8000 * math.sin(2 * math.pi * 440 * i / sr)) if tone else 0
            frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return Path(path)
