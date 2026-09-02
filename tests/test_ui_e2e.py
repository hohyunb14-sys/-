"""브라우저(Chromium) 로 검토 화면을 실제로 조작해 보는 종단 테스트. playwright 가 없으면 건너뛴다."""
import os
import socket
import threading
import time

import httpx
import pytest
from helpers import write_tone_wav

from voicecut import pipeline
from voicecut.server import create_app

pw = pytest.importorskip("playwright.sync_api")

CHROME = os.environ.get("VOICECUT_CHROMIUM") or ("/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)


@pytest.fixture
def server(tmp_path, monkeypatch):
    from test_server import FakeBackend

    monkeypatch.setattr(pipeline, "get_backend", lambda n, s: FakeBackend())
    for k in ("RTZR_CLIENT_ID", "RTZR_CLIENT_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    import uvicorn

    app = create_app(tmp_path / "jobs")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def _wait_ready(base, jid):
    s = None
    for _ in range(400):
        s = httpx.get(f"{base}/api/jobs/{jid}?light=1").json()
        if s["status"] in ("ready", "error"):
            return s
        time.sleep(0.2)
    raise AssertionError(s)


def test_review_page_interactions(server, tmp_path):
    base = server
    src = write_tone_wav(tmp_path / "e2e.wav", [(1, False), (1, True), (2, False), (1, True), (1, False)])
    jid = httpx.post(f"{base}/api/jobs", data={"path": str(src), "backend": "auto"}).json()["id"]
    s = _wait_ready(base, jid)
    assert s["status"] == "ready", s
    api_cuts = httpx.get(f"{base}/api/jobs/{jid}").json()["cuts"]
    assert api_cuts

    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True) if CHROME else p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/job/{jid}")
        page.wait_for_selector("#main:not([hidden])")
        assert page.locator(".cut-item").count() == len(api_cuts)
        assert page.locator(".w").count() == 6
        assert "원본 00:06.0" in page.inner_text("#summary")
        assert page.locator(".pill").count() >= 1  # 무음 표시

        # 컷 하나 해제 → 서버에 저장되는지
        first = page.locator(".cut-item").first
        cid = first.get_attribute("data-id")
        first.locator("input[type=checkbox]").click()
        page.wait_for_timeout(800)
        saved = {c["id"]: c for c in httpx.get(f"{base}/api/jobs/{jid}").json()["cuts"]}
        assert saved[cid]["selected"] is False
        assert page.locator(f'.cut-item[data-id="{cid}"]').get_attribute("class").endswith("off")

        # 단어 두 개 클릭 → 직접 컷 추가
        ws = page.locator(".w")
        ws.nth(0).click()
        assert page.locator("#anchorHint").is_visible()
        ws.nth(2).click()
        page.wait_for_timeout(800)
        manual = [c for c in httpx.get(f"{base}/api/jobs/{jid}").json()["cuts"] if c["kind"] == "manual"]
        assert len(manual) == 1 and manual[0]["start"] == 1.0 and manual[0]["end"] == 2.0 and manual[0]["source"] == "user"
        assert page.locator(".cut-item.k-manual").count() == 1
        assert page.locator(".w.cut.on.k-manual").count() == 3

        # 찾기
        page.fill("#search", "비용")
        page.press("#search", "Enter")
        assert page.locator(".w.hit").inner_text() == "비용"

        # 다시 분석해도 직접 컷과 선택 상태 유지
        page.locator("summary", has_text="탐지 기준 조정").click()
        page.fill("#c_silence_min", "0.5")
        page.click("#btnRedetect")
        page.wait_for_timeout(2000)
        assert page.locator(".cut-item.k-manual").count() == 1
        after = {c["id"]: c for c in httpx.get(f"{base}/api/jobs/{jid}").json()["cuts"]}
        assert after[cid]["selected"] is False

        # 내보내기
        page.click("#btnExport")
        page.wait_for_selector("#dl:not([hidden])", timeout=90000)
        assert "e2e_edited.wav" in page.inner_text("#exportInfo")
        assert httpx.get(f"{base}/api/jobs/{jid}/download").status_code == 200

        # 목록 페이지와 설정 페이지
        page.goto(base + "/")
        page.wait_for_selector("#jobs tbody tr")
        assert "e2e.wav" in page.inner_text("#jobs")
        page.goto(base + "/settings")
        page.wait_for_function("document.getElementById('d_silence_min').value !== ''")
        page.fill("#rtzr_client_id", "abc")
        page.click("#save")
        page.wait_for_selector("#msg:has-text('저장')")
        assert httpx.get(f"{base}/api/settings").json()["rtzr_client_id"] == "abc"
        assert errors == [], errors
        browser.close()
