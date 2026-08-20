"""word timestamp 単位で確定境界を管理する、リアルタイム文字起こしの確定判定。

背景:
    確定線 stable_until は次 window の開始時刻と数学的に一致する
    (window_end - overlap == window_start + step)。そのため segment 単位で
    「確定線までに終わったものだけ確定する」判定にすると、確定線をまたぐ segment は
    保留になったまま次 window に含まれず、テキストが失われる。
    segment 単位で捨てる/丸ごと確定するどちらも欠落か重複を生むため、
    word 単位で確定し、既確定分は絶対時刻を主判定にして除去する。

判定の優先順:
    1. 主判定 = 絶対音声時刻と確定済み音声境界
    2. 補助   = 曖昧帯 (±JITTER_MAX_SECONDS) 内での系列位置決めのみ
    文字列一致そのものを重複除去の根拠にはしない
    (「はい、はい」のような正当な繰り返しを消してしまうため)。
"""
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

EPS = 1e-6

# ---------------------------------------------------------------------------
# 設計値（ここで一元管理する。各所へ数値を散らさない）
# ---------------------------------------------------------------------------
# 暫定的な設計値。faster-whisper small + word_timestamps=True で、同一の日本語音声を
# 開始位置の異なる窓で推論し、同じ語の絶対時刻のずれを実測して決めた。
#   測定条件: 音声2種（句読点あり22.5秒 / 無音の少ない長文32.3秒）、
#             chunk 6/8/10/12秒、窓開始 0..8秒、対応 768 語
#   結果:     中央値 0.000s / p95 0.080s / p99 0.180s / 実測最大 0.440s
# JITTER_MAX_SECONDS は実測最大 0.440s を上回る最小の切りの良い値とする。
# モデル・話者・録音環境を変えると分布が変わるため、
#   .venv/bin/python scripts/measure_word_jitter.py
# で再測定して見直すこと（README「word timestamp のずれの再測定」を参照）。
JITTER_MAX_SECONDS = 0.5

# カーソル追従の安全余裕。追従先を「未確定音声の先頭 - この値」に置くことで、
# 時刻推定誤差で音声を飛び越えるのを防ぐ。根拠は JITTER_MAX_SECONDS と同じ実測値。
CURSOR_MARGIN_SECONDS = 0.5

# 確定済み末尾のうちアンカー照合に使う語数。状態量を有界に保つため上限を持つ。
ANCHOR_WORDS = 6

# 潰れた区間（start == end）に与える最小長。
MIN_WORD_DURATION_SECONDS = 0.05

# カーソルが前進できないときの最小前進量。
MIN_ADVANCE_SECONDS = 0.5

# 同一 window を連続して処理してよい回数の上限。
MAX_SAME_WINDOW_RETRIES = 3

# 推論失敗時に同じ window を再試行する回数。
MAX_ERROR_RETRIES = 2

# 窓が確定語を1つも出さなかったとき、窓境界をずらして再試行する回数の上限。
# whisper は窓の切り方によって先頭数秒を文字化しないことがある
# （実測: 12秒窓のうち先頭9.2秒に segment を1つも返さない）。
# 境界をずらして再試行すると文字化できるため、上限まで粘る。
MAX_NO_PROGRESS_RETRIES = 8

# 停止時の排出ループの上限 window 数。
DRAIN_MAX_WINDOWS = 256


