/**
 * 低音量警告の状態機械（0019）。
 *
 * Backend が **瞬時状態** を判定して heartbeat で送ってくる
 * （`services/live_session.py` の `evaluate_level_state`）。
 * ここは「何秒続いたら出すか」「いつ消すか」「連打しないか」という
 * UI 方針だけを持つ。純関数なので単体で検証できる。
 *
 * 明確に分離すること:
 *   - Backend / 文字起こしの**停止**は `features/transcription/watchdog.ts` の担当。
 *     本モジュールは一切関与しない。両方が同じ事象へ反応すると、
 *     どちらの誤検知かが判別できなくなる。
 *   - `silent_ok`（発話が無く本当に静か）は**通常の無言時間**であり警告しない。
 */

/** Backend から届く瞬時状態。 */
export type InputLevelState =
  | 'ok'
  | 'silent_ok'
  | 'no_input'
  | 'too_quiet'
  | 'low_snr'
  | 'clipping';

/** 警告を出す対象の状態。`ok` / `silent_ok` は含めない。 */
export const WARNING_STATES: readonly InputLevelState[] = [
  'no_input',
  'too_quiet',
  'low_snr',
  'clipping'
];

export function isWarningState(state: InputLevelState): boolean {
  return (WARNING_STATES as readonly string[]).includes(state);
}

/** 状態ごとの文言。原因が分かる書き方にする。 */
export const WARNING_MESSAGES: Record<Exclude<InputLevelState, 'ok' | 'silent_ok'>, string> = {
  no_input:
    '入力音声を検出できません。マイクの接続と、システム設定のマイク権限を確認してください。',
  too_quiet:
    '入力音量が低いため、遠い話者の音声が文字起こしされない可能性があります。入力テストまたはマイク位置の調整を行ってください。',
  low_snr:
    '環境音に対して話し声が小さいため、認識精度が落ちる可能性があります。マイクを話者へ近づけるか、騒音源を減らしてください。',
  clipping:
    '入力音量が大きすぎて音割れしています。マイクを話者から離すか、入力音量を下げてください。'
};

/** 状態ごとの短い見出し。320px 幅でも読める長さにする。 */
export const WARNING_HEADINGS: Record<Exclude<InputLevelState, 'ok' | 'silent_ok'>, string> = {
  no_input: '入力なし',
  too_quiet: '音量が小さい',
  low_snr: '環境音が大きい',
  clipping: '音割れ'
};

export interface WarningState {
  /** 現在観測している状態。 */
  state: InputLevelState;
  /**
   * その状態になった時刻（ms）。**負値だけが「未観測」**。
   * 0 を未観測の印にすると、`performance.now()` 基準や単体テストのように
   * 時刻 0 から始まる系で最初の警告が永久に出なくなる。
   */
  since: number;
  /** 既に警告を出したか。状態が変わるまで再通知しない。 */
  raised: boolean;
}

export const IDLE_WARNING: WarningState = { state: 'ok', since: -1, raised: false };

export interface WarningDecision {
  next: WarningState;
  /** この遷移で新しく出すべき警告。無ければ null。 */
  raise: Exclude<InputLevelState, 'ok' | 'silent_ok'> | null;
  /** この遷移で消すべきか（警告中に正常へ戻った）。 */
  clear: boolean;
}

/** 録音開始時。前セッションの状態を持ち越さない。 */
export function beginWarning(now: number): WarningState {
  return { state: 'ok', since: now, raised: false };
}

/**
 * 状態を 1 つ取り込み、警告を出す/消すかを決める。
 *
 * - 状態が変わったら計時をやり直す
 * - `thresholdMs` 以上続き、まだ出していなければ出す（境界は `>=`）
 * - 警告対象でない状態へ移ったら、出していた警告を消す
 * - 同じ警告状態が続く間は再通知しない（連打防止）
 */
export function updateWarning(
  current: WarningState,
  state: InputLevelState,
  now: number,
  thresholdMs: number
): WarningDecision {
  if (state !== current.state) {
    const wasRaised = current.raised;
    const next: WarningState = { state, since: now, raised: false };
    // 警告中に別の状態へ移ったら、いったん消す。
    // 新しい状態が警告対象なら、改めて thresholdMs だけ待って出し直す。
    return { next, raise: null, clear: wasRaised };
  }

  if (!isWarningState(state)) {
    return { next: current, raise: null, clear: false };
  }
  if (current.raised) {
    return { next: current, raise: null, clear: false };
  }
  if (current.since < 0) {
    return { next: { ...current, since: now }, raise: null, clear: false };
  }
  if (now - current.since < thresholdMs) {
    return { next: current, raise: null, clear: false };
  }
  return {
    next: { ...current, raised: true },
    raise: state as Exclude<InputLevelState, 'ok' | 'silent_ok'>,
    clear: false
  };
}

/** 警告が有効かどうか（設定で切られていたら常に無効）。 */
export function warningEnabled(settings: { lowInputWarning: boolean }): boolean {
  return Boolean(settings?.lowInputWarning);
}

/** 設定の秒数をミリ秒へ。範囲外は既定へ落とす。 */
export function thresholdMsFrom(seconds: number): number {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return 20_000;
  return Math.min(120, Math.max(5, value)) * 1000;
}

/** 警告バナーに出す本文。 */
export function warningMessage(state: Exclude<InputLevelState, 'ok' | 'silent_ok'>): string {
  return WARNING_MESSAGES[state];
}
