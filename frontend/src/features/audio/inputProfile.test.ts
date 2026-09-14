import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PROFILE,
  MAX_GAIN_DB,
  PRESETS,
  describeResolution,
  detectMode,
  normalizeProfile,
  normalizeProfileMap,
  profileForDevice,
  rememberProfile,
  resolveMode,
  resolveProfileForActualDevice,
  toBackendPayload
} from './inputProfile';

describe('detectMode', () => {
  it('明確な仮想オーディオデバイスだけを内部音声と判定する', () => {
    for (const label of [
      'BlackHole 2ch',
      'blackhole 16ch',
      'Soundflower (2ch)',
      'Loopback Audio',
      'VB-Cable',
      'VB Audio Virtual Cable',
      'Existential Audio BlackHole',
      'iShowU Audio Capture',
      'Aggregate Device',
      'Multi-Output Device'
    ]) {
      expect(detectMode(label), label).toBe('loopback');
    }
  });

  it('物理マイクはマイクと判定する', () => {
    for (const label of [
      'MacBook Pro のマイク',
      'MacBook Pro Microphone',
      '外部マイク',
      'USB Audio Device',
      'AirPods Pro',
      'Yeti Stereo Microphone'
    ]) {
      expect(detectMode(label), label).toBe('mic');
    }
  });

  it('デバイス名が無ければマイク扱いにする（補正が効くだけで音は壊れない）', () => {
    expect(detectMode('')).toBe('mic');
    expect(detectMode(null)).toBe('mic');
    expect(detectMode(undefined)).toBe('mic');
  });
});

describe('resolveMode', () => {
  it('auto は自動判定を採用し、判定結果も返す', () => {
    const resolved = resolveMode('auto', 'BlackHole 2ch');
    expect(resolved.mode).toBe('loopback');
    expect(resolved.detected).toBe('loopback');
    expect(resolved.isAuto).toBe(true);
  });

  it('手動選択は自動判定より優先されるが、判定結果は保持する', () => {
    const resolved = resolveMode('mic', 'BlackHole 2ch');
    expect(resolved.mode).toBe('mic');
    expect(resolved.detected).toBe('loopback');
    expect(resolved.isAuto).toBe(false);
  });

  it('未知の値は auto へ落とす', () => {
    expect(resolveMode('nonsense', '内蔵マイク').requested).toBe('auto');
    expect(resolveMode(null, '内蔵マイク').isAuto).toBe(true);
  });
});

describe('normalizeProfile', () => {
  it('既定は auto かつマイクプリセット', () => {
    const profile = normalizeProfile({}, '内蔵マイク');
    expect(profile.inputMode).toBe('auto');
    expect(profile.gainMode).toBe('auto');
    expect(profile.maxGainDb).toBe(MAX_GAIN_DB);
    expect(profile.silenceMode).toBe('relative');
  });

  it('内部音声では補正を行わない設定になる', () => {
    const profile = normalizeProfile({ inputMode: 'auto' }, 'BlackHole 2ch');
    expect(profile.gainMode).toBe('none');
    expect(profile.maxGainDb).toBe(0);
    expect(profile.silenceMode).toBe('absolute');
  });

  it('モードを選び直すと前のモードの値が残らない', () => {
    const profile = normalizeProfile(
      { inputMode: 'loopback', gainMode: 'manual', manualGainDb: 20 },
      '内蔵マイク'
    );
    expect(profile.gainMode).toBe('none');
    expect(profile.manualGainDb).toBe(0);
  });

  it('custom では手動値を受け入れ、範囲外は clamp する', () => {
    const profile = normalizeProfile(
      {
        inputMode: 'custom',
        gainMode: 'manual',
        manualGainDb: 999,
        maxGainDb: -10,
        silenceMode: 'manual',
        manualSilenceRms: 5
      },
      '内蔵マイク'
    );
    expect(profile.gainMode).toBe('manual');
    expect(profile.manualGainDb).toBe(MAX_GAIN_DB);
    expect(profile.maxGainDb).toBe(0);
    expect(profile.manualSilenceRms).toBe(0.05);
  });

  it('不正な列挙値は安全な既定へ落とす', () => {
    const profile = normalizeProfile(
      { inputMode: 'custom', gainMode: 'boom', silenceMode: 'boom' },
      '内蔵マイク'
    );
    expect(profile.gainMode).toBe('auto');
    expect(profile.silenceMode).toBe('relative');
  });

  it('警告の継続時間は 5〜120 秒に収める', () => {
    expect(normalizeProfile({ lowInputWarningSeconds: 1 }, 'mic').lowInputWarningSeconds).toBe(5);
    expect(normalizeProfile({ lowInputWarningSeconds: 9999 }, 'mic').lowInputWarningSeconds).toBe(120);
    expect(normalizeProfile({ lowInputWarningSeconds: 'x' }, 'mic').lowInputWarningSeconds).toBe(20);
  });

  it('警告の ON/OFF はモードを問わずユーザー設定を尊重する', () => {
    const profile = normalizeProfile(
      { inputMode: 'auto', lowInputWarning: false },
      'BlackHole 2ch'
    );
    expect(profile.lowInputWarning).toBe(false);
    expect(profile.gainMode).toBe('none');
  });

  it('null / undefined でも壊れない', () => {
    expect(normalizeProfile(null, null)).toEqual(DEFAULT_PROFILE);
    expect(normalizeProfile(undefined, undefined)).toEqual(DEFAULT_PROFILE);
  });
});