# ---------------------------------------------------------------------------
# 縮退の reason コード
# ---------------------------------------------------------------------------
class DegradeReason:
    INFERENCE_FAILED = "inference_failed"                # Whisper が例外を投げた
    INFERENCE_RETRY_EXHAUSTED = "inference_retry_exhausted"
    SEGMENT_WITHOUT_WORDS = "segment_without_words"      # segment はあるが words が空
    MISSING_WORD_TIMES = "missing_word_times"            # word.start/end が欠落
    REVERSED_WORD_TIMES = "reversed_word_times"          # start > end の逆転
    INVALID_WORD_TIMES = "invalid_word_times"            # 数値でない/窓外などの不正値
    COLLAPSED_WORD_SPAN = "collapsed_word_span"          # start == end で長さ0
    REINFER_UNCONFIRMED_RANGE = "reinfer_unconfirmed_range"  # 擬似word重なり -> 未確定区間を再推論
    REINFER_FAILED = "reinfer_failed"
    ANCHOR_MISMATCH = "anchor_mismatch"                  # 曖昧帯でアンカーと一致しなかった
    FORCED_COMMIT = "forced_commit"                      # 前進保証のための強制確定
    FORCED_COMMIT_PENDING = "forced_commit_pending"      # 解消不能な pending の強制確定
    CURSOR_MIN_ADVANCE = "cursor_min_advance"            # 最小前進で前へ出した
    CURSOR_WOULD_SKIP = "cursor_would_skip"              # 未確定音声を飛び越えかけた
    CURSOR_WINDOW_CLAMPED = "cursor_window_clamped"      # リングバッファ範囲へ収めた
    SAME_WINDOW_RETRY = "same_window_retry"              # 同一 window の連続処理
    DRAIN_LIMIT_REACHED = "drain_limit_reached"          # 排出が上限に達した
    DRAIN_UNRECOVERED = "drain_unrecovered"              # 回収できなかった音声が残った
    WINDOW_YIELDED_NOTHING = "window_yielded_nothing"    # 窓が確定語を1つも出さなかった
    AUDIO_LEFT_UNTRANSCRIBED = "audio_left_untranscribed"  # 再試行しても文字化できず先へ進めた


# ---------------------------------------------------------------------------
# アンカー照合用のテキスト正規化
# ---------------------------------------------------------------------------
# whisper は窓ごとに句読点や空白の付き方を変える。アンカー照合ではその差だけを
# 無視したいので、下記だけを取り除く。
#   - 空白（半角/全角/タブ/改行）
#   - 句読点・記号（和文/欧文）
# 長音符「ー」、促音、繰り返し記号「々」など語の一部になる文字は取り除かない。
_ANCHOR_IGNORED = " \t\r\n　、。，．,.!?！?？・…‥「」『』（）()〔〕[]{}：:；;〜~-–—\"'“”‘’"
_ANCHOR_TRANSLATION = {ord(ch): None for ch in _ANCHOR_IGNORED}


def normalize_anchor_text(text: str) -> str:
    """アンカー照合用にテキストを正規化する（句読点と空白の差だけを無視する）。"""
    return str(text or "").translate(_ANCHOR_TRANSLATION)


# ---------------------------------------------------------------------------
# word
# ---------------------------------------------------------------------------
@dataclass
class CommitWord:
    """確定判定の単位。絶対時刻（セッション先頭からの秒）で保持する。"""
    start: float
    end: float
    text: str
    # word timestamp が得られず segment 全体を1語として扱った場合 True。
    # 擬似 word はテキスト系列で照合できないため、通常確定と区別して扱う。
    pseudo: bool = False

    @property
    def anchor_key(self) -> str:
        return normalize_anchor_text(self.text)


@dataclass
class WindowCounters:
    """縮退・強制の発生状況。診断ログと metrics に出す。"""
    pseudo_word_count: int = 0
    forced_commit_count: int = 0
    degraded_window_count: int = 0
    anchor_mismatch_count: int = 0
    same_window_retry_count: int = 0
    reinferred_count: int = 0
    inference_error_count: int = 0
    cursor_min_advance_count: int = 0
    drain_window_count: int = 0
    unrecovered_seconds: float = 0.0
    # whisper が窓の一部を文字化しなかったため、再試行しても確定できなかった秒数。
    untranscribed_seconds: float = 0.0
    # アンカー不一致時に使う中点フォールバックの発生回数と、その結果。
    midpoint_fallback_count: int = 0
    midpoint_dropped_words: int = 0
    midpoint_kept_words: int = 0

    def as_dict(self) -> dict:
        return {
            "pseudo_word_count": self.pseudo_word_count,
            "forced_commit_count": self.forced_commit_count,
            "degraded_window_count": self.degraded_window_count,
            "anchor_mismatch_count": self.anchor_mismatch_count,
            "same_window_retry_count": self.same_window_retry_count,
            "reinferred_count": self.reinferred_count,
            "inference_error_count": self.inference_error_count,
            "cursor_min_advance_count": self.cursor_min_advance_count,
            "drain_window_count": self.drain_window_count,
            "unrecovered_seconds": round(self.unrecovered_seconds, 2),
            "untranscribed_seconds": round(self.untranscribed_seconds, 2),
            "midpoint_fallback_count": self.midpoint_fallback_count,
            "midpoint_dropped_words": self.midpoint_dropped_words,
            "midpoint_kept_words": self.midpoint_kept_words,
        }


