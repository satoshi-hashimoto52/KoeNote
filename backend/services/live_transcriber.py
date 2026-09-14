import os
import shutil
import subprocess
import tempfile
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Dict

import numpy as np

from .audio_levels import ABSOLUTE_SILENCE_RMS, pcm16_to_float32, rms as _rms
from .pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE
from .transcriber import resolve_ffmpeg_dir

SUPPORTED_MODELS = {"tiny", "base", "small", "medium"}
DEFAULT_MODEL = "small"

# 0019: 固定 MIN_RMS = 0.006 は撤去した。
#
# 本体マイクで遠方の会話を拾うと全編が -48 dBFS 前後になり、実測ノイズフロア
# 0.00081（-61.8 dBFS）の 7.4 倍にあたる 0.006 では、463 窓中 366 窓（79.0%）が
# Whisper へ渡る前に破棄された（docs/issues/0019）。
#
# 代わりに使うのは
#   1. ABSOLUTE_SILENCE_RMS: 無効入力・デジタル無音だけを弾く十分低い絶対下限
#   2. 呼び出し側が渡す相対しきい値（ノイズフロア基準。既定は絶対下限と同じ）
#   3. faster-whisper の vad_filter（Silero VAD）による実際の発話区間の切り出し
# の 3 層。詳細は services/audio_levels.py を参照。
DEFAULT_SILENCE_RMS = ABSOLUTE_SILENCE_RMS

# segment の品質判定に使う閾値。model.transcribe へ渡す値と揃える。
NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0
COMPRESSION_RATIO_THRESHOLD = 2.4
HALLUCINATION_PHRASES = {
    "ご視聴ありがとうございました",
    "ご清聴ありがとうございました",
    "ありがとうございました",
    "あっはっは",
    "あはは",
}

# 反復ハルシネーション検出。compression_ratio が閾値内でも、短い n-gram が
# テキストの大半を占めるセグメントは異常反復とみなす（0019 で実測した
# 「はい」×223 回のような列は compression_ratio 51.46 で捕まるが、
# より短い反復は閾値内に収まることがある）。
REPETITION_MIN_LENGTH = 24
REPETITION_NGRAM_SIZES = (1, 2, 3, 4)
REPETITION_COVERAGE = 0.6

# realtime の推論パラメータ。wav 経路と PCM 経路で認識挙動を一致させるため 1 箇所に集約する
# （片方だけ変えると同じ音声で結果が変わる）。
# word_timestamps: 確定境界を word 単位で決めるために必須。
#   segment 単位だと確定線をまたぐ segment のテキストが失われる
#   （確定線は次 window の開始時刻と一致するため再評価の機会がない）。
#   実測コスト +6〜7%（small / 10秒窓で 1.86s -> 1.96s）。
# temperature: **スカラーにしてはいけない**（0019）。
#   faster_whisper/transcribe.py は
#     temperatures=(temperature if isinstance(temperature, (list, tuple)) else [temperature])
#   としており、スカラーだと fallback ループが 1 周で終わる。
#   その結果 compression_ratio_threshold を超えても再デコードされず、
#   「はい」の反復のようなハルシネーションが確定テキストへそのまま入る
#   （実測: compression_ratio 51.46 / 446 字。実会話 30 秒分が失われた）。
#   tuple/list は faster-whisper 1.2.1 の型注釈
#   Union[float, List[float], Tuple[float, ...]] で正式に受け付ける形式。
#   fallback は閾値を割ったときだけ走るため、通常音声の速度・品質は変わらない。
TRANSCRIBE_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

TRANSCRIBE_KWARGS = {
    "language": "ja",
    "beam_size": 3,
    "vad_filter": True,
    "temperature": TRANSCRIBE_TEMPERATURES,
    "condition_on_previous_text": False,
    "no_speech_threshold": NO_SPEECH_THRESHOLD,
    "log_prob_threshold": LOGPROB_THRESHOLD,
    "compression_ratio_threshold": COMPRESSION_RATIO_THRESHOLD,
    "word_timestamps": True,
}

_model_cache: Dict[str, object] = {}
_model_lock = threading.Lock()


def _load_model(model_name: str):
    normalized = (model_name or DEFAULT_MODEL).strip().lower()
    if normalized not in SUPPORTED_MODELS:
        raise ValueError(f"未対応のモデルです: {model_name}")

    with _model_lock:
        cached = _model_cache.get(normalized)
        if cached is not None:
            return cached

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper が未インストールです。backend/requirements.txt を再インストールしてください。"
            ) from exc

        model = WhisperModel(normalized, device="cpu", compute_type="int8")
        _model_cache[normalized] = model
        return model


