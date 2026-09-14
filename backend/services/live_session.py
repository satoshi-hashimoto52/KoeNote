import os
import shutil
import subprocess
import tempfile
import wave
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .audio_levels import (
    ABSOLUTE_SILENCE_RMS,
    CLIP_LEVEL,
    LEVEL_FRAME_SAMPLES,
    MIN_SNR_DB,
    db_from_amplitude,
)
from .input_gain import AdaptiveGain
from .input_profile import InputProfile, build_profile
from .live_transcriber import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    convert_webm_bytes_to_wav,
    transcribe_pcm16,
    transcribe_wav_file,
)
from .pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE, PcmRingBuffer, Resampler16k
from .segments_writer import SegmentsWriter, build_record
from .transcriber import resolve_ffmpeg_dir
from .word_commit import (
    ANCHOR_WORDS,
    CURSOR_MARGIN_SECONDS,
    DRAIN_MAX_WINDOWS,
    EPS,
    MAX_ERROR_RETRIES,
    MAX_NO_PROGRESS_RETRIES,
    MAX_SAME_WINDOW_RETRIES,
    MIN_ADVANCE_SECONDS,
    CommitWord,
    DegradeReason,
    WindowCounters,
    classify_words,
    normalize_words,
    trim_committed,
)


DELAY_PRESETS = {
    "low_latency": {"chunk_seconds": 8.0, "overlap_seconds": 2.0},
    "balanced": {"chunk_seconds": 10.0, "overlap_seconds": 2.0},
    "accuracy": {"chunk_seconds": 12.0, "overlap_seconds": 3.0},
}