DegradeLogger = Callable[..., None]


def _noop_logger(reason: str, **detail) -> None:
    return None


# ---------------------------------------------------------------------------
# 正規化: whisper の出力を安全な word 列にする
# ---------------------------------------------------------------------------
def normalize_words(
    segments: Iterable[dict],
    window_start: float,
    window_end: float,
    counters: WindowCounters,
    log: DegradeLogger = _noop_logger,
) -> list[CommitWord]:
    """segment 列を絶対時刻の word 列にする。

    word 情報が使えない segment は「segment 全体を1語とする擬似 word」にする。
    擬似 word も通常の判定経路（確定/保留）に載せる。全確定も全破棄もしない。
    時刻は必ず [window_start, window_end] に収める。
    """
    words: list[CommitWord] = []
    degraded_window = False

    for segment in segments:
        raw_words = segment.get("words") or []
        usable: list[CommitWord] = []
        for raw in raw_words:
            start, end = raw.get("start"), raw.get("end")
            text = str(raw.get("text", "") or "")
            if not text.strip():
                continue
            if start is None or end is None:
                log(DegradeReason.MISSING_WORD_TIMES, text=text[:20])
                degraded_window = True
                continue
            try:
                start, end = float(start), float(end)
            except (TypeError, ValueError):
                log(DegradeReason.INVALID_WORD_TIMES, text=text[:20], start=start, end=end)
                degraded_window = True
                continue
            if start != start or end != end:  # NaN
                log(DegradeReason.INVALID_WORD_TIMES, text=text[:20], start=start, end=end)
                degraded_window = True
                continue
            if end < start:
                log(DegradeReason.REVERSED_WORD_TIMES, text=text[:20], start=start, end=end)
                degraded_window = True
                start, end = end, start
            start = min(max(start, window_start), window_end)
            end = min(max(end, start), window_end)
            if end - start < MIN_WORD_DURATION_SECONDS:
                log(DegradeReason.COLLAPSED_WORD_SPAN, text=text[:20], start=start)
                degraded_window = True
                end = min(window_end, start + MIN_WORD_DURATION_SECONDS)
            usable.append(CommitWord(start=start, end=end, text=text))

        if usable:
            words.extend(usable)
            continue

        # word 情報が取れない -> segment 全体を1個の擬似 word にする。
        segment_text = str(segment.get("text", "") or "")
        if not segment_text.strip():
            continue
        log(DegradeReason.SEGMENT_WITHOUT_WORDS, text_length=len(segment_text))
        degraded_window = True
        counters.pseudo_word_count += 1
        start = min(max(float(segment.get("start", window_start) or window_start), window_start), window_end)
        end = min(max(float(segment.get("end", window_end) or window_end), start), window_end)
        if end - start < MIN_WORD_DURATION_SECONDS:
            end = min(window_end, start + MIN_WORD_DURATION_SECONDS)
        words.append(CommitWord(start=start, end=end, text=segment_text, pseudo=True))

    if degraded_window:
        counters.degraded_window_count += 1
    words.sort(key=lambda w: (w.start, w.end))
    return words


