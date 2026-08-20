#!/usr/bin/env python3
"""realtime 文字起こしの長時間連続動作テスト。

起動中の Backend へ実際の WebSocket で合成 PCM を流し込み、
Backend プロセスの RSS が時間経過で増え続けないことを確認する。

  # 1 時間ぶんを約 1 分で（既定）
  .venv/bin/python scripts/live_soak.py --minutes 60 --speed 60

  # 実時間で 2 時間（就寝前に回す用）
  .venv/bin/python scripts/live_soak.py --minutes 120 --speed 1
"""
import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import websockets

SAMPLE_RATE = 16000
FRAME_SAMPLES = 2048
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


def synthetic_frames(total_seconds: float):
    """会話に近い、無音と有音が交互に来る PCM フレームを作る。"""
    total_frames = int(total_seconds / FRAME_SECONDS)
    phase = 0.0
    for index in range(total_frames):
        # 6 秒周期で「話している/黙っている」を切り替える。
        speaking = (index * FRAME_SECONDS) % 6.0 < 4.0
        if speaking:
            t = (np.arange(FRAME_SAMPLES) + phase) / SAMPLE_RATE
            wave = 0.25 * np.sin(2 * np.pi * 180 * t) * (1 + 0.4 * np.sin(2 * np.pi * 3 * t))
            frame = (wave * 32767).astype("<i2")
        else:
            frame = np.zeros(FRAME_SAMPLES, dtype="<i2")
        phase += FRAME_SAMPLES
        yield frame.tobytes()


def find_backend_pid(port: int) -> int | None:
    """uvicorn 本体の Python プロセスだけを拾う。

    シェルのラッパープロセスも cmdline に "uvicorn" と port を含むため、
    素朴な部分一致では別プロセスの RSS を測ってしまい判定が偽陽性になる。
    """
    candidates = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        cmdline = [str(part) for part in (proc.info.get("cmdline") or [])]
        if not cmdline:
            continue
        # 実体は `<python> -m uvicorn main:app --port <port>` の形。
        if "python" not in (proc.info.get("name") or "").lower() and "python" not in cmdline[0].lower():
            continue
        if "uvicorn" not in cmdline:
            continue
        if str(port) not in cmdline:
            continue
        candidates.append(proc)
    if not candidates:
        return None
    # 複数あればメモリが最大のもの（= モデルを載せた本体）を選ぶ。
    return max(candidates, key=lambda p: p.memory_info().rss).pid


def linear_slope(xs, ys) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


