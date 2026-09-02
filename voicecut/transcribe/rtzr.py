"""리턴제로(RTZR, 구 VITO) STT OpenAPI 클라이언트.

- 간투어(음, 어)를 지우지 않도록 use_disfluency_filter=False 로 요청한다.
- 단어별 타임스탬프(use_word_timestamp=True)를 받아 컷 위치를 정한다.
- 공개 문서 기준으로 작성했으며, 응답 필드 이름이 다르면 parse_rtzr_result 에서 너그럽게 읽는다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from ..transcript import Transcript, Utterance, Word, spread_words
from . import ProgressCb, TranscribeError

AUTH_URL = "https://openapi.vito.ai/v1/authenticate"
TRANSCRIBE_URL = "https://openapi.vito.ai/v1/transcribe"


class RTZRBackend:
    name = "rtzr"

    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None,
                 poll_interval: float = 3.0, timeout: float = 3600.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _auth(self) -> str:
        r = self.session.post(AUTH_URL, data={"client_id": self.client_id, "client_secret": self.client_secret}, timeout=30)
        if r.status_code != 200:
            raise TranscribeError(f"리턴제로 인증 실패 ({r.status_code}): {r.text[:200]}")
        token = r.json().get("access_token")
        if not token:
            raise TranscribeError("리턴제로 인증 응답에 access_token이 없습니다.")
        return token

    def transcribe(self, audio_path: str, keep_fillers: bool = True, progress_cb: ProgressCb = None) -> Transcript:
        token = self._auth()
        headers = {"Authorization": f"bearer {token}"}
        config = {
            "use_diarization": False,
            "use_itn": True,
            "use_disfluency_filter": not keep_fillers,
            "use_profanity_filter": False,
            "use_paragraph_splitter": True,
            "paragraph_splitter": {"max": 60},
            "use_word_timestamp": True,
        }
        if progress_cb:
            progress_cb("리턴제로에 음성 업로드 중")
        path = Path(audio_path)
        with path.open("rb") as fh:
            r = self.session.post(
                TRANSCRIBE_URL, headers=headers,
                files={"file": (path.name, fh)}, data={"config": json.dumps(config)}, timeout=600,
            )
        if r.status_code not in (200, 201, 202):
            raise TranscribeError(f"리턴제로 전사 요청 실패 ({r.status_code}): {r.text[:300]}")
        job_id = r.json().get("id")
        if not job_id:
            raise TranscribeError(f"리턴제로 응답에 작업 id가 없습니다: {r.text[:200]}")
        deadline = time.time() + self.timeout
        while True:
            if progress_cb:
                progress_cb("리턴제로에서 음성 인식 중")
            time.sleep(self.poll_interval)
            g = self.session.get(f"{TRANSCRIBE_URL}/{job_id}", headers=headers, timeout=60)
            if g.status_code != 200:
                raise TranscribeError(f"리턴제로 결과 조회 실패 ({g.status_code}): {g.text[:200]}")
            data = g.json()
            status = str(data.get("status", "")).lower()
            if status == "completed":
                return parse_rtzr_result(data)
            if status in ("failed", "error"):
                raise TranscribeError(f"리턴제로 전사 실패: {json.dumps(data, ensure_ascii=False)[:300]}")
            if time.time() > deadline:
                raise TranscribeError("리턴제로 전사가 제한 시간 안에 끝나지 않았습니다.")


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_words(raw_words: Any, utt_start: float, utt_end: float, msg: str) -> list[Word]:
    """words 필드를 최대한 너그럽게 읽는다. 없거나 형식이 낯설면 발화 길이에 맞춰 단어를 배치한다."""
    if not isinstance(raw_words, list) or not raw_words:
        return spread_words(msg, utt_start, utt_end)
    words: list[Word] = []
    for item in raw_words:
        text, start, end = None, None, None
        if isinstance(item, dict):
            text = item.get("text") or item.get("word") or item.get("msg")
            if "start_at" in item:
                start = _num(item["start_at"]) / 1000.0
                if "duration" in item:
                    end = start + _num(item["duration"]) / 1000.0
                elif "end_at" in item:
                    end = _num(item["end_at"]) / 1000.0
            elif "start" in item:
                start = _num(item["start"])
                end = _num(item.get("end", start))
                if start > 1000 and end > 1000:  # 밀리초로 보이면 초로
                    start, end = start / 1000.0, end / 1000.0
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            start = _num(item[0]) / 1000.0
            second = _num(item[1]) / 1000.0
            text = str(item[2])
            end = second if second >= start else start + second
        if text is None or start is None:
            return spread_words(msg, utt_start, utt_end)
        if end is None or end < start:
            end = start
        words.append(Word(str(text).strip(), round(start, 3), round(end, 3)))
    return [w for w in words if w.text]


def parse_rtzr_result(data: dict) -> Transcript:
    results = data.get("results") or {}
    utterances_raw = results.get("utterances") or data.get("utterances") or []
    utterances: list[Utterance] = []
    for u in utterances_raw:
        msg = str(u.get("msg") or u.get("text") or "").strip()
        start = _num(u.get("start_at")) / 1000.0
        end = start + _num(u.get("duration")) / 1000.0
        words = _parse_words(u.get("words"), start, end, msg)
        if not words:
            continue
        u_start = min(start, words[0].start)
        u_end = max(end, words[-1].end)
        utterances.append(Utterance(round(u_start, 3), round(u_end, 3), words))
    return Transcript(utterances, backend="rtzr")
