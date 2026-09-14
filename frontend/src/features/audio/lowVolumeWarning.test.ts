import { describe, expect, it } from 'vitest';
import {
  IDLE_WARNING,
  WARNING_HEADINGS,
  WARNING_MESSAGES,
  beginWarning,
  isWarningState,
  thresholdMsFrom,
  updateWarning,
  warningEnabled,
  warningMessage
} from './lowVolumeWarning';

const THRESHOLD = 20_000;

describe('isWarningState', () => {
  it('通常の無言時間は警告対象にしない', () => {
    expect(isWarningState('silent_ok')).toBe(false);
    expect(isWarningState('ok')).toBe(false);
  });

  it('原因のある状態だけ警告対象にする', () => {
    expect(isWarningState('no_input')).toBe(true);
    expect(isWarningState('too_quiet')).toBe(true);
    expect(isWarningState('low_snr')).toBe(true);
    expect(isWarningState('clipping')).toBe(true);
  });
});

describe('updateWarning', () => {
  it('設定時間に達するまで警告を出さない', () => {
    let state = beginWarning(0);
    let decision = updateWarning(state, 'too_quiet', 0, THRESHOLD);
    state = decision.next;
    expect(decision.raise).toBeNull();

    decision = updateWarning(state, 'too_quiet', 19_999, THRESHOLD);
    state = decision.next;
    expect(decision.raise).toBeNull();
  });

  it('境界ちょうどで発火する', () => {
    let state = beginWarning(0);
    state = updateWarning(state, 'too_quiet', 0, THRESHOLD).next;
    const decision = updateWarning(state, 'too_quiet', 20_000, THRESHOLD);
    expect(decision.raise).toBe('too_quiet');
    expect(decision.next.raised).toBe(true);
  });

  it('同じ状態が続いても連打しない', () => {
    let state = beginWarning(0);
    state = updateWarning(state, 'too_quiet', 0, THRESHOLD).next;
    state = updateWarning(state, 'too_quiet', 20_000, THRESHOLD).next;
    for (const now of [21_000, 30_000, 120_000]) {
      const decision = updateWarning(state, 'too_quiet', now, THRESHOLD);
      state = decision.next;
      expect(decision.raise).toBeNull();
    }
  });

  it('正常へ戻ったら解除する', () => {
    let state = beginWarning(0);
    state = updateWarning(state, 'too_quiet', 0, THRESHOLD).next;
    state = updateWarning(state, 'too_quiet', 20_000, THRESHOLD).next;
    const decision = updateWarning(state, 'ok', 21_000, THRESHOLD);
    expect(decision.clear).toBe(true);
    expect(decision.next.raised).toBe(false);
    expect(decision.next.state).toBe('ok');
  });

  it('無言時間が長く続いても警告しない', () => {
    let state = beginWarning(0);
    for (const now of [0, 20_000, 60_000, 600_000]) {
      const decision = updateWarning(state, 'silent_ok', now, THRESHOLD);
      state = decision.next;
      expect(decision.raise).toBeNull();
      expect(decision.clear).toBe(false);
    }
  });

  it('状態が切り替わると計時をやり直す', () => {
    let state = beginWarning(0);
    state = updateWarning(state, 'too_quiet', 0, THRESHOLD).next;
    // 19 秒で別状態へ
    state = updateWarning(state, 'low_snr', 19_000, THRESHOLD).next;
    // 元の経過時間を引き継いでいたら即発火してしまう
    expect(updateWarning(state, 'low_snr', 30_000, THRESHOLD).raise).toBeNull();
    expect(updateWarning(state, 'low_snr', 39_000, THRESHOLD).raise).toBe('low_snr');
  });

  it('警告中に別の警告状態へ移ったら、まず消してから出し直す', () => {
    let state = beginWarning(0);
    state = updateWarning(state, 'too_quiet', 0, THRESHOLD).next;
    state = updateWarning(state, 'too_quiet', 20_000, THRESHOLD).next;
    const switched = updateWarning(state, 'clipping', 21_000, THRESHOLD);
    expect(switched.clear).toBe(true);
    expect(switched.raise).toBeNull();
    state = switched.next;
    expect(updateWarning(state, 'clipping', 41_000, THRESHOLD).raise).toBe('clipping');
  });

  it('4 つの原因を区別して返す', () => {
    for (const cause of ['no_input', 'too_quiet', 'low_snr', 'clipping'] as const) {
      let state = beginWarning(0);
      state = updateWarning(state, cause, 0, THRESHOLD).next;
      expect(updateWarning(state, cause, THRESHOLD, THRESHOLD).raise).toBe(cause);
    }
  });

  it('録音開始で前セッションの状態を持ち越さない', () => {
    let state = beginWarning(0);
    state = updateWarning(state, 'too_quiet', 0, THRESHOLD).next;
    state = updateWarning(state, 'too_quiet', 20_000, THRESHOLD).next;
    expect(state.raised).toBe(true);
    const fresh = beginWarning(50_000);
    expect(fresh.raised).toBe(false);
    expect(fresh.state).toBe('ok');
  });

  it('未観測（since=0）でも即発火しない', () => {
    const decision = updateWarning({ ...IDLE_WARNING, state: 'too_quiet' }, 'too_quiet', 999_999, THRESHOLD);
    expect(decision.raise).toBeNull();
    expect(decision.next.since).toBe(999_999);
  });
});

describe('thresholdMsFrom', () => {
  it('秒をミリ秒へ変換し、範囲外は clamp する', () => {
    expect(thresholdMsFrom(20)).toBe(20_000);
    expect(thresholdMsFrom(1)).toBe(5_000);
    expect(thresholdMsFrom(9999)).toBe(120_000);
    expect(thresholdMsFrom(Number.NaN)).toBe(20_000);
  });
});

describe('warningEnabled', () => {
  it('設定で切れる', () => {
    expect(warningEnabled({ lowInputWarning: true })).toBe(true);
    expect(warningEnabled({ lowInputWarning: false })).toBe(false);
  });
});

describe('文言', () => {
  it('4 つの原因すべてに本文と見出しがある', () => {
    for (const cause of ['no_input', 'too_quiet', 'low_snr', 'clipping'] as const) {
      expect(WARNING_MESSAGES[cause].length).toBeGreaterThan(10);
      expect(WARNING_HEADINGS[cause].length).toBeGreaterThan(0);
      expect(warningMessage(cause)).toBe(WARNING_MESSAGES[cause]);
    }
  });

  it('低音量の文言に対処方法が含まれる', () => {
    expect(WARNING_MESSAGES.too_quiet).toContain('入力テスト');
    expect(WARNING_MESSAGES.too_quiet).toContain('マイク位置');
  });
});
