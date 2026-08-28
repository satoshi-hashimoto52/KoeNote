import { describe, it, expect } from 'vitest';
import {
  NO_MARK,
  STARTUP_STALL_MULTIPLIER,
  beginSession,
  isTranscriptionStalled,
  stallLimitMs,
  updateProcessedMark,
  type ProcessedMark
} from './watchdog';

const STALL_MS = 60_000;

/** heartbeat を時系列で流し、発火時刻を返す。発火しなければ null。 */
function runTimeline(
  events: Array<{ at: number; sessionId: string | null; processed: number }>,
  opts: { start?: { at: number; sessionId: string | null }; untilMs: number; tickMs?: number }
): number | null {
  let mark: ProcessedMark = opts.start ? beginSession(opts.start.sessionId, opts.start.at) : NO_MARK;
  const tick = opts.tickMs ?? 1000;
  const t0 = opts.start?.at ?? 0;
  let fired: number | null = null;
  let raised = false;
  for (let now = t0; now <= opts.untilMs; now += tick) {
    for (const e of events) {
      if (e.at > now - tick && e.at <= now) {
        mark = updateProcessedMark(mark, e.sessionId, e.processed, e.at);
      }
    }
    if (!raised && isTranscriptionStalled(mark, now, STALL_MS)) {
      fired = now;
      raised = true; // 一度だけ。以降は繰り返さない
    }
  }
  return fired;
}

describe('0011: transcription_stalled の判定', () => {
  // 通常録音: processed が増え続ける
  it('通常録音では発火しない', () => {
    const events = [];
    for (let i = 1; i <= 120; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: i * 0.9 });
    expect(runTimeline(events, { start: { at: 0, sessionId: 's1' }, untilMs: 120_000 })).toBeNull();
  });

  // 90 秒無音でも processed は進む（B-1 の前提）
  it('90 秒無音でも processed が増えていれば発火しない', () => {
    const events = [];
    for (let i = 1; i <= 150; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: i * 1.0 });
    expect(runTimeline(events, { start: { at: 0, sessionId: 's1' }, untilMs: 150_000 })).toBeNull();
  });

  // 本当に止まった場合は検知する（機能を殺さない）
  it('processed が 60 秒以上動かなければ 1 回だけ発火する', () => {
    const events = [
      { at: 1000, sessionId: 's1', processed: 10 },
      { at: 2000, sessionId: 's1', processed: 20 }
      // 以降 heartbeat は来るが値が動かない想定
    ];
    for (let i = 3; i <= 120; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: 20 });
    const fired = runTimeline(events, { start: { at: 0, sessionId: 's1' }, untilMs: 120_000 });
    expect(fired).not.toBeNull();
    expect(fired!).toBeGreaterThanOrEqual(62_000);
    expect(fired!).toBeLessThanOrEqual(64_000);
  });

  // ★ 再現1: Backend 再起動で processed が 0 へ戻る
  it('Backend 再接続で processed が 0 へ戻っても誤検知しない', () => {
    const events = [];
    // 旧セッションで 200 秒ぶん処理済み
    for (let i = 1; i <= 20; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: i * 10 });
    // 21 秒目に新セッションへ。processed は 0 から数え直し
    for (let i = 21; i <= 120; i += 1) {
      events.push({ at: i * 1000, sessionId: 's2', processed: (i - 20) * 0.9 });
    }
    expect(runTimeline(events, { start: { at: 0, sessionId: 's1' }, untilMs: 120_000 })).toBeNull();
  });

  // ★ 再現2: 録音開始直後、モデルロード中で processed が 0 のまま
  it('開始直後に processed が 0 のまま続いても、動き出せば誤検知しない', () => {
    const events = [];
    // 最初の 65 秒は processed=0（small モデルのロード中）
    for (let i = 1; i <= 65; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: 0 });
    // 65 秒後から進み出す
    for (let i = 66; i <= 120; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: (i - 65) * 1.0 });
    expect(runTimeline(events, { start: { at: 0, sessionId: 's1' }, untilMs: 120_000 })).toBeNull();
  });

  // 新しい session_id への切り替えで基準がリセットされる
  it('session_id が変われば value と基準時刻をリセットする', () => {
    const before: ProcessedMark = { sessionId: 's1', value: 500, at: 1000 };
    const after = updateProcessedMark(before, 's2', 3, 90_000);
    expect(after).toEqual({ sessionId: 's2', value: 3, at: 90_000 });
    expect(isTranscriptionStalled(after, 90_000, STALL_MS)).toBe(false);
  });

  // 新規録音 start で前回の監視状態を持ち越さない
  it('新規録音 start は前回セッションの状態を持ち越さない', () => {
    const stale: ProcessedMark = { sessionId: 'old', value: 999, at: 1 };
    const fresh = beginSession('new', 500_000);
    expect(fresh).toEqual({ sessionId: 'new', value: 0, at: 500_000 });
    expect(isTranscriptionStalled(fresh, 500_000, STALL_MS)).toBe(false);
    expect(stale.value).toBe(999); // 元は変更しない
  });

  // 初回進捗が一度も出なければ、猶予を過ぎた時点で検知する（検知機能を捨てない）
  it('初回進捗が出ないまま止まっていれば猶予後に発火する', () => {
    const events = [];
    for (let i = 1; i <= 300; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: 0 });
    const fired = runTimeline(events, { start: { at: 0, sessionId: 's1' }, untilMs: 300_000 });
    expect(fired).not.toBeNull();
    // 60 秒 * 3 = 180 秒の猶予を過ぎてから
    expect(fired!).toBeGreaterThan(180_000);
    expect(fired!).toBeLessThanOrEqual(183_000);
  });

  // 未観測（at=0）では判定しない
  it('未観測（at=0）のうちは発火しない', () => {
    expect(isTranscriptionStalled(NO_MARK, 10_000_000, STALL_MS)).toBe(false);
  });

  // 一度発火したら監視周期ごとに繰り返さない（呼び出し側の raised 管理を含めて確認）
  it('一度発火した後は毎 tick 繰り返さない', () => {
    const events = [{ at: 1000, sessionId: 's1', processed: 5 }];
    for (let i = 2; i <= 200; i += 1) events.push({ at: i * 1000, sessionId: 's1', processed: 5 });
    let mark: ProcessedMark = beginSession('s1', 0);
    let count = 0;
    let raised = false;
    for (let now = 0; now <= 200_000; now += 1000) {
      for (const e of events) {
        if (e.at > now - 1000 && e.at <= now) mark = updateProcessedMark(mark, e.sessionId, e.processed, e.at);
      }
      if (isTranscriptionStalled(mark, now, STALL_MS)) {
        if (!raised) count += 1;
        raised = true;
      }
    }
    expect(count).toBe(1);
  });

  // 停止中・停止処理中は判定そのものを呼ばない（呼び出し側の責務の確認）
  it('停止後に判定を呼ばなければ追加発火しない', () => {
    const mark: ProcessedMark = { sessionId: 's1', value: 10, at: 0 };
    expect(isTranscriptionStalled(mark, 10_000_000, STALL_MS)).toBe(false);
  });
});

