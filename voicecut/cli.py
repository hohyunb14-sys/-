"""명령줄 진입점: `voicecut serve` (웹 UI), `voicecut process 파일` (일괄 처리)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(Path(args.jobs_dir), port=args.port, open_browser=not args.no_open)
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    from . import pipeline

    jobs_dir = Path(args.jobs_dir)
    settings = pipeline.load_settings(jobs_dir)
    detect = dict(settings.get("detect", {}))
    if args.silence_min is not None:
        detect["silence_min"] = args.silence_min
    if args.silence_keep is not None:
        detect["silence_keep"] = args.silence_keep
    if args.markers is not None:
        detect["markers"] = args.markers
    try:
        d = pipeline.create_job(jobs_dir, local_path=args.input, backend=args.backend, ai_review=args.ai, detect=detect)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"작업 폴더: {d}")

    def log(text: str) -> None:
        if text.startswith("오류:") and not args.verbose:
            return  # 상세 traceback 은 --verbose 일 때만
        print("  " + text)

    pipeline.run_job(d, settings, log=log)
    state = pipeline.load_state(d)
    if state["status"] != "ready":
        print("실패:", state.get("error"), file=sys.stderr)
        return 1
    summary = state["summary"]
    print(f"원본 {_fmt(summary['duration'])} → 편집 후 {_fmt(summary['result_duration'])} "
          f"(컷 {summary['selected']}/{summary['count']}개 선택)")
    for kind, v in summary["by_kind"].items():
        print(f"  {kind:8s} {v['selected']:4d}/{v['count']:<4d} {v['seconds']:7.1f}초")
    if args.apply:
        print("렌더링 중…")
        out = pipeline.export_job(d, audio_only=args.audio_only)
        print(f"결과 파일: {out}")
    else:
        print(f"검토하려면 `voicecut serve` 를 실행하고 브라우저에서 이 작업을 여세요. 바로 렌더링하려면 --apply 를 붙이세요.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voicecut", description="내 목소리 녹음에서 무음·간투어·다시 말한 구간을 잘라 주는 도구")
    p.add_argument("--version", action="version", version=f"voicecut {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="브라우저 검토 화면 실행")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--jobs-dir", default="voicecut_jobs")
    s.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않음")
    s.set_defaults(func=cmd_serve)

    pr = sub.add_parser("process", help="파일 하나를 명령줄에서 분석(및 렌더링)")
    pr.add_argument("input")
    pr.add_argument("--jobs-dir", default="voicecut_jobs")
    pr.add_argument("--backend", default="auto", choices=["auto", "rtzr", "whisper"])
    pr.add_argument("--ai", action="store_true", help="Claude AI 검토도 실행")
    pr.add_argument("--apply", action="store_true", help="기본 선택된 컷을 바로 적용해 결과 파일 생성")
    pr.add_argument("--audio-only", action="store_true", help="영상 입력이라도 오디오만 출력")
    pr.add_argument("--silence-min", type=float)
    pr.add_argument("--silence-keep", type=float)
    pr.add_argument("--markers", help="쉼표로 구분한 마커 단어 (기본: 다시,컷,엔지)")
    pr.add_argument("--verbose", action="store_true", help="오류 시 상세 내용 출력")
    pr.set_defaults(func=cmd_process)

    args = p.parse_args(argv)
    if not args.cmd:
        args = p.parse_args(["serve"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
