"""컷 후보 탐지: 무음, 간투어, 마커('다시') 재녹음, 반복 발화."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, fields
from difflib import SequenceMatcher
from typing import Iterable, Sequence

from .transcript import Transcript

_NONWORD_RE = re.compile(r"[^\w가-힣]+")
_ELONGATED_RE = re.compile(r"(\.\.\.|…|~|-)\s*$")
# 음, 어, 으, 아, 에, 흠, 엄 이 1~4번 반복된 소리 (음음, 어어 …)
_FILLER_SOUND_RE = re.compile(r"^([음어으아에흠엄])\1{0,3}$")


def norm(text: str) -> str:
    return _NONWORD_RE.sub("", text).lower()


def _split_list(value) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,\s]+", value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


@dataclass
class DetectConfig:
    # 무음
    silence_enabled: bool = True
    silence_min: float = 0.6        # 이 길이(초) 이상의 무음만 줄인다
    silence_keep: float = 0.3       # 줄인 뒤 남길 길이(초)
    silence_noise_db: float = -35.0  # 이 값(dB) 아래를 무음으로 본다
    # 간투어
    filler_enabled: bool = True
    fillers: list[str] = field(default_factory=lambda: ["음", "어", "으", "아", "에", "흠", "엄", "어우", "음음", "어어"])
    conditional_fillers: list[str] = field(default_factory=lambda: ["그", "저", "뭐"])
    conditional_gap: float = 0.3    # 조건부 간투어는 뒤에 이만큼 멈춤이 있을 때만
    # 마커(다시 말하기 신호)
    marker_enabled: bool = True
    markers: list[str] = field(default_factory=lambda: ["다시", "컷", "엔지"])
    marker_tails: list[str] = field(default_factory=lambda: [
        "요", "할게요", "할게", "할께요", "갈게요", "갈게", "갑니다", "합니다", "해볼게요", "하겠습니다", "해요", "한번",
    ])
    marker_gap: float = 0.25        # 마커 뒤 멈춤 최소 길이(초)
    marker_max_span: float = 30.0   # 마커 컷 최대 길이(초)
    # 반복 발화(마커 없이 같은 문장을 다시 말한 경우)
    repeat_enabled: bool = True
    repeat_similarity: float = 0.8
    repeat_min_chars: int = 4       # 이 글자 수 이상 겹칠 때만 반복으로 본다
    repeat_max_span: float = 15.0
    repeat_window_words: int = 30
    phrase_gap: float = 0.35        # 이만큼 멈추면 새 구절 시작으로 본다
    # 공통
    pad: float = 0.03               # 단어 경계 여유(초)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "DetectConfig":
        cfg = cls()
        if not d:
            return cfg
        for f in fields(cls):
            if f.name not in d or d[f.name] is None:
                continue
            v = d[f.name]
            if f.type == "bool" or isinstance(getattr(cfg, f.name), bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
            elif isinstance(getattr(cfg, f.name), list):
                v = _split_list(v)
            elif isinstance(getattr(cfg, f.name), int) and not isinstance(getattr(cfg, f.name), bool):
                v = int(float(v))
            elif isinstance(getattr(cfg, f.name), float):
                v = float(v)
            setattr(cfg, f.name, v)
        return cfg


@dataclass
class Cut:
    id: str
    start: float
    end: float
    kind: str            # silence | filler | marker | repeat | ai | manual
    reason: str
    text: str = ""
    confidence: float = 1.0
    selected: bool = True
    source: str = "rule"  # rule | ai | user

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Cut":
        return cls(
            id=str(d["id"]), start=float(d["start"]), end=float(d["end"]), kind=str(d.get("kind", "manual")),
            reason=str(d.get("reason", "")), text=str(d.get("text", "")), confidence=float(d.get("confidence", 1.0)),
            selected=bool(d.get("selected", True)), source=str(d.get("source", "rule")),
        )


def make_cut(kind: str, start: float, end: float, reason: str, text: str = "", confidence: float = 1.0,
             selected: bool = True, source: str = "rule") -> Cut:
    start = round(max(0.0, start), 3)
    end = round(end, 3)
    cid = f"{kind}-{int(round(start * 1000))}-{int(round(end * 1000))}"
    return Cut(cid, start, end, kind, reason, text[:80], round(confidence, 2), selected, source)


@dataclass
class IWord:
    i: int
    text: str
    start: float
    end: float
    utt: int
    norm: str


def index_words(transcript: Transcript) -> list[IWord]:
    out: list[IWord] = []
    for ui, u in enumerate(transcript.utterances):
        for w in u.words:
            out.append(IWord(len(out), w.text, w.start, w.end, ui, norm(w.text)))
    return out


def gap_before(words: Sequence[IWord], i: int) -> float:
    if i <= 0:
        return math.inf
    return max(0.0, words[i].start - words[i - 1].end)


def gap_after(words: Sequence[IWord], i: int) -> float:
    if i >= len(words) - 1:
        return math.inf
    return max(0.0, words[i + 1].start - words[i].end)


def is_phrase_start(words: Sequence[IWord], i: int, cfg: DetectConfig) -> bool:
    return i == 0 or words[i].utt != words[i - 1].utt or gap_before(words, i) >= cfg.phrase_gap


def _join(words: Iterable[IWord]) -> str:
    return " ".join(w.text for w in words)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ----------------------------------------------------------------------------- 무음

def silence_cuts(silences: Sequence[tuple[float, float]], cfg: DetectConfig, duration: float | None) -> list[Cut]:
    cuts: list[Cut] = []
    if not cfg.silence_enabled:
        return cuts
    for s, e in silences:
        s = max(0.0, s)
        if duration:
            e = min(e, duration)
        length = e - s
        if length < cfg.silence_min:
            continue
        if s <= 0.01:  # 맨 앞 무음: 말 시작 0.15초 전까지 잘라냄
            cs, ce = 0.0, e - 0.15
        elif duration and e >= duration - 0.01:  # 맨 뒤 무음: 말 끝난 뒤 0.3초만 남김
            cs, ce = s + 0.3, duration
        else:
            half = cfg.silence_keep / 2
            cs, ce = s + half, e - half
        if ce - cs < 0.05:
            continue
        cuts.append(make_cut("silence", cs, ce, f"무음 {length:.1f}초 → {cfg.silence_keep:.1f}초로 줄임", confidence=0.95))
    return cuts


# ---------------------------------------------------------------------------- 간투어

def is_filler(word: IWord, cfg: DetectConfig) -> tuple[bool, bool]:
    """(강한 간투어인지, 조건부 간투어 조건을 만족하는지)"""
    n = word.norm
    if not n:
        return False, False
    strong = n in set(cfg.fillers) or bool(_FILLER_SOUND_RE.match(n))
    return strong, (not strong) and n in set(cfg.conditional_fillers)


def filler_cuts(words: Sequence[IWord], cfg: DetectConfig) -> list[Cut]:
    cuts: list[Cut] = []
    if not cfg.filler_enabled:
        return cuts
    for i, w in enumerate(words):
        strong, cond = is_filler(w, cfg)
        if cond:
            cond = gap_after(words, i) >= cfg.conditional_gap or bool(_ELONGATED_RE.search(w.text))
        if not (strong or cond):
            continue
        gb, ga = gap_before(words, i), gap_after(words, i)
        start = w.start - min(cfg.pad, gb if gb != math.inf else cfg.pad)
        end = w.end + min(cfg.pad, ga if ga != math.inf else cfg.pad)
        reason = "간투어" if strong else "간투어로 보임(뒤에 멈춤)"
        cuts.append(make_cut("filler", start, end, reason, text=w.text, confidence=0.85 if strong else 0.6))
    return cuts


# ------------------------------------------------------------------------------ 마커

def _phrase_start_before(words: Sequence[IWord], k: int, gap: float = 0.5) -> int:
    p = k - 1
    while p > 0 and words[p].utt == words[p - 1].utt and gap_before(words, p) < gap:
        p -= 1
    return max(p, 0)


def find_retake_start(words: Sequence[IWord], k: int, j: int, cfg: DetectConfig) -> tuple[int, float, str]:
    """마커(k..j) 앞에서 잘못 말한 시도의 시작 단어 인덱스를 찾는다. (index, confidence, 설명)"""
    retake = words[j + 1: j + 9]
    b = "".join(w.norm for w in retake)
    best_score, best_i = 0.0, None
    if len(b) >= 2:
        for i in range(max(0, k - 40), k):
            a = "".join(w.norm for w in words[i:k])
            if len(a) < 2:
                continue
            length = min(len(a), len(b))
            score = _similarity(a[:length], b[:length])
            if score > best_score + 1e-9:
                best_score, best_i = score, i
    if best_i is not None and best_score >= 0.6 and words[k].start - words[best_i].start <= cfg.marker_max_span:
        return best_i, round(0.7 + 0.3 * best_score, 2), f"다시 말한 문장과 대조해 앞 시도 삭제(유사도 {best_score:.0%})"
    p = _phrase_start_before(words, k)
    if words[k].start - words[p].start <= cfg.marker_max_span:
        return p, 0.6, "직전 구절 시작부터 삭제(대조 실패, 확인 필요)"
    return k, 0.4, "마커만 삭제(앞 구간을 정하지 못함)"


def marker_cuts(words: Sequence[IWord], cfg: DetectConfig) -> list[Cut]:
    cuts: list[Cut] = []
    if not cfg.marker_enabled or not cfg.markers:
        return cuts
    markers = {norm(m) for m in cfg.markers if norm(m)}
    tails = {norm(t) for t in cfg.marker_tails if norm(t)}

    def is_marker(w: IWord) -> bool:
        if w.norm in markers:
            return True
        return any(w.norm.startswith(m) and w.norm[len(m):] in tails for m in markers)

    i = 0
    while i < len(words):
        if not is_marker(words[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(words) and (words[j + 1].start - words[j].end) <= 0.5 and (
            words[j + 1].norm in tails or is_marker(words[j + 1])
        ):
            j += 1
        repeated = any(is_marker(words[x]) for x in range(i + 1, j + 1))
        ends_utt = (j + 1 >= len(words)) or words[j + 1].utt != words[j].utt
        pause_after = gap_after(words, j) >= cfg.marker_gap
        if not (repeated or ends_utt or pause_after):
            i += 1  # "다시 한번 정리하면" 처럼 이어지는 말은 마커로 보지 않음
            continue
        end = words[j].end + min(cfg.pad, gap_after(words, j) if gap_after(words, j) != math.inf else cfg.pad)
        if i == 0:
            cuts.append(make_cut("marker", words[i].start, end, f"'{words[i].text}' 마커 삭제", text=_join(words[i:j + 1]), confidence=0.8))
            i = j + 1
            continue
        s_idx, conf, how = find_retake_start(words, i, j, cfg)
        start = words[s_idx].start - min(cfg.pad, gap_before(words, s_idx) if gap_before(words, s_idx) != math.inf else cfg.pad)
        cuts.append(make_cut("marker", start, end, f"'{words[i].text}' 마커: {how}", text=_join(words[s_idx:j + 1]), confidence=conf))
        i = j + 1
    return cuts


# ------------------------------------------------------------------------------ 반복

def _overlaps(a: tuple[float, float], ranges: Iterable[tuple[float, float]]) -> bool:
    return any(a[0] < e and s < a[1] for s, e in ranges)


def repeat_cuts(words: Sequence[IWord], cfg: DetectConfig, exclude: Sequence[tuple[float, float]] = ()) -> list[Cut]:
    cuts: list[Cut] = []
    if not cfg.repeat_enabled:
        return cuts
    taken: list[tuple[float, float]] = list(exclude)
    for j in range(1, len(words)):
        if not is_phrase_start(words, j, cfg):
            continue
        b = "".join(w.norm for w in words[j:j + 8])
        if len(b) < cfg.repeat_min_chars:
            continue
        best: tuple[float, int] | None = None
        for i in range(max(0, j - cfg.repeat_window_words), j):
            a = "".join(w.norm for w in words[i:j])
            if len(a) < cfg.repeat_min_chars:
                continue
            if len(a) < 6 and len(b) <= len(a):
                continue  # 짧은 반복은 뒤 문장이 더 길게 이어질 때만(말 시작을 다시 한 경우) 인정
            length = min(len(a), len(b))
            score = _similarity(a[:length], b[:length])
            if score >= cfg.repeat_similarity and (best is None or score > best[0]):
                best = (score, i)
        if best is None:
            continue
        score, i = best
        start = words[i].start - min(cfg.pad, gap_before(words, i) if gap_before(words, i) != math.inf else cfg.pad)
        end = words[j].start - min(cfg.pad, gap_before(words, j))
        if end <= start or end - start > cfg.repeat_max_span or _overlaps((start, end), taken):
            continue
        taken.append((start, end))
        cuts.append(make_cut(
            "repeat", start, end, f"같은 말을 다시 함(유사도 {score:.0%}), 앞 시도 삭제",
            text=_join(words[i:j]), confidence=score, selected=score >= 0.85,
        ))
    return cuts


# ------------------------------------------------------------------------------ 종합

def detect_all(
    transcript: Transcript,
    silences: Sequence[tuple[float, float]],
    cfg: DetectConfig,
    duration: float | None = None,
) -> list[Cut]:
    words = index_words(transcript)
    cuts: list[Cut] = []
    m_cuts = marker_cuts(words, cfg)
    cuts += m_cuts
    taken = [(c.start, c.end) for c in m_cuts]
    r_cuts = repeat_cuts(words, cfg, exclude=taken)
    cuts += r_cuts
    taken += [(c.start, c.end) for c in r_cuts]
    cuts += [c for c in filler_cuts(words, cfg) if not _overlaps((c.start, c.end), taken)]
    big = [(c.start, c.end) for c in cuts]
    for c in silence_cuts(silences, cfg, duration):
        # 다른 컷 안에 완전히 포함된 무음은 목록에서 뺀다 (어차피 같이 잘림)
        if any(s <= c.start and c.end <= e for s, e in big):
            continue
        cuts.append(c)
    cuts.sort(key=lambda c: (c.start, c.end))
    return cuts
