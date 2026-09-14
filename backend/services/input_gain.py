"""解析用 PCM のストリーミング音量補正（0019）。

**録音ファイルには一切適用しない。** `recording.wav` は補正前の生音声を保存し、
ここで作った補正後 PCM は Whisper へ渡す解析経路にだけ流す
（`LiveSession.append_pcm` の中でのみ使う）。

不変条件:
    - **出力サンプル数は入力サンプル数と常に等しい。**
      フレーム境界でバッファリングすると `session.pcm.total_samples` が
      クライアントの送信済みサンプル数から遅れ、再接続時に誤った `gap` が
      挿入されて絶対時刻がずれる。測定だけをフレーム単位で行い、
      ゲイン適用は到着したぶんを即座に返す。
    - クリッピングを発生させない（直近ピークでゲインを頭打ち + 天井で clip）
    - 無音・環境音だけの区間ではゲインを更新しない（ノイズを持ち上げない）
    - ゲイン変化は attack/release で平滑化する（ポンピングを避ける）

ゲインは 1 フレーム（128ms）遅れて反映される。先読みを持たない代わりに
不連続が生じず、遅れも会議音声では問題にならない。

実測（Issue #0019 の 61 分音声、本方式のプロトタイプ）:
    ノイズフロア推定 中央値 -57.2 dBFS / ゲイン中央値 +19.8 dB（最大 +23.5 dB）
    補正後 発話フレーム中央値 -25.5 dBFS（目標 -24.0）
    補正後ピーク -0.45 dBFS（天井ちょうど、超過なし）/ 天井到達フレーム 0.07%
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .audio_levels import (
    ABSOLUTE_SILENCE_RMS,
    CEILING,
    CLIP_LEVEL,
    LEVEL_FRAME_SAMPLES,
    NoiseFloorTracker,
    amplitude_from_db,
    db_from_amplitude,
    float32_to_pcm16,
    pcm16_to_float32,
    smoothing_alpha,
)
from .input_profile import (
    GAIN_AUTO,
    GAIN_MANUAL,
    GAIN_NONE,
    SILENCE_ABSOLUTE,
    SILENCE_MANUAL,
    InputProfile,
)
from .pcm_stream import SAMPLE_RATE

# ゲインを下げる方向（音が大きくなった）は速く、上げる方向はゆっくり。
ATTACK_SECONDS = 0.25
RELEASE_SECONDS = 4.0
# 直近ピークの保持時間。ここからゲインの上限を決めてクリップを事前に防ぐ。
PEAK_HOLD_SECONDS = 1.0
# 発話レベル推定の平滑化係数（フレームごと）。
SPEECH_LEVEL_ALPHA = 0.92


@dataclass
class FrameLevel:
    """1 フレーム（既定 128ms）の測定結果。無音判定・警告判定はこれを使う。"""

    raw_rms: float
    corrected_rms: float
    noise_floor: float
    gain: float
    is_speech: bool
    raw_peak: float
    corrected_peak: float
    limited: bool


class GainTelemetry:
    """診断ログ用の集計。フレーム単位のログは残さず、ここへ積んで定期的に出す。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.frames = 0
        self._raw_square_sum = 0.0
        self._corrected_square_sum = 0.0
        self.raw_peak = 0.0
        self.corrected_peak = 0.0
        self._gain_sum_db = 0.0
        self.gain_min_db: Optional[float] = None
        self.gain_max_db: Optional[float] = None
        self.limited_frames = 0
        self.clipped_samples = 0
        self.speech_frames = 0
        self._noise_floor_sum = 0.0

    def push(self, level: FrameLevel) -> None:
        self.frames += 1
        self._raw_square_sum += level.raw_rms * level.raw_rms
        self._corrected_square_sum += level.corrected_rms * level.corrected_rms
        self.raw_peak = max(self.raw_peak, level.raw_peak)
        self.corrected_peak = max(self.corrected_peak, level.corrected_peak)
        g_db = db_from_amplitude(level.gain)
        self._gain_sum_db += g_db
        self.gain_min_db = g_db if self.gain_min_db is None else min(self.gain_min_db, g_db)
        self.gain_max_db = g_db if self.gain_max_db is None else max(self.gain_max_db, g_db)
        if level.limited:
            self.limited_frames += 1
        if level.is_speech:
            self.speech_frames += 1
        self._noise_floor_sum += level.noise_floor

    def as_dict(self) -> dict:
        if self.frames == 0:
            return {
                "frames": 0,
                "raw_rms_dbfs": None,
                "corrected_rms_dbfs": None,
                "gain_db": None,
                "gain_min_db": None,
                "gain_max_db": None,
                "raw_peak_dbfs": None,
                "corrected_peak_dbfs": None,
                "limited_frames": 0,
                "clipped_samples": 0,
                "speech_frame_ratio": 0.0,
                "noise_floor_dbfs": None,
            }
        raw_rms = (self._raw_square_sum / self.frames) ** 0.5
        corrected_rms = (self._corrected_square_sum / self.frames) ** 0.5
        return {
            "frames": self.frames,
            "raw_rms_dbfs": round(db_from_amplitude(raw_rms), 1),
            "corrected_rms_dbfs": round(db_from_amplitude(corrected_rms), 1),
            "gain_db": round(self._gain_sum_db / self.frames, 1),
            "gain_min_db": None if self.gain_min_db is None else round(self.gain_min_db, 1),
            "gain_max_db": None if self.gain_max_db is None else round(self.gain_max_db, 1),
            "raw_peak_dbfs": round(db_from_amplitude(self.raw_peak), 1),
            "corrected_peak_dbfs": round(db_from_amplitude(self.corrected_peak), 1),
            "limited_frames": self.limited_frames,
            "clipped_samples": self.clipped_samples,
            "speech_frame_ratio": round(self.speech_frames / self.frames, 4),
            "noise_floor_dbfs": round(db_from_amplitude(self._noise_floor_sum / self.frames), 1),
        }


