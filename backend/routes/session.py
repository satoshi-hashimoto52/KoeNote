"""KoeNote の会議セッション管理エンドポイント。

会議フォルダの作成・確定と、保存先の書き込み可否チェックを提供する。
ネイティブ操作（ダイアログ・Finder・クリップボード・URL 起動）は Electron 側の責務。
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import session_store
from services.wav_recorder import repair_wav_header

router = APIRouter(prefix="/api/session")


class CreateSessionRequest(BaseModel):
    title: str
    output_base: str
    gpt_url: str = ""
    create_base_if_missing: bool = True


class FinalizeSessionRequest(BaseModel):
    session_dir: str
    status: str = "done"
    ended_at: Optional[str] = None


class CheckOutputRequest(BaseModel):
    output_base: str


class DiagnosticsRequest(BaseModel):
    session_dir: str
    message: str


class RepairAudioRequest(BaseModel):
    session_dir: str


@router.post("/check_output")
def check_output(payload: CheckOutputRequest):
    return session_store.check_output_base(payload.output_base)


@router.post("/create")
def create(payload: CreateSessionRequest):
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="タイトルを入力してください")
    if not (payload.output_base or "").strip():
        raise HTTPException(status_code=400, detail="保存先を指定してください")
    try:
        return session_store.create_meeting_directory(
            output_base=payload.output_base,
            title=title,
            gpt_url=payload.gpt_url,
            create_base_if_missing=payload.create_base_if_missing,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"会議フォルダの作成に失敗しました: {exc}")


@router.post("/finalize")
def finalize(payload: FinalizeSessionRequest):
    if not Path(payload.session_dir).is_dir():
        raise HTTPException(status_code=404, detail="session_dir が見つかりません")
    try:
        return session_store.finalize_session(payload.session_dir, payload.status, payload.ended_at)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"session.json の更新に失敗しました: {exc}")


@router.post("/diagnostics")
def diagnostics(payload: DiagnosticsRequest):
    """異常停止の記録を diagnostics.log に残す（事後解析のための唯一の永続ログ）。"""
    meeting_dir = Path(payload.session_dir)
    if not meeting_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_dir が見つかりません")
    try:
        session_store.append_diagnostics(payload.session_dir, payload.message)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"diagnostics.log の書き込みに失敗しました: {exc}")
    return {"ok": True, "diagnostics_path": str(meeting_dir / session_store.DIAGNOSTICS_FILENAME)}


@router.post("/repair_audio")
def repair_audio(payload: RepairAudioRequest):
    """強制終了でヘッダが古くなった recording.wav を実ファイル長から復旧する。"""
    meeting_dir = Path(payload.session_dir)
    if not meeting_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_dir が見つかりません")
    audio_path = session_store.raw_audio_path(meeting_dir)
    if not audio_path.is_file():
        return {"ok": False, "reason": "not_found", "audio_path": str(audio_path), "seconds": 0.0}
    try:
        seconds = repair_wav_header(audio_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"録音ファイルの修復に失敗しました: {exc}")
    return {"ok": True, "audio_path": str(audio_path), "seconds": round(seconds, 2)}
