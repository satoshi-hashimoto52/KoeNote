import asyncio
import json
import os
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional

from services.live_session import LiveSession, LiveSessionConfig
from services.transcriber import ALLOWED_EXTENSIONS, TranscriptionStageError, run_transcribe, save_upload_file

router = APIRouter(prefix="/api/whisper")
live_router = APIRouter()

_jobs = {}
_jobs_lock = threading.Lock()
JOB_RETENTION_SECONDS = 6 * 60 * 60
TERMINAL_STATUSES = {"done", "error", "cancelled"}
ALLOWED_STATUS_TRANSITIONS = {
    "queued": {"running"},
    "running": TERMINAL_STATUSES,
    "done": set(),
    "error": set(),
    "cancelled": set(),
}


class TranscribeRequest(BaseModel):
    input_path: str
    output_folder: Optional[str] = None
    decode_mode: str = "speed"
    write_to_file: bool = True


def _set_job_status(job_id, status):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            current_status = job["status"]
            if status == current_status:
                return True
            if status not in ALLOWED_STATUS_TRANSITIONS.get(current_status, set()):
                job["log"] += f"[warning] 不許可の状態遷移を無視しました: {current_status} -> {status}\n"
                job["updated_at"] = time.time()
                job["last_heartbeat_at"] = job["updated_at"]
                return False
            job["status"] = status
            job["updated_at"] = time.time()
            return True
    return False


