import { describe, it, expect } from 'vitest';
import { createNoticeGate, selectValueForMics } from './deviceNotice';

/** enumerateDevices() の戻りを最小限で模したもの。 */
function dev(deviceId: string, label: string): MediaDeviceInfo {
  return { deviceId, label, kind: 'audioinput', groupId: 'g', toJSON: () => ({}) } as MediaDeviceInfo;
}
const MICS = [dev('aaa111', 'MacBook Airのマイク'), dev('bbb222', 'BlackHole 2ch')];

describe('createNoticeGate', () => {
  it('同じ通知は1度しか出さない', () => {
    const gate = createNoticeGate();
    expect(gate.shouldShow('保存された入力デバイスが見つかりません')).toBe(true);
    expect(gate.shouldShow('保存された入力デバイスが見つかりません')).toBe(false);
    expect(gate.shouldShow('保存された入力デバイスが見つかりません')).toBe(false);
  });

  it('別の通知は出す', () => {
    const gate = createNoticeGate();
    expect(gate.shouldShow('A')).toBe(true);
    expect(gate.shouldShow('B')).toBe(true);
  });

  it('null は通知しない', () => {
    expect(createNoticeGate().shouldShow(null)).toBe(false);
  });

  it('reset 後は再度通知できる（設定を変えたとき用）', () => {
    const gate = createNoticeGate();
    expect(gate.shouldShow('A')).toBe(true);
    gate.reset();
    expect(gate.shouldShow('A')).toBe(true);
  });
});

describe('selectValueForMics', () => {
  it('保存済み ID が一覧にあればそのまま選択状態にする', () => {
    expect(selectValueForMics('aaa111', MICS)).toBe('aaa111');
  });

  it('保存済み ID が一覧に無ければ空欄にせず既定入力を選択状態にする', () => {
    expect(selectValueForMics('4611f138-stale', MICS)).toBe('');
  });

  it('未設定なら既定入力', () => {
    expect(selectValueForMics('', MICS)).toBe('');
  });

  it('一覧が空でも既定入力を返す', () => {
    expect(selectValueForMics('aaa111', [])).toBe('');
  });
});
