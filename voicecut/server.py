"""내 컴퓨터에서만 접속하는 로컬 웹 앱 (127.0.0.1)."""
from __future__ import annotations

import json
import mimetypes
import re
import shutil
import tempfile
import threading
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from . import pipeline
from .detect import DetectConfig

STATIC_DIR = Path(__file__).parent / "static"
_JOB_ID_RE = re.compile(r"^[\w\-]+$")
MASK = "••••••••"


def _page(name: str) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / name).read_text(encoding="utf-8"))


def create_app(jobs_dir: Path) -> FastAPI:
    jobs_dir = Path(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="voicecut", docs_url=None, redoc_url=None)
    running: set[str] = set()
    running_lock = threading.Lock()

    def job_dir(job_id: str) -> Path:
        if not _JOB_ID_RE.match(job_id):
            raise HTTPException(400, "잘못된 작업 id")
        d = jobs_dir / job_id
        if not (d / pipeline.JOB_FILE).exists():
            raise HTTPException(404, "작업을 찾을 수 없습니다")
        return d

    def start_thread(job_id: str, target, *args) -> bool:
        with running_lock:
            if job_id in running:
                return False
            running.add(job_id)

        def wrapper():
            try:
                target(*args)
            finally:
                with running_lock:
                    running.discard(job_id)

        threading.Thread(target=wrapper, daemon=True).start()
        return True

    # ------------------------------------------------------------ 페이지
    @app.get("/", response_class=HTMLResponse)
    def index():
        return _page("index.html")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page():
        return _page("settings.html")

    @app.get("/job/{job_id}", response_class=HTMLResponse)
    def job_page(job_id: str):
        job_dir(job_id)
        return _page("review.html")

    # ------------------------------------------------------------ 설정
    @app.get("/api/settings")
    def get_settings():
        s = pipeline.load_settings(jobs_dir)
        out = dict(s)
        for k in ("rtzr_client_secret", "anthropic_api_key"):
            out[k] = MASK if s.get(k) else ""
        out["capabilities"] = pipeline.capabilities(s)
        out["jobs_dir"] = str(jobs_dir.resolve())
        return out

    @app.post("/api/settings")
    async def post_settings(request: Request):
        body = await request.json()
        s = pipeline.load_settings(jobs_dir)
        for k in ("rtzr_client_id", "rtzr_client_secret", "anthropic_api_key", "backend", "whisper_model", "ai_model"):
            if k in body:
                v = str(body[k] or "").strip()
                if v == MASK:
                    continue
                s[k] = v
        if "ai_review" in body:
            s["ai_review"] = bool(body["ai_review"])
        if isinstance(body.get("detect"), dict):
            s["detect"] = DetectConfig.from_dict({**s["detect"], **body["detect"]}).to_dict()
        pipeline.save_settings(jobs_dir, s)
        return {"ok": True, "capabilities": pipeline.capabilities(s)}

    # ------------------------------------------------------------ 작업
    @app.get("/api/jobs")
    def jobs():
        return pipeline.list_jobs(jobs_dir)

    @app.post("/api/jobs")
    async def create(
        file: Optional[UploadFile] = File(None),
        path: Optional[str] = Form(None),
        backend: str = Form("auto"),
        ai_review: str = Form("false"),
        detect: Optional[str] = Form(None),
    ):
        s = pipeline.load_settings(jobs_dir)
        detect_cfg = dict(s.get("detect", {}))
        if detect:
            try:
                detect_cfg.update(json.loads(detect))
            except json.JSONDecodeError:
                raise HTTPException(400, "detect 설정이 올바른 JSON이 아닙니다")
        want_ai = str(ai_review).lower() in ("1", "true", "on", "yes")
        try:
            if file is not None and file.filename:
                tmp_fd, tmp_name = tempfile.mkstemp(prefix="voicecut_upload_", dir=str(jobs_dir))
                with open(tmp_fd, "wb") as out:
                    shutil.copyfileobj(file.file, out, length=1024 * 1024)
                d = pipeline.create_job(jobs_dir, upload=Path(tmp_name), original_name=file.filename,
                                        backend=backend, ai_review=want_ai, detect=detect_cfg)
            elif path:
                d = pipeline.create_job(jobs_dir, local_path=path, backend=backend, ai_review=want_ai, detect=detect_cfg)
            else:
                raise HTTPException(400, "파일을 올리거나 경로를 입력하세요")
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc))
        start_thread(d.name, pipeline.run_job, d, s)
        return {"id": d.name}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, light: int = 0):
        return pipeline.job_view(job_dir(job_id), include_data=not light)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        d = job_dir(job_id)
        with running_lock:
            if job_id in running:
                raise HTTPException(409, "처리 중인 작업은 지울 수 없습니다")
        shutil.rmtree(d, ignore_errors=True)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        d = job_dir(job_id)
        s = pipeline.load_settings(jobs_dir)
        pipeline.update_state(d, status="queued", error=None, stage="대기 중")
        if not start_thread(job_id, pipeline.run_job, d, s):
            raise HTTPException(409, "이미 처리 중입니다")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/audio")
    def audio_file(job_id: str, request: Request):
        d = job_dir(job_id)
        p = d / "preview.mp3"
        if not p.exists():
            raise HTTPException(404, "미리듣기 파일이 아직 없습니다")
        return ranged_file(p, request, "audio/mpeg")

    @app.post("/api/jobs/{job_id}/cuts")
    async def put_cuts(job_id: str, request: Request):
        d = job_dir(job_id)
        body = await request.json()
        cuts = body.get("cuts")
        if not isinstance(cuts, list):
            raise HTTPException(400, "cuts 목록이 필요합니다")
        return {"ok": True, "summary": pipeline.set_cuts(d, cuts)}

    @app.post("/api/jobs/{job_id}/redetect")
    async def redetect(job_id: str, request: Request):
        d = job_dir(job_id)
        body = await request.json()
        cuts = pipeline.redetect(d, body.get("detect") or {})
        state = pipeline.load_state(d)
        return {"ok": True, "cuts": [c.to_dict() for c in cuts], "summary": state.get("summary"), "settings": state.get("settings")}

    @app.post("/api/jobs/{job_id}/ai-review")
    def ai_review(job_id: str):
        d = job_dir(job_id)
        s = pipeline.load_settings(jobs_dir)
        caps = pipeline.capabilities(s)
        if not caps["anthropic_installed"]:
            raise HTTPException(400, "anthropic 패키지가 설치되어 있지 않습니다 (pip install anthropic)")
        if not caps["anthropic_key"]:
            raise HTTPException(400, "설정에서 Anthropic API 키를 먼저 넣어 주세요")
        if not start_thread(job_id, pipeline.run_ai_review, d, s):
            raise HTTPException(409, "이미 처리 중입니다")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/export")
    async def export(job_id: str, request: Request):
        d = job_dir(job_id)
        body = await request.json() if int(request.headers.get("content-length", "0") or 0) > 0 else {}
        audio_only = bool(body.get("audio_only", False))
        if not start_thread(job_id, _safe_export, d, audio_only):
            raise HTTPException(409, "이미 처리 중입니다")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str, request: Request):
        d = job_dir(job_id)
        state = pipeline.load_state(d)
        name = (state.get("export") or {}).get("file")
        if not name or not (d / name).exists():
            raise HTTPException(404, "내보낸 파일이 없습니다")
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        resp = ranged_file(d / name, request, media)
        resp.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + urllib.parse.quote(name)
        return resp

    @app.get("/api/jobs/{job_id}/transcript.txt")
    def transcript_txt(job_id: str):
        d = job_dir(job_id)
        from .plan import cleaned_text
        from .transcript import Transcript

        tpath = d / "transcript.json"
        if not tpath.exists():
            raise HTTPException(404, "전사가 아직 없습니다")
        text = cleaned_text(Transcript.load(tpath), pipeline.load_cuts(d))
        return PlainTextResponse(text, headers={"Content-Disposition": "attachment; filename*=UTF-8''transcript_edited.txt"})

    return app


def _safe_export(d: Path, audio_only: bool) -> None:
    try:
        pipeline.export_job(d, audio_only=audio_only)
    except Exception:  # 상태에 이미 기록됨
        pass


def ranged_file(path: Path, request: Request, media_type: str) -> Response:
    """브라우저 탐색(seek)을 위해 Range 요청을 처리한다."""
    size = path.stat().st_size
    rng = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    if rng and rng.startswith("bytes="):
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    length = end - start + 1

    def stream():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), status_code=status, media_type=media_type, headers=headers)


def serve(jobs_dir: Path, port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    app = create_app(jobs_dir)
    url = f"http://127.0.0.1:{port}/"
    print(f"voicecut 실행 중: {url}  (작업 폴더: {Path(jobs_dir).resolve()})  종료: Ctrl+C")
    if open_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