# ---------------------------------------------------------------------------
# 既確定分の除去
# ---------------------------------------------------------------------------
def trim_committed(
    words: list[CommitWord],
    committed_until: float,
    anchor: list[CommitWord],
    counters: WindowCounters,
    log: DegradeLogger = _noop_logger,
) -> list[CommitWord]:
    """既に確定した語を除去する。

    (1) 主判定: 曖昧帯より前に終わる語は無条件に既確定とみなす。
    (2) 曖昧帯 (committed_until ± JITTER_MAX_SECONDS) は、確定済み末尾（アンカー）と
        単調な接頭辞一致する分だけ既確定とみなす。長い一致を優先することで、
        同じ語が連続していても順に正しく消費できる。
    (3) 一致しなかった場合は帯の中身を新規として扱う（欠落させない）。
        一致しなかった事実はデバッグログに残す。
    """
    if not words:
        return words

    boundary = committed_until - JITTER_MAX_SECONDS
    index = 0
    while index < len(words) and words[index].end <= boundary:
        index += 1
    rest = words[index:]
    if not rest or not anchor:
        return rest

    # 曖昧帯に入っている語の個数（この範囲までがアンカー照合の対象）
    limit = 0
    for position, word in enumerate(rest):
        if word.start < committed_until + JITTER_MAX_SECONDS:
            limit = position + 1
        else:
            break
    if limit == 0:
        return rest

    # 同じ語が密に連続する場合（「あの あの あの…」）はテキストが全部一致するため、
    # 「一致する最長 n」を選ぶと誤ったオフセットで揃ってしまい、欠落や重複を生む。
    # テキスト一致を候補の条件に留め、時刻差が最小になる整合を選ぶ。
    best_n, best_error = 0, None
    for n in range(1, min(len(anchor), limit) + 1):
        tail, head = anchor[-n:], rest[:n]
        if not all(a.anchor_key and a.anchor_key == b.anchor_key for a, b in zip(tail, head)):
            continue
        error = max(abs(a.start - b.start) for a, b in zip(tail, head))
        if error > JITTER_MAX_SECONDS:
            continue
        # 誤差が同じなら長い一致を採る（より多くの既確定を消費できる）。
        if best_error is None or error < best_error - EPS or (
            abs(error - best_error) <= EPS and n > best_n
        ):
            best_n, best_error = n, error
    if best_n:
        return rest[best_n:]

    counters.anchor_mismatch_count += 1
    log(
        DegradeReason.ANCHOR_MISMATCH,
        committed_until=round(committed_until, 3),
        anchor=[a.anchor_key for a in anchor[-3:]],
        band=[w.anchor_key for w in rest[:min(3, limit)]],
    )
    # アンカーが一致しないのは、同じ音声が窓ごとに別テキストで認識された場合
    # （「段階的」->「感解的」のような認識揺れ）。テキストでは対応が取れないので
    # 時刻だけで判断する: 音声の大半が確定境界より前にある語は既確定とみなす。
    #   end > committed_until だけを条件にすると、確定境界で終わる語（同じ音声の
    #   再観測）が残り 1 文字ぶんの本文重複になる（実測: 10分音声で24箇所）。
    counters.midpoint_fallback_count += 1
    kept = [w for w in rest if (w.start + w.end) / 2.0 >= committed_until - EPS]
    counters.midpoint_kept_words += len(kept)
    counters.midpoint_dropped_words += len(rest) - len(kept)
    return kept


# ---------------------------------------------------------------------------
# 確定 / 保留の振り分け
# ---------------------------------------------------------------------------
def classify_words(
    words: list[CommitWord],
    window_start: float,
    stable_until: float,
    counters: WindowCounters,
    log: DegradeLogger = _noop_logger,
) -> tuple[list[CommitWord], list[CommitWord]]:
    """確定できる語と保留する語に分ける。

    確定線までに終わる語は確定する。確定線をまたぐ語のうち、window の先頭以前から
    始まっているものは「これより前から始まる window が今後存在しない」ため、
    ここで確定しなければ永久に確定できない。よって強制確定する。
    この規則がカーソル前進保証の根拠にもなる（pending の先頭は必ず window_start より後）。
    """
    confirm: list[CommitWord] = []
    pending: list[CommitWord] = []
    for word in words:
        if word.end <= stable_until + EPS:
            confirm.append(word)
        elif word.start <= window_start + EPS:
            counters.forced_commit_count += 1
            log(
                DegradeReason.FORCED_COMMIT,
                start=round(word.start, 3),
                end=round(word.end, 3),
                pseudo=word.pseudo,
                text=word.text[:20],
            )
            confirm.append(word)
        else:
            pending.append(word)
    return confirm, pending
