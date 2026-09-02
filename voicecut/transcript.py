"""전사 결과의 공통 자료형. STT 백엔드(리턴제로, Whisper)가 모두 이 형태로 변환한다."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Word:
    text: str
    start: float  # 초
    end: float    # 초


@dataclass
class Utterance:
    start: float
    end: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Transcript:
    utterances: list[Utterance]
    backend: str = ""
    language: str = "ko"

    @property
    def words(self) -> list[Word]:
        return [w for u in self.utterances for w in u.words]

    @property
    def text(self) -> str:
        return "\n".join(u.text for u in self.utterances)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "language": self.language,
            "utterances": [
                {
                    "start": u.start,
                    "end": u.end,
                    "words": [{"text": w.text, "start": w.start, "end": w.end} for w in u.words],
                }
                for u in self.utterances
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        utts = []
        for u in d.get("utterances", []):
            words = [Word(w["text"], float(w["start"]), float(w["end"])) for w in u.get("words", [])]
            utts.append(Utterance(float(u["start"]), float(u["end"]), words))
        return cls(utts, backend=d.get("backend", ""), language=d.get("language", "ko"))

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def spread_words(text: str, start: float, end: float) -> list[Word]:
    """단어별 시간이 없을 때, 글자 수에 비례해 발화 구간 안에 단어를 배치한다."""
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []
    total_chars = sum(len(t) for t in tokens) or 1
    span = max(end - start, 0.0)
    words: list[Word] = []
    cursor = start
    for t in tokens:
        dur = span * len(t) / total_chars
        words.append(Word(t, round(cursor, 3), round(cursor + dur, 3)))
        cursor += dur
    return words