class AdaptiveGain:
    """発話レベルに追従する緩慢ゲイン + クリッピング防止。

    ``process()`` は PCM16LE を受け取り、**同じサンプル数の** PCM16LE と、
    その呼び出しで完成したフレームの測定結果を返す。
    """

    def __init__(
        self,
        profile: InputProfile,
        *,
        sample_rate: int = SAMPLE_RATE,
        frame_samples: int = LEVEL_FRAME_SAMPLES,
    ) -> None:
        self.profile = profile
        self.sample_rate = int(sample_rate)
        self.frame_samples = max(int(frame_samples), 1)
        self.target = amplitude_from_db(profile.target_speech_dbfs)
        self.max_gain = amplitude_from_db(profile.max_gain_db)
        self.manual_gain = amplitude_from_db(profile.manual_gain_db)
        self.speech_factor = amplitude_from_db(profile.speech_over_floor_db)

        self._floor = NoiseFloorTracker(
            frame_samples=self.frame_samples, sample_rate=self.sample_rate
        )
        self._a_attack = smoothing_alpha(ATTACK_SECONDS, self.frame_samples, self.sample_rate)
        self._a_release = smoothing_alpha(RELEASE_SECONDS, self.frame_samples, self.sample_rate)
        self._a_peak = smoothing_alpha(PEAK_HOLD_SECONDS, self.frame_samples, self.sample_rate)

        self._gain = self.manual_gain if profile.gain_mode == GAIN_MANUAL else 1.0
        self._speech_level: Optional[float] = None
        self._peak_hold = 0.0
        # 測定用アキュムレータ（生 / 補正後）。出力は溜めない。
        self._raw_acc = np.zeros(self.frame_samples, dtype=np.float32)
        self._corrected_acc = np.zeros(self.frame_samples, dtype=np.float32)
        self._acc_len = 0
        # 呼び出し境界が奇数バイトで切れても 1 サンプルも失わないための持ち越し。
        # 切り捨てると出力サンプル数が入力より減り、絶対時刻がずれる。
        self._byte_carry = b""
        self.telemetry = GainTelemetry()

    # --- 参照 -----------------------------------------------------------------

    @property
    def gain(self) -> float:
        return self._gain

    @property
    def gain_db(self) -> float:
        return db_from_amplitude(self._gain)

    @property
    def noise_floor(self) -> float:
        return self._floor.value

    @property
    def pending_samples(self) -> int:
        """まだフレームが完成していない測定待ちサンプル数（出力は既に返済済み）。"""
        return self._acc_len

    @property
    def pending_bytes(self) -> int:
        """奇数バイトで切れて持ち越している端数（0 か 1）。"""
        return len(self._byte_carry)

    def speech_threshold(self) -> float:
        """発話フレームとみなす下限（生 PCM のレベルに対する値）。

        loopback は無音が厳密なデジタル 0 でノイズフロアが 0 へ張り付くため、
        相対判定を使わず絶対下限のみで判定する。
        """
        if self.profile.silence_mode == SILENCE_ABSOLUTE:
            return ABSOLUTE_SILENCE_RMS
        if self.profile.silence_mode == SILENCE_MANUAL:
            return max(float(self.profile.manual_silence_rms), 0.0)
        return max(self._floor.value * self.speech_factor, ABSOLUTE_SILENCE_RMS)

    # --- 更新 -----------------------------------------------------------------

    def _target_gain(self, frame_rms: float, is_speech: bool) -> float:
        mode = self.profile.gain_mode
        if mode == GAIN_NONE:
            return 1.0
        if mode == GAIN_MANUAL:
            target = self.manual_gain
        else:
            # 発話が無い区間ではゲインの目標を更新しない（環境音を持ち上げない）。
            if is_speech and frame_rms > 0.0:
                self._speech_level = (
                    frame_rms
                    if self._speech_level is None
                    else self._speech_level * SPEECH_LEVEL_ALPHA
                    + frame_rms * (1.0 - SPEECH_LEVEL_ALPHA)
                )
            if self._speech_level is None or self._speech_level <= 0.0:
                return self._gain
            target = min(self.target / self._speech_level, self.max_gain)

        # 減衰はしない。入力より小さくして認識を悪くする理由が無い。
        target = max(target, 1.0)
        # 直近ピークからクリップしない上限を決める（リミッタの前段）。
        if self.profile.clip_protection and self._peak_hold > 1e-9:
            target = min(target, max(1.0, CEILING / self._peak_hold))
        return target

    def _finish_frame(self) -> FrameLevel:
        raw = self._raw_acc.astype(np.float64)
        corrected = self._corrected_acc.astype(np.float64)
        raw_rms = float(np.sqrt(np.mean(np.square(raw))))
        raw_peak = float(np.abs(raw).max())
        corrected_rms = float(np.sqrt(np.mean(np.square(corrected))))
        corrected_peak = float(np.abs(corrected).max())

        floor = self._floor.push(raw_rms)
        self._peak_hold = max(raw_peak, self._peak_hold * self._a_peak)
        is_speech = raw_rms > self.speech_threshold()
        applied_gain = self._gain
        limited = self.profile.clip_protection and corrected_peak >= CEILING - 1e-6
        clipped = int(np.count_nonzero(np.abs(corrected) >= CLIP_LEVEL))

        # 次フレームへ向けてゲインを更新する。
        target = self._target_gain(raw_rms, is_speech)
        alpha = self._a_attack if target < self._gain else self._a_release
        self._gain = self._gain * alpha + target * (1.0 - alpha)

        level = FrameLevel(
            raw_rms=raw_rms,
            corrected_rms=corrected_rms,
            noise_floor=floor,
            gain=applied_gain,
            is_speech=is_speech,
            raw_peak=raw_peak,
            corrected_peak=corrected_peak,
            limited=limited,
        )
        self.telemetry.push(level)
        self.telemetry.clipped_samples += clipped
        return level

    def process(self, pcm_bytes: bytes) -> tuple[bytes, list[FrameLevel]]:
        """PCM16LE を補正して返す。**出力サンプル数は入力と必ず一致する。**

        ``gain_enabled`` が False（loopback の素通し）でも測定は行う。
        警告と診断はどちらのモードでも必要なため。
        """
        if self._byte_carry:
            pcm_bytes = self._byte_carry + bytes(pcm_bytes)
            self._byte_carry = b""
        if len(pcm_bytes) % 2:
            self._byte_carry = bytes(pcm_bytes[-1:])
            pcm_bytes = pcm_bytes[:-1]
        samples = pcm16_to_float32(pcm_bytes)
        if samples.size == 0:
            return b"", []

        passthrough = not self.profile.gain_enabled
        out = np.empty(samples.size, dtype=np.float32)
        levels: list[FrameLevel] = []

        pos = 0
        while pos < samples.size:
            take = min(self.frame_samples - self._acc_len, samples.size - pos)
            chunk = samples[pos : pos + take]

            if passthrough:
                corrected = chunk
            else:
                corrected = chunk * np.float32(self._gain)
                if self.profile.clip_protection:
                    corrected = np.clip(corrected, -CEILING, CEILING)

            out[pos : pos + take] = corrected
            self._raw_acc[self._acc_len : self._acc_len + take] = chunk
            self._corrected_acc[self._acc_len : self._acc_len + take] = corrected
            self._acc_len += take
            pos += take

            if self._acc_len >= self.frame_samples:
                levels.append(self._finish_frame())
                self._acc_len = 0

        return float32_to_pcm16(out), levels
