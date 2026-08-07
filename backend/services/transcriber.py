import json
import math
import os
import subprocess
import sys
import tempfile
import time
import shutil
from pathlib import Path
from typing import Optional

from .exporter import create_output_directory, export_transcription_files
from .file_utils import write_text_file

MODEL_ENV = "BRIDGELOG_MODEL_PATH"
MODEL_NAME_ENV = "BRIDGELOG_MODEL_NAME"
PYTHON_ENV = "BRIDGELOG_PYTHON"
FFMPEG_DIR_ENV = "BRIDGELOG_FFMPEG_DIR"
DEFAULT_MODEL_NAME = "small"
SEGMENT_DURATION_SECONDS = 10 * 60
MAX_WHISPER_WORKER_RSS_BYTES = 6 * 1024 * 1024 * 1024
NON_RETRYABLE_EXCEPTIONS = (NameError, ImportError, AttributeError)

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus",
}


def _emit_diagnostic(diagnostic_callback, event):
    # 診断ログは補助処理のため、コールバック側の一時失敗でWorkerを停止させない。
    if diagnostic_callback is None:
        return
    try:
        diagnostic_callback(event)
    except Exception:
        pass


def _stage_started(diagnostic_callback, stage: str, segment_index=None, total_segments=None):
    event = {"event": "stage_started", "stage": stage, "started_at": time.time()}
    if segment_index is not None:
        event["segment"] = segment_index
    if total_segments is not None:
        event["total_segments"] = total_segments
    _emit_diagnostic(diagnostic_callback, event)


def _stage_finished(diagnostic_callback, stage: str, started_at: float, segment_index=None, total_segments=None):
    event = {
        "event": "stage_finished",
        "stage": stage,
        "started_at": started_at,
        "finished_at": time.time(),
        "elapsed": round(time.time() - started_at, 3),
    }
    if segment_index is not None:
        event["segment"] = segment_index
    if total_segments is not None:
        event["total_segments"] = total_segments
    _emit_diagnostic(diagnostic_callback, event)


def _terminate_and_reap(process):
    """例外時にも子プロセスを停止し、PIPEの読み取りを完了させる。"""
    try:
        if process.poll() is None:
            process.terminate()
    except Exception:
        pass
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()


class TranscriptionStageError(RuntimeError):
    def __init__(self, stage: str, original: Exception, segment_index: Optional[int] = None):
        self.stage = stage
        self.segment_index = segment_index
        self.original = original
        super().__init__(str(original))


def _raise_stage_error(stage: str, exc: Exception, segment_index: Optional[int] = None):
    # 実装エラーは再試行しても解消しないため、保存済みの途中結果を残して直ちに終了する。
    raise TranscriptionStageError(stage, exc, segment_index) from exc


