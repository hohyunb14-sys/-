"""ffmpeg 기반 오디오/비디오 처리: 정보 조회, 오디오 추출, 무음 탐지, 컷 적용 렌더링."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

ProgressCb = Optional[Callable[[float], None]]

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SIL_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SIL_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_PROGRESS_RE = re.compile(r"^out_time_(?:us|ms)=(\d+)")

# 출력 확장자별 오디오 코덱 인자
AUDIO_CODECS: dict[str, list[str]] = {
    ".wav": ["-c:a", "pcm_s16le"],
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".aac": ["-c:a", "aac", "-b:a", "192k"],
    ".flac": ["-c:a", "flac"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
}


def find_ffmpeg() -> str:
    """ffmpeg 실행 파일 경로. 환경변수 → PATH → imageio-ffmpeg 내장 바이너리 순으로 찾는다."""
    env = os.environ.get("VOICECUT_FFMPEG")
    if env and Path(env).exists():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "ffmpeg를 찾을 수 없습니다. `pip install imageio-ffmpeg`를 실행하거나 ffmpeg를 설치하세요."
        ) from exc


def _run(args: Sequence[str], *, ok_codes: Sequence[int] = (0,)) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode not in ok_codes:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg 실행 실패 (code {proc.returncode}):\n{tail}")
    return proc


@dataclass
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool


def probe(path: str | Path) -> MediaInfo:
    """길이(초)와 영상/음성 스트림 유무. `ffmpeg -i`의 stderr를 읽는다 (ffprobe 불필요)."""
    proc = _run([find_ffmpeg(), "-hide_banner", "-i", str(path)], ok_codes=(0, 1))
    err = proc.stderr
    m = _DURATION_RE.search(err)
    if not m:
        tail = "\n".join(err.strip().splitlines()[-4:])
        raise RuntimeError(f"미디어 파일을 읽을 수 없습니다: {path}\n{tail}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    stream_lines = [line for line in err.splitlines() if "Stream #" in line]
    has_video = any("Video:" in line and "attached pic" not in line for line in stream_lines)
    has_audio = any("Audio:" in line for line in stream_lines)
    if not has_audio:
        raise RuntimeError("이 파일에는 음성 트랙이 없습니다.")
    return MediaInfo(duration=round(duration, 3), has_video=has_video, has_audio=has_audio)


def extract_wav(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    """분석용 16kHz 모노 WAV 추출."""
    _run([
        find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst),
    ])
    return Path(dst)


def make_preview(src: str | Path, dst: str | Path, bitrate: str = "64k") -> Path:
    """브라우저 미리듣기 및 STT 업로드용 저용량 mp3 (모든 브라우저가 재생할 수 있는 형식)."""
    _run([
        find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", bitrate, str(dst),
    ])
    return Path(dst)


def detect_silences(
    wav: str | Path, noise_db: float = -35.0, min_duration: float = 0.5, total_duration: float | None = None
) -> list[tuple[float, float]]:
    """ffmpeg silencedetect로 (시작, 끝) 무음 구간 목록을 얻는다."""
    proc = _run([
        find_ffmpeg(), "-hide_banner", "-nostats", "-i", str(wav),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-",
    ])
    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in proc.stderr.splitlines():
        ms = _SIL_START_RE.search(line)
        if ms:
            pending = max(0.0, float(ms.group(1)))
            continue
        me = _SIL_END_RE.search(line)
        if me and pending is not None:
            silences.append((round(pending, 3), round(float(me.group(1)), 3)))
            pending = None
    if pending is not None:
        end = total_duration if total_duration is not None else pending
        if end > pending:
            silences.append((round(pending, 3), round(end, 3)))
    return silences


def output_extension(input_path: str | Path, has_video: bool, audio_only: bool) -> str:
    if has_video and not audio_only:
        return ".mp4"
    if has_video and audio_only:
        return ".m4a"
    ext = Path(input_path).suffix.lower()
    return ext if ext in AUDIO_CODECS else ".m4a"


def build_filter_script(segments: Sequence[tuple[float, float]], with_video: bool, fade: float = 0.008) -> str:
    """남길 구간들을 trim/atrim + concat 으로 잇는 filter_complex 스크립트."""
    parts: list[str] = []
    labels: list[str] = []
    for i, (s, e) in enumerate(segments):
        dur = e - s
        fade_d = min(fade, dur / 2) if fade > 0 else 0.0
        chain = f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS"
        if fade_d > 0:
            chain += f",afade=t=in:st=0:d={fade_d:.3f},afade=t=out:st={max(dur - fade_d, 0):.3f}:d={fade_d:.3f}"
        parts.append(chain + f"[a{i}]")
        if with_video:
            parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
            labels.append(f"[v{i}][a{i}]")
        else:
            labels.append(f"[a{i}]")
    n = len(segments)
    if with_video:
        parts.append("".join(labels) + f"concat=n={n}:v=1:a=1[outv][outa]")
    else:
        parts.append("".join(labels) + f"concat=n={n}:v=0:a=1[outa]")
    return ";".join(parts)


@dataclass
class RenderOptions:
    audio_only: bool = False
    fade: float = 0.008
    video_crf: int = 20
    video_preset: str = "veryfast"
    audio_bitrate: str = "192k"


def render(
    src: str | Path,
    dst: str | Path,
    keep_segments: Sequence[tuple[float, float]],
    *,
    has_video: bool,
    options: RenderOptions | None = None,
    progress_cb: ProgressCb = None,
) -> Path:
    """남길 구간만 이어 붙여 dst로 인코딩한다. 영상은 mp4(H.264/AAC), 음성은 확장자에 맞는 코덱."""
    options = options or RenderOptions()
    if not keep_segments:
        raise ValueError("남길 구간이 없습니다. 컷 선택을 확인하세요.")
    with_video = has_video and not options.audio_only
    script = build_filter_script(keep_segments, with_video, options.fade)
    fd, script_path = tempfile.mkstemp(suffix=".txt", prefix="voicecut_filter_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(script)
    dst = Path(dst)
    try:
        args = [
            find_ffmpeg(), "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1",
            "-i", str(src), "-filter_complex_script", script_path,
        ]
        if with_video:
            args += [
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", options.video_preset, "-crf", str(options.video_crf), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", options.audio_bitrate, "-movflags", "+faststart",
            ]
        else:
            codec = AUDIO_CODECS.get(dst.suffix.lower(), AUDIO_CODECS[".m4a"])
            args += ["-map", "[outa]", "-vn"] + codec
            if dst.suffix.lower() in (".m4a", ".aac"):
                args += ["-movflags", "+faststart"]
        args.append(str(dst))
        expected = sum(e - s for s, e in keep_segments) or 1.0
        _run_with_progress(args, expected, progress_cb)
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass
    return dst


def _run_with_progress(args: list[str], expected_seconds: float, progress_cb: ProgressCb) -> None:
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    stderr_lines: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        m = _PROGRESS_RE.match(line.strip())
        if m and progress_cb:
            done = int(m.group(1)) / 1_000_000.0  # out_time_us/out_time_ms 모두 마이크로초 단위
            progress_cb(max(0.0, min(1.0, done / expected_seconds)))
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        tail = "".join(stderr_lines[-8:]).strip()
        raise RuntimeError(f"ffmpeg 렌더링 실패 (code {proc.returncode}):\n{tail}")
    if progress_cb:
        progress_cb(1.0)
