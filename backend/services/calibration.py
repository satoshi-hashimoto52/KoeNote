"""入力テスト（キャリブレーション）の算出（0019）。

frontend が録った生 PCM をそのまま受け取り、**録音時と同じコード**
（services.audio_levels / services.input_profile）で評価する。
UI 側で別実装にすると、テストの判定と実際の挙動がずれる。

数値はすべて **dBFS**（フルスケール基準の相対値）。
物理音圧 dB SPL ではないので、UI でも必ず dBFS と表記すること。
"""
from typing import Optional

import numpy as np

from .audio_levels import (
    ABSOLUTE_SILENCE_RMS,
    CLIP_RATIO_WARN,
    GOOD_SNR_DB,
    HEAVY_GAIN_DB,
    MAX_GAIN_DB,
    MIN_SNR_DB,
    TARGET_SPEECH_DBFS,
    LevelStats,
    amplitude_from_db,
    analyze_levels,
    classify_verdict,
    db_from_amplitude,
    estimate_pass_ratio,
    frame_rms,
    pcm16_to_float32,
    rms,
    recommended_gain_db,
    required_gain_db,
)
from .input_profile import (
    GAIN_AUTO,
    GAIN_MANUAL,
    InputProfile,
    build_profile,
)
from .pcm_stream import SAMPLE_RATE

# 「文字起こしへ確実に回るレベル」の目安。目標発話レベルから 12 dB 下。
# 実測では補正後の発話フレーム中央値が -25.1 dBFS だったので、
# -36 dBFS を下回る発話は補正しても取りこぼしが出やすい。
TRANSCRIBABLE_DBFS = TARGET_SPEECH_DBFS - 12.0
TRANSCRIBABLE_RMS = amplitude_from_db(TRANSCRIBABLE_DBFS)

# 入力テストの想定時間。frontend の案内文と合わせる。
NOISE_STEP_SECONDS = 5.0
SPEECH_STEP_SECONDS = 18.0

TEST_SENTENCE = (
    "これはKoeNoteのマイク入力テストです。\n"
    "会議中と同じ声量で、普段どおりに話しています。\n"
    "離れた位置からでも、音声が正しく認識されるか確認します。"
)


def _current_gain_db(profile: InputProfile, stats: LevelStats) -> float:
    """現在の設定で実際に掛かるゲイン。判定を「現状のまま」で行うために使う。"""
    if profile.gain_mode == GAIN_MANUAL:
        return float(profile.manual_gain_db)
    if profile.gain_mode == GAIN_AUTO:
        return min(recommended_gain_db(stats, max_gain_db=profile.max_gain_db), profile.max_gain_db)
    return 0.0


