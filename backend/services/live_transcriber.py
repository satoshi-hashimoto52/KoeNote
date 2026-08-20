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

from .pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE
from .transcriber import resolve_ffmpeg_dir

SUPPORTED_MODELS = {"tiny", "base", "small", "medium"}
DEFAULT_MODEL = "small"
MIN_RMS = 0.006
HALLUCINATION_PHRASES = {
    "ご視聴ありがとうございました",
    "ご清聴ありがとうございました",
    "ありがとうございました",
    "あっはっは",
    "あはは",
}

# realtime の推論パラメータ。wav 経路と PCM 経路で認識挙動を一致させるため 1 箇所に集約する
# （片方だけ変えると同じ音声で結果が変わる）。
TRANSCRIBE_KWARGS = {
    "language": "ja",
    "beam_size": 3,
    "vad_filter": True,
    "temperature": 0,
    "condition_on_previous_text": False,
    "no_speech_threshold": 0.6,
    "log_prob_threshold": -1.0,
    "compression_ratio_threshold": 2.4,
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


def _pcm_rms(samples: np.ndarray) -> float:
    """-1.0..1.0 に正規化済みの float 配列から RMS を求める。"""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """PCM16LE を faster-whisper がそのまま受け取れる float32 配列にする。"""
    remainder = len(pcm_bytes) % BYTES_PER_SAMPLE
    if remainder:
        pcm_bytes = pcm_bytes[: len(pcm_bytes) - remainder]
    if not pcm_bytes:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0


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


def _debug_chunk_dir() -> Path:
    path = Path(tempfile.gettempdir()) / f"bridgelog_live_debug_{datetime.now().strftime('%Y%m%d')}"
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
        if no_speech_prob > 0.6:
            dropped_reasons.append(f"no_speech_prob={no_speech_prob:.2f}")
            continue
        if avg_logprob < -1.0:
            dropped_reasons.append(f"avg_logprob={avg_logprob:.2f}")
            continue
        if compression_ratio > 2.4:
            dropped_reasons.append(f"compression_ratio={compression_ratio:.2f}")
            continue
        if _is_hallucination_text(text):
            dropped_reasons.append("hallucination_phrase")
            continue
        accepted_texts.append(text)
        accepted_segments.append({
            "start": float(getattr(segment, "start", 0.0) or 0.0),
            "end": float(getattr(segment, "end", 0.0) or 0.0),
            "text": text,
        })
    return "".join(accepted_texts).strip(), accepted_segments, dropped_reasons


def _result(text, segments, model_key, rms, dropped_reasons, debug_path=None, debug_wav_path=None) -> dict:
    return {
        "text": text,
        "segments": segments,
        "model": model_key,
        "rms": rms,
        "skipped": not bool(text),
        "skip_reason": ", ".join(dropped_reasons) if dropped_reasons else "",
        "debug_path": debug_path,
        "debug_wav_path": debug_wav_path,
    }


def _skipped_result(model_key, rms, reason, debug_path=None, debug_wav_path=None) -> dict:
    return {
        "text": "",
        "segments": [],
        "model": model_key,
        "rms": rms,
        "skipped": True,
        "skip_reason": reason,
        "debug_path": debug_path,
        "debug_wav_path": debug_wav_path,
    }


def transcribe_pcm16(
    pcm_bytes: bytes,
    model_name: str,
    debug_save: bool = False,
    sample_rate: int = SAMPLE_RATE,
) -> dict:
    """PCM16LE mono を直接 faster-whisper へ渡す realtime 経路。

    faster-whisper は ndarray をそのまま受け取れる（ndarray 以外のときだけ内部で
    decode_audio を呼ぶ）ので、一時 wav も ffmpeg も不要。1 窓の処理コストは
    録音の長さに依存しない。
    """
    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    samples = pcm16_to_float32(pcm_bytes)
    if samples.size == 0:
        return _skipped_result(model_key, 0.0, "empty_pcm")

    rms = _pcm_rms(samples)
    debug_wav_path = _write_debug_pcm(pcm_bytes, sample_rate) if debug_save else None
    if rms < MIN_RMS:
        return _skipped_result(model_key, rms, f"low_rms<{MIN_RMS}", debug_wav_path=debug_wav_path)

    model = _load_model(model_key)
    segments, _info = model.transcribe(samples, **TRANSCRIBE_KWARGS)
    text, accepted, dropped = _collect_segments(segments)
    return _result(text, accepted, model_key, rms, dropped, debug_wav_path=debug_wav_path)


def transcribe_audio_chunk(audio_bytes: bytes, mime_type: str, model_name: str, debug_save: bool = False) -> dict:
    if not audio_bytes:
        return _skipped_result(model_name or DEFAULT_MODEL, 0.0, "empty_chunk")

    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    suffix = ".mp4" if "mp4" in (mime_type or "") else ".webm"

    with tempfile.TemporaryDirectory(prefix="bridgelog_live_chunk_") as tmp:
        input_path = Path(tmp) / f"chunk{suffix}"
        wav_path = Path(tmp) / "chunk.wav"
        input_path.write_bytes(audio_bytes)
        _convert_webm_to_wav(input_path, wav_path)
        rms = _calculate_wav_rms(wav_path)
        debug_path = _save_debug_file(input_path, suffix) if debug_save else None
        debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None

        if rms < MIN_RMS:
            return _skipped_result(model_key, rms, f"low_rms<{MIN_RMS}", debug_path, debug_wav_path)

        model = _load_model(model_key)
        segments, _info = model.transcribe(str(wav_path), **TRANSCRIBE_KWARGS)
        text, accepted, dropped = _collect_segments(segments)
        return _result(text, accepted, model_key, rms, dropped, debug_path, debug_wav_path)


def transcribe_wav_file(wav_path: Path, model_name: str, debug_save: bool = False) -> dict:
    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        return _skipped_result(model_key, 0.0, "empty_wav")

    rms = _calculate_wav_rms(wav_path)
    debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None
    if rms < MIN_RMS:
        return _skipped_result(model_key, rms, f"low_rms<{MIN_RMS}", debug_wav_path=debug_wav_path)

    model = _load_model(model_key)
    segments, _info = model.transcribe(str(wav_path), **TRANSCRIBE_KWARGS)
    text, accepted, dropped = _collect_segments(segments)
    return _result(text, accepted, model_key, rms, dropped, debug_wav_path=debug_wav_path)


def convert_webm_bytes_to_wav(audio_bytes: bytes, mime_type: str, debug_save: bool = False) -> dict:
    suffix = ".mp4" if "mp4" in (mime_type or "") else ".webm"

    with tempfile.TemporaryDirectory(prefix="bridgelog_live_joined_") as tmp:
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
