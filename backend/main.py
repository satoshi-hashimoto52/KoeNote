"""KoeNote Backend (FastAPI).

Electron から子プロセスとして起動される想定。127.0.0.1:8765 で待ち受ける（既定値。Electron から --port で指定される）。
MyLauncher 固有のルーター（my_tool / sevenseg）や CLI 実行・HTML 配信は移植しない。
"""
import sys
from pathlib import Path

# backend/ をパッケージルートとして services / routes を import できるようにする。
BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.whisper import live_router as whisper_live_router
from routes.whisper import router as whisper_router
from routes.session import router as session_router

app = FastAPI(title="KoeNote API")

app.add_middleware(
    CORSMiddleware,
    # ローカルの Vite / Electron からのみアクセスされる想定。
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whisper_router)
app.include_router(whisper_live_router)
app.include_router(session_router)


@app.get("/api/health")
def health():
    # Backend プロセスから見た ffmpeg/ffprobe の解決状況も返し、
    # realtime デコード失敗（空文字化）の一次切り分けに使えるようにする。
    import shutil
    from services.transcriber import resolve_ffmpeg_dir

    ffmpeg_dir = resolve_ffmpeg_dir()
    if ffmpeg_dir is not None:
        ffmpeg = str(ffmpeg_dir / "ffmpeg") if (ffmpeg_dir / "ffmpeg").is_file() else shutil.which("ffmpeg")
        ffprobe = str(ffmpeg_dir / "ffprobe") if (ffmpeg_dir / "ffprobe").is_file() else shutil.which("ffprobe")
    else:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
    return {
        "status": "ok",
        "app": "KoeNote",
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "ffmpeg_ok": bool(ffmpeg and ffprobe),
    }