describe('0011: 閾値の境界（比較は >= 。閾値ちょうどで発火する）', () => {
  const T0 = 1_000_000;
  const STARTUP_MS = STALL_MS * STARTUP_STALL_MULTIPLIER; // 180,000

  /** 初回進捗前（value=0）。at は確定済み。 */
  const beforeFirstAdvance: ProcessedMark = { sessionId: 's1', value: 0, at: T0 };
  /** 初回進捗後（value>0）。 */
  const afterFirstAdvance: ProcessedMark = { sessionId: 's1', value: 12.5, at: T0 };

  it('適用される閾値は初回進捗の前後で切り替わる', () => {
    expect(STARTUP_MS).toBe(180_000);
    expect(stallLimitMs(beforeFirstAdvance, STALL_MS)).toBe(180_000);
    expect(stallLimitMs(afterFirstAdvance, STALL_MS)).toBe(60_000);
  });

  // --- 初回進捗前: 180 秒 ---
  it('初回進捗前、179 秒では発火しない', () => {
    expect(isTranscriptionStalled(beforeFirstAdvance, T0 + 179_000, STALL_MS)).toBe(false);
  });

  it('初回進捗前、180 秒未満（179,999ms）では発火しない', () => {
    expect(isTranscriptionStalled(beforeFirstAdvance, T0 + 179_999, STALL_MS)).toBe(false);
  });

  it('初回進捗前、180 秒ちょうどで発火する', () => {
    expect(isTranscriptionStalled(beforeFirstAdvance, T0 + 180_000, STALL_MS)).toBe(true);
  });

  it('初回進捗前、180 秒超過で発火する', () => {
    expect(isTranscriptionStalled(beforeFirstAdvance, T0 + 180_001, STALL_MS)).toBe(true);
    expect(isTranscriptionStalled(beforeFirstAdvance, T0 + 600_000, STALL_MS)).toBe(true);
  });

  // --- 初回進捗後: 60 秒 ---
  it('初回進捗が 1 度でも発生すれば閾値は 60 秒へ戻る', () => {
    const advanced = updateProcessedMark(beforeFirstAdvance, 's1', 10, T0 + 5_000);
    expect(advanced.value).toBe(10);
    expect(stallLimitMs(advanced, STALL_MS)).toBe(60_000);
    // 進捗時刻から 179 秒後でも、60 秒を超えているので発火する
    expect(isTranscriptionStalled(advanced, T0 + 5_000 + 60_000, STALL_MS)).toBe(true);
  });

  it('初回進捗後、59 秒では発火しない', () => {
    expect(isTranscriptionStalled(afterFirstAdvance, T0 + 59_000, STALL_MS)).toBe(false);
  });

  it('初回進捗後、60 秒未満（59,999ms）では発火しない', () => {
    expect(isTranscriptionStalled(afterFirstAdvance, T0 + 59_999, STALL_MS)).toBe(false);
  });

  it('初回進捗後、60 秒ちょうどで発火する', () => {
    expect(isTranscriptionStalled(afterFirstAdvance, T0 + 60_000, STALL_MS)).toBe(true);
  });

  it('初回進捗後、60 秒超過で発火する', () => {
    expect(isTranscriptionStalled(afterFirstAdvance, T0 + 60_001, STALL_MS)).toBe(true);
  });

  // --- 重複発火しない ---
  it('初回進捗前も発火後に監視周期ごとの重複発火をしない', () => {
    let raised = false;
    let count = 0;
    for (let now = T0; now <= T0 + 400_000; now += 1000) {
      if (isTranscriptionStalled(beforeFirstAdvance, now, STALL_MS)) {
        if (!raised) count += 1;
        raised = true;
      }
    }
    expect(count).toBe(1);
  });

  // --- セッション切替で 180 秒猶予が再適用される ---
  it('session_id 変更後は新セッションとして 180 秒猶予を適用する', () => {
    const stale: ProcessedMark = { sessionId: 's1', value: 500, at: T0 };
    const switched = updateProcessedMark(stale, 's2', 0, T0 + 300_000);
    expect(switched).toEqual({ sessionId: 's2', value: 0, at: T0 + 300_000 });
    expect(stallLimitMs(switched, STALL_MS)).toBe(180_000);
    expect(isTranscriptionStalled(switched, T0 + 300_000 + 179_999, STALL_MS)).toBe(false);
    expect(isTranscriptionStalled(switched, T0 + 300_000 + 180_000, STALL_MS)).toBe(true);
  });

  // --- processed の巻き戻りでも基準が作り直される ---
  it('processed 値が減少したら新しい基準から監視する', () => {
    const high: ProcessedMark = { sessionId: 's1', value: 500, at: T0 };
    const rolledBack = updateProcessedMark(high, 's1', 3, T0 + 400_000);
    expect(rolledBack).toEqual({ sessionId: 's1', value: 3, at: T0 + 400_000 });
    // value > 0 なので通常閾値 60 秒
    expect(stallLimitMs(rolledBack, STALL_MS)).toBe(60_000);
    expect(isTranscriptionStalled(rolledBack, T0 + 400_000 + 59_999, STALL_MS)).toBe(false);
    expect(isTranscriptionStalled(rolledBack, T0 + 400_000 + 60_000, STALL_MS)).toBe(true);
  });

  it('processed が 0 へ巻き戻った場合は 180 秒猶予になる', () => {
    const high: ProcessedMark = { sessionId: 's1', value: 500, at: T0 };
    const zeroed = updateProcessedMark(high, 's1', 0, T0 + 400_000);
    expect(zeroed.value).toBe(0);
    expect(stallLimitMs(zeroed, STALL_MS)).toBe(180_000);
  });
});