def resolve_model_spec() -> str:
    env_path = os.environ.get(MODEL_ENV, "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise ValueError(f"{MODEL_ENV} が存在しません。")
    return os.environ.get(MODEL_NAME_ENV, "").strip() or DEFAULT_MODEL_NAME


def resolve_python() -> str:
    env_path = os.environ.get(PYTHON_ENV, "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    if sys.executable and Path(sys.executable).is_file():
        return sys.executable
    return "python3"


def resolve_ffmpeg_dir() -> Optional[Path]:
    """ffmpeg/ffprobe を含むディレクトリを返す。

    優先順位: 環境変数 BRIDGELOG_FFMPEG_DIR > 同梱 resources/ffmpeg/bin > None(=システムPATH)。
    MyLauncher のような絶対パスは持たない。
    """
    env_dir = os.environ.get(FFMPEG_DIR_ENV, "").strip()
    if env_dir:
        candidate = Path(env_dir).expanduser()
        if candidate.is_dir():
            return candidate
    # backend/services/transcriber.py -> project root は parents[2]
    bundled = Path(__file__).resolve().parents[2] / "resources" / "ffmpeg" / "bin"
    if bundled.is_dir():
        return bundled
    return None


def probe_duration(input_path: str, env: dict) -> Optional[float]:
    ffprobe = "ffprobe"
    ffmpeg_dir = resolve_ffmpeg_dir()
    if ffmpeg_dir is not None and (ffmpeg_dir / "ffprobe").is_file():
        ffprobe = str(ffmpeg_dir / "ffprobe")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=10,
        )
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _save_progress(progress_path: Path, payload: dict) -> None:
    try:
        write_text_file(progress_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:
        _raise_stage_error("progress_write", exc)


def _run_batch_worker(input_paths, decode_mode, model_spec, env, process_callback, diagnostic_callback, cancel_callback):
    cmd = [resolve_python(), str(Path(__file__).with_name("runner.py"))]
    for input_path in input_paths:
        cmd.extend(["--input", str(input_path)])
    cmd.extend(["--mode", "speed" if decode_mode == "speed" else "accuracy", "--model", model_spec, "--write", "0"])
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    if process_callback:
        process_callback(process)
    stdout, stderr = "", ""
    try:
        while True:
            try:
                # 長時間推論中の監視は低頻度にして、親プロセス側の監視処理を軽く保つ。
                stdout, stderr = process.communicate(timeout=10.0)
                break
            except subprocess.TimeoutExpired:
                _emit_diagnostic(diagnostic_callback, {"event": "progress", "current_position": "Worker実行中"})
                if cancel_callback and cancel_callback():
                    _terminate_and_reap(process)
                    raise RuntimeError("cancelled: 音声書き起こしWorkerをキャンセルしました")
    except Exception:
        if process.poll() is None:
            stdout, stderr = _terminate_and_reap(process)
        raise
    finally:
        return_code = process.returncode
        _emit_diagnostic(diagnostic_callback, {"event": "process_finished", "returncode": return_code, "stderr_tail": (stderr or "")[-4000:]})
        if process_callback and process.poll() is not None:
            process_callback(None)
    if return_code != 0:
        raise RuntimeError(stderr or "Whisper Workerが失敗しました。")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Whisper出力JSONを解析できません: {stdout[-1000:]}") from exc
    return payload.get("results", [])


def _run_segmented_transcribe(
    input_path: str,
    output_folder: str,
    decode_mode: str,
    write_to_file: bool,
    duration: float,
    env: dict,
    job_id: Optional[str],
    process_callback,
    diagnostic_callback,
    cancel_callback,
) -> dict:
    estimated_audio_bytes = int(duration * 32 * 1024)
    required_bytes = max(256 * 1024 * 1024, int(estimated_audio_bytes * 2.5) + 512 * 1024 * 1024)
    input_free = shutil.disk_usage(Path(input_path).parent).free
    temp_free = shutil.disk_usage(Path(tempfile.gettempdir())).free
    if input_free < required_bytes or temp_free < required_bytes:
        raise RuntimeError(
            f"disk_space: 長時間音声処理に必要な空き容量が不足しています。"
            f"必要見込み={required_bytes} bytes input_free={input_free} bytes temp_free={temp_free} bytes"
        )
    job_directory = Path(tempfile.gettempdir()) / "bridgelog_jobs" / (job_id or os.urandom(8).hex())
    job_directory.mkdir(parents=True, exist_ok=True)
    progress_path = job_directory / "progress.json"
    total_segments = max(1, math.ceil(duration / SEGMENT_DURATION_SECONDS))
    progress = {"completed_segments": 0, "total_segments": total_segments, "last_completed_at": None, "status": "running"}
    _emit_diagnostic(
        diagnostic_callback,
        {"event": "progress", "segment": 0, "total_segments": total_segments, "current_position": "セグメント処理中"},
    )
    if progress_path.is_file():
        try:
            saved = json.loads(progress_path.read_text(encoding="utf-8"))
            if saved.get("total_segments") == total_segments:
                progress.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
    _save_progress(progress_path, progress)
    results = []
    combined_segments = []
    for completed_index in range(progress["completed_segments"]):
        saved_result_path = job_directory / f"segment_{completed_index + 1:04d}.json"
        if not saved_result_path.is_file():
            progress["completed_segments"] = completed_index
            break
        saved_result = json.loads(saved_result_path.read_text(encoding="utf-8"))
        results.append(saved_result)
        offset = completed_index * SEGMENT_DURATION_SECONDS
        for segment in saved_result.get("segments", []):
            adjusted = dict(segment)
            if adjusted.get("start") is not None:
                adjusted["start"] = float(adjusted["start"]) + offset
            if adjusted.get("end") is not None:
                adjusted["end"] = float(adjusted["end"]) + offset
            combined_segments.append(adjusted)
    segment_paths = []
    segment_indexes = []
    for index in range(progress["completed_segments"], total_segments):
        if cancel_callback and cancel_callback():
            raise RuntimeError("cancelled: セグメント処理をキャンセルしました")
        offset = index * SEGMENT_DURATION_SECONDS
        segment_duration = min(SEGMENT_DURATION_SECONDS, duration - offset)
        segment_path = job_directory / f"segment_{index + 1:04d}.wav"
        ffmpeg = "ffmpeg"
        ffmpeg_dir = resolve_ffmpeg_dir()
        if ffmpeg_dir is not None and (ffmpeg_dir / "ffmpeg").is_file():
            ffmpeg = str(ffmpeg_dir / "ffmpeg")
        ffmpeg_cmd = [
            ffmpeg, "-nostdin", "-y", "-ss", str(offset), "-t", str(segment_duration), "-i", input_path,
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(segment_path),
        ]
        ffmpeg_started_at = time.time()
        _stage_started(diagnostic_callback, "audio_extract", index + 1, total_segments)
        ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        _emit_diagnostic(diagnostic_callback, {"event": "child_process_started", "pid": ffmpeg_process.pid, "stage": "audio_extract"})
        if process_callback:
            process_callback(ffmpeg_process)
        stdout, stderr = "", ""
        try:
            while True:
                try:
                    stdout, stderr = ffmpeg_process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    _emit_diagnostic(
                        diagnostic_callback,
                        {"event": "segment_ffmpeg_progress", "segment": index + 1, "total_segments": total_segments, "current_position": f"ffmpeg segment {index + 1}/{total_segments}"},
                    )
                    if cancel_callback and cancel_callback():
                        _terminate_and_reap(ffmpeg_process)
                        raise RuntimeError("cancelled: ffmpegセグメント処理をキャンセルしました")
        except Exception:
            _terminate_and_reap(ffmpeg_process)
            raise
        _emit_diagnostic(diagnostic_callback, {
                "event": "segment_ffmpeg",
                "segment": index + 1,
                "total_segments": total_segments,
                "command": ffmpeg_cmd,
                "returncode": ffmpeg_process.returncode,
                "stderr_tail": (stderr or "")[-4000:],
                "current_position": f"segment {index + 1}/{total_segments}",
            })
        _stage_finished(diagnostic_callback, "audio_extract", ffmpeg_started_at, index + 1, total_segments)
        # 次の文字起こしWorkerを監視する前に、音声抽出用子プロセスの参照を確実に解放する。
        if process_callback:
            process_callback(None)
        if ffmpeg_process.returncode != 0:
            raise RuntimeError(f"ffmpeg_failure: {(stderr or stdout or 'ffmpeg failed')[-4000:]}")
        segment_paths.append(segment_path)
        segment_indexes.append(index)

    if segment_paths:
        batch_started_at = time.time()
        _stage_started(diagnostic_callback, "transcription", segment_indexes[0] + 1, total_segments)
        batch_results = _run_batch_worker(
            segment_paths, decode_mode, resolve_model_spec(), env,
            process_callback, diagnostic_callback, cancel_callback,
        )
        _stage_finished(diagnostic_callback, "transcription", batch_started_at, segment_indexes[0] + 1, total_segments)
        if len(batch_results) != len(segment_paths):
            raise RuntimeError(f"Worker結果数が不一致です。期待={len(segment_paths)} 実際={len(batch_results)}")
        for index, segment_path, result in zip(segment_indexes, segment_paths, batch_results):
            _emit_diagnostic(
                diagnostic_callback,
                {
                    "event": "segment_timing",
                    "segment": index + 1,
                    "total_segments": total_segments,
                    "elapsed": result.get("processing_elapsed_seconds"),
                    "current_position": f"segment {index + 1}/{total_segments}",
                },
            )
            segment_result_path = job_directory / f"segment_{index + 1:04d}.json"
            try:
                write_text_file(segment_result_path, json.dumps(result, ensure_ascii=False))
            except Exception as exc:
                _raise_stage_error("segment_result_write", exc, index + 1)
            results.append(result)
            offset = index * SEGMENT_DURATION_SECONDS
            for segment in result.get("segments", []):
                adjusted = dict(segment)
                if adjusted.get("start") is not None:
                    adjusted["start"] = float(adjusted["start"]) + offset
                if adjusted.get("end") is not None:
                    adjusted["end"] = float(adjusted["end"]) + offset
                combined_segments.append(adjusted)
            progress["completed_segments"] = index + 1
            progress["last_completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            _save_progress(progress_path, progress)
            _emit_diagnostic(diagnostic_callback, {"event": "segment_completed", "segment": index + 1, "total_segments": total_segments, "current_position": f"segment {index + 1}/{total_segments}"})
            cleanup_started_at = time.time()
            _stage_started(diagnostic_callback, "temporary_cleanup", index + 1, total_segments)
            segment_path.unlink(missing_ok=True)
            _stage_finished(diagnostic_callback, "temporary_cleanup", cleanup_started_at, index + 1, total_segments)
    final_text = "\n".join(result.get("text", "") for result in results if result.get("text")).strip()
    raw_text = "\n".join(result.get("raw_text", "") for result in results if result.get("raw_text")).strip()
    def format_timestamp(seconds):
        total = max(0, int(float(seconds or 0)))
        minutes, remainder = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{remainder:02}"

    timestamped_text = "\n".join(
        f"[{format_timestamp(segment.get('start'))} --> {format_timestamp(segment.get('end'))}] {segment.get('raw_text', '')}"
        for segment in combined_segments
        if segment.get("raw_text")
    ).strip()
    output_paths = {}
    if write_to_file:
        result_write_started_at = time.time()
        _stage_started(diagnostic_callback, "result_write")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_directory, timestamp = create_output_directory(output_folder, input_path, timestamp)
        try:
            output_paths = export_transcription_files(
                output_directory, timestamp, final_text, raw_text, timestamped_text, combined_segments,
                {"input_path": input_path, "model": resolve_model_spec(), "segment_duration_seconds": SEGMENT_DURATION_SECONDS},
            )
        except Exception as exc:
            _raise_stage_error("final_result_write", exc)
        missing_outputs = [path for path in output_paths.values() if not Path(path).is_file()]
        if missing_outputs:
            _raise_stage_error("final_result_verify", FileNotFoundError(", ".join(missing_outputs)))
        _stage_finished(diagnostic_callback, "result_write", result_write_started_at)
    _emit_diagnostic(diagnostic_callback, {"event": "worker_main_processing_finished", "current_position": "処理本体完了"})
    progress["status"] = "done"
    _save_progress(progress_path, progress)
    return {
        "text": final_text,
        "raw_text": raw_text,
        "timestamped_text": timestamped_text,
        "output_directory": str(Path(output_paths["output_path"]).parent) if output_paths else None,
        "output_path": output_paths.get("output_path"),
        "raw_output_path": output_paths.get("raw_output_path"),
        "timestamped_output_path": output_paths.get("timestamped_output_path"),
        "segments_output_path": output_paths.get("segments_output_path"),
        "segment_count": total_segments,
        "job_directory": str(job_directory),
    }


def run_transcribe(
    input_path: str,
    output_folder: str,
    decode_mode: str,
    write_to_file: bool,
    cleanup: bool,
    process_callback=None,
    diagnostic_callback=None,
    cancel_callback=None,
    job_id: Optional[str] = None,
    _force_single: bool = False,
) -> dict:
    input_path = os.path.abspath(input_path)
    ext = Path(input_path).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("対応していないファイル形式です。")

    model_spec = resolve_model_spec()
    python_bin = resolve_python()
    runner_path = Path(__file__).with_name("runner.py")

    if not runner_path.is_file():
        raise ValueError("runner.py が見つかりません。")

    if not output_folder and cleanup and write_to_file:
        today_str = __import__("datetime").datetime.today().strftime("%Y%m%d")
        output_folder = os.path.expanduser(f"~/Desktop/bridgelog_output_{today_str}")

    cmd = [
        python_bin,
        str(runner_path),
        "--input",
        input_path,
        "--mode",
        "speed" if decode_mode == "speed" else "accuracy",
        "--model",
        model_spec,
        "--write",
        "1" if write_to_file else "0",
    ]
    if output_folder:
        cmd.extend(["--output-folder", output_folder])

    try:
        env = os.environ.copy()
        ffmpeg_dir = resolve_ffmpeg_dir()
        if ffmpeg_dir is not None:
            env["PATH"] = f"{str(ffmpeg_dir)}{os.pathsep}{env.get('PATH', '')}"
            ffmpeg_bin = ffmpeg_dir / "ffmpeg"
            if ffmpeg_bin.is_file():
                env["FFMPEG_BINARY"] = str(ffmpeg_bin)
        probe_started_at = time.time()
        _stage_started(diagnostic_callback, "media_probe")
        duration = probe_duration(input_path, env)
        _stage_finished(diagnostic_callback, "media_probe", probe_started_at)
        if not _force_single and duration is not None and duration > SEGMENT_DURATION_SECONDS:
            return _run_segmented_transcribe(
                input_path, output_folder, decode_mode, write_to_file, duration, env, job_id,
                process_callback, diagnostic_callback, cancel_callback,
            )
        diagnostic = {
            "event": "start",
            "input_path": input_path,
            "file_size_bytes": os.path.getsize(input_path),
            "audio_duration_seconds": duration,
            "model": model_spec,
            "command": cmd,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "current_position": "Worker起動前",
        }
        _emit_diagnostic(diagnostic_callback, diagnostic)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        _emit_diagnostic(diagnostic_callback, {"event": "child_process_started", "pid": process.pid, "stage": "transcription"})
        if process_callback:
            process_callback(process)
        _emit_diagnostic(diagnostic_callback, {"event": "process_started", "pid": process.pid, "current_position": "Worker実行中"})
        stdout = ""
        stderr = ""
        try:
            import psutil
            worker = psutil.Process(process.pid)
        except Exception:
            # psutil 未導入、またはWorkerへのアタッチ失敗（即時終了・権限）でも
            # RSS監視を諦めるだけで本処理は継続する（監視はWorkerを止めない）。
            worker = None
        peak_rss_mb = 0.0
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if worker is not None:
                        try:
                            rss_bytes = worker.memory_info().rss
                            peak_rss_mb = max(peak_rss_mb, rss_bytes / (1024 * 1024))
                            if rss_bytes >= MAX_WHISPER_WORKER_RSS_BYTES:
                                _emit_diagnostic(diagnostic_callback, {"event": "memory_limit_reached", "current_position": "現在セグメント終了後にWorkerを再起動", "peak_rss_mb": round(peak_rss_mb, 1)})
                        except Exception as exc:
                            _emit_diagnostic(diagnostic_callback, {"event": "warning", "message": f"[warning] RSS取得失敗: {exc}"})
                    _emit_diagnostic(diagnostic_callback, {"event": "progress", "current_position": "Worker実行中", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "peak_rss_mb": round(peak_rss_mb, 1)})
                    if cancel_callback and cancel_callback():
                        _terminate_and_reap(process)
                        raise RuntimeError("cancelled: 音声書き起こしWorkerをキャンセルしました")
        except Exception:
            if process.poll() is None:
                stdout, stderr = _terminate_and_reap(process)
            raise
        finally:
            return_code = process.returncode
            _emit_diagnostic(diagnostic_callback, {
                        "event": "process_finished",
                        "returncode": return_code,
                        "stderr_tail": (stderr or "")[-4000:],
                        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "current_position": "Worker終了",
                        "peak_rss_mb": round(peak_rss_mb, 1),
                    })
            if process_callback and process.poll() is not None:
                process_callback(None)
            _emit_diagnostic(diagnostic_callback, {"event": "worker_process_exited", "returncode": return_code})
        if return_code != 0:
            if return_code == -9:
                raise RuntimeError("worker_killed_by_memory: Whisper WorkerがSIGKILLで終了しました")
            raise RuntimeError(stderr or "Whisper 実行に失敗しました。")
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Whisper出力JSONを解析できません: {stdout[-1000:]}") from exc
        _emit_diagnostic(diagnostic_callback, {"event": "worker_main_processing_finished", "current_position": "処理本体完了"})
        return result
    finally:
        _emit_diagnostic(diagnostic_callback, {"event": "worker_finalization_started", "current_position": "Worker終了処理"})
        if cleanup:
            try:
                os.remove(input_path)
            except OSError:
                pass
        _emit_diagnostic(diagnostic_callback, {"event": "worker_finalization_finished", "current_position": "Worker終了処理完了"})
        _emit_diagnostic(diagnostic_callback, {"event": "worker_returning", "current_position": "Worker return直前"})


def save_upload_file(upload) -> str:
    temp_root = Path(tempfile.gettempdir()) / "bridgelog_whisper"
    temp_root.mkdir(parents=True, exist_ok=True)
    filename = Path(getattr(upload, "filename", "") or "upload").name
    target = temp_root / f"{os.urandom(8).hex()}_{filename}"
    with open(target, "wb") as f:
        f.write(upload.file.read())
    return str(target)