def analyze_calibration(
    speech_pcm: bytes,
    *,
    noise_pcm: Optional[bytes] = None,
    device_label: str = "",
    profile_payload: Optional[dict] = None,
    sample_rate: int = SAMPLE_RATE,
) -> dict:
    """環境音と遠方発話の 2 ステップから、入力の評価と推奨設定を返す。

    ``noise_pcm`` があればそのノイズフロアを発話測定へ持ち込む。
    無ければ発話測定そのものの 10%ile を使う（片方だけでも動くようにする）。
    """
    profile = build_profile(profile_payload, device_label)

    noise_stats = None
    noise_floor = None
    if noise_pcm:
        noise_samples = pcm16_to_float32(noise_pcm)
        if noise_samples.size:
            noise_stats = analyze_levels(noise_samples, sample_rate=sample_rate)
            noise_floor = _noise_floor_from(noise_samples, sample_rate)

    speech_samples = pcm16_to_float32(speech_pcm)
    stats = analyze_levels(speech_samples, sample_rate=sample_rate, noise_floor=noise_floor)

    gain_db = _current_gain_db(profile, stats)
    recommended_db = recommended_gain_db(stats, max_gain_db=profile.max_gain_db or MAX_GAIN_DB)
    required_db = required_gain_db(stats)

    current_pass = estimate_pass_ratio(stats, gain_db, threshold_rms=TRANSCRIBABLE_RMS)
    recommended_pass = estimate_pass_ratio(stats, recommended_db, threshold_rms=TRANSCRIBABLE_RMS)
    verdict = classify_verdict(
        stats,
        pass_ratio=recommended_pass,
        required_db=required_db,
        max_gain_db=MAX_GAIN_DB,
    )

    # 推奨ゲインを掛けたときにクリップしうるか。ピークで見る。
    projected_peak = stats.peak * amplitude_from_db(recommended_db)
    if projected_peak >= 1.0:
        clipping_risk = "high"
    elif projected_peak >= 0.95:
        clipping_risk = "medium"
    else:
        clipping_risk = "low"

    return {
        "device_label": device_label,
        "input_mode": profile.mode,
        "detected_mode": profile.detected_mode,
        "requested_mode": profile.requested_mode,
        "gain_mode": profile.gain_mode,
        "unit": "dBFS",
        "noise": None if noise_stats is None else noise_stats.as_dict(),
        "speech": stats.as_dict(),
        "noise_floor_dbfs": round(db_from_amplitude(stats.noise_floor), 1),
        "speech_median_dbfs": round(db_from_amplitude(stats.speech_median), 1),
        "speech_low_dbfs": round(db_from_amplitude(stats.speech_low), 1),
        "speech_peak_dbfs": round(db_from_amplitude(stats.peak), 1),
        "snr_db": round(stats.snr_db, 1),
        "speech_ratio": round(stats.speech_ratio, 4),
        "current_gain_db": round(gain_db, 1),
        "current_pass_ratio": round(current_pass, 4),
        "current_skip_ratio": round(1.0 - current_pass, 4),
        "recommended_gain_db": round(recommended_db, 1),
        "recommended_pass_ratio": round(recommended_pass, 4),
        "recommended_skip_ratio": round(1.0 - recommended_pass, 4),
        "required_gain_db": round(required_db, 1),
        "max_gain_db": round(MAX_GAIN_DB, 1),
        "target_speech_dbfs": round(TARGET_SPEECH_DBFS, 1),
        "transcribable_dbfs": round(TRANSCRIBABLE_DBFS, 1),
        "clipping_risk": clipping_risk,
        "clip_ratio": round(stats.clip_ratio, 6),
        "verdict": verdict,
        "thresholds": {
            "good_snr_db": GOOD_SNR_DB,
            "min_snr_db": MIN_SNR_DB,
            "heavy_gain_db": HEAVY_GAIN_DB,
            "clip_ratio_warn": CLIP_RATIO_WARN,
            "absolute_silence_rms": ABSOLUTE_SILENCE_RMS,
        },
        "recommended_settings": recommended_settings(profile, stats, recommended_db),
    }


def _noise_floor_from(samples: np.ndarray, sample_rate: int) -> float:
    """環境音ステップからノイズフロアを求める。

    「話さずに待つ」前提なので、全フレームの**中央値**を採る。
    10%ile だと一瞬の静寂へ引っ張られ、実際の定常騒音より低く出る。
    """
    frames = frame_rms(samples)
    if frames.size == 0:
        return max(rms(samples), 0.0)
    return float(np.median(frames))


def recommended_settings(
    profile: InputProfile, stats: LevelStats, recommended_db: float
) -> dict:
    """「推奨設定を適用」で書き戻す値。

    自動補正で足りるなら auto のまま（実入力に追従する方が固定より強い）。
    最大ゲインだけを必要量へ引き上げる。
    """
    needed = min(max(recommended_db, 0.0), MAX_GAIN_DB)
    # 実運用では話者が近づくこともあるので、必要量そのままではなく少し余裕を持たせる。
    max_gain = min(MAX_GAIN_DB, max(needed + 3.0, 6.0))
    return {
        "inputMode": profile.mode,
        "gainMode": GAIN_AUTO if profile.mode != "loopback" else "none",
        "maxGainDb": round(max_gain, 1),
        "manualGainDb": 0.0,
        "lowInputWarning": True,
    }