describe('profileForDevice', () => {
  it('保存済み設定があればそれを使う', () => {
    const profiles = { '内蔵マイク': { inputMode: 'custom' as const, gainMode: 'manual' as const, manualGainDb: 6 } };
    const profile = profileForDevice(profiles, '内蔵マイク');
    expect(profile.gainMode).toBe('manual');
    expect(profile.manualGainDb).toBe(6);
  });

  it('フォールバック先へ旧デバイスの設定を持ち込まない', () => {
    // BlackHole 用の「補正なし」を保存している状態で、本体マイクへ落ちた場合。
    const profiles = { 'BlackHole 2ch': { inputMode: 'loopback' as const } };
    const profile = profileForDevice(profiles, 'MacBook Pro のマイク');
    expect(profile.gainMode).toBe('auto');
    expect(profile.maxGainDb).toBe(MAX_GAIN_DB);
  });

  it('未知のデバイスはそのデバイス名から判定し直す', () => {
    const profile = profileForDevice({}, 'BlackHole 2ch');
    expect(profile.gainMode).toBe('none');
  });

  it('デバイス名が無ければ既定プリセット', () => {
    const profile = profileForDevice({}, '');
    expect(profile).toEqual(DEFAULT_PROFILE);
  });
});

describe('rememberProfile', () => {
  it('デバイスごとに保存し、他のデバイスへは触れない', () => {
    let profiles = rememberProfile({}, '内蔵マイク', {
      ...DEFAULT_PROFILE,
      inputMode: 'custom',
      manualGainDb: 9
    });
    profiles = rememberProfile(profiles, 'BlackHole 2ch', {
      ...DEFAULT_PROFILE,
      inputMode: 'loopback'
    });
    expect(Object.keys(profiles)).toEqual(['内蔵マイク', 'BlackHole 2ch']);
    expect(profiles['内蔵マイク'].manualGainDb).toBe(9);
  });

  it('デバイス名が空なら保存しない', () => {
    expect(rememberProfile({}, '', DEFAULT_PROFILE)).toEqual({});
  });
});

describe('normalizeProfileMap', () => {
  it('壊れた値を捨てる', () => {
    expect(normalizeProfileMap(null)).toEqual({});
    expect(normalizeProfileMap([1, 2])).toEqual({});
    expect(normalizeProfileMap({ mic: 'oops', ok: { inputMode: 'mic' } })).toEqual({
      ok: { inputMode: 'mic' }
    });
  });
});

describe('toBackendPayload', () => {
  it('Backend の input_profile 形式へ写す', () => {
    const payload = toBackendPayload(DEFAULT_PROFILE, '内蔵マイク');
    expect(payload).toEqual({
      mode: 'auto',
      device_label: '内蔵マイク',
      gain_mode: 'auto',
      manual_gain_db: 0,
      max_gain_db: MAX_GAIN_DB,
      silence_mode: 'relative',
      manual_silence_rms: 0.0002,
      low_input_warning: true,
      low_input_warning_seconds: 20
    });
  });
});