# 推論が遅れたときに音声を捨てずに追いつける猶予。180秒で約5.8MB（録音長に依存しない）。
BUFFER_CAPACITY_SECONDS = 180.0
# 停止時にこれ未満の端切れしか残っていなければ、無理に1窓回さない。
MIN_FLUSH_TAIL_SECONDS = 0.5
# 診断ログの保持行数（状態量を有界に保つ）。
MAX_DEGRADE_LOG_LINES = 200
# 遅延がこれを超えたら overlap を捨ててスループットを稼ぐ（音声を落とす前の安全弁）。
LAG_CATCHUP_THRESHOLD_SECONDS = 30.0
# 入力レベルの集計を診断へ吐く間隔。チャンクごとに書くとログが肥大化する（0019）。
LEVEL_REPORT_INTERVAL_SECONDS = 60.0
# 窓を推論へ回すために必要な発話フレーム数。1 で実測スキップ率 0.8%（旧 79.2%）。
MIN_SPEECH_FRAMES_PER_WINDOW = 1
# フレーム測定の履歴として保持する秒数。窓の発話判定に使うぶんだけあればよい。
LEVEL_HISTORY_SECONDS = 90.0


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
    # 0019: 入力モード / プリセット。未指定なら auto（デバイス名から判定）。
    input_profile: InputProfile = field(default_factory=InputProfile)

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
            input_profile=build_profile(
                payload.get("input_profile"),
                payload.get("device_label"),
            ),
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
        self.segments_path: Optional[str] = None
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

        # --- word 単位の確定（方式G） ---
        # 確定済み末尾。アンカー照合に使うぶんだけ保持するので状態量は有界。
        self.committed_words: list[CommitWord] = []
        # 直近1窓の未確定語。表示専用でファイルへは出さない。
        self.pending_words: list[CommitWord] = []
        self.counters = WindowCounters()
        self.degrade_log: list[str] = []
        self._error_retries = 0
        self._pending_rounds = 0
        self._same_window_count = 0
        self._no_progress_rounds = 0
        self._giving_up_on_window = False
        self._needs_recheck = False
        self._last_planned: Optional[tuple[int, int]] = None

        # --- 0019: 入力レベルの補正と測定 ---
        self.profile = config.input_profile
        self.gain = AdaptiveGain(self.profile, sample_rate=SAMPLE_RATE,
                                 frame_samples=LEVEL_FRAME_SAMPLES)
        # フレーム測定の履歴。絶対サンプル番号で addressing し、古い分は捨てる。
        # (frame_end_sample, raw_rms, is_speech) の 3 つ組だけ持つので状態量は有界。
        self._level_frames: list[tuple[int, float, bool]] = []
        self._level_history_frames = max(
            int(LEVEL_HISTORY_SECONDS * SAMPLE_RATE / LEVEL_FRAME_SAMPLES), 1
        )
        self._level_reported_at = 0.0
        self.level_state = "ok"
        self.level_state_changed_at: Optional[str] = None
        # 診断へ出すための累計。level_skipped と vad_silence は意味が違う（0019）。
        self.level_skipped_samples = 0
        self.vad_silence_samples = 0
        self.repetitive_dropped_count = 0
        self.pending_diagnostics: list[str] = []

        self._segments_writer: Optional[SegmentsWriter] = None
        if config.write_to_file:
            self.saved_path = self._prepare_output_path(config.output_folder)
            self._file = open(self.saved_path, "a", encoding="utf-8")
            # 0019 / 旧 #0004: session.json の宣言どおり segments も実際に書き出す。
            try:
                self._segments_writer = SegmentsWriter(Path(self.saved_path).parent)
            except OSError:
                # セグメント保存は補助情報。作れなくても録音は続ける。
                self._segments_writer = None

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._segments_writer is not None:
            self.segments_path = self._segments_writer.finalize()
            self._segments_writer = None

    @property
    def segments_written(self) -> int:
        return self._segments_writer.count if self._segments_writer is not None else 0

    def finalize(self) -> dict:
        """最終確定。partial_text を無条件に追記しない。

        PCM 経路では drain_on_stop が同じ判定で末尾まで確定させるため、ここでは
        追記しない（無条件追記は既確定分の二重書き込みの原因になる）。
        レガシー webm 経路には drain が無いので、そちらだけ従来どおり回収する。
        """
        if self.config.send_mode != "pcm16":
            remaining = _join_transcript("", self.partial_text)
            if remaining:
                self.committed_text = _join_transcript(self.committed_text, remaining)
                self._append_to_file(remaining)
                self.partial_segments = []
                self.partial_text = ""
        elif self.pending_words:
            # drain 後に残っていてはいけない。残っていたら異常として記録する。
            self.log_degraded(
                DegradeReason.DRAIN_UNRECOVERED,
                pending_words=len(self.pending_words),
                phase="finalize",
            )
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
            "counters": self.counters.as_dict(),
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
            "pending_word_count": len(self.pending_words),
            "window_index": self.window_index,
            "received_chunk_count": self.received_chunk_count,
            "last_audio_received_at": self.last_audio_received_at,
            "last_transcription_at": self.last_transcription_at,
            "degraded": self.degraded,
            **self.counters.as_dict(),
        }

    # ------------------------------------------------------------------
    # PCM 経路: 取り込み（event loop スレッド、必ず軽量）
    # ------------------------------------------------------------------

    def append_pcm(self, pcm: bytes) -> int:
        """受信 PCM を補正してリングバッファへ追記する。ここでは推論しない。

        **補正するのは解析用のリングバッファだけ。** `recording.wav` へは
        呼び出し側（routes/whisper.py）が補正前の生バイトを書く。
        `AdaptiveGain.process` は入力と同じサンプル数を返すため、
        `pcm.total_samples`（= server_total_samples）はクライアントの送信済み
        サンプル数と一致し続ける。
        """
        if not pcm:
            return self.pcm.total_samples
        if self._resampler is not None:
            pcm = self._resampler.process(pcm)
            if not pcm:
                return self.pcm.total_samples

        base = self.pcm.total_samples
        pending_before = self.gain.pending_samples
        corrected, levels = self.gain.process(pcm)
        pcm = corrected if corrected else pcm
        total = self.pcm.append(pcm)
        if levels:
            self._record_levels(levels, base, pending_before)
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

    # ------------------------------------------------------------------
    # 0019: 入力レベルの記録と判定
    # ------------------------------------------------------------------

    def _record_levels(self, levels, base_sample: int, pending_before: int) -> None:
        """完成したフレームを絶対サンプル番号付きで履歴へ積む。

        フレーム k はこの呼び出しの入力を
        ``(frame - pending_before) + k * frame`` サンプル消費した時点で完成する。
        絶対位置はリングバッファの座標系（= server_total_samples）で持つ。
        """
        frame = self.gain.frame_samples
        first_end = base_sample + (frame - pending_before)
        for k, level in enumerate(levels):
            end_sample = first_end + k * frame
            self._level_frames.append((end_sample, level.raw_rms, level.is_speech))
        if len(self._level_frames) > self._level_history_frames:
            del self._level_frames[: len(self._level_frames) - self._level_history_frames]

    def window_has_speech(self, start_sample: int, end_sample: int) -> bool:
        """窓に発話フレームが含まれるか。

        **窓平均 RMS では判定しない**（0019 の主原因）。10 秒窓の平均は発話の
        合間の無音を含むため、ノイズフロアで相対化しても実測スキップ率 85.7%
        に達し、遠方の通常発話を捨ててしまう。
        判定は「窓内にノイズフロア +9dB を超えるフレームがあるか」で行う。

        測定履歴が無い区間（履歴から溢れた古い窓など）は True を返す。
        判断材料が無いときに捨てる側へ倒してはいけない。
        """
        frame = self.gain.frame_samples
        hit = 0
        seen = 0
        for end, _raw, is_speech in self._level_frames:
            if end <= start_sample or end - frame >= end_sample:
                continue
            seen += 1
            if is_speech:
                hit += 1
                if hit >= MIN_SPEECH_FRAMES_PER_WINDOW:
                    return True
        if seen == 0:
            return True
        return False

    def evaluate_level_state(self, window_seconds: float = 20.0) -> str:
        """直近の入力レベルから状態を 1 つ決める（0019）。

        返す値と意味:
            ok        正常
            silent_ok 発話が無く本当に静か（**警告してはいけない**通常の無言時間）
            no_input  完全に入力がない
            too_quiet 音量が小さい（最大ゲインでも目標へ届かない）
            low_snr   環境音が大きすぎる
            clipping  音割れしている

        Backend/文字起こしの停止はここでは扱わない。既存の無進捗ウォッチドッグ
        （frontend/features/transcription/watchdog.ts）の担当であり、
        両者を混ぜると誤検知が相互に汚染する。

        「継続時間」の判定は行わない。UI 方針（何秒で出すか、いつ消すか）は
        frontend の lowVolumeWarning が持つ。ここは瞬時状態だけを返す。
        """
        if not self._level_frames:
            return "ok"
        cutoff = self.pcm.total_samples - int(max(window_seconds, 1.0) * SAMPLE_RATE)
        recent = [f for f in self._level_frames if f[0] > cutoff]
        if not recent:
            recent = self._level_frames[-1:]

        raws = [f[1] for f in recent]
        speech = [f[1] for f in recent if f[2]]

        if max(raws) < ABSOLUTE_SILENCE_RMS:
            return "no_input"

        telemetry = self.gain.telemetry
        if telemetry.frames > 0:
            # 天井に張り付くフレームが多い = 入力が大きすぎる。
            if telemetry.limited_frames / telemetry.frames > 0.05:
                return "clipping"
        if max(raws) >= CLIP_LEVEL:
            return "clipping"

        if not speech:
            # 入力はあるが発話が無い。無言時間として正常に扱う。
            return "silent_ok"

        speech_sorted = sorted(speech)
        median = speech_sorted[len(speech_sorted) // 2]
        floor = max(self.gain.noise_floor, 1e-9)
        snr_db = db_from_amplitude(median) - db_from_amplitude(floor)
        if snr_db < MIN_SNR_DB:
            return "low_snr"

        required_db = self.profile.target_speech_dbfs - db_from_amplitude(median)
        if required_db > self.profile.max_gain_db:
            return "too_quiet"
        return "ok"

    def level_snapshot(self) -> dict:
        """UI・診断へ渡す現在のレベル状態。"""
        recorded = max(self.recorded_seconds, 1e-9)
        return {
            "input_mode": self.profile.mode,
            "detected_mode": self.profile.detected_mode,
            "gain_mode": self.profile.gain_mode,
            "gain_db": round(self.gain.gain_db, 1),
            "noise_floor_dbfs": round(db_from_amplitude(self.gain.noise_floor), 1),
            "speech_threshold_dbfs": round(db_from_amplitude(self.gain.speech_threshold()), 1),
            "level_skipped_seconds": round(self.level_skipped_samples / float(SAMPLE_RATE), 2),
            "level_skipped_ratio": round(
                (self.level_skipped_samples / float(SAMPLE_RATE)) / recorded, 4
            ),
            "vad_silence_seconds": round(self.vad_silence_samples / float(SAMPLE_RATE), 2),
            "repetitive_dropped_count": self.repetitive_dropped_count,
            "level_state": self.level_state,
        }

    def maybe_report_levels(self) -> Optional[str]:
        """60 秒ごとに集計を 1 行だけ返す。まだなら None。

        チャンクごとにログを書くと diagnostics.log が肥大化するため、
        一定間隔の集計と状態変化時だけに絞る（0019）。
        """
        now = self.recorded_seconds
        if now - self._level_reported_at < LEVEL_REPORT_INTERVAL_SECONDS:
            return None
        self._level_reported_at = now
        telemetry = self.gain.telemetry.as_dict()
        self.gain.telemetry.reset()
        if not telemetry["frames"]:
            return None
        snapshot = self.level_snapshot()
        parts = [
            f"window={LEVEL_REPORT_INTERVAL_SECONDS:.0f}s",
            f"at={now:.0f}s",
            f"noise_floor_dbfs={telemetry['noise_floor_dbfs']}",
            f"raw_rms_dbfs={telemetry['raw_rms_dbfs']}",
            f"corrected_rms_dbfs={telemetry['corrected_rms_dbfs']}",
            f"gain_db={telemetry['gain_db']}",
            f"gain_range_db={telemetry['gain_min_db']}..{telemetry['gain_max_db']}",
            f"raw_peak_dbfs={telemetry['raw_peak_dbfs']}",
            f"corrected_peak_dbfs={telemetry['corrected_peak_dbfs']}",
            f"limited_frames={telemetry['limited_frames']}",
            f"clipped_samples={telemetry['clipped_samples']}",
            f"speech_frame_ratio={telemetry['speech_frame_ratio']}",
            f"level_skipped_seconds={snapshot['level_skipped_seconds']}",
            f"level_skipped_ratio={snapshot['level_skipped_ratio']}",
            f"vad_silence_seconds={snapshot['vad_silence_seconds']}",
            f"repetitive_dropped={snapshot['repetitive_dropped_count']}",
        ]
        return "input_levels " + " ".join(parts)

    def set_level_state(self, state: str) -> Optional[str]:
        """レベル状態が変わったときだけ診断行を返す。連続記録を避ける。"""
        if state == self.level_state:
            return None
        previous = self.level_state
        self.level_state = state
        self.level_state_changed_at = _timestamp()
        return f"input_level_state from={previous} to={state} at={self.recorded_seconds:.0f}s"

    def log_degraded(self, reason: str, **detail) -> None:
        """縮退・強制の発生を reason コード付きで記録する。"""
        parts = " ".join(f"{k}={v}" for k, v in detail.items())
        line = f"[{_timestamp()}] {reason} {parts}".rstrip()
        self.degrade_log.append(line)
        if len(self.degrade_log) > MAX_DEGRADE_LOG_LINES:
            del self.degrade_log[: len(self.degrade_log) - MAX_DEGRADE_LOG_LINES]
        print(f"[degraded] session={self.session_id} {line}", flush=True)

    def _effective_step(self) -> int:
        # 遅れているときは overlap を捨てて追いつく（音声を落とすより先に試す手）。
        if self.lag_seconds > LAG_CATCHUP_THRESHOLD_SECONDS:
            return self.chunk_samples
        return self.step_samples

    # ------------------------------------------------------------------
    # PCM 経路: 窓の決定（event loop スレッド）
    # ------------------------------------------------------------------

    def plan_window(self) -> Optional[tuple[int, int]]:
        """次に処理すべき窓 [start, end) を絶対サンプル番号で返す。

        ``window_end`` を常に「最新の音声」にすると、推論が遅れた分の音声が
        無言で文字起こしされないまま消える。カーソルを step ずつ進めることで、
        遅れても順に消化して取りこぼさない。
        """
        # 推論に連続して失敗した窓は、上限を超えたら最小前進で先へ出す。
        # 取り逃した区間は停止時の排出で回収する。
        if self._error_retries > MAX_ERROR_RETRIES:
            self._error_retries = 0
            self.counters.cursor_min_advance_count += 1
            self.log_degraded(
                DegradeReason.INFERENCE_RETRY_EXHAUSTED,
                next_window_end=self.next_window_end,
            )
            self.next_window_end += self._min_advance_samples()

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

        plan = (start, self.next_window_end)
        if not self._check_window_safety(plan):
            return None

        # 同じ窓を無限に処理しないための上限。
        if self._last_planned == plan:
            self._same_window_count += 1
            if self._same_window_count >= MAX_SAME_WINDOW_RETRIES:
                self.counters.same_window_retry_count += 1
                self.log_degraded(
                    DegradeReason.SAME_WINDOW_RETRY,
                    window=f"{plan[0]}-{plan[1]}",
                    count=self._same_window_count,
                )
                self._same_window_count = 0
                self.next_window_end += self._min_advance_samples()
                return self.plan_window()
        else:
            self._same_window_count = 0
        self._last_planned = plan
        return plan

    def _min_advance_samples(self) -> int:
        return max(int(round(MIN_ADVANCE_SECONDS * SAMPLE_RATE)), 1)

    def _check_window_safety(self, plan: tuple[int, int]) -> bool:
        """窓が安全かを実行時に検証する。assert に依存せずログと縮退で処理する。"""
        start, end = plan
        if end <= start:
            self.log_degraded(DegradeReason.CURSOR_WINDOW_CLAMPED, cause="empty", start=start, end=end)
            return False
        length = end - start
        if length > self.chunk_samples + self._min_advance_samples():
            # 窓長が想定を超えた。chunk に収め直す。
            self.log_degraded(
                DegradeReason.CURSOR_WINDOW_CLAMPED,
                cause="too_long",
                length_seconds=round(length / SAMPLE_RATE, 2),
            )
            return False
        if start < self.pcm.earliest_sample:
            self.log_degraded(
                DegradeReason.CURSOR_WINDOW_CLAMPED,
                cause="outside_ring_buffer",
                start=start,
                earliest=self.pcm.earliest_sample,
            )
            return False
        return True

    def advance_cursor(self, end_sample: int) -> None:
        """カーソルを進める。未確定音声の先頭を飛び越えない。

        更新式:
            pending なし: next = end + step
            pending あり: next = min(end + step,
                                     round((pending[0].start - CURSOR_MARGIN) * SR) + chunk)
            共通:         next <= end なら next = end + MIN_ADVANCE
        """
        previous = end_sample
        step = self._effective_step()
        candidate = end_sample + step

        # 次窓が「未確定の音声の先頭」を必ず含むように制限する。
        # 基準は次の2つの早い方:
        #   - 保留語の先頭 - CURSOR_MARGIN
        #     （報告時刻に推定誤差があるため余裕を引く）
        #   - 確定端 committed_until（余裕は引かない）
        #     確定端は確定した語の終端そのもので、そこから始まる窓は未確定分を必ず含む。
        #     余裕を引くと通常時も窓が余分に重なり、step が縮んでしまう。
        #     保留だけを基準にすると、whisper が窓の一部を文字化しなかった区間
        #     （保留にも入らない）を飛び越えてしまう。実測で 8.2 秒の欠落が起きた。
        anchor_seconds = None
        if self.pending_words:
            anchor_seconds = max(0.0, self.pending_words[0].start - CURSOR_MARGIN_SECONDS)
        if self._needs_recheck and not self._giving_up_on_window:
            # 音声があるのに確定できなかった窓の直後だけ、確定端まで引き戻す。
            hard = self.committed_until_seconds
            anchor_seconds = hard if anchor_seconds is None else min(anchor_seconds, hard)
        if anchor_seconds is not None:
            limit = int(round(max(0.0, anchor_seconds) * SAMPLE_RATE)) + self.chunk_samples
            if limit < candidate:
                candidate = limit

        if candidate <= previous:
            # 時刻推定誤差などで前進できない場合の保護。
            self.counters.cursor_min_advance_count += 1
            self.log_degraded(
                DegradeReason.CURSOR_MIN_ADVANCE,
                previous=previous,
                candidate=candidate,
            )
            candidate = previous + self._min_advance_samples()

        # 未確定音声の先頭を飛び越えていないことの検証。
        anchor_samples = int(round((anchor_seconds if anchor_seconds is not None else 0.0)
                                   * SAMPLE_RATE))
        if (anchor_seconds is not None and candidate - self.chunk_samples > anchor_samples
                and not self._giving_up_on_window):
            self.log_degraded(
                DegradeReason.CURSOR_WOULD_SKIP,
                anchor=anchor_samples,
                window_start=candidate - self.chunk_samples,
            )
            candidate = max(previous + self._min_advance_samples(),
                            anchor_samples + self.chunk_samples)

        self.next_window_end = candidate

    # ------------------------------------------------------------------
    # PCM 経路: 推論（worker スレッド、同時 1 本）
    # ------------------------------------------------------------------

    def _infer_range(self, start_sample: int, end_sample: int) -> Optional[dict]:
        """[start, end) を推論する。失敗時は None（カーソルを進めない）。"""
        pcm = self.pcm.read(start_sample, end_sample)
        if pcm is None:
            return None
        # 窓平均 RMS ではなくフレーム基準で発話の有無を決める（0019）。
        has_speech = self.window_has_speech(start_sample, end_sample)
        try:
            return transcribe_pcm16(
                pcm,
                self.config.model,
                debug_save=self.config.debug_chunks,
                # 補正後 PCM に対する絶対下限。無効入力だけを弾く。
                silence_rms=ABSOLUTE_SILENCE_RMS,
                has_speech=has_speech,
            )
        except Exception as exc:
            self.counters.inference_error_count += 1
            self._error_retries += 1
            self.log_degraded(
                DegradeReason.INFERENCE_FAILED,
                error_type=type(exc).__name__,
                message=str(exc)[:120],
                retries=self._error_retries,
            )
            return None

    def _absolute_segments(self, chunk_result: dict, window_start: float) -> list[dict]:
        """窓内相対時刻の segment を絶対時刻へ移す。"""
        segments = []
        for segment in chunk_result.get("segments", []):
            words = []
            for word in segment.get("words", []) or []:
                start, end = word.get("start"), word.get("end")
                words.append({
                    "start": None if start is None else window_start + float(start),
                    "end": None if end is None else window_start + float(end),
                    "text": word.get("text", ""),
                })
            segments.append({
                "start": window_start + float(segment.get("start", 0.0) or 0.0),
                "end": window_start + float(segment.get("end", 0.0) or 0.0),
                "text": segment.get("text", ""),
                "words": words,
            })
        return segments

    def run_window(self, start_sample: int, end_sample: int) -> Optional[dict]:
        """[start, end) を文字起こしして結果を返す。

        None を返すのは「カーソルを進めてはいけない」場合（破棄済み区間 / 推論失敗）。
        """
        before = self.committed_until_seconds
        result = self._process_window(start_sample, end_sample, final=False)
        if result is None:
            return None

        # 0019: 「音声があるか」は固定 RMS ではなくフレーム基準の相対判定で決める。
        # 旧実装は rms < MIN_RMS を「正常な無音」として黙って飛ばしていたため、
        # 79% の窓が捨てられても診断に何も残らなかった。
        window_seconds = max(0.0, (end_sample - start_sample) / float(SAMPLE_RATE))
        if result.get("level_skipped"):
            # レベル判定で推論へ回さなかった。正常な無音であっても必ず計上する。
            self.level_skipped_samples += max(0, end_sample - start_sample)
            self.counters.level_skipped_seconds += window_seconds
            has_speech = False
        else:
            has_speech = self.window_has_speech(start_sample, end_sample)
            if result.get("no_speech"):
                # 推論はしたが Silero VAD / 品質判定で発話が採れなかった。
                self.vad_silence_samples += max(0, end_sample - start_sample)
                self.counters.vad_silence_seconds += window_seconds
        dropped = int(result.get("repetitive_dropped") or 0)
        if dropped:
            self.repetitive_dropped_count += dropped
            self.counters.repetitive_dropped_count += dropped
            self.log_degraded(
                DegradeReason.REPETITIVE_SEGMENT_DROPPED,
                window=f"{start_sample / SAMPLE_RATE:.2f}-{end_sample / SAMPLE_RATE:.2f}",
                count=dropped,
            )

        if self.committed_until_seconds > before + EPS:
            self._no_progress_rounds = 0
            self._giving_up_on_window = False
            self._needs_recheck = False
        elif not has_speech:
            # 発話フレームが無い窓で確定が進まないのは正常。カーソルは通常どおり進める。
            # ここで再確認を要求すると無音区間を最小前進で這うことになる。
            # ただしスキップ秒数は上で必ず計上済み。黙って隠さない（0019）。
            self._no_progress_rounds = 0
            self._needs_recheck = False
        else:
            # 音声はあるのに確定語が出なかった。whisper が窓の一部を文字化しない
            # ことがあるため、窓境界をずらして再確認する。
            self._needs_recheck = True
            self._no_progress_rounds += 1
            self.log_degraded(
                DegradeReason.WINDOW_YIELDED_NOTHING,
                window=f"{start_sample / SAMPLE_RATE:.2f}-{end_sample / SAMPLE_RATE:.2f}",
                attempt=self._no_progress_rounds,
                committed_until=round(before, 2),
                rms=round(float(result.get("rms", 0.0) or 0.0), 4),
            )
            if self._no_progress_rounds >= MAX_NO_PROGRESS_RETRIES:
                skipped = max(0.0, end_sample / SAMPLE_RATE
                              - float(self.config.overlap_seconds) - before)
                self.counters.untranscribed_seconds += skipped
                self.log_degraded(
                    DegradeReason.AUDIO_LEFT_UNTRANSCRIBED,
                    seconds=round(skipped, 2),
                    from_seconds=round(before, 2),
                )
                self._no_progress_rounds = 0
                self._giving_up_on_window = True
                self._needs_recheck = False
        return result

    def _process_window(self, start_sample: int, end_sample: int, final: bool) -> Optional[dict]:
        window_start = start_sample / float(SAMPLE_RATE)
        window_end = end_sample / float(SAMPLE_RATE)
        chunk_result = self._infer_range(start_sample, end_sample)
        if chunk_result is None:
            return None
        self._error_retries = 0

        segments = self._absolute_segments(chunk_result, window_start)
        words = normalize_words(segments, window_start, window_end, self.counters, self.log_degraded)

        # 縮退（word 情報が無く segment 全体を擬似 word として扱う）時に既確定と重なると、
        # テキスト系列で照合できず分割もできない。未確定区間だけを再推論して重なりを消す。
        if any(w.pseudo and w.start < self.committed_until_seconds - EPS for w in words) \
                and self.committed_until_seconds < window_end - EPS:
            reinfer_start = max(start_sample, int(round(self.committed_until_seconds * SAMPLE_RATE)))
            if reinfer_start < end_sample:
                self.counters.reinferred_count += 1
                self.log_degraded(
                    DegradeReason.REINFER_UNCONFIRMED_RANGE,
                    window=f"{window_start:.2f}-{window_end:.2f}",
                    reinfer_from=round(reinfer_start / SAMPLE_RATE, 2),
                )
                again = self._infer_range(reinfer_start, end_sample)
                if again is None:
                    self.log_degraded(DegradeReason.REINFER_FAILED)
                    return None
                chunk_result = again
                window_start = reinfer_start / float(SAMPLE_RATE)
                segments = self._absolute_segments(chunk_result, window_start)
                words = normalize_words(segments, window_start, window_end,
                                        self.counters, self.log_degraded)

        stable_until = window_end if final else max(window_start,
                                                    window_end - float(self.config.overlap_seconds))
        anchor = self.committed_words[-ANCHOR_WORDS:]
        fresh = trim_committed(words, self.committed_until_seconds, anchor,
                               self.counters, self.log_degraded)
        confirm, pending = classify_words(fresh, window_start, stable_until,
                                          self.counters, self.log_degraded)

        self.processed_samples = max(self.processed_samples, end_sample)
        self.processed_audio_seconds = self.processed_samples / float(SAMPLE_RATE)
        self.last_transcription_at = _timestamp()

        return self._apply_word_window(
            chunk_result,
            confirm=confirm,
            pending=pending,
            window_start=window_start,
            window_end=window_end,
            stable_until=stable_until,
        )

    def _apply_word_window(
        self,
        chunk_result: dict,
        *,
        confirm: list,
        pending: list,
        window_start: float,
        window_end: float,
        stable_until: float,
    ) -> dict:
        """確定語をコミットし、差分を含む結果を作る。"""
        self.window_index += 1
        committed_text_before = self.committed_text
        committed_until_before = self.committed_until_seconds

        committed_append = ""
        for word in confirm:
            committed_append = f"{committed_append}{word.text}"
            self.committed_segments.append({"start": word.start, "end": word.end, "text": word.text})
            self.committed_words.append(word)
            self.committed_until_seconds = max(self.committed_until_seconds, word.end)
        # アンカーに必要なぶんだけ保持する（状態量を録音時間に依存させない）。
        if len(self.committed_words) > ANCHOR_WORDS:
            del self.committed_words[: len(self.committed_words) - ANCHOR_WORDS]

        committed_append = committed_append.strip()
        if committed_append:
            self.committed_text = _join_transcript(self.committed_text, committed_append)
            self._append_to_file(committed_append)
            self._append_segment_record(confirm, chunk_result)

        self.pending_words = pending
        self.partial_segments = [{"start": w.start, "end": w.end, "text": w.text} for w in pending]
        self.partial_text = "".join(w.text for w in pending).strip()
        self.final_text = self.committed_text

        if self.committed_text.startswith(committed_text_before):
            committed_delta = self.committed_text[len(committed_text_before):]
            needs_snapshot = False
        else:
            committed_delta = ""
            needs_snapshot = True

        step_seconds = self.step_samples / float(SAMPLE_RATE)
        return {
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
            "commit_segment_count": len(confirm),
            "partial_segment_count": len(pending),
            "classification_warning": None,
            "segments": [{"start": w.start, "end": w.end, "text": w.text} for w in confirm + pending],
            "display_text": f"{self.committed_text}{self.partial_text}".strip(),
            "timestamp": _timestamp(),
            "saved_path": self.saved_path,
            "model": chunk_result["model"],
            "rms": chunk_result["rms"],
            "duration_seconds": window_end,
            "received_audio_seconds": self.received_audio_seconds,
            "processed_audio_seconds": self.processed_audio_seconds,
            "window_start": window_start,
            "window_end": window_end,
            "overlap_seconds": float(self.config.overlap_seconds),
            "new_audio_start": max(0.0, window_end - max(step_seconds, 1.0)),
            "new_audio_end": window_end,
            "session_id": self.session_id,
            "window_index": self.window_index,
            "result_id": f"{self.session_id}:{self.window_index}",
            "skipped": not committed_append and not self.partial_text,
            "skip_reason": chunk_result["skip_reason"],
            "debug_path": None,
            "debug_wav_path": chunk_result.get("debug_wav_path"),
            "lag_seconds": round(self.lag_seconds, 2),
            "dropped_seconds": round(self.dropped_seconds, 2),
            "counters": self.counters.as_dict(),
        }

    # ------------------------------------------------------------------
    # 停止時の排出
    # ------------------------------------------------------------------

    def drain_on_stop(self) -> list[dict]:
        """停止後、確定端から音声終端まで取り逃しなく走査して確定する。

        終了条件:
          1. probe が音声終端へ到達（正常）
          2. pending が空で確定端が終端に達した（正常）
          3. DRAIN_MAX_WINDOWS に到達（異常。回収できなかった秒数を記録）
        """
        results: list[dict] = []
        total = self.pcm.total_samples
        probe = int(round(self.committed_until_seconds * SAMPLE_RATE))
        probe = max(probe, self.pcm.earliest_sample)
        guard = 0
        min_advance = self._min_advance_samples()

        while probe < total - int(MIN_FLUSH_TAIL_SECONDS * SAMPLE_RATE) and guard < DRAIN_MAX_WINDOWS:
            guard += 1
            self.counters.drain_window_count += 1
            end = min(probe + self.chunk_samples, total)
            start = max(self.pcm.earliest_sample, max(0, end - self.chunk_samples))
            if end <= start:
                break
            before = self.committed_until_seconds
            # stable = window_end。停止後は overlap を残す理由がないので全て確定可能。
            result = self._process_window(start, end, final=True)
            if result is not None:
                results.append(result)
                # 排出時は保留を残さない。強制確定として記録する。
                if self.pending_words:
                    forced = self.pending_words
                    self.pending_words = []
                    for word in forced:
                        self.counters.forced_commit_count += 1
                        self.log_degraded(
                            DegradeReason.FORCED_COMMIT_PENDING,
                            start=round(word.start, 3),
                            end=round(word.end, 3),
                            pseudo=word.pseudo,
                        )
                    tail = self._apply_word_window(
                        {"model": self.config.model, "rms": 0.0, "skip_reason": "",
                         "debug_wav_path": None, "segments": []},
                        confirm=forced, pending=[],
                        window_start=forced[0].start, window_end=forced[-1].end,
                        stable_until=forced[-1].end,
                    )
                    results.append(tail)
            if self.committed_until_seconds > before + EPS:
                probe = int(round(self.committed_until_seconds * SAMPLE_RATE))
            else:
                # この窓では何も確定できなかった（無音 or 推論失敗）。
                # 打ち切らず最小前進で先へ進め、全区間を必ず走査する。
                probe += min_advance

        if guard >= DRAIN_MAX_WINDOWS:
            self.log_degraded(DegradeReason.DRAIN_LIMIT_REACHED, windows=guard)
        unrecovered = max(0.0, (total - max(probe, int(round(self.committed_until_seconds * SAMPLE_RATE))))
                          / float(SAMPLE_RATE))
        if unrecovered > MIN_FLUSH_TAIL_SECONDS:
            self.counters.unrecovered_seconds = unrecovered
            self.log_degraded(DegradeReason.DRAIN_UNRECOVERED, seconds=round(unrecovered, 2))
        return results

    def flush_tail(self) -> Optional[dict]:
        """後方互換のため残す。drain_on_stop の最後の結果を返す。"""
        results = self.drain_on_stop()
        return results[-1] if results else None

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

        with tempfile.TemporaryDirectory(prefix="koenote_live_tail_") as tmp:
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
            folder = Path.home() / "Desktop" / f"koenote_live_{datetime.now().strftime('%Y%m%d')}"
        folder.mkdir(parents=True, exist_ok=True)
        filename = self.config.output_filename or f"meeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        return str(folder / filename)

    def _append_segment_record(self, confirm: list, chunk_result: dict) -> None:
        """確定したぶんを transcript_segments.jsonl へ 1 行追記する（0019）。

        確定は word 単位だが、1 語 1 行では実用にならないため、
        **1 回の確定バッチ = 1 レコード**にする。transcript.txt への追記単位と
        一致するので、2 つのファイルの内容がずれない。
        時刻はセッション基準の絶対経過秒。
        """
        if self._segments_writer is None or not confirm:
            return
        quality = chunk_result.get("quality") or {}
        self._segments_writer.append(
            build_record(
                start=confirm[0].start,
                end=confirm[-1].end,
                text="".join(word.text for word in confirm).strip(),
                state="committed",
                avg_logprob=quality.get("avg_logprob"),
                no_speech_prob=quality.get("no_speech_prob"),
                compression_ratio=quality.get("compression_ratio"),
            )
        )

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
