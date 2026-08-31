import { describe, it, expect } from 'vitest';
import {
  FALLBACK_NOTICE,
  RELABEL_NOTICE,
  buildAudioConstraints,
  describeGetUserMediaError,
  resolveInputDevice
} from './inputDevice';

/** enumerateDevices() の戻りを最小限で模したもの。 */
function dev(deviceId: string, label: string): MediaDeviceInfo {
  return { deviceId, label, kind: 'audioinput', groupId: 'g', toJSON: () => ({}) } as MediaDeviceInfo;
}

const DEVICES = [
  dev('default', 'Default - MacBook Airのマイク (Built-in)'),
  dev('aaa111', 'MacBook Airのマイク (Built-in)'),
  dev('bbb222', 'BlackHole 2ch (Virtual)')
];

describe('resolveInputDevice', () => {
  it('保存 deviceId が現在の一覧に存在すれば exact 指定する', () => {
    const r = resolveInputDevice('aaa111', 'MacBook Airのマイク (Built-in)', DEVICES);
    expect(r.ok).toBe(true);
    expect(r.effectiveDeviceId).toBe('aaa111');
    expect(r.matchedBy).toBe('deviceId');
    expect(r.fallbackReason).toBeNull();
    expect(r.notice).toBeNull();
  });

  it('開発版由来の deviceId が存在しなければ既定入力へフォールバックする', () => {
    // 実際に起きた事象: http://localhost:5173 で保存した ID は file:// に存在しない。
    const r = resolveInputDevice('4611f1386b1a799cfa9492798c24a002', '', DEVICES);
    expect(r.ok).toBe(true);
    expect(r.effectiveDeviceId).toBeNull();
    expect(r.matchedBy).toBe('default');
    expect(r.fallbackReason).toBe('device_id_not_found');
    expect(r.notice).toBe(FALLBACK_NOTICE);
  });

  it('deviceId は違ってもラベルが一意に一致すれば現 origin の ID へ再解決する', () => {
    const r = resolveInputDevice('stale-id', 'BlackHole 2ch (Virtual)', DEVICES);
    expect(r.ok).toBe(true);
    expect(r.effectiveDeviceId).toBe('bbb222');
    expect(r.matchedBy).toBe('label');
    expect(r.fallbackReason).toBe('device_id_not_found');
    expect(r.notice).toBe(RELABEL_NOTICE);
  });

  it('同じラベルが複数あるときは勝手に選ばず既定入力にする', () => {
    const dup = [dev('x1', 'USB Mic'), dev('x2', 'USB Mic')];
    const r = resolveInputDevice('stale-id', 'USB Mic', dup);
    expect(r.effectiveDeviceId).toBeNull();
    expect(r.matchedBy).toBe('default');
    expect(r.fallbackReason).toBe('label_ambiguous');
    expect(r.notice).toBe(FALLBACK_NOTICE);
  });

  it('deviceLabel を持たない旧設定でも正常にフォールバックする', () => {
    for (const label of ['', undefined, null]) {
      const r = resolveInputDevice('stale-id', label as unknown as string, DEVICES);
      expect(r.ok).toBe(true);
      expect(r.effectiveDeviceId).toBeNull();
      expect(r.matchedBy).toBe('default');
    }
  });

  it('deviceId = "default" は既定入力として扱う', () => {
    const r = resolveInputDevice('default', '', DEVICES);
    expect(r.effectiveDeviceId).toBeNull();
    expect(r.matchedBy).toBe('default');
    expect(r.fallbackReason).toBeNull();
    expect(r.notice).toBeNull();
  });

  it('保存 deviceId が未設定なら既定入力（通知なし）', () => {
    const r = resolveInputDevice('', '', DEVICES);
    expect(r.effectiveDeviceId).toBeNull();
    expect(r.matchedBy).toBe('default');
    expect(r.notice).toBeNull();
  });

  it('audioinput が 0 件なら明示的なエラーを返す', () => {
    const r = resolveInputDevice('aaa111', 'x', []);
    expect(r.ok).toBe(false);
    expect(r.effectiveDeviceId).toBeNull();
    expect(r.notice).toMatch(/入力デバイス/);
  });

  it('audioinput 以外は無視する', () => {
    const mixed = [dev('aaa111', 'Mic'), { ...dev('cam', 'Camera'), kind: 'videoinput' } as MediaDeviceInfo];
    expect(resolveInputDevice('cam', '', mixed).matchedBy).toBe('default');
    expect(resolveInputDevice('aaa111', '', mixed).matchedBy).toBe('deviceId');
  });

  it('診断ログ用の要約に完全な deviceId を含めない', () => {
    const r = resolveInputDevice('4611f1386b1a799cfa9492798c24a002', '', DEVICES);
    expect(r.logSummary).not.toContain('4611f1386b1a799cfa9492798c24a002');
    expect(r.logSummary).toContain('default');
    expect(r.logSummary).toContain('device_id_not_found');
  });
});

