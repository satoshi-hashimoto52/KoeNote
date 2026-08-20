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

from .live_transcriber import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    convert_webm_bytes_to_wav,
    transcribe_pcm16,
    transcribe_wav_file,
)
from .pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE, PcmRingBuffer, Resampler16k
from .transcriber import resolve_ffmpeg_dir


DELAY_PRESETS = {
    "low_latency": {"chunk_seconds": 8.0, "overlap_seconds": 2.0},
    "balanced": {"chunk_seconds": 10.0, "overlap_seconds": 2.0},
    "accuracy": {"chunk_seconds": 12.0, "overlap_seconds": 3.0},
}

# 推論が遅れたときに音声を捨てずに追いつける猶予。180秒で約5.8MB（録音長に依存しない）。
BUFFER_CAPACITY_SECONDS = 180.0
# 停止時にこれ未満の端切れしか残っていなければ、無理に1窓回さない。
MIN_FLUSH_TAIL_SECONDS = 0.5
# 遅延がこれを超えたら overlap を捨ててスループットを稼ぐ（音声を落とす前の安全弁）。
LAG_CATCHUP_THRESHOLD_SECONDS = 30.0


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
    sample_rate: int = SAMPLE_RATE
    debug: bool = False

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
        raw_send_mode = str(payload.get("send_mode", "")).lower()
        if raw_send_mode == "pcm16":
            send_mode = "pcm16"
        elif raw_send_mode == "full":
            send_mode = "full"
        else:
            send_mode = "chunks"
        sample_rate = int(_clamp_float(payload.get("sample_rate"), SAMPLE_RATE, 8000.0, 192000.0))
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
            send_mode=send_mode,
            sample_rate=sample_rate,
            debug=bool(payload.get("debug", False)),
        )


