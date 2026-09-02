"""Claude로 전사문을 읽어 규칙이 놓친 말실수·다시 말한 구간을 찾고, 규칙 컷 중 의심스러운 것을 표시한다.

선택 기능: anthropic 패키지와 API 키가 있을 때만 동작한다. 전사문(텍스트)만 보내며 음성 파일은 보내지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Sequence

from .detect import Cut, make_cut, norm
from .transcript import Transcript

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """당신은 혼자 말하는 한국어 녹음(유튜브 강의, 안내 영상 등)의 편집 보조입니다.
전사문에는 발화 번호(#n)와 시간이 붙어 있습니다. 화자가 최종본에서 잘라내고 싶어 할 구간을 찾으세요.

잘라낼 것:
1. 말을 시작했다가 끊고 처음부터 다시 말한 경우: 앞의 시도 (마지막 시도는 남김)
2. 같은 내용을 두 번 이상 말한 경우: 덜 매끄러운 쪽
3. 말이 꼬여서 완성되지 않은 채 버려진 문장
4. 녹음 중 혼잣말이나 스스로에게 하는 지시 ("다시 할게요", "잠깐만", "아 틀렸다", "이거 아니다" 등)

잘라내지 말 것:
- 강조를 위한 의도적 반복
- 내용이 자연스럽게 이어지는 정상 발화
- 이미 규칙 컷 목록에 들어 있는 구간 (같은 구간을 다시 제안하지 마세요)

규칙 컷 목록도 함께 줍니다. 그중 실제로는 정상 발화로 보이는 것(예: "다시 한번 정리하면"의 "다시"를 마커로 오인한 경우)은 doubtful에 넣으세요.

각 제안은 utterance(시작 발화 번호), end_utterance(끝 발화 번호, 같은 발화면 같은 값), from_text(잘라낼 구간의 첫 몇 단어), to_text(잘라낼 구간의 마지막 몇 단어)로 지정합니다.
from_text와 to_text는 전사문에서 글자 그대로 복사하세요. 확신이 없으면 confidence를 낮추세요. 제안할 것이 없으면 빈 목록을 돌려주세요."""

SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "utterance": {"type": "integer"},
                    "end_utterance": {"type": "integer"},
                    "from_text": {"type": "string"},
                    "to_text": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["utterance", "end_utterance", "from_text", "to_text", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
        "doubtful": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"cut_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["cut_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["suggestions", "doubtful"],
    "additionalProperties": False,
}


@dataclass
class AiReview:
    cuts: list[Cut] = field(default_factory=list)      # 새로 제안된 컷
    doubts: dict[str, str] = field(default_factory=dict)  # 의심스러운 규칙 컷 id → 이유
    skipped: int = 0                                     # 위치를 못 찾아 버린 제안 수
    model: str = ""
    raw: dict = field(default_factory=dict)


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def format_transcript(transcript: Transcript) -> str:
    return "\n".join(f"#{i} [{_fmt(u.start)}-{_fmt(u.end)}] {u.text}" for i, u in enumerate(transcript.utterances))


def format_cuts(cuts: Sequence[Cut]) -> str:
    rows = [c for c in cuts if c.kind in ("marker", "repeat", "filler") and c.source == "rule"]
    if not rows:
        return "(없음)"
    return "\n".join(f"{c.id} [{_fmt(c.start)}-{_fmt(c.end)}] {c.kind}: {c.text!r} — {c.reason}" for c in rows)


def build_user_prompt(transcript: Transcript, cuts: Sequence[Cut]) -> str:
    return (
        "## 전사문\n" + format_transcript(transcript) +
        "\n\n## 규칙 기반으로 이미 제안된 컷 (무음 컷은 생략)\n" + format_cuts(cuts)
    )


def _fuzzy_find(hay: str, needle: str, start: int = 0, threshold: float = 0.75,
                boundaries: set[int] | None = None) -> int:
    """needle과 가장 비슷한 부분 문자열의 시작 위치. 단어 경계에서 시작하는 후보를 먼저 본다."""
    if not needle or len(hay) < 2:
        return -1
    n = len(needle)

    def best_in(positions) -> tuple[int, float]:
        best_pos, best_score = -1, 0.0
        for length in (n, max(2, n - 2), n + 2):
            for pos in positions:
                if pos < start or pos > max(start, len(hay) - length):
                    continue
                score = SequenceMatcher(None, hay[pos:pos + length], needle).ratio()
                if score > best_score + 1e-9:
                    best_pos, best_score = pos, score
        return best_pos, best_score

    if boundaries:
        pos, score = best_in(sorted(boundaries))
        if score >= threshold:
            return pos
    pos, score = best_in(range(start, len(hay)))
    return pos if score >= threshold else -1


def locate_span(transcript: Transcript, u_from: int, u_to: int, from_text: str, to_text: str) -> tuple[float, float] | None:
    n_utt = len(transcript.utterances)
    if n_utt == 0:
        return None
    u_from = max(0, min(u_from, n_utt - 1))
    u_to = max(u_from, min(u_to, n_utt - 1))
    words = [w for u in transcript.utterances[u_from:u_to + 1] for w in u.words]
    if not words:
        return None
    concat, owner = "", []
    for wi, w in enumerate(words):
        n = norm(w.text)
        concat += n
        owner += [wi] * len(n)
    f, t = norm(from_text), norm(to_text)
    if not f or not concat:
        return None
    bounds = {i for i in range(len(concat)) if i == 0 or owner[i] != owner[i - 1]}
    fpos = concat.find(f)
    if fpos < 0:
        fpos = _fuzzy_find(concat, f, boundaries=bounds)
    if fpos < 0:
        return None
    start_w = owner[min(fpos, len(owner) - 1)]
    end_char = min(fpos + len(f) - 1, len(concat) - 1)
    if t:
        tpos = concat.find(t, fpos)
        if tpos < 0:
            tpos = _fuzzy_find(concat, t, fpos, boundaries=bounds)
        if tpos >= 0:
            end_char = min(tpos + len(t) - 1, len(concat) - 1)
    end_w = owner[end_char]
    if end_w < start_w:
        end_w = start_w
    return words[start_w].start, words[end_w].end


def apply_response(transcript: Transcript, cuts: Sequence[Cut], data: dict, model: str = "") -> AiReview:
    """모델 응답(JSON)을 컷 목록으로 바꾼다. 위치를 못 찾는 제안은 버린다."""
    review = AiReview(model=model, raw=data)
    existing = [(c.start, c.end) for c in cuts]
    for s in data.get("suggestions", []) or []:
        span = locate_span(transcript, int(s.get("utterance", 0)), int(s.get("end_utterance", s.get("utterance", 0))),
                           str(s.get("from_text", "")), str(s.get("to_text", "")))
        if span is None or span[1] - span[0] < 0.05:
            review.skipped += 1
            continue
        start, end = span
        # 이미 규칙 컷이 거의 같은 구간을 덮고 있으면 중복이므로 버린다
        if any(abs(a - start) < 0.3 and abs(b - end) < 0.3 for a, b in existing):
            review.skipped += 1
            continue
        conf = {"high": 0.85, "medium": 0.65, "low": 0.45}.get(str(s.get("confidence", "medium")), 0.65)
        text = " ".join(w.text for w in transcript.words if start <= (w.start + w.end) / 2 <= end)
        review.cuts.append(make_cut(
            "ai", start, end, f"AI 제안: {s.get('reason', '')}", text=text, confidence=conf,
            selected=conf >= 0.85, source="ai",
        ))
    known = {c.id for c in cuts}
    for d in data.get("doubtful", []) or []:
        cid = str(d.get("cut_id", ""))
        if cid in known:
            review.doubts[cid] = str(d.get("reason", ""))
    return review


def review(transcript: Transcript, cuts: Sequence[Cut], api_key: str | None = None, model: str = DEFAULT_MODEL) -> AiReview:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic 패키지가 없습니다. `pip install anthropic` 을 실행하세요.") from exc

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(transcript, cuts)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": "claude-opus-4-8"}],
        )
    except anthropic.AuthenticationError as exc:
        raise RuntimeError("Anthropic API 키가 올바르지 않습니다.") from exc
    except anthropic.RateLimitError as exc:
        raise RuntimeError("Anthropic API 사용량 제한에 걸렸습니다. 잠시 후 다시 시도하세요.") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Anthropic API 오류 ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError("Anthropic API에 연결하지 못했습니다. 인터넷 연결을 확인하세요.") from exc

    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 이 요청 처리를 거절했습니다.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 너무 길어 잘렸습니다. 녹음을 나눠서 시도해 보세요.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AI 응답을 해석하지 못했습니다.") from exc
    return apply_response(transcript, cuts, data, model=getattr(response, "model", model))
