"""선택된 컷을 합쳐 남길 구간을 계산하고, 요약과 정리된 전사문을 만든다."""
from __future__ import annotations

from typing import Iterable, Sequence

from .detect import Cut
from .transcript import Transcript


def merged_ranges(cuts: Iterable[Cut], only_selected: bool = True, join_gap: float = 0.0) -> list[tuple[float, float]]:
    ranges = sorted(
        (c.start, c.end) for c in cuts if (c.selected or not only_selected) and c.end > c.start
    )
    merged: list[list[float]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1] + join_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(round(s, 3), round(e, 3)) for s, e in merged]


def keep_segments(cuts: Iterable[Cut], duration: float, min_keep: float = 0.08) -> list[tuple[float, float]]:
    """컷의 여집합. min_keep보다 짧은 조각은 버린다(사실상 이웃 컷과 합쳐짐)."""
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in merged_ranges(cuts):
        s, e = max(0.0, s), min(duration, e)
        if s - cursor >= min_keep:
            segments.append((round(cursor, 3), round(s, 3)))
        cursor = max(cursor, e)
    if duration - cursor >= min_keep:
        segments.append((round(cursor, 3), round(duration, 3)))
    return segments


def summarize(cuts: Sequence[Cut], duration: float) -> dict:
    by_kind: dict[str, dict] = {}
    for c in cuts:
        k = by_kind.setdefault(c.kind, {"count": 0, "selected": 0, "seconds": 0.0})
        k["count"] += 1
        if c.selected:
            k["selected"] += 1
            k["seconds"] += c.duration
    removed = sum(e - s for s, e in merged_ranges(cuts))
    return {
        "duration": round(duration, 3),
        "removed": round(removed, 3),
        "result_duration": round(max(0.0, duration - removed), 3),
        "by_kind": {k: {**v, "seconds": round(v["seconds"], 3)} for k, v in by_kind.items()},
        "count": len(cuts),
        "selected": sum(1 for c in cuts if c.selected),
    }


def cleaned_text(transcript: Transcript, cuts: Iterable[Cut]) -> str:
    """선택된 컷에 들어간 단어를 뺀 전사문."""
    ranges = merged_ranges(cuts)
    lines: list[str] = []
    for u in transcript.utterances:
        kept = []
        for w in u.words:
            mid = (w.start + w.end) / 2
            if any(s <= mid <= e for s, e in ranges):
                continue
            kept.append(w.text)
        if kept:
            lines.append(" ".join(kept))
    return "\n".join(lines)
