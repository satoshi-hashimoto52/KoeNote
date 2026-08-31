import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  FALLBACK_AUTO_DISMISS_MS,
  createNoticeAutoDismiss,
  errorNotice,
  inputDeviceFallbackNotice,
  isAutoDismissible,
  okNotice,
  warnNotice,
  type UiNotice
} from './uiNotice';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const FALLBACK = inputDeviceFallbackNotice('保存された入力デバイスが見つからないため、既定の入力デバイスを使用しました。');

describe('UiNotice の分類', () => {
  it('入力デバイスのフォールバックだけが自動消去の対象', () => {
    expect(isAutoDismissible(FALLBACK)).toBe(true);
    expect(isAutoDismissible(errorNotice('マイクの権限が拒否されています'))).toBe(false);
    expect(isAutoDismissible(warnNotice('Backend を再起動しています…'))).toBe(false);
    expect(isAutoDismissible(okNotice('全文をコピーしました'))).toBe(false);
    expect(isAutoDismissible(null)).toBe(false);
  });

  it('文字列一致ではなく kind で判定する', () => {
    // 同じ文言でも kind が error なら消さない。
    const sameText = errorNotice(FALLBACK.message);
    expect(isAutoDismissible(sameText)).toBe(false);
  });

  it('自動消去は 8 秒', () => {
    expect(FALLBACK_AUTO_DISMISS_MS).toBe(8000);
  });
});

describe('createNoticeAutoDismiss', () => {
  it('フォールバック通知は 8000ms で消える', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(7999);
    expect(onExpire).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onExpire).toHaveBeenCalledTimes(1);
    expect(onExpire).toHaveBeenCalledWith(FALLBACK);
  });

  it('7999ms 時点ではまだ表示されている', () => {
    const onExpire = vi.fn();
    createNoticeAutoDismiss(onExpire).schedule(FALLBACK);
    vi.advanceTimersByTime(7999);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('新しい通知で 8 秒を数え直す', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    const first = inputDeviceFallbackNotice('1回目');
    const second = inputDeviceFallbackNotice('2回目');
    c.schedule(first);
    vi.advanceTimersByTime(5000);
    c.schedule(second);
    // first の 8000ms 地点。まだ second は 3000ms しか経っていない。
    vi.advanceTimersByTime(3000);
    expect(onExpire).not.toHaveBeenCalled();
    vi.advanceTimersByTime(5000);
    expect(onExpire).toHaveBeenCalledTimes(1);
    expect(onExpire).toHaveBeenCalledWith(second);
  });

  it('古い timer が新しい通知を消さない', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(7000);
    const later = inputDeviceFallbackNotice('あとから来た通知');
    c.schedule(later);
    vi.advanceTimersByTime(1000); // 古い timer の発火時刻
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('重大エラーへ切り替わったら、保留中の timer は発火しない', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(5000);
    c.schedule(errorNotice('マイクの権限が拒否されています'));
    vi.advanceTimersByTime(60000);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('重大エラーは 8 秒後も消えない', () => {
    const onExpire = vi.fn();
    createNoticeAutoDismiss(onExpire).schedule(errorNotice('Backend の再起動に失敗しました'));
    vi.advanceTimersByTime(60000);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('null を渡すと保留中の timer を解除する', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(5000);
    c.schedule(null);
    vi.advanceTimersByTime(60000);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('dispose（アンマウント相当）で timer を解除する', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(5000);
    c.dispose();
    vi.advanceTimersByTime(60000);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('dispose 後に schedule しても発火しない', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    c.dispose();
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(60000);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('同じ通知を繰り返し schedule しても多重に発火しない', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire);
    const n: UiNotice = FALLBACK;
    c.schedule(n);
    c.schedule(n);
    c.schedule(n);
    vi.advanceTimersByTime(60000);
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it('自動消去の間隔は差し替えられる（テスト用）', () => {
    const onExpire = vi.fn();
    const c = createNoticeAutoDismiss(onExpire, 100);
    c.schedule(FALLBACK);
    vi.advanceTimersByTime(100);
    expect(onExpire).toHaveBeenCalledTimes(1);
  });
});

describe('通知のファクトリ', () => {
  it('tone は既存の banner クラスに合わせる', () => {
    expect(inputDeviceFallbackNotice('x').tone).toBe('warn');
    expect(errorNotice('x').tone).toBe('error');
    expect(warnNotice('x').tone).toBe('warn');
    expect(okNotice('x').tone).toBe('ok');
  });

  it('kind を持つ', () => {
    expect(inputDeviceFallbackNotice('x').kind).toBe('input-device-fallback');
    expect(errorNotice('x').kind).toBe('error');
    expect(warnNotice('x').kind).toBe('warn');
    expect(okNotice('x').kind).toBe('ok');
  });
});
