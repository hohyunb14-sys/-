from helpers import transcript, utt

from voicecut.detect import make_cut
from voicecut.plan import cleaned_text, keep_segments, merged_ranges, summarize


def test_merge_and_keep():
    cuts = [make_cut("a", 1.0, 2.0, ""), make_cut("b", 1.5, 2.5, ""), make_cut("c", 4.0, 4.5, "", selected=False), make_cut("d", 6.0, 6.05, "")]
    assert merged_ranges(cuts) == [(1.0, 2.5), (6.0, 6.05)]
    assert merged_ranges(cuts, only_selected=False) == [(1.0, 2.5), (4.0, 4.5), (6.0, 6.05)]
    assert keep_segments(cuts, 10.0) == [(0.0, 1.0), (2.5, 6.0), (6.05, 10.0)]
    tiny = [make_cut("a", 1.0, 2.0, ""), make_cut("b", 2.03, 3.0, "")]
    assert keep_segments(tiny, 5.0) == [(0.0, 1.0), (3.0, 5.0)]
    assert keep_segments([], 5.0) == [(0.0, 5.0)]


def test_summarize_and_cleaned_text():
    t = transcript(utt("오늘은 음 장례 절차에 대해서 말씀드리겠습니다", 0.0))
    w = t.words
    cuts = [make_cut("filler", w[1].start, w[1].end, "간투어", selected=True), make_cut("silence", 5.0, 6.0, "", selected=False)]
    s = summarize(cuts, 10.0)
    assert s["removed"] == 0.3 and s["result_duration"] == 9.7 and s["count"] == 2 and s["selected"] == 1
    assert s["by_kind"]["filler"]["seconds"] == 0.3 and s["by_kind"]["silence"]["selected"] == 0
    assert cleaned_text(t, cuts) == "오늘은 장례 절차에 대해서 말씀드리겠습니다"
