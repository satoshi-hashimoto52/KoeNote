import asyncio
import json
import os
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional

from services import session_store
from services.live_registry import registry
from services.live_session import LiveSession, LiveSessionConfig
from services.pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE
from services.transcriber import ALLOWED_EXTENSIONS, TranscriptionStageError, run_transcribe, save_upload_file
from services.wav_recorder import AsyncWavAppender, CrashSafeWavWriter

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


# ---------------------------------------------------------------------------
# realtime (WebSocket)
# ---------------------------------------------------------------------------
# 受信・推論・heartbeat・送信を独立したタスクに分ける。
# 受信ループが推論を await すると、uvicorn が 1 メッセージごとに transport.pause_reading()
# するため PONG も読まれず、1 サイクルが ws_ping_timeout を超えた瞬間に切断される。
# また受信キューが無制限に伸びて RSS が膨らむ。分離はその両方の構造的な対策。

HEARTBEAT_INTERVAL_SECONDS = 2.0
INFERENCE_IDLE_SLEEP_SECONDS = 0.2
SEND_QUEUE_MAXSIZE = 128
# 正常停止時、session_final が実際に送られるのを待つ上限。
SENDER_DRAIN_TIMEOUT_SECONDS = 10.0
# 混雑時に捨ててよいのは診断系だけ。本文・警告・確定は絶対に捨てない。
DROPPABLE_MESSAGE_TYPES = {"log", "metrics", "heartbeat"}


class _Outbox:
    """送信を 1 タスクに集約する上限付きキュー。

    heartbeat タスクと推論タスクが両方 ws.send_json を await すると
    フレームが混ざりうるため、writer は 1 本に限定する。
    上限があることが送信側のメモリ上限（受信側のリングバッファと対称）になる。
    """

    def __init__(self, maxsize: int = SEND_QUEUE_MAXSIZE):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def put_soon(self, message: dict) -> None:
        """診断系の送信。混雑していたら捨てる（呼び出し側は絶対にブロックしない）。"""
        assert message.get("type") in DROPPABLE_MESSAGE_TYPES, (
            f"put_soon は診断系のみ: {message.get('type')} は await put() を使う"
        )
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped += 1

    async def put(self, message) -> None:
        """本文・警告・確定など、捨てられない種別。ここでは背圧をかける。"""
        await self._queue.put(message)

    async def get(self):
        return await self._queue.get()


async def _sender(websocket: WebSocket, outbox: _Outbox) -> None:
    """ws.send_json を await する唯一のタスク。"""
    while True:
        message = await outbox.get()
        if message is None:
            return
        await websocket.send_json(message)


