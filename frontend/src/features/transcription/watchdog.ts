/**
 * 文字起こし失速ウォッチドッグの判定ロジック（純関数）。
 *
 * React フックの中に埋まっていると単体で検証できないため、状態遷移だけを
 * ここへ出す。フック側は薄く呼ぶだけにする。
 */

export interface ProcessedMark {
  /** 監視対象のセッション。切り替わりを検知するために持つ。 */
  sessionId: string | null;
  /** これまでに観測した processed_audio_seconds の最大値。 */
  value: number;
  /** value が「健全に進んでいる」と最後に確認できた時刻。0 は未観測。 */
  at: number;
}

export const NO_MARK: ProcessedMark = { sessionId: null, value: 0, at: 0 };

/** 録音開始。前のセッションの監視状態を持ち越さない。 */
export function beginSession(sessionId: string | null, now: number): ProcessedMark {
  return { sessionId, value: 0, at: now };
}

/**
 * heartbeat の processed_audio_seconds を取り込む。
 *
 * `at` を更新する条件は「値が増えた」だけではない。Backend を再起動すると
 * 新しいセッションになり processed_audio_seconds は 0 から数え直しになる。
 * また録音開始直後はモデルのロードが終わるまで値が 0 のまま動かない。
 * どちらも「進んでいない」のではなく「まだ進む前」なので、失速として
 * 扱ってはいけない（0011）。
 */
export function updateProcessedMark(
  mark: ProcessedMark,
  sessionId: string | null,
  processed: number,
  now: number
): ProcessedMark {
  // セッションが変わったら基準を作り直す。旧セッションの大きな値と
  // 比較すると、新セッションの小さな値が永久に追いつけず誤検知する。
  if (sessionId !== mark.sessionId) {
    return { sessionId, value: processed, at: now };
  }
  // 値が増えた = 確実に進んでいる。
  if (processed > mark.value) {
    return { sessionId, value: processed, at: now };
  }
  // 値が減った = 別セッションの値を受け取った可能性がある。基準を作り直す。
  if (processed < mark.value) {
    return { sessionId, value: processed, at: now };
  }
  // 未観測なら起点を作る。
  if (mark.at === 0) {
    return { sessionId, value: processed, at: now };
  }
  return mark;
}

/**
 * 初回進捗が出るまでの猶予倍率。
 *
 * 録音開始直後は Whisper モデルのロードと最初の窓の推論が終わるまで
 * processed_audio_seconds が 0 のまま動かない。Backend を再起動した直後は
 * モデルが未ロードなので特に長い。この区間は「進んでいない」のではなく
 * 「まだ進む前」であり、本当の停止と区別できない。
 * 検知を捨てずに済むよう、初回進捗までは閾値を延ばす（0011）。
 */
export const STARTUP_STALL_MULTIPLIER = 3;

/**
 * 失速したか。`at` が未設定のうちは判定しない。
 *
 * 境界は **`>=`**（閾値ちょうどで発火する）。
 * - 初回進捗前（`value === 0`）: `thresholdMs * STARTUP_STALL_MULTIPLIER`
 * - 初回進捗後（`value > 0`）  : `thresholdMs`
 */
export function isTranscriptionStalled(
  mark: ProcessedMark,
  now: number,
  thresholdMs: number
): boolean {
  if (mark.at <= 0) return false;
  const limit = stallLimitMs(mark, thresholdMs);
  return now - mark.at >= limit;
}

/** 現在適用される失速閾値。初回進捗の前後で切り替わる。 */
export function stallLimitMs(mark: ProcessedMark, thresholdMs: number): number {
  return mark.value > 0 ? thresholdMs : thresholdMs * STARTUP_STALL_MULTIPLIER;
}

/** 経過ミリ秒。表示用。 */
export function stalledForMs(mark: ProcessedMark, now: number): number {
  if (mark.at <= 0) return 0;
  return now - mark.at;
}
