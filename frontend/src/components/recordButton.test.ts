import { describe, it, expect } from 'vitest';
import { resolveRecordButton } from './recordButton';

const base = { recording: false, starting: false, finalizing: false, anomaly: false };

describe('0015: 開始／停止統合ボタン', () => {
  it('停止中は「開始」で start を呼ぶ（紫系）', () => {
    const s = resolveRecordButton(base);
    expect(s.label).toBe('開始');
    expect(s.action).toBe('start');
    expect(s.disabled).toBe(false);
    expect(s.tone).toBe('primary');
  });

  it('録音中は「停止」で stop を呼ぶ（赤系）', () => {
    const s = resolveRecordButton({ ...base, recording: true });
    expect(s.label).toBe('停止');
    expect(s.action).toBe('stop');
    expect(s.disabled).toBe(false);
    expect(s.tone).toBe('danger');
  });

  it('開始処理中は「開始中…」で押せない', () => {
    const s = resolveRecordButton({ ...base, starting: true });
    expect(s.label).toBe('開始中…');
    expect(s.action).toBe('none');
    expect(s.disabled).toBe(true);
  });

  it('停止処理中は「停止中…」で押せない', () => {
    const s = resolveRecordButton({ ...base, recording: true, finalizing: true });
    expect(s.label).toBe('停止中…');
    expect(s.action).toBe('none');
    expect(s.disabled).toBe(true);
  });

  it('停止処理中は開始処理中より優先される', () => {
    const s = resolveRecordButton({ ...base, starting: true, finalizing: true });
    expect(s.label).toBe('停止中…');
  });

  it('処理中は連打しても action が none のまま', () => {
    for (const input of [{ ...base, starting: true }, { ...base, finalizing: true }]) {
      const s = resolveRecordButton(input);
      expect(s.action).toBe('none');
      expect(s.disabled).toBe(true);
    }
  });

  it('異常発生中に録音が続いていれば停止のまま（開始へ戻さない）', () => {
    const s = resolveRecordButton({ ...base, recording: true, anomaly: true });
    expect(s.action).toBe('stop');
    expect(s.tone).toBe('danger');
    expect(s.ariaLabel).toContain('異常');
  });

  it('異常で録音が止まっていても「開始」にせず保存へ誘導する', () => {
    const s = resolveRecordButton({ ...base, recording: false, anomaly: true });
    expect(s.label).toBe('停止して保存');
    expect(s.action).toBe('stop');
    expect(s.tone).toBe('danger');
    // 異常状態を通常待機（開始ボタン）として隠さない
    expect(s.label).not.toBe('開始');
  });

  it('すべての状態で aria-label が空でない', () => {
    const cases = [
      base,
      { ...base, recording: true },
      { ...base, starting: true },
      { ...base, finalizing: true },
      { ...base, anomaly: true }
    ];
    for (const c of cases) expect(resolveRecordButton(c).ariaLabel.length).toBeGreaterThan(0);
  });

  // 画面上のラベルは短縮するが、aria-label / title では役割を具体的にする（0015）
  it('表示ラベルは短く、aria-label は具体的', () => {
    const idle = resolveRecordButton(base);
    expect(idle.label).toBe('開始');
    expect(idle.label.length).toBeLessThanOrEqual(6);
    expect(idle.ariaLabel).toBe('文字起こしを開始');

    const rec = resolveRecordButton({ ...base, recording: true });
    expect(rec.label).toBe('停止');
    expect(rec.label.length).toBeLessThanOrEqual(6);
    expect(rec.ariaLabel).toBe('文字起こしを停止');
  });

  it('異常時の aria-label は異常であることを伝える', () => {
    expect(resolveRecordButton({ ...base, recording: true, anomaly: true }).ariaLabel).toContain('異常');
    expect(resolveRecordButton({ ...base, anomaly: true }).ariaLabel).toContain('異常');
  });

  it('処理中のラベルは開始中… / 停止中…', () => {
    expect(resolveRecordButton({ ...base, starting: true }).label).toBe('開始中…');
    expect(resolveRecordButton({ ...base, finalizing: true }).label).toBe('停止中…');
  });

  it('色は 開始=primary / 停止=danger', () => {
    expect(resolveRecordButton(base).tone).toBe('primary');
    expect(resolveRecordButton({ ...base, recording: true }).tone).toBe('danger');
    expect(resolveRecordButton({ ...base, starting: true }).tone).toBe('primary');
    expect(resolveRecordButton({ ...base, finalizing: true }).tone).toBe('danger');
  });
});
