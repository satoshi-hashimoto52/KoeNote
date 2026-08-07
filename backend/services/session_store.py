"""BridgeLog の会議セッション（フォルダ・メタデータ）管理。

会議ごとに専用ディレクトリ `YYYYMMDD_<safe_title>/` を作り、
session.json / attachments.json を原子的に書き込む。元資料は移動・削除しない。
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .file_utils import write_text_file

TRANSCRIPT_FILENAME = "transcript.txt"
SEGMENTS_FILENAME = "transcript_segments.json"
SESSION_FILENAME = "session.json"
ATTACHMENTS_FILENAME = "attachments.json"
DIAGNOSTICS_FILENAME = "diagnostics.log"

# ファイル名に使えない/避けたい文字を安全な文字へ置換する。
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_title(title: str, max_length: int = 80) -> str:
    """タイトルをフォルダ名に使える安全な文字列へ変換する（元文字列は保持しない）。"""
    text = (title or "").strip()
    if not text:
        return "untitled"
    text = _FORBIDDEN.sub("_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._")
    if not text:
        return "untitled"
    return text[:max_length]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def check_output_base(output_base: str) -> dict:
    """保存先の存在・書き込み可否・空き容量を確認する。"""
    if not (output_base or "").strip():
        return {"ok": False, "reason": "empty", "exists": False, "writable": False, "free_bytes": None}
    base = Path(output_base).expanduser()
    exists = base.is_dir()
    free_bytes = None
    writable = False
    if exists:
        try:
            free_bytes = shutil.disk_usage(base).free
        except OSError:
            free_bytes = None
        import os as _os
        writable = _os.access(str(base), _os.W_OK)
    else:
        # 親が書き込み可能なら作成できる余地がある。
        parent = base.parent
        if parent.is_dir():
            import os as _os
            writable = _os.access(str(parent), _os.W_OK)
    return {
        "ok": bool(exists and writable and (free_bytes is None or free_bytes > 200 * 1024 * 1024)),
        "exists": exists,
        "writable": writable,
        "free_bytes": free_bytes,
        "path": str(base),
    }


def create_meeting_directory(
    output_base: str,
    title: str,
    gpt_url: str = "",
    attachments: Optional[list[str]] = None,
    create_base_if_missing: bool = True,
) -> dict:
    """会議ディレクトリを作り、session.json / attachments.json を書き出す。"""
    attachments = attachments or []
    base = Path(output_base).expanduser()
    if not base.exists():
        if not create_base_if_missing:
            raise FileNotFoundError(f"保存先が存在しません: {base}")
        base.mkdir(parents=True, exist_ok=True)

    safe_title = sanitize_title(title)
    date_prefix = datetime.now().strftime("%Y%m%d")
    candidate_name = f"{date_prefix}_{safe_title}"
    suffix = 1
    while True:
        meeting_dir = base / candidate_name
        try:
            meeting_dir.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            suffix += 1
            candidate_name = f"{date_prefix}_{safe_title}_{suffix:02}"

    session = {
        "app": "BridgeLog",
        "title": title,  # 元のタイトル文字列を保持
        "safe_title": safe_title,
        "gpt_url": gpt_url,
        "status": "recording",
        "started_at": _now_iso(),
        "ended_at": None,
        "transcript_path": TRANSCRIPT_FILENAME,
        "segments_path": SEGMENTS_FILENAME,
        "attachments": list(attachments),
    }
    write_session(meeting_dir, session)
    write_attachments(meeting_dir, attachments)
    return {
        "session_dir": str(meeting_dir),
        "transcript_path": str(meeting_dir / TRANSCRIPT_FILENAME),
        "segments_path": str(meeting_dir / SEGMENTS_FILENAME),
        "session_json_path": str(meeting_dir / SESSION_FILENAME),
        "attachments_json_path": str(meeting_dir / ATTACHMENTS_FILENAME),
        "diagnostics_path": str(meeting_dir / DIAGNOSTICS_FILENAME),
        "transcript_filename": TRANSCRIPT_FILENAME,
        "session": session,
    }


def write_session(meeting_dir: Path, session: dict) -> None:
    write_text_file(Path(meeting_dir) / SESSION_FILENAME, json.dumps(session, ensure_ascii=False, indent=2) + "\n")


def write_attachments(meeting_dir: Path, attachments: list[str]) -> None:
    records = []
    for path in attachments:
        p = Path(path).expanduser()
        records.append({
            "path": str(p),
            "name": p.name,
            "exists": p.exists(),
        })
    payload = {"attachments": records, "updated_at": _now_iso()}
    write_text_file(Path(meeting_dir) / ATTACHMENTS_FILENAME, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_session(meeting_dir: str) -> dict:
    path = Path(meeting_dir) / SESSION_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


def finalize_session(meeting_dir: str, status: str = "done", ended_at: Optional[str] = None) -> dict:
    """録音停止時などに session.json のステータスと終了時刻を更新する。"""
    session = read_session(meeting_dir)
    session["status"] = status
    session["ended_at"] = ended_at or _now_iso()
    write_session(Path(meeting_dir), session)
    return session


def append_diagnostics(meeting_dir: str, message: str) -> None:
    path = Path(meeting_dir) / DIAGNOSTICS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"[{_now_iso()}] {message}\n")