async def _heartbeat(session: LiveSession, outbox: _Outbox, stop_event: asyncio.Event) -> None:
    """推論が worker スレッドで詰まっていても刻み続ける「生存」信号。

    進捗ではなく生存を伝えるので、クライアントは無応答を確実に検知できる。
    """
    seq = 0
    while not stop_event.is_set():
        seq += 1
        outbox.put_soon(
            {
                "type": "heartbeat",
                "seq": seq,
                "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "pid": os.getpid(),
                **session.progress_snapshot(),
            }
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


def _update_message(result: dict) -> dict:
    """確定済み全文ではなく差分だけを載せる（1時間で約4MBの冗長JSONを削る）。"""
    return {
        "type": "update",
        "committed_delta": result["committed_delta"],
        "committed_length_before": result["committed_length_before"],
        "committed_length": result["committed_length"],
        "needs_snapshot": result["needs_snapshot"],
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
        "timestamp": result["timestamp"],
    }


def _metrics_message(result: dict, inference_ms: int) -> dict:
    """診断用。数値のみ。セグメントのテキストは絶対に載せない。"""
    return {
        "type": "metrics",
        "window_index": result["window_index"],
        "window_start": round(result["window_start"], 2),
        "window_end": round(result["window_end"], 2),
        "stable_until": round(result["stable_until"], 2),
        "committed_until": round(result["committed_until"], 2),
        "committed_length": result["committed_length"],
        "commit_segment_count": result["commit_segment_count"],
        "partial_segment_count": result["partial_segment_count"],
        "segment_count": len(result.get("segments", [])),
        "rms": round(float(result.get("rms", 0.0) or 0.0), 4),
        "inference_ms": inference_ms,
        "lag_seconds": result.get("lag_seconds", 0.0),
        "dropped_seconds": result.get("dropped_seconds", 0.0),
        "skip_reason": result.get("skip_reason") or "",
        "timestamp": result["timestamp"],
    }


async def _emit_window(session: LiveSession, outbox: _Outbox, result: dict, inference_ms: int) -> None:
    if result.get("needs_snapshot"):
        await outbox.put(
            {
                "type": "snapshot",
                "committed_text": session.committed_text,
                "committed_length": len(session.committed_text),
                "partial_text": session.partial_text,
                "session_id": session.session_id,
                "timestamp": result["timestamp"],
            }
        )
    else:
        await outbox.put(_update_message(result))

    if session.config.debug:
        outbox.put_soon(_metrics_message(result, inference_ms))
    if result.get("classification_warning"):
        outbox.put_soon(
            {"type": "log", "message": result["classification_warning"], "timestamp": result["timestamp"]}
        )


async def _inference_driver(
    session: LiveSession,
    outbox: _Outbox,
    stop_event: asyncio.Event,
    recorder,
) -> None:
    warned_degraded = False
    warned_lagging = False
    while not stop_event.is_set():
        plan = session.plan_window()
        if plan is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=INFERENCE_IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(session.run_window, *plan)
        except Exception as exc:
            print(f"[WS] ERROR stage=inference type={type(exc).__name__} msg={exc}", flush=True)
            await outbox.put(
                {
                    "type": "error",
                    "stage": "inference",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "worker_pid": os.getpid(),
                    "worker_alive": True,
                    "last_audio_received_at": session.last_audio_received_at,
                    "last_transcription_at": session.last_transcription_at,
                }
            )
            # 1窓の失敗でセッションを落とさない。次の窓へ進む。
            session.advance_cursor(plan[1])
            continue
        inference_ms = int((time.monotonic() - started) * 1000)

        if result is None:
            # 破棄済み区間だった。plan_window が計上済みなので再計画に任せる。
            continue

        session.advance_cursor(plan[1])
        await _emit_window(session, outbox, result, inference_ms)

        if session.degraded and not warned_degraded:
            warned_degraded = True
            await outbox.put(
                {
                    "type": "warning",
                    "code": "audio_dropped",
                    "message": (
                        f"処理が追いつかず音声を {session.dropped_seconds:.0f} 秒ぶん破棄しました。"
                        "より小さいモデルを選んでください。"
                    ),
                    **session.progress_snapshot(),
                }
            )
        elif session.lag_seconds > 30 and not warned_lagging:
            warned_lagging = True
            await outbox.put(
                {
                    "type": "warning",
                    "code": "inference_lagging",
                    "message": (
                        f"文字起こしが約 {session.lag_seconds:.0f} 秒遅れています。"
                        "録音は継続しています。"
                    ),
                    **session.progress_snapshot(),
                }
            )
        elif session.lag_seconds < 10:
            warned_lagging = False

        if recorder is not None and recorder.dropped_frames and not recorder.drop_reported:
            recorder.drop_reported = True
            await outbox.put(
                {
                    "type": "warning",
                    "code": "wav_write_failed",
                    "message": "録音ファイルへの書き込みが追いついていません。",
                }
            )
        print(
            f"[whisper] session={session.session_id} window={result['window_index']} "
            f"range={result['window_start']:.1f}-{result['window_end']:.1f}s "
            f"commit={result['commit_segment_count']} partial={result['partial_segment_count']} "
            f"committed_len={result['committed_length']} infer={inference_ms}ms "
            f"lag={session.lag_seconds:.1f}s dropped={session.dropped_seconds:.1f}s",
            flush=True,
        )


@live_router.websocket("/ws/live")
async def live_transcribe(websocket: WebSocket):
    await websocket.accept()
    session: Optional[LiveSession] = None
    recorder = None
    stop_event = asyncio.Event()
    outbox = _Outbox()
    stopped_normally = False

    try:
        first = await websocket.receive_text()
        payload = json.loads(first)
        if payload.get("type") != "config":
            await websocket.send_json({"type": "error", "message": "最初に config を送信してください。"})
            await websocket.close(code=1003)
            return

        config = LiveSessionConfig.from_payload(payload)
        if config.send_mode == "full":
            # 送信済み音声を毎回全部送り直す経路は録音時間の2乗でコストが増える。
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "send_mode='full' は O(T^2) のため realtime では使用しません。'pcm16' を使ってください。",
                }
            )
            await websocket.close(code=1003)
            return

        resume_id = str(payload.get("resume_session_id", "") or "").strip()
        resumed = False
        if resume_id:
            session = registry.acquire(resume_id)
            resumed = session is not None
        if session is None:
            session = LiveSession(config, session_id=uuid.uuid4().hex)
            registry.register(session)
        else:
            # 再接続では debug 表示の切り替えだけ引き継ぐ。
            session.config.debug = config.debug

        if session.config.write_to_file and session.config.output_folder:
            try:
                recorder = AsyncWavAppender(
                    CrashSafeWavWriter(session_store.raw_audio_path(session.config.output_folder))
                )
            except OSError as exc:
                print(f"[WS] WARN recording_disabled msg={exc}", flush=True)
                await outbox.put(
                    {
                        "type": "warning",
                        "code": "wav_write_failed",
                        "message": f"録音ファイルを開けませんでした: {exc}",
                    }
                )

        print(
            f"[WS] {'resumed' if resumed else 'ready'} session={session.session_id} "
            f"model={session.config.model} chunk={session.config.chunk_seconds}s "
            f"overlap={session.config.overlap_seconds}s mode={session.config.send_mode} "
            f"sr={session.config.sample_rate} saved={session.saved_path}",
            flush=True,
        )
        await websocket.send_json(
            {
                "type": "resumed" if resumed else "ready",
                "session_id": session.session_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "saved_path": session.saved_path,
                "audio_path": recorder.path if recorder is not None else None,
                "server_total_samples": session.pcm.total_samples,
                "committed_length": len(session.committed_text),
                "sample_rate": SAMPLE_RATE,
                "chunk_seconds": session.config.chunk_seconds,
                "overlap_seconds": session.config.overlap_seconds,
                "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
            }
        )
        if resumed:
            # 差分の基準がずれないよう、再接続直後は全文を渡す。
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "committed_text": session.committed_text,
                    "committed_length": len(session.committed_text),
                    "partial_text": session.partial_text,
                    "session_id": session.session_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )

        # TaskGroup は本体の例外を ExceptionGroup に包むため WebSocketDisconnect の
        # 判別ができなくなる。タスクは明示的に管理し、通常の例外意味論を保つ。
        sender_task = asyncio.create_task(_sender(websocket, outbox))
        heartbeat_task = asyncio.create_task(_heartbeat(session, outbox, stop_event))
        driver = asyncio.create_task(_inference_driver(session, outbox, stop_event, recorder))
        background = [sender_task, heartbeat_task, driver]

        try:
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
                    kind = command.get("type")

                    if kind == "stop":
                        session.stopping = True
                        stop_event.set()
                        await driver
                        # カーソルより先の端切れ（従来は無言で捨てられていた）を回収する。
                        try:
                            tail = await asyncio.to_thread(session.flush_tail)
                        except Exception as exc:
                            print(f"[WS] WARN flush_tail failed: {exc}", flush=True)
                            tail = None
                        if tail is not None:
                            await outbox.put(_update_message(tail))
                        final_result = session.finalize()
                        print(
                            f"[WS] session_final session={session.session_id} "
                            f"committed_len={len(final_result['committed_text'])} "
                            f"recorded={final_result['recorded_seconds']:.1f}s "
                            f"dropped={final_result['dropped_seconds']:.1f}s saved={session.saved_path}",
                            flush=True,
                        )
                        await outbox.put(
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
                                "audio_path": recorder.path if recorder is not None else None,
                                "recorded_seconds": final_result["recorded_seconds"],
                                "dropped_seconds": final_result["dropped_seconds"],
                            }
                        )
                        stopped_normally = True
                        break

                    if kind == "resync":
                        await outbox.put(
                            {
                                "type": "snapshot",
                                "committed_text": session.committed_text,
                                "committed_length": len(session.committed_text),
                                "partial_text": session.partial_text,
                                "session_id": session.session_id,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }
                        )
                        continue

                    if kind == "set_debug":
                        session.config.debug = bool(command.get("value"))
                        continue

                    if kind == "gap":
                        # クライアントが保持しきれなかった区間を無音で埋め、絶対時刻を壁時計に合わせる。
                        samples = int(command.get("samples") or 0)
                        if samples > 0:
                            session.append_gap(samples)
                            if recorder is not None:
                                recorder.append(b"\x00" * (samples * BYTES_PER_SAMPLE))
                        continue

                    continue

                audio_bytes = message.get("bytes")
                if not audio_bytes:
                    continue
                # 受信側は追記だけ。ここをブロックさせないことが全体の前提。
                session.append_pcm(audio_bytes)
                if recorder is not None:
                    recorder.append(audio_bytes)

        finally:
            stop_event.set()
            if stopped_normally:
                # session_final を積んだ直後に sender を cancel すると最終確定が届かない。
                # 番兵を入れて sender が自然終了するのを待ってから畳む。
                await outbox.put(None)
                try:
                    await asyncio.wait_for(sender_task, timeout=SENDER_DRAIN_TIMEOUT_SECONDS)
                except (asyncio.TimeoutError, Exception) as exc:
                    print(f"[WS] WARN sender drain failed: {type(exc).__name__} {exc}", flush=True)
            for task in background:
                task.cancel()
            results = await asyncio.gather(*background, return_exceptions=True)
            for task_result in results:
                if isinstance(task_result, Exception) and not isinstance(task_result, asyncio.CancelledError):
                    print(
                        f"[WS] ERROR background type={type(task_result).__name__} msg={task_result}",
                        flush=True,
                    )

    except WebSocketDisconnect:
        print(
            f"[WS] disconnect session={session.session_id if session else '-'} "
            f"state={websocket.client_state.name} "
            f"received_bytes={session.received_audio_bytes if session else 0} "
            f"recorded={session.recorded_seconds if session else 0:.1f}s",
            flush=True,
        )
    except Exception as exc:
        print(f"[WS] ERROR fatal type={type(exc).__name__} msg={exc}", flush=True)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        stop_event.set()
        # 正常停止は明示的に clean close する。1006(異常終了)のままだと
        # クライアント側の「正常停止/異常切断」の判別材料が濁る。
        if stopped_normally:
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
        if recorder is not None:
            recorder.close()
        if session is not None:
            if stopped_normally:
                registry.discard(session.session_id)
                session.close()
            else:
                # 異常切断は再接続を待つ。TXT は追記済みなので確定分は失われない。
                registry.detach(session.session_id)
        for reaped in registry.reap():
            reaped.close()
