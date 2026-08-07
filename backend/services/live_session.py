import os
import shutil
import subprocess
import tempfile
import wave
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .live_transcriber import DEFAULT_MODEL, SUPPORTED_MODELS, convert_webm_bytes_to_wav, transcribe_wav_file
from .transcriber import resolve_ffmpeg_dir


DELAY_PRESETS = {
    "low_latency": {"chunk_seconds": 8.0, "overlap_seconds": 2.0},
    "balanced": {"chunk_seconds": 10.0, "overlap_seconds": 2.0},
    "accuracy": {"chunk_seconds": 12.0, "overlap_seconds": 3.0},
}


def _clamp_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_result_text(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _join_transcript(previous: str, new_text: str) -> str:
    previous = previous.rstrip()
    new_text = new_text.strip()
    if not previous:
        return new_text
    if not new_text:
        return previous
    if previous.endswith(("。", "！", "？", "\n")):
        return f"{previous}\n{new_text}"
    return f"{previous}{new_text}"


@dataclass
class LiveSessionConfig:
    model: str = DEFAULT_MODEL
    delay_mode: str = "balanced"
    chunk_seconds: float = 10.0
    overlap_seconds: float = 2.0
    write_to_file: bool = True
    output_folder: str = ""
    output_filename: Optional[str] = None
    mime_type: str = "audio/webm"
    debug_chunks: bool = False
    send_mode: str = "chunks"

    @classmethod
    def from_payload(cls, payload: dict) -> "LiveSessionConfig":
        delay_mode = str(payload.get("delay_mode", "balanced") or "balanced")
        preset = DELAY_PRESETS.get(delay_mode, DELAY_PRESETS["balanced"])
        model = str(payload.get("model", DEFAULT_MODEL) or DEFAULT_MODEL).lower()
        if model not in SUPPORTED_MODELS:
            model = DEFAULT_MODEL
        chunk_seconds = _clamp_float(
            payload.get("chunk_seconds"),
            preset["chunk_seconds"],
            1.0,
            30.0,
        )
        overlap_seconds = _clamp_float(
            payload.get("overlap_seconds"),
            preset["overlap_seconds"],
            0.0,
            max(0.0, chunk_seconds - 0.1),
        )
        output_filename = str(payload.get("output_filename", "") or "").strip() or None
        return cls(
            model=model,
            delay_mode=delay_mode if delay_mode in DELAY_PRESETS else "balanced",
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
            write_to_file=bool(payload.get("write_to_file", True)),
            output_folder=str(payload.get("output_folder", "") or "").strip(),
            output_filename=output_filename,
            mime_type=str(payload.get("mime_type", "audio/webm") or "audio/webm"),
            debug_chunks=bool(payload.get("debug_chunks", False)),
            send_mode="full" if str(payload.get("send_mode", "")).lower() == "full" else "chunks",
        )


class LiveSession:
    def __init__(self, config: LiveSessionConfig, session_id: Optional[str] = None):
        self.config = config
        self.session_id = session_id or uuid.uuid4().hex
        self.final_text = ""
        self.committed_text = ""
        self.partial_text = ""
        self.committed_until_seconds = 0.0
        self.committed_segments: list[dict] = []
        self.partial_segments: list[dict] = []
        self.saved_path: Optional[str] = None
        self._file = None
        self.raw_chunks: list[bytes] = []
        self.last_inferred_duration = 0.0
        self.last_audio_received_at: Optional[str] = None
        self.last_transcription_at: Optional[str] = None
        self.received_chunk_count = 0
        self.received_audio_bytes = 0
        self.received_audio_seconds = 0.0
        self.processed_audio_seconds = 0.0
        self.window_index = 0
        if config.write_to_file:
            self.saved_path = self._prepare_output_path(config.output_folder)
            self._file = open(self.saved_path, "a", encoding="utf-8")

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def finalize(self) -> dict:
        """停止時だけ暫定文を確定し、途中仮説を通常TXTへ書き込まない。"""
        remaining = _join_transcript("", self.partial_text)
        if remaining:
            self.committed_text = _join_transcript(self.committed_text, remaining)
            self._append_to_file(remaining)
            self.partial_segments = []
            self.partial_text = ""
        self.final_text = self.committed_text
        return {
            "text": self.final_text,
            "committed_text": self.final_text,
            "partial_text": "",
            "display_text": self.final_text,
            "result_id": f"{self.session_id}:final",
            "session_id": self.session_id,
            "window_index": self.window_index,
            "saved_path": self.saved_path,
        }

    def transcribe_chunk(self, audio_bytes: bytes) -> dict:
        self.last_audio_received_at = _timestamp()
        self.received_chunk_count += 1
        self.received_audio_bytes += len(audio_bytes or b"")
        if audio_bytes and self.config.send_mode == "full":
            self.raw_chunks = [audio_bytes]
        elif audio_bytes:
            self.raw_chunks.append(audio_bytes)

        joined_bytes = b"".join(self.raw_chunks)
        try:
            wav_result = convert_webm_bytes_to_wav(
                joined_bytes,
                self.config.mime_type,
                debug_save=self.config.debug_chunks,
            )
        except Exception as exc:
            return {
                "type": "result",
                "partial": "",
                "final": "",
                "timestamp": _timestamp(),
                "saved_path": self.saved_path,
                "model": self.config.model,
                    "rms": 0.0,
                    "skipped": True,
                    "skip_reason": f"convert_waiting_next_chunk: {exc}",
                    "duration_seconds": 0.0,
                    "debug_path": None,
                    "debug_wav_path": None,
                }

        with tempfile.TemporaryDirectory(prefix="bridgelog_live_tail_") as tmp:
            source_wav = Path(tmp) / "joined.wav"
            tail_wav = Path(tmp) / "tail.wav"
            source_wav.write_bytes(wav_result["wav_bytes"])
            duration = self._wav_duration_seconds(source_wav)
            self.received_audio_seconds = max(self.received_audio_seconds, duration)
            step_seconds = max(
                float(self.config.chunk_seconds) - float(self.config.overlap_seconds),
                1.0,
            )
            window_end = self.received_audio_seconds
            window_start = max(0.0, window_end - float(self.config.chunk_seconds))
            stable_until = max(window_start, window_end - float(self.config.overlap_seconds))
            new_audio_start = max(0.0, window_end - step_seconds)
            window_metadata = {
                "duration_seconds": window_end,
                "window_start": window_start,
                "window_end": window_end,
                "stable_until": stable_until,
                "new_audio_start": new_audio_start,
                "new_audio_end": window_end,
                "received_audio_seconds": self.received_audio_seconds,
                "processed_audio_seconds": self.processed_audio_seconds,
                "window_index": self.window_index,
            }
            if duration < float(self.config.chunk_seconds):
                return {
                    "type": "result",
                    "partial": "",
                    "final": "",
                    "timestamp": _timestamp(),
                    "saved_path": self.saved_path,
                    "model": self.config.model,
                    "rms": 0.0,
                    "skipped": True,
                    "skip_reason": f"waiting_more_audio duration={duration:.1f}s",
                    "debug_path": wav_result["debug_path"],
                    "debug_wav_path": wav_result["debug_wav_path"],
                    **window_metadata,
                }
            if self.last_inferred_duration > 0 and duration - self.last_inferred_duration < step_seconds:
                return {
                    "type": "result",
                    "partial": "",
                    "final": "",
                    "timestamp": _timestamp(),
                    "saved_path": self.saved_path,
                    "model": self.config.model,
                    "rms": 0.0,
                    "skipped": True,
                    "skip_reason": f"waiting_next_window duration={duration:.1f}s",
                    "debug_path": wav_result["debug_path"],
                    "debug_wav_path": wav_result["debug_wav_path"],
                    **window_metadata,
                }
            self._extract_tail_wav(source_wav, tail_wav)
            chunk_result = transcribe_wav_file(
                tail_wav,
                self.config.model,
                debug_save=False,
            )
            self.last_inferred_duration = duration
            self.processed_audio_seconds = duration
            self.last_transcription_at = _timestamp()

        text = _normalize_result_text(chunk_result["text"])
        model = chunk_result["model"]
        self.window_index += 1
        window_end = self.received_audio_seconds
        window_start = max(0.0, window_end - float(self.config.chunk_seconds))
        new_audio_start = max(0.0, window_end - max(step_seconds, 1.0))
        stable_until = max(window_start, window_end - float(self.config.overlap_seconds))
        committed_until_before = self.committed_until_seconds
        absolute_segments = []
        for segment in chunk_result.get("segments", []):
            absolute_start = window_start + float(segment.get("start", 0.0) or 0.0)
            absolute_end = window_start + float(segment.get("end", 0.0) or 0.0)
            absolute_segments.append({
                "start": absolute_start,
                "end": absolute_end,
                "text": _normalize_result_text(segment.get("text", "")),
            })

        commit_segments = []
        partial_segments = []
        for segment in absolute_segments:
            if not segment["text"] or segment["end"] <= self.committed_until_seconds:
                continue
            # 境界をまたぐsegmentは途中切断せず、次のwindowで再評価する。
            if segment["start"] >= self.committed_until_seconds and segment["end"] <= stable_until:
                commit_segments.append(segment)
            else:
                partial_segments.append(segment)

        committed_append = ""
        for segment in commit_segments:
            committed_append = _join_transcript(committed_append, segment["text"])
            self.committed_segments.append(segment)
            self.committed_until_seconds = max(self.committed_until_seconds, segment["end"])
        if committed_append:
            self.committed_text = _join_transcript(self.committed_text, committed_append)
            self._append_to_file(committed_append)
        self.partial_segments = partial_segments
        self.partial_text = " ".join(segment["text"] for segment in partial_segments).strip()
        fallback_warning = None
        if text and not committed_append and not self.partial_text:
            # セグメント時刻が欠落しても、認識文字列を画面から失わないよう暫定表示へ退避する。
            self.partial_text = text
            fallback_warning = "segment_classification_empty: 認識文字列をpartial_textへ退避しました"
        self.final_text = self.committed_text
        return {
            "type": "result",
            "partial": self.partial_text,
            "final": committed_append,
            "committed_append": committed_append,
            "committed_text": self.committed_text,
            "partial_text": self.partial_text,
            "committed_until": self.committed_until_seconds,
            "committed_until_before": committed_until_before,
            "stable_until": stable_until,
            "commit_segment_count": len(commit_segments),
            "partial_segment_count": len(partial_segments),
            "classification_warning": fallback_warning,
            "segments": absolute_segments,
            "display_text": f"{self.committed_text}{self.partial_text}".strip(),
            "timestamp": _timestamp(),
            "saved_path": self.saved_path,
            "model": model,
            "rms": chunk_result["rms"],
            "duration_seconds": duration,
            "received_audio_seconds": self.received_audio_seconds,
            "processed_audio_seconds": self.processed_audio_seconds,
            "window_start": window_start,
            "window_end": window_end,
            "stable_until": stable_until,
            "overlap_seconds": float(self.config.overlap_seconds),
            "new_audio_start": new_audio_start,
            "new_audio_end": window_end,
            "session_id": self.session_id,
            "window_index": self.window_index,
            "result_id": f"{self.session_id}:{self.window_index}",
            "skipped": chunk_result["skipped"] or not bool(text),
            "skip_reason": chunk_result["skip_reason"],
            "debug_path": wav_result["debug_path"],
            "debug_wav_path": wav_result["debug_wav_path"] or chunk_result["debug_wav_path"],
        }

    def _prepare_output_path(self, output_folder: str) -> str:
        if output_folder:
            folder = Path(output_folder).expanduser()
        else:
            folder = Path.home() / "Desktop" / f"bridgelog_live_{datetime.now().strftime('%Y%m%d')}"
        folder.mkdir(parents=True, exist_ok=True)
        filename = self.config.output_filename or f"meeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        return str(folder / filename)

    def _append_to_file(self, text: str) -> None:
        if self._file is None:
            return
        self._file.write(text + os.linesep)
        self._file.flush()

    def _extract_tail_wav(self, source_wav: Path, output_wav: Path) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        ffmpeg_dir = resolve_ffmpeg_dir()
        if ffmpeg_dir is not None and (ffmpeg_dir / "ffmpeg").is_file():
            ffmpeg_bin = str(ffmpeg_dir / "ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError("ffmpeg が見つかりません。")

        seconds = max(float(self.config.chunk_seconds), 1.0)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-sseof",
            f"-{seconds}",
            "-i",
            str(source_wav),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output_wav),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            raise RuntimeError(f"末尾wav抽出に失敗しました。{detail}".strip())

    def _wav_duration_seconds(self, wav_path: Path) -> float:
        with wave.open(str(wav_path), "rb") as wav:
            frame_rate = wav.getframerate()
            frame_count = wav.getnframes()
        if frame_rate <= 0:
            return 0.0
        return frame_count / float(frame_rate)
