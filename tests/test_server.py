import time

import pytest
from fastapi.testclient import TestClient
from helpers import write_tone_wav

from voicecut import pipeline
from voicecut.server import MASK, create_app
from voicecut.transcript import Transcript, Utterance, Word


class FakeBackend:
    name = "fake"

    def transcribe(self, path, keep_fillers=True, progress_cb=None):
        return Transcript([
            Utterance(1.0, 2.0, [Word("안녕하세요", 1.0, 1.4), Word("음", 1.5, 1.7), Word("반갑습니다", 1.75, 2.0)]),
            Utterance(4.0, 5.0, [Word("오늘은", 4.0, 4.4), Word("비용", 4.5, 4.7), Word("이야기입니다", 4.75, 5.0)]),
        ], backend="fake")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_backend", lambda name, settings: FakeBackend())
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return TestClient(create_app(tmp_path / "jobs"))


def wait(client, jid, getter, want, timeout=90):
    s = None
    for _ in range(int(timeout * 5)):
        s = client.get(f"/api/jobs/{jid}?light=1").json()
        if getter(s) in want:
            return s
        time.sleep(0.2)
    raise AssertionError(f"timeout: {s}")


def test_full_flow(client, tmp_path):
    src = write_tone_wav(tmp_path / "녹음 테스트.wav", [(1, False), (1, True), (2, False), (1, True), (1, False)])
    with src.open("rb") as f:
        r = client.post("/api/jobs", files={"file": ("녹음 테스트.wav", f, "audio/wav")}, data={"backend": "auto", "ai_review": "false", "detect": "{}"})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    s = wait(client, jid, lambda s: s["status"], {"ready", "error"})
    assert s["status"] == "ready", s.get("error")
    full = client.get(f"/api/jobs/{jid}").json()
    kinds = {c["kind"] for c in full["cuts"]}
    assert "silence" in kinds and "filler" in kinds
    assert full["transcript"]["utterances"][0]["words"][1]["text"] == "음"
    assert full["backend"] == "fake" and abs(full["duration"] - 6.0) < 0.05

    a = client.get(f"/api/jobs/{jid}/audio", headers={"Range": "bytes=0-99"})
    assert a.status_code == 206 and len(a.content) == 100 and a.headers["content-range"].startswith("bytes 0-99/")
    assert client.get(f"/api/jobs/{jid}/audio").status_code == 200

    cuts = full["cuts"]
    for c in cuts:
        c["selected"] = False
    cuts.append({"id": "manual-1", "start": 1.0, "end": 1.5, "kind": "manual", "reason": "직접", "selected": True, "source": "user"})
    r = client.post(f"/api/jobs/{jid}/cuts", json={"cuts": cuts})
    assert r.status_code == 200 and abs(r.json()["summary"]["removed"] - 0.5) < 0.01

    r = client.post(f"/api/jobs/{jid}/redetect", json={"detect": {"silence_min": 0.5, "markers": "다시"}})
    d = r.json()
    assert r.status_code == 200 and d["settings"]["markers"] == ["다시"]
    assert any(c["kind"] == "manual" for c in d["cuts"])
    assert all(not c["selected"] for c in d["cuts"] if c["kind"] != "manual")

    r = client.post(f"/api/jobs/{jid}/export", json={"audio_only": False})
    assert r.status_code == 200
    s = wait(client, jid, lambda s: s["export"]["status"], {"done", "error"})
    assert s["export"]["status"] == "done", s["export"]
    assert s["export"]["file"] == "녹음 테스트_edited.wav"
    dl = client.get(f"/api/jobs/{jid}/download")
    assert dl.status_code == 200 and len(dl.content) > 1000 and "filename*=UTF-8''" in dl.headers["content-disposition"]
    txt = client.get(f"/api/jobs/{jid}/transcript.txt")
    assert txt.status_code == 200 and "안녕하세요" not in txt.text and "반갑습니다" in txt.text  # 직접 컷(1.0~1.5)이 첫 단어를 지움

    assert client.get("/api/jobs").json()[0]["id"] == jid
    assert client.get(f"/job/{jid}").status_code == 200
    assert client.get("/").status_code == 200 and client.get("/settings").status_code == 200
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert client.get(f"/api/jobs/{jid}").status_code == 404
    assert client.get("/job/nope").status_code == 404


def test_local_path_and_errors(client, tmp_path):
    src = write_tone_wav(tmp_path / "local.wav", [(0.5, True), (1.0, False), (0.5, True)])
    r = client.post("/api/jobs", data={"path": str(src), "backend": "auto"})
    assert r.status_code == 200
    jid = r.json()["id"]
    s = wait(client, jid, lambda s: s["status"], {"ready", "error"})
    assert s["status"] == "ready", s.get("error")
    assert s["input"] == str(src.resolve())
    assert client.post("/api/jobs", data={"path": str(tmp_path / "없음.wav")}).status_code == 400
    assert client.post("/api/jobs", data={}).status_code == 400
    assert client.post("/api/jobs/nope/export", json={}).status_code == 404


def test_settings_mask(client):
    r = client.post("/api/settings", json={"rtzr_client_id": "abc", "rtzr_client_secret": "s3cret", "ai_review": True,
                                           "detect": {"silence_min": "0.9", "markers": "다시, 컷"}})
    assert r.status_code == 200 and r.json()["capabilities"]["rtzr"] is True
    s = client.get("/api/settings").json()
    assert s["rtzr_client_id"] == "abc" and s["rtzr_client_secret"] == MASK and s["ai_review"] is True
    assert s["detect"]["silence_min"] == 0.9 and s["detect"]["markers"] == ["다시", "컷"]
    client.post("/api/settings", json={"rtzr_client_secret": MASK, "anthropic_api_key": ""})
    saved = pipeline.load_settings(s["jobs_dir"])
    assert saved["rtzr_client_secret"] == "s3cret" and saved["anthropic_api_key"] == ""