def _heartbeat(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["last_heartbeat_at"] = time.time()
            job["worker_heartbeat_at"] = job["last_heartbeat_at"]
            job["updated_at"] = time.time()


def _append_job_log(job_id, message):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["log"] += message
            job["updated_at"] = time.time()
            job["last_heartbeat_at"] = time.time()


def _set_job_process(job_id, process):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            previous_process = job.get("process")
            job["process"] = process
            if process is not None:
                job["worker_pid"] = process.pid
                job["process_returncode"] = None
                job["returncode"] = None
            elif previous_process is not None:
                try:
                    process_returncode = previous_process.poll()
                except Exception:
                    process_returncode = None
                if process_returncode is not None:
                    job["process_returncode"] = process_returncode
                    job["returncode"] = process_returncode
            job["updated_at"] = time.time()


def _is_cancel_requested(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _diagnostic_log(job_id, event):
    event = dict(event)
    event["cancel_requested"] = _is_cancel_requested(job_id)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            for source, target in (
                ("pid", "worker_pid"),
                ("returncode", "process_returncode"),
                ("current_position", "current_position"),
                ("peak_rss_mb", "peak_rss_mb"),
                ("segment", "segment"),
                ("total_segments", "total_segments"),
                ("stderr_tail", "stderr_tail"),
            ):
                if source in event:
                    job[target] = event[source]
            if event.get("event") == "stage_started":
                job["stage"] = event.get("stage")
                job["stage_started_at"] = event.get("started_at")
            elif event.get("event") == "stage_finished":
                job["stage"] = event.get("stage")
            if event.get("event") == "progress":
                job["progress_updated_at"] = time.time()
            if event.get("event") == "child_process_started":
                job["child_pid"] = event.get("pid")
                job["child_started_at"] = time.time()
            # Workerから届いた診断イベントの受信時刻を、APIで使う数値のハートビートにする。
            job["last_heartbeat_at"] = time.time()
            job["worker_heartbeat_at"] = job["last_heartbeat_at"]
            if event.get("stderr_tail") or event.get("event") in {"segment_ffmpeg", "process_finished"}:
                job["child_output_at"] = job["last_heartbeat_at"]
            job["updated_at"] = time.time()
    labels = {
        "start": "開始",
        "process_started": "Worker開始",
        "progress": "進捗",
        "process_finished": "Worker終了",
    }
    parts = [f"[diagnostic] {labels.get(event.get('event'), event.get('event', 'unknown'))}"]
    for key in (
        "job_id", "input_path", "file_size_bytes", "audio_duration_seconds", "model", "pid",
        "command", "started_at", "current_position", "updated_at", "returncode", "ended_at",
        "cancel_requested", "peak_rss_mb", "message", "stage", "elapsed", "finished_at",
    ):
        if key in event:
            parts.append(f"{key}={event[key]}")
    if event.get("stderr_tail"):
        parts.append(f"stderr_tail={event['stderr_tail'][-4000:]}")
    _append_job_log(job_id, " ".join(parts) + "\n")


def _set_job_result(job_id, result):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["result"] = result
            job["updated_at"] = time.time()


def _run_job(job_id, fn):
    _set_job_status(job_id, "running")
    try:
        result = fn(job_id)
        _set_job_result(job_id, result)
        _append_job_log(job_id, "[終了理由] completed\n")
        _set_job_status(job_id, "done")
    except Exception as exc:
        if _worker_is_alive(job_id):
            _append_job_log(job_id, f"[warning] 監視処理の一時失敗: {exc}\n")
            _wait_for_worker(job_id)
            returncode = _worker_returncode(job_id)
            if returncode in (None, 0):
                _set_job_status(job_id, "done")
                return
        reason = "accidental_cancel" if _is_cancel_requested(job_id) else _classify_failure(exc)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job["exit_reason"] = reason
                job["error"] = str(exc)
                job["error_type"] = type(exc).__name__
                if isinstance(exc, TranscriptionStageError):
                    job["stage"] = exc.stage
        if isinstance(exc, TranscriptionStageError):
            segment = f"\n[segment_index] {exc.segment_index}" if exc.segment_index is not None else ""
            _append_job_log(
                job_id,
                f"\n[終了理由] {reason}\n[工程] {exc.stage}{segment}\n"
                f"[例外] {type(exc.original).__name__}\n[error] {exc.original}\n[再試行] しません\n",
            )
        else:
            _append_job_log(job_id, f"\n[終了理由] {reason}\n[error] {exc}\n")
        _set_job_status(job_id, "cancelled" if _is_cancel_requested(job_id) else "error")
    finally:
        # 監視側の例外でWorkerが生きたまま参照を失うと、状態APIが実態を確認できない。
        # Worker終了後だけ参照を解放し、terminal状態と生存中Workerを同時に作らない。
        if not _worker_is_alive(job_id):
            _set_job_process(job_id, None)


def _worker_is_alive(job_id):
    with _jobs_lock:
        process = _jobs.get(job_id, {}).get("process")
    if process is None:
        return False
    try:
        return process.poll() is None
    except Exception as exc:
        _append_job_log(job_id, f"[warning] Worker状態取得失敗: {exc}\n")
        return True


def _worker_returncode(job_id):
    with _jobs_lock:
        process = _jobs.get(job_id, {}).get("process")
        returncode = _jobs.get(job_id, {}).get("process_returncode")
    if returncode is not None:
        return returncode
    if process is None:
        return None
    try:
        return process.poll()
    except Exception as exc:
        _append_job_log(job_id, f"[warning] Worker終了状態取得失敗: {exc}\n")
        return None


def _wait_for_worker(job_id):
    while _worker_is_alive(job_id):
        time.sleep(0.2)


def _start_job(fn):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        now = time.time()
        for old_job_id, old_job in list(_jobs.items()):
            if old_job.get("status") in {"done", "error", "cancelled"} and now - old_job.get("updated_at", now) >= JOB_RETENTION_SECONDS:
                _jobs.pop(old_job_id, None)
        _jobs[job_id] = {
            "status": "queued",
            "log": "",
            "result": None,
            "error": None,
            "error_type": None,
            "exit_reason": None,
            "stage": None,
            "stderr_tail": None,
            "created_at": time.time(),
            "updated_at": now,
            "last_heartbeat_at": now,
            "process": None,
            "worker_pid": None,
            "process_returncode": None,
            "returncode": None,
            "current_position": "起動待ち",
            "peak_rss_mb": None,
            "segment": None,
            "total_segments": None,
            "child_pid": None,
            "worker_heartbeat_at": now,
            "progress_updated_at": None,
            "child_output_at": None,
            "stage_started_at": None,
            "child_started_at": None,
            "cancel_requested": False,
            "job_directory": None,
        }
    thread = threading.Thread(target=_run_job, args=(job_id, fn), daemon=True)
    thread.start()
    return job_id


def _classify_failure(exc):
    text = str(exc).casefold()
    if "worker_killed_by_memory" in text:
        return "worker_killed_by_memory"
    if "disk" in text or "no space left" in text:
        return "disk_space"
    if "ffmpeg" in text:
        return "ffmpeg_failure"
    if "cancel" in text:
        return "accidental_cancel"
    return "transcription_exception"


@router.post("/transcribe")
def transcribe(payload: TranscribeRequest):
    input_path = payload.input_path.strip()
    if not input_path:
        raise HTTPException(status_code=400, detail="input_path を指定してください")

    job_id = _start_job(
        lambda job_id: run_transcribe(
            input_path=input_path,
            output_folder=payload.output_folder or "",
            decode_mode=payload.decode_mode,
            write_to_file=payload.write_to_file,
            cleanup=False,
            process_callback=lambda process: _set_job_process(job_id, process),
            diagnostic_callback=lambda event: _diagnostic_log(job_id, {**event, "job_id": job_id}),
            cancel_callback=lambda: _is_cancel_requested(job_id),
            job_id=job_id,
        )
    )
    return {"job_id": job_id}


@router.post("/upload")
def upload(
    file: UploadFile = File(...),
    decode_mode: str = Form("speed"),
    write_to_file: str = Form("1"),
    output_folder: str = Form(""),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="file が指定されていません")
    ext = (file.filename.rsplit(".", 1)[-1]).lower()
    if f".{ext}" not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="対応していないファイル形式です")

    input_path = save_upload_file(file)
    job_id = _start_job(
        lambda job_id: run_transcribe(
            input_path=input_path,
            output_folder=output_folder or "",
            decode_mode=decode_mode,
            write_to_file=write_to_file in ("1", "true", "True", "on"),
            cleanup=True,
            process_callback=lambda process: _set_job_process(job_id, process),
            diagnostic_callback=lambda event: _diagnostic_log(job_id, {**event, "job_id": job_id}),
            cancel_callback=lambda: _is_cancel_requested(job_id),
            job_id=job_id,
        )
    )
    return {"job_id": job_id}


@router.get("/status/{job_id}")
def status(job_id: str, offset: Optional[int] = None):
    warning = None
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="not found")
        process = job.get("process")
        worker_alive = False
        returncode = job.get("process_returncode")
        if process is not None:
            try:
                returncode = process.poll()
                worker_alive = returncode is None
            except Exception as exc:
                warning = f"[warning] Worker状態取得失敗: {exc}\n"
                worker_alive = True
        if not worker_alive and returncode is not None and returncode != 0 and job.get("status") in {"queued", "running"}:
            job["status"] = "error"
            job["exit_reason"] = _classify_failure(f"worker returncode={returncode}")
            job["error"] = f"Workerが終了コード {returncode} で終了しました"
            job["error_type"] = "WorkerExitError"
            job["updated_at"] = time.time()
        if job.get("status") == "running" or worker_alive:
            job["last_heartbeat_at"] = time.time()
            job["worker_heartbeat_at"] = job["last_heartbeat_at"]
            job["updated_at"] = time.time()
        effective_status = job["status"]
        if worker_alive and job.get("cancel_requested"):
            effective_status = "cancelling"
        elif worker_alive:
            effective_status = "running"
        log = job["log"]
        log_length = len(log)
        if offset is None or offset < 0 or offset > log_length:
            offset = 0
        delta = log[offset:]
        payload = {
            "status": effective_status,
            "log": log,
            "delta": delta,
            "log_length": log_length,
            "result": job.get("result"),
            "exit_reason": job.get("exit_reason"),
            "error": job.get("error"),
            "error_type": job.get("error_type"),
            "stage": job.get("stage"),
            "stderr_tail": job.get("stderr_tail"),
            "worker_pid": job.get("worker_pid"),
            "child_pid": job.get("child_pid"),
            "process_pid": job.get("worker_pid"),
            "worker_alive": worker_alive,
            "returncode": returncode,
            "cancel_requested": bool(job.get("cancel_requested")),
            "current_position": job.get("current_position"),
            "segment": job.get("segment"),
            "total_segments": job.get("total_segments"),
            "peak_rss_mb": job.get("peak_rss_mb"),
            "last_heartbeat_at": job.get("last_heartbeat_at"),
            "worker_heartbeat_at": job.get("worker_heartbeat_at"),
            "progress_updated_at": job.get("progress_updated_at"),
            "child_output_at": job.get("child_output_at"),
            "stage_started_at": job.get("stage_started_at"),
            "updated_at": job["updated_at"],
        }
    if warning:
        _append_job_log(job_id, warning)
    return payload