class LiveSession:
    """1 回のリアルタイム文字起こしセッションの状態。

    スレッド契約:
      - ``append_pcm`` / ``plan_window`` / ``advance_cursor`` は event loop スレッドから呼ぶ。
      - ``run_window`` / ``flush_tail`` は worker スレッドから、かつ同時に 1 本だけ呼ぶ。
      - ``committed_*`` は ``run_window`` 内でのみ変更し、loop 側は ``await`` 復帰後に読む。
      - PCM バッファは自身でロックを持つ。これ以外のロックは不要。
    """

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

        # --- PCM 経路（録音長に依存しない構造） ---
        self.pcm = PcmRingBuffer(capacity_seconds=BUFFER_CAPACITY_SECONDS)
        self._resampler = (
            Resampler16k(config.sample_rate) if config.sample_rate != SAMPLE_RATE else None
        )
        self.chunk_samples = max(int(round(config.chunk_seconds * SAMPLE_RATE)), SAMPLE_RATE)
        step_seconds = max(float(config.chunk_seconds) - float(config.overlap_seconds), 1.0)
        self.step_samples = max(int(round(step_seconds * SAMPLE_RATE)), SAMPLE_RATE)
        self.next_window_end = self.chunk_samples
        self.processed_samples = 0
        self.dropped_samples = 0
        self.degraded = False
        self.stopping = False

        if config.write_to_file:
            self.saved_path = self._prepare_output_path(config.output_folder)
            self._file = open(self.saved_path, "a", encoding="utf-8")

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

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
            "recorded_seconds": self.recorded_seconds,
            "dropped_seconds": self.dropped_seconds,
        }

    # ------------------------------------------------------------------
    # 進捗の参照（heartbeat / 診断表示用）
    # ------------------------------------------------------------------

    @property
    def recorded_seconds(self) -> float:
        return self.pcm.total_samples / float(SAMPLE_RATE)

    @property
    def dropped_seconds(self) -> float:
        return self.dropped_samples / float(SAMPLE_RATE)

    @property
    def lag_seconds(self) -> float:
        return max(0.0, (self.pcm.total_samples - self.processed_samples) / float(SAMPLE_RATE))

    def progress_snapshot(self) -> dict:
        return {
            "received_audio_seconds": round(self.received_audio_seconds, 2),
            "processed_audio_seconds": round(self.processed_audio_seconds, 2),
            "recorded_seconds": round(self.recorded_seconds, 2),
            "lag_seconds": round(self.lag_seconds, 2),
            "dropped_seconds": round(self.dropped_seconds, 2),
            "committed_length": len(self.committed_text),
            "window_index": self.window_index,
            "received_chunk_count": self.received_chunk_count,
            "last_audio_received_at": self.last_audio_received_at,
            "last_transcription_at": self.last_transcription_at,
            "degraded": self.degraded,
        }

    # ------------------------------------------------------------------
    # PCM 経路: 取り込み（event loop スレッド、必ず軽量）
    # ------------------------------------------------------------------

    def append_pcm(self, pcm: bytes) -> int:
        """受信 PCM をリングバッファへ追記する。ここでは推論しない。"""
        if not pcm:
            return self.pcm.total_samples
        if self._resampler is not None:
            pcm = self._resampler.process(pcm)
            if not pcm:
                return self.pcm.total_samples
        total = self.pcm.append(pcm)
        self.received_chunk_count += 1
        self.received_audio_bytes += len(pcm)
        self.received_audio_seconds = total / float(SAMPLE_RATE)
        self.last_audio_received_at = _timestamp()
        return total

    def append_gap(self, samples: int) -> int:
        """再接続で失われた区間を無音で埋め、絶対時刻を壁時計に合わせ続ける。"""
        if samples <= 0:
            return self.pcm.total_samples
        total = self.pcm.append_silence(samples)
        self.dropped_samples += samples
        self.received_audio_seconds = total / float(SAMPLE_RATE)
        return total

    # ------------------------------------------------------------------
    # PCM 経路: 窓の決定（event loop スレッド）
    # ------------------------------------------------------------------

    def _effective_step(self) -> int:
        # 遅れているときは overlap を捨てて追いつく（音声を落とすより先に試す手）。
        if self.lag_seconds > LAG_CATCHUP_THRESHOLD_SECONDS:
            return self.chunk_samples
        return self.step_samples

    def plan_window(self) -> Optional[tuple[int, int]]:
        """次に処理すべき窓 [start, end) を絶対サンプル番号で返す。

        ``window_end`` を常に「最新の音声」にすると、推論が遅れた分の音声が
        無言で文字起こしされないまま消える。カーソルを step ずつ進めることで、
        遅れても順に消化して取りこぼさない。
        """
        total = self.pcm.total_samples
        if total < self.next_window_end:
            return None

        earliest = self.pcm.earliest_sample
        start = max(0, self.next_window_end - self.chunk_samples)
        if start < earliest:
            # バッファ容量を超えて遅れた。落とす分を計上してから前方へ飛ぶ。
            skip_to = earliest + self.chunk_samples
            self.dropped_samples += max(0, skip_to - self.next_window_end)
            self.degraded = True
            self.next_window_end = skip_to
            if total < self.next_window_end:
                return None
            start = max(0, self.next_window_end - self.chunk_samples)
        return (start, self.next_window_end)

    def advance_cursor(self, end_sample: int) -> None:
        self.next_window_end = end_sample + self._effective_step()

    # ------------------------------------------------------------------
    # PCM 経路: 推論（worker スレッド、同時 1 本）
    # ------------------------------------------------------------------

    def run_window(self, start_sample: int, end_sample: int) -> Optional[dict]:
        """[start, end) を文字起こしして結果を返す。破棄済み区間なら None。"""
        pcm = self.pcm.read(start_sample, end_sample)
        if pcm is None:
            return None

        chunk_result = transcribe_pcm16(
            pcm,
            self.config.model,
            debug_save=self.config.debug_chunks,
        )
        window_start = start_sample / float(SAMPLE_RATE)
        window_end = end_sample / float(SAMPLE_RATE)
        stable_until = max(window_start, window_end - float(self.config.overlap_seconds))
        step_seconds = self.step_samples / float(SAMPLE_RATE)

        self.processed_samples = max(self.processed_samples, end_sample)
        self.processed_audio_seconds = self.processed_samples / float(SAMPLE_RATE)
        self.last_transcription_at = _timestamp()

        return self._apply_window_result(
            chunk_result,
            window_start=window_start,
            window_end=window_end,
            stable_until=stable_until,
            step_seconds=step_seconds,
            duration=window_end,
        )

    def flush_tail(self) -> Optional[dict]:
        """停止時、カーソルより先の端切れを回収する（現状は無言で捨てられている）。"""
        total = self.pcm.total_samples
        committed_samples = int(round(self.committed_until_seconds * SAMPLE_RATE))
        if total - committed_samples < int(MIN_FLUSH_TAIL_SECONDS * SAMPLE_RATE):
            return None

        start = max(self.pcm.earliest_sample, max(0, total - self.chunk_samples))
        if total <= start:
            return None
        pcm = self.pcm.read(start, total)
        if pcm is None:
            return None

        chunk_result = transcribe_pcm16(pcm, self.config.model)
        window_start = start / float(SAMPLE_RATE)
        window_end = total / float(SAMPLE_RATE)
        self.processed_samples = max(self.processed_samples, total)
        self.processed_audio_seconds = self.processed_samples / float(SAMPLE_RATE)
        self.last_transcription_at = _timestamp()
        # 停止時は overlap を残す理由がないので、末尾まで確定させる。
        return self._apply_window_result(
            chunk_result,
            window_start=window_start,
            window_end=window_end,
            stable_until=window_end,
            step_seconds=self.step_samples / float(SAMPLE_RATE),
            duration=window_end,
        )

    # ------------------------------------------------------------------
    # レガシー webm 経路
    # ------------------------------------------------------------------

    def transcribe_chunk(self, audio_bytes: bytes) -> dict:
        """レガシー webm 経路。送信済み音声を毎回全体デコードするため O(T^2)。

        live ルートは ``send_mode='full'`` を拒否するので、実運用では到達しない。
        回帰テストで窓・コミット判定の互換性を保証するために残している。
        """
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

        window_end = self.received_audio_seconds
        window_start = max(0.0, window_end - float(self.config.chunk_seconds))
        stable_until = max(window_start, window_end - float(self.config.overlap_seconds))
        return self._apply_window_result(
            chunk_result,
            window_start=window_start,
            window_end=window_end,
            stable_until=stable_until,
            step_seconds=step_seconds,
            duration=duration,
            extra={
                "debug_path": wav_result["debug_path"],
                "debug_wav_path": wav_result["debug_wav_path"] or chunk_result["debug_wav_path"],
            },
        )

    # ------------------------------------------------------------------
    # 窓の結果をコミット/暫定へ振り分ける（両経路の共通部）
    # ------------------------------------------------------------------

    def _apply_window_result(
        self,
        chunk_result: dict,
        *,
        window_start: float,
        window_end: float,
        stable_until: float,
        step_seconds: float,
        duration: float,
        extra: Optional[dict] = None,
    ) -> dict:
        text = _normalize_result_text(chunk_result["text"])
        model = chunk_result["model"]
        self.window_index += 1
        new_audio_start = max(0.0, window_end - max(step_seconds, 1.0))
        committed_until_before = self.committed_until_seconds
        committed_text_before = self.committed_text
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

        # 確定済みは再処理も再送もせず、差分だけをクライアントへ渡す。
        # _join_transcript の rstrip で前方一致が崩れた場合だけ全文同期を要求する。
        if self.committed_text.startswith(committed_text_before):
            committed_delta = self.committed_text[len(committed_text_before):]
            needs_snapshot = False
        else:
            committed_delta = ""
            needs_snapshot = True

        result = {
            "type": "result",
            "partial": self.partial_text,
            "final": committed_append,
            "committed_append": committed_append,
            "committed_text": self.committed_text,
            "committed_delta": committed_delta,
            "committed_length_before": len(committed_text_before),
            "committed_length": len(self.committed_text),
            "needs_snapshot": needs_snapshot,
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
            "overlap_seconds": float(self.config.overlap_seconds),
            "new_audio_start": new_audio_start,
            "new_audio_end": window_end,
            "session_id": self.session_id,
            "window_index": self.window_index,
            "result_id": f"{self.session_id}:{self.window_index}",
            "skipped": chunk_result["skipped"] or not bool(text),
            "skip_reason": chunk_result["skip_reason"],
            "debug_path": None,
            "debug_wav_path": chunk_result.get("debug_wav_path"),
            "lag_seconds": round(self.lag_seconds, 2),
            "dropped_seconds": round(self.dropped_seconds, 2),
        }
        if extra:
            result.update(extra)
        return result

    # ------------------------------------------------------------------
    # ファイル出力 / レガシー ffmpeg ヘルパ
    # ------------------------------------------------------------------

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
