from helpers import transcript, utt

from voicecut.detect import DetectConfig, detect_all, filler_cuts, index_words, marker_cuts, repeat_cuts, silence_cuts


def test_filler_basic():
    t = transcript(utt("오늘은 음 장례 절차에 대해서 어... 말씀드리겠습니다", 0.0))
    cuts = filler_cuts(index_words(t), DetectConfig())
    assert [c.text for c in cuts] == ["음", "어..."]
    assert all(c.kind == "filler" for c in cuts)
    assert cuts[0].start == 0.32 and cuts[0].end == 0.68  # 단어 경계 ± pad


def test_conditional_filler_needs_pause():
    t = transcript(utt("그 사람은 그 잘 모릅니다", 0.0, pauses={2: 0.5}))
    cuts = filler_cuts(index_words(t), DetectConfig())
    assert len(cuts) == 1
    assert cuts[0].text == "그" and abs(cuts[0].start - 0.67) < 0.01 and cuts[0].confidence < 0.85


def test_marker_retake_aligned():
    t = transcript(utt("오늘은 장례 절차에 대해 다시 오늘은 장례 절차에 대해서 말씀드리겠습니다", 0.0, pauses={4: 0.6}))
    cuts = marker_cuts(index_words(t), DetectConfig())
    assert len(cuts) == 1
    c = cuts[0]
    assert c.kind == "marker" and c.start == 0.0
    assert abs(c.end - (4 * 0.35 + 0.3 + 0.03)) < 0.01
    assert c.confidence >= 0.9
    assert c.text == "오늘은 장례 절차에 대해 다시"


def test_marker_in_normal_speech_not_cut():
    t = transcript(utt("그래서 다시 한번 정리하면 장례식장은 세 곳을 비교하세요", 0.0))
    assert marker_cuts(index_words(t), DetectConfig()) == []


def test_marker_with_tail_and_pause():
    t = transcript(utt("비용은 세 가지로 나뉩니다 다시 할게요 비용은 크게 세 가지입니다", 0.0, pauses={5: 0.5}))
    cuts = marker_cuts(index_words(t), DetectConfig())
    assert len(cuts) == 1 and cuts[0].text.endswith("다시 할게요") and cuts[0].start == 0.0


def test_marker_fallback_phrase_start():
    t = transcript(utt("첫 번째로 비용 이야기를 하겠습니다 그래서 비용이 어 다시 준비하실 때는 보험을 먼저 확인하세요", 0.0, pauses={4: 0.6, 8: 0.6}))
    words = index_words(t)
    cuts = marker_cuts(words, DetectConfig())
    assert len(cuts) == 1
    c = cuts[0]
    assert abs(c.start - (words[5].start - 0.03)) < 0.01  # "그래서" 부터
    assert c.confidence == 0.6 and "확인 필요" in c.reason


def test_repeat_detection():
    t = transcript(utt("장례식장을 고를 때는 장례식장을 고를 때는 세 가지를 보셔야 합니다", 0.0, pauses={2: 0.5}))
    words = index_words(t)
    cuts = repeat_cuts(words, DetectConfig())
    assert len(cuts) == 1
    c = cuts[0]
    assert c.kind == "repeat" and c.selected and c.confidence >= 0.85
    assert c.text == "장례식장을 고를 때는"
    assert abs(c.end - (words[3].start - 0.03)) < 0.01


def test_repeat_ignores_short_or_different():
    t = transcript(utt("네 네 그렇습니다 오늘 주제는 비용입니다", 0.0, pauses={1: 0.5, 2: 0.5}))
    assert repeat_cuts(index_words(t), DetectConfig()) == []
    # 마무리 인사처럼 같은 길이로 끝나는 짧은 반복은 두지만, 짧은 말 시작을 다시 한 경우는 잡는다
    t2 = transcript(utt("감사합니다 감사합니다", 0.0, pauses={0: 0.5}))
    assert repeat_cuts(index_words(t2), DetectConfig()) == []
    t3 = transcript(utt("세 번째는 세 번째는 계약서입니다 꼭 확인하세요", 0.0, pauses={1: 0.5}))
    cuts = repeat_cuts(index_words(t3), DetectConfig())
    assert len(cuts) == 1 and cuts[0].text == "세 번째는"


def test_silence_cuts():
    cuts = silence_cuts([(0.0, 1.0), (5.0, 7.0), (9.0, 10.0), (3.0, 3.4)], DetectConfig(), duration=10.0)
    assert [(c.start, c.end) for c in cuts] == [(0.0, 0.85), (5.15, 6.85), (9.3, 10.0)]
    assert all(c.kind == "silence" for c in cuts)


def test_detect_all_drops_silence_inside_marker_cut():
    t = transcript(utt("오늘은 장례 절차에 대해 다시 오늘은 장례 절차에 대해서 말씀드리겠습니다", 0.0, pauses={1: 0.7, 4: 0.6}))
    cuts = detect_all(t, [(0.65, 1.40)], DetectConfig(), duration=8.0)
    kinds = [c.kind for c in cuts]
    assert kinds == ["marker"]
    assert cuts == sorted(cuts, key=lambda c: (c.start, c.end))


def test_config_from_dict_strings():
    cfg = DetectConfig.from_dict({"silence_min": "0.5", "marker_enabled": "false", "markers": "다시, 컷", "repeat_window_words": "20", "unknown": 1})
    assert cfg.silence_min == 0.5 and cfg.marker_enabled is False
    assert cfg.markers == ["다시", "컷"] and cfg.repeat_window_words == 20
    assert DetectConfig.from_dict(None) == DetectConfig()
