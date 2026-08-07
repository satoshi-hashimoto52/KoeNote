"""BridgeLog の会議セッション管理エンドポイント。

会議フォルダの作成・確定と、保存先の書き込み可否チェックを提供する。
ネイティブ操作（ダイアログ・Finder・クリップボード・URL 起動）は Electron 側の責務。
"""
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import session_store

router = APIRouter(prefix="/api/session")


class CreateSessionRequest(BaseModel):
    title: str
    output_base: str
    gpt_url: str = ""
    attachments: List[str] = []
    create_base_if_missing: bool = True


class FinalizeSessionRequest(BaseModel):
    session_dir: str
    status: str = "done"
    ended_at: Optional[str] = None


class CheckOutputRequest(BaseModel):
    output_base: str


class UpdateAttachmentsRequest(BaseModel):
    session_dir: str
    attachments: List[str] = []


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
            attachments=payload.attachments,
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


@router.post("/attachments")
def update_attachments(payload: UpdateAttachmentsRequest):
    meeting_dir = Path(payload.session_dir)
    if not meeting_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_dir が見つかりません")
    try:
        session_store.write_attachments(meeting_dir, payload.attachments)
        session = session_store.read_session(payload.session_dir)
        session["attachments"] = list(payload.attachments)
        session_store.write_session(meeting_dir, session)
        return {"ok": True}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"attachments.json の更新に失敗しました: {exc}")
