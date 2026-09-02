from helpers import transcript, utt

from voicecut.ai_review import apply_response, build_user_prompt, locate_span
from voicecut.detect import make_cut


def _t():
    return transcript(utt("오늘은 장례 절차에 대해서 말씀드리겠습니다", 0.0), utt("두 번째는 비용입니다", 5.0))


def test_locate_exact_cross_and_fuzzy():
    t = _t()
    assert locate_span(t, 0, 0, "장례 절차에", "대해서") == (0.35, 1.35)
    assert locate_span(t, 0, 1, "말씀드리겠습니다", "두 번째는") == (1.4, 5.65)
    assert locate_span(t, 0, 0, "없는말입니다", "") is None
    assert locate_span(t, 0, 0, "장례절차의", "")[0] == 0.35  # 근사 일치
    assert locate_span(t, 9, 9, "비용입니다", "") == (5.7, 6.0)  # 범위 밖 번호는 마지막 발화로


def test_apply_response():
    t = _t()
    cuts = [make_cut("marker", 0.0, 1.0, "x")]
    data = {
        "suggestions": [
            {"utterance": 0, "end_utterance": 0, "from_text": "장례 절차에", "to_text": "대해서", "reason": "말이 꼬임", "confidence": "high"},
            {"utterance": 0, "end_utterance": 0, "from_text": "zzz", "to_text": "", "reason": "", "confidence": "low"},
            {"utterance": 0, "end_utterance": 0, "from_text": "오늘은 장례", "to_text": "절차에", "reason": "중복", "confidence": "medium"},
        ],
        "doubtful": [{"cut_id": cuts[0].id, "reason": "정상 발화"}, {"cut_id": "nope", "reason": ""}],
    }
    r = apply_response(t, cuts, data, model="m")
    assert len(r.cuts) == 1 and r.cuts[0].kind == "ai" and r.cuts[0].selected and r.cuts[0].source == "ai"
    assert r.cuts[0].text == "장례 절차에 대해서"
    assert r.skipped == 2  # 못 찾은 것 + 규칙 컷과 겹치는 것
    assert r.doubts == {cuts[0].id: "정상 발화"} and r.model == "m"
    prompt = build_user_prompt(t, cuts)
    assert "#0 [00:00.0-01:40.0]" not in prompt and "#0 [00:00.0-" in prompt and cuts[0].id in prompt
