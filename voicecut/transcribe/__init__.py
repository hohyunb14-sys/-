"""STT 백엔드 선택. 리턴제로(RTZR) API 키가 있으면 그것을, 없으면 로컬 Whisper를 쓴다."""
from __future__ import annotations

import os
from typing import Callable, Optional, Protocol

from ..transcript import Transcript

ProgressCb = Optional[Callable[[str], None]]


class TranscribeError(RuntimeError):
    pass


class Backend(Protocol):
    name: str

    def transcribe(self, audio_path: str, keep_fillers: bool = True, progress_cb: ProgressCb = None) -> Transcript: ...


def rtzr_credentials(settings: dict | None = None) -> tuple[str, str] | None:
    settings = settings or {}
    cid = os.environ.get("RTZR_CLIENT_ID") or settings.get("rtzr_client_id") or ""
    sec = os.environ.get("RTZR_CLIENT_SECRET") or settings.get("rtzr_client_secret") or ""
    return (cid.strip(), sec.strip()) if cid.strip() and sec.strip() else None


def get_backend(name: str = "auto", settings: dict | None = None) -> Backend:
    settings = settings or {}
    name = (name or "auto").lower()
    creds = rtzr_credentials(settings)
    if name == "auto":
        name = "rtzr" if creds else "whisper"
    if name == "rtzr":
        if not creds:
            raise TranscribeError("리턴제로 API 키가 없습니다. 설정에서 client_id / client_secret을 넣어 주세요.")
        from .rtzr import RTZRBackend

        return RTZRBackend(creds[0], creds[1])
    if name == "whisper":
        from .whisper import WhisperBackend

        return WhisperBackend(
            model_size=settings.get("whisper_model") or "large-v3-turbo",
            device=settings.get("whisper_device") or "auto",
            compute_type=settings.get("whisper_compute_type") or "auto",
        )
    raise TranscribeError(f"알 수 없는 STT 백엔드: {name}")
