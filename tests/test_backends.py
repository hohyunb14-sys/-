import json
from types import SimpleNamespace

import pytest

from voicecut.transcribe import TranscribeError, get_backend, rtzr_credentials
from voicecut.transcribe.rtzr import RTZRBackend, parse_rtzr_result
from voicecut.transcribe.whisper import segments_to_transcript


def test_rtzr_parse_dict_words():
    data = {"status": "completed", "results": {"utterances": [{
        "start_at": 1000, "duration": 1500, "msg": "안녕하세요 음 반갑습니다",
        "words": [{"start_at": 1000, "duration": 400, "text": "안녕하세요"}, {"start_at": 1500, "duration": 200, "text": "음"},
                  {"start_at": 1800, "duration": 700, "text": "반갑습니다"}],
    }]}}
    t = parse_rtzr_result(data)
    assert t.backend == "rtzr" and len(t.utterances) == 1
    ws = t.words
    assert [w.text for w in ws] == ["안녕하세요", "음", "반갑습니다"]
    assert ws[1].start == 1.5 and ws[1].end == 1.7
    assert t.utterances[0].start == 1.0 and t.utterances[0].end == 2.5


def test_rtzr_parse_list_words_and_missing():
    data = {"results": {"utterances": [
        {"start_at": 0, "duration": 1000, "msg": "하나 둘", "words": [[0, 400, "하나"], [500, 1000, "둘"]]},
        {"start_at": 2000, "duration": 1000, "msg": "셋 넷"},
        {"start_at": 4000, "duration": 500, "msg": ""},
    ]}}
    t = parse_rtzr_result(data)
    assert [(w.text, w.start, w.end) for w in t.utterances[0].words] == [("하나", 0.0, 0.4), ("둘", 0.5, 1.0)]
    u2 = t.utterances[1]
    assert [w.text for w in u2.words] == ["셋", "넷"] and u2.words[0].start == 2.0 and abs(u2.words[-1].end - 3.0) < 0.01
    assert len(t.utterances) == 2


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._d = data
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._d


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        return _Resp(200, {"access_token": "tok"} if "authenticate" in url else {"id": "job1"})

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return _Resp(200, {"status": "completed", "results": {"utterances": [{"start_at": 0, "duration": 500, "msg": "안녕"}]}})


def test_rtzr_backend_flow(tmp_path):
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x")
    b = RTZRBackend("id", "sec", session=_Session(), poll_interval=0.0)
    stages = []
    t = b.transcribe(str(f), progress_cb=stages.append)
    assert t.words[0].text == "안녕" and stages
    cfg = json.loads(b.session.calls[1][2]["data"]["config"])
    assert cfg["use_disfluency_filter"] is False and cfg["use_word_timestamp"] is True
    assert b.session.calls[1][2]["headers"]["Authorization"] == "bearer tok"


def test_whisper_segments():
    seg = SimpleNamespace(start=0.0, end=2.0, text=" 안녕하세요 음 ", words=[
        SimpleNamespace(word=" 안녕하세요", start=0.0, end=0.8), SimpleNamespace(word=" 음", start=1.0, end=1.2)])
    seg2 = SimpleNamespace(start=3.0, end=4.0, text="반갑습니다", words=None)
    seg3 = SimpleNamespace(start=5.0, end=5.5, text="  ", words=None)
    t = segments_to_transcript([seg, seg2, seg3])
    assert [w.text for w in t.words] == ["안녕하세요", "음", "반갑습니다"]
    assert t.utterances[1].words[0].start == 3.0 and len(t.utterances) == 2


def test_get_backend_auto(monkeypatch):
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)
    assert rtzr_credentials({}) is None
    assert get_backend("auto", {"rtzr_client_id": "a", "rtzr_client_secret": "b"}).name == "rtzr"
    assert get_backend("auto", {}).name == "whisper"
    with pytest.raises(TranscribeError):
        get_backend("rtzr", {})
    with pytest.raises(TranscribeError):
        get_backend("nope", {})
    monkeypatch.setenv("RTZR_CLIENT_ID", "x")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "y")
    assert rtzr_credentials({}) == ("x", "y")