def _resolve_ffmpeg_binary() -> str:
    ffmpeg_dir = resolve_ffmpeg_dir()
    if ffmpeg_dir is not None:
        candidate = ffmpeg_dir / "ffmpeg"
        if candidate.is_file():
            return str(candidate)

    system_bin = shutil.which("ffmpeg")
    if system_bin:
        return system_bin

    raise RuntimeError("ffmpeg が見つかりません。")


def _convert_webm_to_wav(input_path: Path, output_path: Path) -> None:
    ffmpeg_bin = _resolve_ffmpeg_binary()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_path),
    ]
    env = os.environ.copy()
    ffmpeg_dir = resolve_ffmpeg_dir()
    if ffmpeg_dir is not None:
        env["PATH"] = f"{str(ffmpeg_dir)}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError(f"音声chunk変換に失敗しました。{detail}".strip())


# レベル計算は services.audio_levels に一本化する（0019）。
# ここに同じ実装を置くと、しきい値の基準がモジュールごとにずれる。
_pcm_rms = _rms


def _calculate_wav_rms(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        sample_width = wav.getsampwidth()
        frame_count = wav.getnframes()
        if sample_width != 2 or frame_count <= 0:
            return 0.0
        data = wav.readframes(frame_count)

    if not data:
        return 0.0
    return _pcm_rms(pcm16_to_float32(data))


def _is_hallucination_text(text: str) -> bool:
    normalized = "".join(str(text or "").split())
    if not normalized:
        return False
    return any(phrase in normalized for phrase in HALLUCINATION_PHRASES)


def is_repetitive_text(text: str) -> bool:
    """短い n-gram がテキストの大半を占める＝異常反復か（0019）。

    temperature fallback を有効化しても、fallback 後の候補がすべて反復に
    なることはありうる。確定テキストへ入れないための最終防壁として使う。
    """
    normalized = "".join(str(text or "").split())
    if len(normalized) < REPETITION_MIN_LENGTH:
        return False
    for n in REPETITION_NGRAM_SIZES:
        if len(normalized) < n * 2:
            continue
        counts: Dict[str, int] = {}
        for i in range(len(normalized) - n + 1):
            gram = normalized[i : i + n]
            counts[gram] = counts.get(gram, 0) + 1
        top = max(counts.values())
        if top * n > REPETITION_COVERAGE * len(normalized):
            return True
    return False


def _debug_chunk_dir() -> Path:
    path = Path(tempfile.gettempdir()) / f"koenote_live_debug_{datetime.now().strftime('%Y%m%d')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_debug_file(source: Path, suffix: str) -> str:
    target = _debug_chunk_dir() / f"chunk_{datetime.now().strftime('%H%M%S_%f')}{suffix}"
    shutil.copy2(source, target)
    return str(target)


def _write_debug_pcm(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> str:
    target = _debug_chunk_dir() / f"chunk_{datetime.now().strftime('%H%M%S_%f')}.wav"
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(BYTES_PER_SAMPLE)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return str(target)


def _extract_words(segment) -> list:
    """segment から word 情報を取り出す。取得できない場合は空リストを返す。

    値の検証・クランプは services.word_commit.normalize_words 側で行う。
    ここでは faster-whisper の属性名を辞書へ写すだけに留める。
    """
    words = []
    for word in (getattr(segment, "words", None) or []):
        words.append({
            "start": getattr(word, "start", None),
            "end": getattr(word, "end", None),
            "text": str(getattr(word, "word", "") or ""),
        })
    return words


def _collect_segments(segments) -> tuple[str, list, list]:
    """segment 列から採用テキスト・採用セグメント・除外理由を取り出す。

    wav 経路と PCM 経路で同一の絞り込みを保証するため 1 箇所に集約する。
    """
    accepted_texts = []
    accepted_segments = []
    dropped_reasons = []
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        no_speech_prob = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
        avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
        compression_ratio = float(getattr(segment, "compression_ratio", 0.0) or 0.0)
        # 無音判定は no_speech_prob 単独では行わない。
        # realtime では窓が語の途中から始まるため no_speech_prob が閾値をわずかに超えやすく
        # （実測 0.62〜0.84）、単独判定では avg_logprob -0.16 のような確信度の高い音声まで
        # segment ごと破棄され、無音境界のない発話が数十秒まとめて失われる。
        # faster-whisper 自身の判定も「no_speech_prob が高く かつ avg_logprob が低い」の
        # 同時成立で無音とみなすため、その意味論に合わせる。
        if no_speech_prob > NO_SPEECH_THRESHOLD and avg_logprob < LOGPROB_THRESHOLD:
            dropped_reasons.append(
                f"silence(no_speech={no_speech_prob:.2f},logprob={avg_logprob:.2f})"
            )
            continue
        if avg_logprob < LOGPROB_THRESHOLD:
            dropped_reasons.append(f"avg_logprob={avg_logprob:.2f}")
            continue
        if compression_ratio > COMPRESSION_RATIO_THRESHOLD:
            dropped_reasons.append(f"compression_ratio={compression_ratio:.2f}")
            continue
        if _is_hallucination_text(text):
            dropped_reasons.append("hallucination_phrase")
            continue
        # 0019: fallback を通しても反復のままなら確定テキストへ入れない。
        if is_repetitive_text(text):
            dropped_reasons.append(f"repetitive_segment(len={len(text)})")
            continue
        accepted_texts.append(text)
        accepted_segments.append({
            "start": float(getattr(segment, "start", 0.0) or 0.0),
            "end": float(getattr(segment, "end", 0.0) or 0.0),
            "text": text,
            "words": _extract_words(segment),
            # transcript_segments.json へ残す認識品質指標（0019）。
            "avg_logprob": avg_logprob,
            "no_speech_prob": no_speech_prob,
            "compression_ratio": compression_ratio,
        })
    return "".join(accepted_texts).strip(), accepted_segments, dropped_reasons


def summarize_quality(segments: list) -> dict:
    """窓内の採用セグメントから、最も悪い側の品質指標を拾う。

    確定は word 単位で行うため segment と 1 対 1 に対応しない。
    窓単位の代表値として「一番怪しい値」を残すことで、事後解析で
    疑わしい区間を絞り込めるようにする。
    """
    if not segments:
        return {}
    logprobs = [s["avg_logprob"] for s in segments if s.get("avg_logprob") is not None]
    no_speech = [s["no_speech_prob"] for s in segments if s.get("no_speech_prob") is not None]
    ratios = [s["compression_ratio"] for s in segments if s.get("compression_ratio") is not None]
    return {
        "avg_logprob": min(logprobs) if logprobs else None,
        "no_speech_prob": max(no_speech) if no_speech else None,
        "compression_ratio": max(ratios) if ratios else None,
    }


def _result(text, segments, model_key, rms, dropped_reasons, debug_path=None, debug_wav_path=None) -> dict:
    return {
        "text": text,
        "segments": segments,
        "model": model_key,
        "rms": rms,
        "skipped": not bool(text),
        "skip_reason": ", ".join(dropped_reasons) if dropped_reasons else "",
        # 0019: 反復ハルシネーションで破棄した件数。診断へ計上する。
        "repetitive_dropped": sum(1 for r in dropped_reasons if r.startswith("repetitive_segment")),
        "quality": summarize_quality(segments),
        # 推論はしたが採用テキストが 0 だった = Silero VAD/品質判定で発話なし。
        "no_speech": not bool(text),
        "level_skipped": False,
        "debug_path": debug_path,
        "debug_wav_path": debug_wav_path,
    }


def _skipped_result(model_key, rms, reason, debug_path=None, debug_wav_path=None,
                    level_skipped: bool = True) -> dict:
    return {
        "text": "",
        "segments": [],
        "model": model_key,
        "rms": rms,
        "skipped": True,
        "skip_reason": reason,
        "repetitive_dropped": 0,
        "quality": {},
        "no_speech": False,
        # レベル判定で推論そのものを行わなかったか（vad_silence とは別に数える）。
        "level_skipped": level_skipped,
        "debug_path": debug_path,
        "debug_wav_path": debug_wav_path,
    }


def transcribe_pcm16(
    pcm_bytes: bytes,
    model_name: str,
    debug_save: bool = False,
    sample_rate: int = SAMPLE_RATE,
    silence_rms: float = DEFAULT_SILENCE_RMS,
    has_speech: bool = True,
) -> dict:
    """PCM16LE mono を直接 faster-whisper へ渡す realtime 経路。

    faster-whisper は ndarray をそのまま受け取れる（ndarray 以外のときだけ内部で
    decode_audio を呼ぶ）ので、一時 wav も ffmpeg も不要。1 窓の処理コストは
    録音の長さに依存しない。

    ``has_speech`` は呼び出し側（LiveSession）がフレーム単位の相対判定で決める。
    **窓平均 RMS で無音判定してはいけない**（0019）。窓平均は発話の合間の無音を
    含むためノイズフロアとの比が小さく、相対化しても実測でスキップ率 85.7% に
    達する。ここで見るのは無効入力を弾く絶対下限だけにする。
    """
    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    samples = pcm16_to_float32(pcm_bytes)
    if samples.size == 0:
        return _skipped_result(model_key, 0.0, "empty_pcm")

    rms = _pcm_rms(samples)
    debug_wav_path = _write_debug_pcm(pcm_bytes, sample_rate) if debug_save else None
    threshold = max(float(silence_rms), 0.0)
    if rms < threshold:
        return _skipped_result(
            model_key, rms, f"below_absolute_silence<{threshold:g}",
            debug_wav_path=debug_wav_path,
        )
    if not has_speech:
        return _skipped_result(
            model_key, rms, "no_speech_frame_in_window", debug_wav_path=debug_wav_path
        )

    model = _load_model(model_key)
    segments, _info = model.transcribe(samples, **TRANSCRIBE_KWARGS)
    text, accepted, dropped = _collect_segments(segments)
    return _result(text, accepted, model_key, rms, dropped, debug_wav_path=debug_wav_path)


def transcribe_audio_chunk(audio_bytes: bytes, mime_type: str, model_name: str, debug_save: bool = False,
                           silence_rms: float = DEFAULT_SILENCE_RMS) -> dict:
    if not audio_bytes:
        return _skipped_result(model_name or DEFAULT_MODEL, 0.0, "empty_chunk")

    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    suffix = ".mp4" if "mp4" in (mime_type or "") else ".webm"

    with tempfile.TemporaryDirectory(prefix="koenote_live_chunk_") as tmp:
        input_path = Path(tmp) / f"chunk{suffix}"
        wav_path = Path(tmp) / "chunk.wav"
        input_path.write_bytes(audio_bytes)
        _convert_webm_to_wav(input_path, wav_path)
        rms = _calculate_wav_rms(wav_path)
        debug_path = _save_debug_file(input_path, suffix) if debug_save else None
        debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None

        threshold = max(float(silence_rms), 0.0)
        if rms < threshold:
            return _skipped_result(
                model_key, rms, f"below_absolute_silence<{threshold:g}", debug_path, debug_wav_path
            )

        model = _load_model(model_key)
        segments, _info = model.transcribe(str(wav_path), **TRANSCRIBE_KWARGS)
        text, accepted, dropped = _collect_segments(segments)
        return _result(text, accepted, model_key, rms, dropped, debug_path, debug_wav_path)


def transcribe_wav_file(wav_path: Path, model_name: str, debug_save: bool = False,
                        silence_rms: float = DEFAULT_SILENCE_RMS) -> dict:
    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        return _skipped_result(model_key, 0.0, "empty_wav")

    rms = _calculate_wav_rms(wav_path)
    debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None
    threshold = max(float(silence_rms), 0.0)
    if rms < threshold:
        return _skipped_result(
            model_key, rms, f"below_absolute_silence<{threshold:g}", debug_wav_path=debug_wav_path
        )

    model = _load_model(model_key)
    segments, _info = model.transcribe(str(wav_path), **TRANSCRIBE_KWARGS)
    text, accepted, dropped = _collect_segments(segments)
    return _result(text, accepted, model_key, rms, dropped, debug_wav_path=debug_wav_path)


def convert_webm_bytes_to_wav(audio_bytes: bytes, mime_type: str, debug_save: bool = False) -> dict:
    suffix = ".mp4" if "mp4" in (mime_type or "") else ".webm"

    with tempfile.TemporaryDirectory(prefix="koenote_live_joined_") as tmp:
        input_path = Path(tmp) / f"joined{suffix}"
        wav_path = Path(tmp) / "joined.wav"
        input_path.write_bytes(audio_bytes)
        _convert_webm_to_wav(input_path, wav_path)
        debug_path = _save_debug_file(input_path, suffix) if debug_save else None
        debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None
        return {
            "wav_path": str(wav_path),
            "wav_bytes": wav_path.read_bytes(),
            "debug_path": debug_path,
            "debug_wav_path": debug_wav_path,
        }
