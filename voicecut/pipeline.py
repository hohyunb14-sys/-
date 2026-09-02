"""작업(job) 한 건의 처리 흐름과 상태 저장.

job 폴더 구성:
  job.json        상태/설정
  input.<ext>     원본(업로드한 경우) — 로컬 경로 모드에서는 job.json 의 input 이 절대 경로
  audio.wav       분석용 16kHz 모노
  preview.mp3     미리듣기·업로드용 저용량
  silences.json   무음 구간
  transcript.json 전사
  cuts.json       컷 목록(선택 상태 포함)
  <원본이름>_edited.<ext>  내보낸 결과
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import audio
from .detect import Cut, DetectConfig, detect_all
from .plan import cleaned_text, keep_segments, summarize
from .transcribe import TranscribeError, get_backend, rtzr_credentials
from .transcript import Transcript

JOB_FILE = "job.json"
SETTINGS_FILE = "settings.json"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

DEFAULT_SETTINGS: dict = {
    "rtzr_client_id": "",
    "rtzr_client_secret": "",
    "anthropic_api_key": "",
    "backend": "auto",
    "whisper_model": "large-v3-turbo",
    "ai_review": False,
    "ai_model": "claude-opus-5",
    "detect": DetectConfig().to_dict(),
}


# ------------------------------------------------------------------ 설정

def load_settings(jobs_dir: Path) -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    p = Path(jobs_dir) / SETTINGS_FILE
    if p.exists():
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k == "detect" and isinstance(v, dict):
                    s["detect"].update(v)
                else:
                    s[k] = v
        except (OSError, json.JSONDecodeError):
            pass
    return s


def save_settings(jobs_dir: Path, settings: dict) -> None:
    Path(jobs_dir).mkdir(parents=True, exist_ok=True)
    (Path(jobs_dir) / SETTINGS_FILE).write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")


def anthropic_key(settings: dict) -> str | None:
    return (os.environ.get("ANTHROPIC_API_KEY") or settings.get("anthropic_api_key") or "").strip() or None


def capabilities(settings: dict) -> dict:
    def installed(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False

    return {
        "rtzr": rtzr_credentials(settings) is not None,
        "anthropic_key": anthropic_key(settings) is not None,
        "anthropic_installed": installed("anthropic"),
        "whisper_installed": installed("faster_whisper"),
    }


# ------------------------------------------------------------------ 상태 파일

def _lock(job_dir: Path) -> threading.Lock:
    key = str(Path(job_dir).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def load_state(job_dir: Path) -> dict:
    p = Path(job_dir) / JOB_FILE
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(job_dir: Path, state: dict) -> None:
    p = Path(job_dir) / JOB_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def update_state(job_dir: Path, **changes) -> dict:
    with _lock(job_dir):
        state = load_state(job_dir)
        for k, v in changes.items():
            if isinstance(v, dict) and isinstance(state.get(k), dict) and k in ("export", "ai_review"):
                state[k].update(v)
            else:
                state[k] = v
        save_state(job_dir, state)
        return state


def load_cuts(job_dir: Path) -> list[Cut]:
    p = Path(job_dir) / "cuts.json"
    if not p.exists():
        return []
    return [Cut.from_dict(d) for d in json.loads(p.read_text(encoding="utf-8"))]


def save_cuts(job_dir: Path, cuts: list[Cut]) -> None:
    cuts = sorted(cuts, key=lambda c: (c.start, c.end))
    (Path(job_dir) / "cuts.json").write_text(
        json.dumps([c.to_dict() for c in cuts], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def input_path(job_dir: Path, state: dict) -> Path:
    p = Path(state["input"])
    return p if p.is_absolute() else Path(job_dir) / p


# ------------------------------------------------------------------ 작업 생성

_SAFE_RE = re.compile(r"[^\w가-힣.\-() ]+")


def safe_name(name: str) -> str:
    name = _SAFE_RE.sub("_", Path(name).name).strip() or "input"
    return name[:120]


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def create_job(jobs_dir: Path, *, upload: Optional[Path] = None, local_path: Optional[str] = None,
               original_name: str = "", backend: str = "auto", ai_review: bool = False,
               detect: dict | None = None) -> Path:
    """업로드 파일(이미 임시 저장됨)이나 로컬 경로로 작업 폴더를 만든다."""
    jobs_dir = Path(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_dir = jobs_dir / new_job_id()
    job_dir.mkdir()
    if upload is not None:
        name = safe_name(original_name or upload.name)
        dest = job_dir / ("input" + Path(name).suffix.lower())
        shutil.move(str(upload), dest)
        input_ref = dest.name
    elif local_path:
        src = Path(local_path).expanduser()
        if not src.is_file():
            shutil.rmtree(job_dir, ignore_errors=True)
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {local_path}")
        name = safe_name(src.name)
        input_ref = str(src.resolve())
    else:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise ValueError("파일이나 경로가 필요합니다.")
    state = {
        "id": job_dir.name,
        "filename": name,
        "input": input_ref,
        "created": time.time(),
        "status": "queued",
        "stage": "대기 중",
        "progress": None,
        "error": None,
        "duration": None,
        "has_video": False,
        "backend": backend,
        "settings": DetectConfig.from_dict(detect).to_dict(),
        "ai_review": {"enabled": bool(ai_review), "status": "idle", "error": None, "added": 0, "doubts": 0, "model": ""},
        "summary": None,
        "export": {"status": "idle", "progress": None, "file": None, "error": None, "audio_only": False},
    }
    save_state(job_dir, state)
    return job_dir


# ------------------------------------------------------------------ 처리

def run_job(job_dir: Path, app_settings: dict, log: Callable[[str], None] | None = None) -> None:
    """분석 파이프라인 전체. 예외는 상태에 기록하고 삼킨다."""
    job_dir = Path(job_dir)

    def stage(text: str, progress: float | None = None) -> None:
        update_state(job_dir, stage=text, progress=progress)
        if log:
            log(text)

    try:
        state = update_state(job_dir, status="processing", error=None)
        src = input_path(job_dir, state)
        stage("파일 정보 읽는 중")
        info = audio.probe(src)
        update_state(job_dir, duration=info.duration, has_video=info.has_video)

        wav = job_dir / "audio.wav"
        preview = job_dir / "preview.mp3"
        if not wav.exists():
            stage("오디오 추출 중")
            audio.extract_wav(src, wav)
        if not preview.exists():
            stage("미리듣기 파일 만드는 중")
            audio.make_preview(wav, preview)

        cfg = DetectConfig.from_dict(state.get("settings"))
        silences = _silences(job_dir, wav, cfg, info.duration, force=False)

        tpath = job_dir / "transcript.json"
        if tpath.exists():
            transcript = Transcript.load(tpath)
        else:
            backend = get_backend(state.get("backend") or app_settings.get("backend") or "auto", app_settings)
            stage(f"음성 인식 준비 중 ({backend.name})")
            source = str(preview if backend.name == "rtzr" else wav)
            try:
                transcript = backend.transcribe(source, keep_fillers=True, progress_cb=lambda t: stage(t))
            except TranscribeError as exc:
                if backend.name == "rtzr" and source != str(wav):
                    stage("mp3 업로드가 거절되어 wav로 다시 시도")
                    transcript = backend.transcribe(str(wav), keep_fillers=True, progress_cb=lambda t: stage(t))
                else:
                    raise
            transcript.save(tpath)
            update_state(job_dir, backend=transcript.backend)
        if not transcript.words:
            raise RuntimeError("음성에서 말을 찾지 못했습니다. 녹음 파일과 STT 설정을 확인하세요.")

        stage("컷 후보 찾는 중")
        cuts = detect_all(transcript, silences, cfg, info.duration)
        save_cuts(job_dir, cuts)
        update_state(job_dir, summary=summarize(cuts, info.duration))

        if state.get("ai_review", {}).get("enabled"):
            run_ai_review(job_dir, app_settings, log=log)

        cuts = load_cuts(job_dir)
        update_state(job_dir, status="ready", stage="완료", progress=None, summary=summarize(cuts, info.duration))
    except Exception as exc:  # noqa: BLE001 - 작업 상태로 보고
        update_state(job_dir, status="error", stage="오류", error=f"{exc}", progress=None)
        if log:
            log("오류: " + traceback.format_exc())


def _silences(job_dir: Path, wav: Path, cfg: DetectConfig, duration: float, force: bool) -> list[tuple[float, float]]:
    p = job_dir / "silences.json"
    key = {"noise_db": cfg.silence_noise_db, "min": round(min(cfg.silence_min, 0.5), 3)}
    if p.exists() and not force:
        cached = json.loads(p.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return [tuple(x) for x in cached["silences"]]
    sil = audio.detect_silences(wav, noise_db=cfg.silence_noise_db, min_duration=key["min"], total_duration=duration)
    p.write_text(json.dumps({"key": key, "silences": sil}), encoding="utf-8")
    return sil


def run_ai_review(job_dir: Path, app_settings: dict, log: Callable[[str], None] | None = None) -> None:
    job_dir = Path(job_dir)
    update_state(job_dir, ai_review={"status": "running", "error": None}, stage="AI 검토 중")
    try:
        from . import ai_review as ai

        transcript = Transcript.load(job_dir / "transcript.json")
        cuts = [c for c in load_cuts(job_dir) if c.source != "ai"]
        result = ai.review(transcript, cuts, api_key=anthropic_key(app_settings),
                           model=app_settings.get("ai_model") or ai.DEFAULT_MODEL)
        for c in cuts:
            if c.id in result.doubts:
                c.selected = False
                c.reason = f"{c.reason} · AI 의견: {result.doubts[c.id]}"
        cuts += result.cuts
        save_cuts(job_dir, cuts)
        state = load_state(job_dir)
        update_state(
            job_dir,
            ai_review={"status": "done", "added": len(result.cuts), "doubts": len(result.doubts),
                       "skipped": result.skipped, "model": result.model, "error": None},
            summary=summarize(cuts, state.get("duration") or 0.0),
        )
    except Exception as exc:  # noqa: BLE001
        update_state(job_dir, ai_review={"status": "error", "error": f"{exc}"})
        if log:
            log("AI 검토 오류: " + traceback.format_exc())


def redetect(job_dir: Path, detect: dict | None) -> list[Cut]:
    """저장된 전사로 컷을 다시 찾는다. 같은 id의 선택 상태와 사용자/AI 컷은 유지."""
    job_dir = Path(job_dir)
    state = load_state(job_dir)
    cfg = DetectConfig.from_dict({**state.get("settings", {}), **(detect or {})})
    transcript = Transcript.load(job_dir / "transcript.json")
    silences = _silences(job_dir, job_dir / "audio.wav", cfg, state.get("duration") or 0.0, force=False)
    old = load_cuts(job_dir)
    old_sel = {c.id: c.selected for c in old}
    keep = [c for c in old if c.source in ("user", "ai")]
    new = detect_all(transcript, silences, cfg, state.get("duration"))
    for c in new:
        if c.id in old_sel:
            c.selected = old_sel[c.id]
    cuts = new + keep
    save_cuts(job_dir, cuts)
    update_state(job_dir, settings=cfg.to_dict(), summary=summarize(cuts, state.get("duration") or 0.0))
    return sorted(cuts, key=lambda c: (c.start, c.end))


def set_cuts(job_dir: Path, cut_dicts: list[dict]) -> dict:
    job_dir = Path(job_dir)
    cuts = [Cut.from_dict(d) for d in cut_dicts]
    save_cuts(job_dir, cuts)
    state = load_state(job_dir)
    summary = summarize(cuts, state.get("duration") or 0.0)
    update_state(job_dir, summary=summary)
    return summary


def export_job(job_dir: Path, audio_only: bool = False) -> Path:
    job_dir = Path(job_dir)
    state = load_state(job_dir)
    src = input_path(job_dir, state)
    cuts = load_cuts(job_dir)
    duration = float(state.get("duration") or audio.probe(src).duration)
    segments = keep_segments(cuts, duration)
    ext = audio.output_extension(src, bool(state.get("has_video")), audio_only)
    out_name = f"{Path(state['filename']).stem}_edited{ext}"
    dst = job_dir / out_name
    update_state(job_dir, export={"status": "running", "progress": 0.0, "file": None, "error": None, "audio_only": audio_only})
    try:
        audio.render(
            src, dst, segments, has_video=bool(state.get("has_video")),
            options=audio.RenderOptions(audio_only=audio_only),
            progress_cb=lambda f: update_state(job_dir, export={"progress": round(f, 3)}),
        )
        (job_dir / "transcript_edited.txt").write_text(
            cleaned_text(Transcript.load(job_dir / "transcript.json"), cuts), encoding="utf-8"
        )
        update_state(job_dir, export={"status": "done", "progress": 1.0, "file": out_name, "error": None})
    except Exception as exc:  # noqa: BLE001
        update_state(job_dir, export={"status": "error", "error": f"{exc}"})
        raise
    return dst


def job_view(job_dir: Path, include_data: bool = True) -> dict:
    job_dir = Path(job_dir)
    state = load_state(job_dir)
    if include_data and state.get("status") == "ready":
        tpath = job_dir / "transcript.json"
        state["transcript"] = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else None
        state["cuts"] = [c.to_dict() for c in load_cuts(job_dir)]
    return state


def list_jobs(jobs_dir: Path) -> list[dict]:
    out = []
    jobs_dir = Path(jobs_dir)
    if not jobs_dir.exists():
        return out
    for d in sorted(jobs_dir.iterdir(), reverse=True):
        if not (d / JOB_FILE).exists():
            continue
        try:
            s = load_state(d)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: s.get(k) for k in ("id", "filename", "created", "status", "stage", "duration", "summary", "export")})
    return out