async def run(args) -> int:
    url = f"ws://127.0.0.1:{args.port}/ws/live"
    pid = find_backend_pid(args.port)
    if pid is None:
        print(f"! Backend (uvicorn on :{args.port}) が見つかりません。先に起動してください。", file=sys.stderr)
        return 2
    proc = psutil.Process(pid)
    baseline_rss = proc.memory_info().rss / 1e6
    print(
        f"backend pid={pid} rss={baseline_rss:.1f}MB model={args.model} "
        f"speed={args.speed}x minutes={args.minutes}"
    )
    if baseline_rss < 30:
        print(f"! pid={pid} の RSS が {baseline_rss:.1f}MB しかありません。"
              " uvicorn 本体ではない可能性があります。", file=sys.stderr)
        return 2

    outdir = Path(args.output_folder).expanduser() if args.output_folder else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    samples: list[tuple[float, float]] = []
    heartbeat_gaps: list[float] = []
    last_heartbeat = time.monotonic()
    last_progress = {}
    updates = 0
    warnings: list[dict] = []
    closed_early = None
    final_seen = False
    sent_seconds = 0.0

    async with websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=60) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "config",
                    "send_mode": "pcm16",
                    "sample_rate": SAMPLE_RATE,
                    "model": args.model,
                    "delay_mode": "balanced",
                    "write_to_file": bool(outdir),
                    "output_folder": str(outdir) if outdir else "",
                    "output_filename": "transcript.txt",
                    "debug": False,
                }
            )
        )
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            print(f"! ready が来ませんでした: {ready}", file=sys.stderr)
            return 2
        print(f"ready session={ready['session_id']} audio={ready.get('audio_path')}")

        stop = asyncio.Event()

        async def reader():
            nonlocal last_heartbeat, updates, closed_early, final_seen
            try:
                async for raw in ws:
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "heartbeat":
                        now = time.monotonic()
                        heartbeat_gaps.append(now - last_heartbeat)
                        last_heartbeat = now
                        last_progress.update(message)
                    elif kind == "update":
                        updates += 1
                    elif kind == "warning":
                        warnings.append(message)
                        print(f"  [warning] {message.get('code')}: {message.get('message')}")
                    elif kind == "session_final":
                        final_seen = True
                        last_progress.update(message)
                        break
                    elif kind == "error":
                        print(f"  [error] {message.get('message')}")
            except Exception as exc:
                # session_final を受け取った後の切断は正常終了。
                if not final_seen:
                    closed_early = f"{type(exc).__name__}: {exc}"
            finally:
                stop.set()

        reader_task = asyncio.create_task(reader())
        started = time.monotonic()
        # speed 倍で送るので、フレーム間隔を 1/speed に縮める。
        interval = FRAME_SECONDS / args.speed
        next_send = started
        next_sample = started

        for frame in synthetic_frames(args.minutes * 60):
            if stop.is_set():
                break
            await ws.send(frame)
            sent_seconds += FRAME_SECONDS
            next_send += interval
            delay = next_send - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if time.monotonic() >= next_sample:
                next_sample = time.monotonic() + max(1.0, 30.0 / args.speed)
                rss = proc.memory_info().rss / 1e6
                samples.append((sent_seconds, rss))
                print(
                    f"  t={sent_seconds/60:6.1f}min rss={rss:7.1f}MB "
                    f"processed={last_progress.get('processed_audio_seconds', 0):8.1f}s "
                    f"lag={last_progress.get('lag_seconds', 0):5.1f}s "
                    f"dropped={last_progress.get('dropped_seconds', 0):5.1f}s "
                    f"updates={updates}"
                )

        if not stop.is_set():
            await ws.send(json.dumps({"type": "stop"}))
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
        reader_task.cancel()

    print()
    if closed_early:
        print(f"! 接続が途中で切れました: {closed_early}")
    # モデル読み込み直後の立ち上がりを傾きに混ぜないため、最初の 10% は除いて評価する。
    steady = samples[max(1, len(samples) // 10):] or samples
    minutes = [s / 60 for s, _ in steady]
    rss_values = [r for _, r in steady]
    slope_per_hour = linear_slope(minutes, rss_values) * 60 if len(steady) >= 2 else 0.0
    max_gap = max(heartbeat_gaps) if heartbeat_gaps else 0.0
    dropped = float(last_progress.get("dropped_seconds", 0.0))
    processed = float(last_progress.get("processed_audio_seconds", 0.0))

    print(f"送信音声      : {sent_seconds/60:.1f} 分")
    print(f"処理済み      : {processed/60:.1f} 分")
    print(f"update 回数   : {updates}")
    print(f"RSS 定常初期/最終 : {rss_values[0]:.1f}MB / {rss_values[-1]:.1f}MB" if rss_values else "RSS: n/a")
    print(f"RSS 最小/最大 : {min(rss_values):.1f}MB / {max(rss_values):.1f}MB" if rss_values else "")
    print(f"RSS 傾き      : {slope_per_hour:+.1f} MB/時")
    print(f"heartbeat 最大間隔: {max_gap:.1f}s")
    print(f"破棄音声      : {dropped:.1f}s")
    print(f"warning       : {len(warnings)}")

    failures = []
    if closed_early:
        failures.append("接続が維持できなかった")
    if not final_seen:
        failures.append("session_final を受信できなかった")
    if slope_per_hour > args.max_slope_mb_per_hour:
        failures.append(f"RSS が増加傾向 ({slope_per_hour:+.1f} MB/時 > {args.max_slope_mb_per_hour})")
    if max_gap > args.max_heartbeat_gap:
        failures.append(f"heartbeat が {max_gap:.1f}s 途切れた")
    if dropped > 0:
        failures.append(f"音声を {dropped:.1f}s 破棄した")

    if outdir:
        audio = outdir / "audio" / "recording.wav"
        if audio.is_file():
            import wave

            with wave.open(str(audio), "rb") as wav:
                recorded = wav.getnframes() / wav.getframerate()
            print(f"録音ファイル  : {recorded/60:.1f} 分 ({audio})")
            if abs(recorded - sent_seconds) > 5.0:
                failures.append(f"録音長が送信量と一致しない ({recorded:.1f}s vs {sent_seconds:.1f}s)")
        else:
            failures.append("録音ファイルが作られなかった")

    print()
    if failures:
        for failure in failures:
            print(f"NG: {failure}")
        return 1
    print("OK: メモリ・接続・録音のいずれも劣化なし")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=60, help="流す音声の長さ(分)")
    parser.add_argument("--speed", type=float, default=60, help="実時間に対する倍速 (1=実時間)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default="tiny", help="加速時は tiny 推奨")
    parser.add_argument("--output-folder", default="", help="指定すると録音/TXTを保存して検証する")
    parser.add_argument("--max-slope-mb-per-hour", type=float, default=25.0)
    parser.add_argument("--max-heartbeat-gap", type=float, default=8.0)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