describe('buildAudioConstraints', () => {
  it('deviceId があれば exact 指定を含める', () => {
    const c = buildAudioConstraints('aaa111') as MediaTrackConstraints;
    expect(c.deviceId).toEqual({ exact: 'aaa111' });
  });

  it('deviceId が null なら deviceId を含めない', () => {
    const c = buildAudioConstraints(null) as MediaTrackConstraints;
    expect('deviceId' in c).toBe(false);
  });

  it('ループバック入力を消さない設定を常に付ける', () => {
    for (const id of ['aaa111', null]) {
      const c = buildAudioConstraints(id) as MediaTrackConstraints;
      expect(c.echoCancellation).toBe(false);
      expect(c.noiseSuppression).toBe(false);
      expect(c.autoGainControl).toBe(false);
    }
  });
});

describe('describeGetUserMediaError', () => {
  it('OverconstrainedError は message が空でも日本語で説明する', () => {
    const e = Object.assign(new Error(''), { name: 'OverconstrainedError' });
    const d = describeGetUserMediaError(e);
    expect(d.retryWithoutDevice).toBe(true);
    expect(d.message).not.toBe('');
    expect(d.message).toMatch(/入力デバイス/);
  });

  it('NotFoundError も deviceId なしで再試行してよい', () => {
    const e = Object.assign(new Error(''), { name: 'NotFoundError' });
    expect(describeGetUserMediaError(e).retryWithoutDevice).toBe(true);
  });

  it('NotAllowedError は再試行せず権限の説明を出す', () => {
    const e = Object.assign(new Error(''), { name: 'NotAllowedError' });
    const d = describeGetUserMediaError(e);
    expect(d.retryWithoutDevice).toBe(false);
    expect(d.message).toMatch(/権限/);
  });

  it('NotReadableError は他アプリ使用中の可能性を示す', () => {
    const e = Object.assign(new Error(''), { name: 'NotReadableError' });
    const d = describeGetUserMediaError(e);
    expect(d.retryWithoutDevice).toBe(false);
    expect(d.message).toMatch(/使用中|取得できない/);
  });

  it('SecurityError はセキュリティ設定の説明を出す', () => {
    const e = Object.assign(new Error(''), { name: 'SecurityError' });
    const d = describeGetUserMediaError(e);
    expect(d.retryWithoutDevice).toBe(false);
    expect(d.message).toMatch(/セキュリティ/);
  });

  it('AbortError は中断の説明を出す', () => {
    const e = Object.assign(new Error(''), { name: 'AbortError' });
    const d = describeGetUserMediaError(e);
    expect(d.retryWithoutDevice).toBe(false);
    expect(d.message).toMatch(/中断/);
  });

  it('未知の例外は name と message を使った一般エラーにする', () => {
    const e = Object.assign(new Error('boom'), { name: 'WeirdError' });
    const d = describeGetUserMediaError(e);
    expect(d.retryWithoutDevice).toBe(false);
    expect(d.message).toContain('WeirdError');
    expect(d.message).toContain('boom');
  });

  it('Error でない値でも空文字にしない', () => {
    expect(describeGetUserMediaError('x').message).not.toBe('');
    expect(describeGetUserMediaError(undefined).message).not.toBe('');
  });
});