@router.post("/cancel/{job_id}")
def cancel(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="not found")
        job["cancel_requested"] = True
        process = job.get("process")
        job["updated_at"] = time.time()
    _append_job_log(job_id, "[cancel] キャンセル要求を受信しました。\n")
    if process is not None and process.poll() is None:
        process.terminate()
    return {"status": "cancel_requested"}


@live_router.websocket("/ws/live")
async def live_transcribe(websocket: WebSocket):
    await websocket.accept()
    session = None
    try:
        first = await websocket.receive_text()
        payload = json.loads(first)
        if payload.get("type") != "config":
            await websocket.send_json({"type": "error", "message": "最初に config を送信してください。"})
            await websocket.close(code=1003)
            return

        session = LiveSession(LiveSessionConfig.from_payload(payload), session_id=uuid.uuid4().hex)
        await websocket.send_json(
            {
                "type": "ready",
                "session_id": session.session_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "saved_path": session.saved_path,
            }
        )

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text is not None:
                try:
                    command = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if command.get("type") == "stop":
                    final_result = session.finalize()
                    await websocket.send_json(
                        {
                            "type": "session_final",
                            "text": final_result["text"],
                            "committed_text": final_result["committed_text"],
                            "partial_text": final_result["partial_text"],
                            "result_id": final_result["result_id"],
                            "session_id": final_result["session_id"],
                            "window_index": final_result["window_index"],
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "saved_path": session.saved_path,
                        }
                    )
                    break
                continue

            audio_bytes = message.get("bytes")
            if not audio_bytes:
                continue
            try:
                byte_size = len(audio_bytes)
                await websocket.send_json(
                    {
                        "type": "log",
                        "message": (
                            f"chunk受信開始 bytes={byte_size} mime={session.config.mime_type} "
                            f"mode={session.config.send_mode}"
                        ),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                await websocket.send_json(
                    {
                        "type": "log",
                        "message": "モデル読み込み",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                await websocket.send_json(
                    {
                        "type": "log",
                        "message": "推論開始",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
                result = await asyncio.to_thread(session.transcribe_chunk, audio_bytes)
            except Exception as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "stage": "websocket_receive",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "worker_pid": os.getpid(),
                        "worker_alive": True,
                        "last_audio_received_at": session.last_audio_received_at,
                        "last_transcription_at": session.last_transcription_at,
                    }
                )
                continue

            rms = float(result.get("rms", 0.0) or 0.0)
            duration = float(result.get("duration_seconds", 0.0) or 0.0)
            segment_summary = ";".join(
                f"{segment.get('start', 0):.1f}-{segment.get('end', 0):.1f}:{str(segment.get('text', ''))[:20]}"
                for segment in result.get("segments", [])
            )
            await websocket.send_json(
                {
                    "type": "log",
                    "message": (
                        f"window={result.get('window_index', '-')} "
                        f"range={result.get('window_start', 0):.1f}-{result.get('window_end', 0):.1f}s "
                        f"stable_until={result.get('stable_until', 0):.1f} "
                        f"committed_before={result.get('committed_until_before', 0):.1f} "
                        f"committed_after={result.get('committed_until', 0):.1f} "
                        f"received_audio={result.get('received_audio_seconds', 0):.1f} "
                        f"processed_audio={result.get('processed_audio_seconds', 0):.1f} "
                        f"committed_text_length={len(result.get('committed_text', ''))} "
                        f"commit_segments={result.get('commit_segment_count', 0)} "
                        f"partial_segments={result.get('partial_segment_count', 0)} "
                        f"result_text_length={len(result.get('text', ''))} "
                        f"segments_count={len(result.get('segments', []))} "
                        f"committed_append_length={len(result.get('committed_append', ''))} "
                        f"partial_text_length={len(result.get('partial_text', ''))} "
                        f"segments={segment_summary}"
                    ),
                    "timestamp": result["timestamp"],
                }
            )
            if result.get("classification_warning"):
                await websocket.send_json(
                    {
                        "type": "log",
                        "message": result["classification_warning"],
                        "timestamp": result["timestamp"],
                    }
                )
            await websocket.send_json(
                {
                    "type": "log",
                    "message": (
                        f"音声デコード完了 duration={duration:.1f}s "
                        f"count={session.received_chunk_count} total_bytes={session.received_audio_bytes} "
                        f"last_audio={session.last_audio_received_at} last_transcription={session.last_transcription_at}"
                    ),
                    "timestamp": result["timestamp"],
                }
            )
            if result.get("debug_path") or result.get("debug_wav_path"):
                await websocket.send_json(
                    {
                        "type": "log",
                        "message": f"debug chunk: {result.get('debug_path') or '-'} wav={result.get('debug_wav_path') or '-'}",
                        "timestamp": result["timestamp"],
                    }
                )
            if result.get("skipped"):
                reason = result.get("skip_reason") or "no_text"
                await websocket.send_json(
                    {
                        "type": "log",
                        "message": f"skip理由={reason} rms={rms:.4f}",
                        "timestamp": result["timestamp"],
                    }
                )
                continue
            await websocket.send_json(
                {
                    "type": "log",
                    "message": f"RMS={rms:.4f}",
                    "timestamp": result["timestamp"],
                }
            )
            if result.get("committed_text") or result.get("partial_text"):
                await websocket.send_json(
                    {
                        "type": "update",
                        "committed_text": result["committed_text"],
                        "partial_text": result["partial_text"],
                        "committed_until": result["committed_until"],
                        "stable_until": result["stable_until"],
                        "commit_segment_count": result["commit_segment_count"],
                        "partial_segment_count": result["partial_segment_count"],
                        "result_id": result["result_id"],
                        "session_id": result["session_id"],
                        "window_index": result["window_index"],
                        "window_start": result["window_start"],
                        "window_end": result["window_end"],
                        "overlap_seconds": result["overlap_seconds"],
                        "new_audio_start": result["new_audio_start"],
                        "new_audio_end": result["new_audio_end"],
                        "timestamp": result["timestamp"],
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if session is not None:
            session.close()
