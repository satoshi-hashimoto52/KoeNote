#!/usr/bin/env python3
"""word timestamp のずれ（jitter）を実測する。

services/word_commit.py の JITTER_MAX_SECONDS と CURSOR_MARGIN_SECONDS は、
この測定結果に基づく設計値。モデル・話者・録音環境・推論パラメータを変えたら
再測定して値を見直すこと。

同じ音声を「開始位置の異なる窓」で推論し、同じ語の絶対時刻がどれだけずれるかを測る。
語の対応付けは「単調 + 時刻局所 + テキスト一致」の制約付き DP で行う（測定専用）。
テキスト一致だけで対応付けると、繰り返し語を誤対応して 3 秒級の偽の外れ値が出る。

使い方:
    # 16kHz mono PCM16LE を用意する
    say -v Kyoko -o sample.aiff "本日の会議を始めます。..."
    ffmpeg -y -i sample.aiff -ac 1 -ar 16000 -f s16le sample.pcm

    .venv/bin/python scripts/measure_word_jitter.py sample.pcm
    .venv/bin/python scripts/measure_word_jitter.py sample.pcm --model medium --chunks 8 10 12
"""
import argparse
import statistics
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from services.live_transcriber import TRANSCRIBE_KWARGS, _load_model, pcm16_to_float32  # noqa: E402
from services.pcm_stream import SAMPLE_RATE  # noqa: E402

# 対応付けを許す時刻差の上限。これを超える対応は誤対応とみなす（測定専用）。
ALIGN_BAND_SECONDS = 1.0


def words_in_window(model, samples, offset_seconds, chunk_seconds):
    """窓 [offset, offset+chunk) を推論し (絶対start, 絶対end, text) の列を返す。"""
    begin = int(offset_seconds * SAMPLE_RATE)
    end = min(int((offset_seconds + chunk_seconds) * SAMPLE_RATE), len(samples))
    if end - begin < SAMPLE_RATE:
        return []
    segments, _info = model.transcribe(samples[begin:end], **TRANSCRIBE_KWARGS)
    out = []
    for segment in segments:
        for word in (getattr(segment, "words", None) or []):
            text = (word.word or "").strip()
            if text:
                out.append((offset_seconds + float(word.start),
                            offset_seconds + float(word.end), text))
    return out


def aligned_pairs(reference, candidate):
    """単調 + 時間局所 + テキスト一致で最大対応を取る DP。"""
    n, m = len(reference), len(candidate)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = max(table[i - 1][j], table[i][j - 1])
            a, b = reference[i - 1], candidate[j - 1]
            if a[2] == b[2] and abs(a[0] - b[0]) <= ALIGN_BAND_SECONDS:
                best = max(best, table[i - 1][j - 1] + 1)
            table[i][j] = best
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        a, b = reference[i - 1], candidate[j - 1]
        if (a[2] == b[2] and abs(a[0] - b[0]) <= ALIGN_BAND_SECONDS
                and table[i][j] == table[i - 1][j - 1] + 1):
            pairs.append((a, b))
            i -= 1
            j -= 1
        elif table[i - 1][j] >= table[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs))


def summarize(deltas):
    if not deltas:
        return None
    values = sorted(deltas)
    count = len(values)

    def percentile(q):
        return values[min(count - 1, int(round(q / 100 * (count - 1))))]

    return {"n": count, "median": statistics.median(values),
            "p95": percentile(95), "p99": percentile(99), "max": values[-1]}


def fmt(stats):
    if not stats:
        return "対応なし"
    return (f"n={stats['n']:>5} 中央値={stats['median']:.3f} p95={stats['p95']:.3f} "
            f"p99={stats['p99']:.3f} 最大={stats['max']:.3f}")


def run(model, samples, chunk_seconds, offsets, label):
    print(f"--- {label} / chunk={chunk_seconds}s ---")
    base_offset = offsets[0]
    base = words_in_window(model, samples, base_offset, chunk_seconds)
    starts, ends = [], []
    for offset in offsets[1:]:
        candidate = words_in_window(model, samples, offset, chunk_seconds)
        pairs = aligned_pairs(base, candidate)
        ds = [abs(a[0] - b[0]) for a, b in pairs]
        starts += ds
        ends += [abs(a[1] - b[1]) for a, b in pairs]
        print(f"  offset {base_offset}s vs {offset:>4.1f}s  {fmt(summarize(ds))}"
              f"  (基準{len(base)}語/比較{len(candidate)}語/対応{len(pairs)}語)")
    print(f"  [start] {fmt(summarize(starts))}")
    print(f"  [end  ] {fmt(summarize(ends))}")
    print()
    return summarize(starts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pcm", nargs="+", help="16kHz mono PCM16LE ファイル（複数指定可）")
    parser.add_argument("--model", default="small")
    parser.add_argument("--chunks", type=float, nargs="+", default=[6, 8, 10, 12])
    parser.add_argument("--offsets", type=float, nargs="+",
                        default=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0])
    args = parser.parse_args()

    model = _load_model(args.model)
    results = {}
    for path in args.pcm:
        samples = pcm16_to_float32(Path(path).read_bytes())
        duration = len(samples) / SAMPLE_RATE
        for chunk in args.chunks:
            if duration < chunk + max(args.offsets):
                print(f"(スキップ) {Path(path).name} chunk={chunk}: 音声が短い ({duration:.1f}s)")
                continue
            key = f"{Path(path).name} chunk={chunk:g}"
            results[key] = run(model, samples, chunk, args.offsets, Path(path).name)

    print("=" * 92)
    print(f"モデル={args.model} / word 絶対 start のずれ (秒)")
    print(f"{'条件':30} {'語数':>6} {'中央値':>8} {'p95':>8} {'p99':>8} {'最大':>8}")
    worst_p99 = worst_max = 0.0
    for key, stats in results.items():
        if not stats:
            continue
        print(f"{key:30} {stats['n']:>6} {stats['median']:>8.3f} "
              f"{stats['p95']:>8.3f} {stats['p99']:>8.3f} {stats['max']:>8.3f}")
        worst_p99 = max(worst_p99, stats["p99"])
        worst_max = max(worst_max, stats["max"])
    print()
    print(f"全条件 p99 = {worst_p99:.3f}s / 実測最大 = {worst_max:.3f}s")
    print(f"→ JITTER_MAX_SECONDS / CURSOR_MARGIN_SECONDS は実測最大を上回る値にする")
    print(f"  （現在の設定値は services/word_commit.py を参照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