describe('describeResolution', () => {
  it('自動判定であることを明示する', () => {
    const text = describeResolution(resolveMode('auto', 'BlackHole 2ch'), 'BlackHole 2ch');
    expect(text).toContain('内部音声');
    expect(text).toContain('自動判定');
  });

  it('手動選択であることを明示する', () => {
    const text = describeResolution(resolveMode('mic', 'BlackHole 2ch'), 'BlackHole 2ch');
    expect(text).toContain('マイク');
    expect(text).toContain('手動');
  });

  it('デバイス名が無ければ既定入力と書く', () => {
    expect(describeResolution(resolveMode('auto', ''), '')).toContain('既定の入力デバイス');
  });
});

describe('プリセットの中身', () => {
  it('マイクは自動補正あり・相対無音判定・警告ON', () => {
    expect(PRESETS.mic.gainMode).toBe('auto');
    expect(PRESETS.mic.silenceMode).toBe('relative');
    expect(PRESETS.mic.lowInputWarning).toBe(true);
    expect(PRESETS.mic.maxGainDb).toBe(MAX_GAIN_DB);
  });

  it('内部音声は 0dB・絶対無音判定・警告ON', () => {
    expect(PRESETS.loopback.gainMode).toBe('none');
    expect(PRESETS.loopback.maxGainDb).toBe(0);
    expect(PRESETS.loopback.silenceMode).toBe('absolute');
    expect(PRESETS.loopback.lowInputWarning).toBe(true);
  });
});

describe('resolveProfileForActualDevice（録音開始直前の再解決）', () => {
  const loopbackProfile = normalizeProfile({ inputMode: 'auto' }, 'BlackHole 2ch');
  const profiles = {
    'BlackHole 2ch': { inputMode: 'auto' as const },
    'マイク A': { inputMode: 'custom' as const, gainMode: 'manual' as const, manualGainDb: 9 }
  };

  it('実デバイスが保存済みと同じなら設定を変えない', () => {
    const r = resolveProfileForActualDevice(
      'BlackHole 2ch', 'BlackHole 2ch', loopbackProfile, profiles
    );
    expect(r.relabeled).toBe(false);
    expect(r.settings).toBe(loopbackProfile);
    expect(r.deviceLabel).toBe('BlackHole 2ch');
  });

  it('BlackHole の「補正なし」を本体マイクへ持ち込まない（0019 再発防止）', () => {
    // BlackHole を外した状態で既定入力（本体マイク）へフォールバックしたケース。
    const r = resolveProfileForActualDevice(
      'BlackHole 2ch', 'MacBook Pro のマイク', loopbackProfile, profiles
    );
    expect(r.relabeled).toBe(true);
    expect(r.deviceLabel).toBe('MacBook Pro のマイク');
    // loopback の「補正なし」ではなく、マイク用の自動補正が効くこと
    expect(r.settings.gainMode).toBe('auto');
    expect(r.settings.maxGainDb).toBe(MAX_GAIN_DB);
    expect(r.settings.silenceMode).toBe('relative');
  });

  it('逆方向：マイク用の自動補正を BlackHole へ持ち込まない', () => {
    const micProfile = normalizeProfile({ inputMode: 'auto' }, 'MacBook Pro のマイク');
    const r = resolveProfileForActualDevice(
      'MacBook Pro のマイク', 'BlackHole 2ch', micProfile, profiles
    );
    expect(r.relabeled).toBe(true);
    expect(r.settings.gainMode).toBe('none');
    expect(r.settings.maxGainDb).toBe(0);
  });

  it('フォールバック先に保存済み設定があればそれを使う', () => {
    const r = resolveProfileForActualDevice(
      'BlackHole 2ch', 'マイク A', loopbackProfile, profiles
    );
    expect(r.settings.gainMode).toBe('manual');
    expect(r.settings.manualGainDb).toBe(9);
  });

  it('実デバイス名が取れないときは保存済み設定を維持する（判断材料が無い）', () => {
    const r = resolveProfileForActualDevice('マイク A', '', loopbackProfile, profiles);
    expect(r.relabeled).toBe(false);
    expect(r.settings).toBe(loopbackProfile);
  });

  it('保存済みラベルが空でも実デバイス名で解決できる', () => {
    const r = resolveProfileForActualDevice('', 'BlackHole 2ch', DEFAULT_PROFILE, {});
    expect(r.relabeled).toBe(true);
    expect(r.settings.gainMode).toBe('none');
  });

  it('profiles が空でもデバイス名から判定し直す', () => {
    const r = resolveProfileForActualDevice(
      'BlackHole 2ch', 'USB Audio Device', loopbackProfile, {}
    );
    expect(r.settings.gainMode).toBe('auto');
  });
});
