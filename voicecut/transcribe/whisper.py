"""로컬 Whisper(faster-whisper) 백엔드. 인터넷 없이 동작하지만 GPU가 없으면 느리고, 간투어를 종종 빼먹는다."""
from __future__ import annotations

from typing import Any, Iterable

from ..transcript import Transcript, Utterance, Word, spread_words
from . import ProgressCb, TranscribeError

# 간투어를 받아쓰도록 유도하는 힌트 문장 (Whisper는 깨끗한 문장으로 정리하려는 경향이 있다)
FILLER_PROMPT = "음... 어... 그... 네, 오늘은 장례 절차에 대해서 어... 말씀드리겠습니다. 음, 다시."


class WhisperBackend:
    name = "whisper"

    def __init__(self, model_size: str = "large-v3-turbo", device: str = "auto", compute_type: str = "auto"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: Any = None

    def _load(self, progress_cb: ProgressCb):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise TranscribeError(
                "faster-whisper가 설치되어 있지 않습니다. `pip install faster-whisper` 를 실행하거나 리턴제로 API 키를 설정하세요."
            ) from exc
        if progress_cb:
            progress_cb(f"Whisper 모델 준비 중 ({self.model_size}) — 처음 한 번은 모델을 내려받습니다")
        self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio_path: str, keep_fillers: bool = True, progress_cb: ProgressCb = None) -> Transcript:
        model = self._load(progress_cb)
        segments, info = model.transcribe(
            audio_path,
            language="ko",
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=FILLER_PROMPT if keep_fillers else None,
            condition_on_previous_text=False,
        )
        total = float(getattr(info, "duration", 0.0) or 0.0)

        def wrapped() -> Iterable[Any]:
            for seg in segments:
                if progress_cb and total > 0:
                    progress_cb(f"Whisper 음성 인식 중 {min(100, int(100 * seg.end / total))}%")
                yield seg

        return segments_to_transcript(wrapped())


def segments_to_transcript(segments: Iterable[Any]) -> Transcript:
    """faster-whisper 세그먼트(.start/.end/.text/.words[.word/.start/.end])를 공통 형식으로."""
    utterances: list[Utterance] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        words: list[Word] = []
        for w in (getattr(seg, "words", None) or []):
            t = (w.word or "").strip()
            if t:
                words.append(Word(t, round(float(w.start), 3), round(float(w.end), 3)))
        if not words:
            words = spread_words(text, float(seg.start), float(seg.end))
        if words:
            utterances.append(Utterance(round(float(seg.start), 3), round(float(seg.end), 3), words))
    return Transcript(utterances, backend="whisper")
