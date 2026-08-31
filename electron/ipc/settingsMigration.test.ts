import { describe, it, expect } from 'vitest';
import { MIGRATED_KEYS, planSettingsMigration } from './settingsMigration';

const legacy = {
  gptUrl: 'https://chatgpt.com/g/g-example',
  saveFolder: '/Users/you/Documents/BridgeLog',
  deviceId: 'device-abc',
  deviceLabel: 'マイク A',
  model: 'small',
  delayMode: 'balanced',
  requestTemplate: '要約してください',
  transcriptHeight: 480,
  windowOpacity: 0.85
};

describe('planSettingsMigration', () => {
  it('KoeNote 側が空なら旧設定を移行する', () => {
    expect(planSettingsMigration({}, legacy)).toEqual(legacy);
  });

  it('windowOpacity が無い旧設定でも移行できる（0018 後方互換）', () => {
    const { windowOpacity: _omit, ...legacyWithoutOpacity } = legacy;
    const migrated = planSettingsMigration({}, legacyWithoutOpacity)!;
    expect(migrated).not.toHaveProperty('windowOpacity');
    expect(migrated.model).toBe('small');
  });

  it('windowOpacity があれば引き継ぐ（0018）', () => {
    expect(planSettingsMigration({}, legacy)!.windowOpacity).toBe(0.85);
  });

  it('deviceLabel が無い旧 BridgeLog 設定でも移行できる（0016 後方互換）', () => {
    const { deviceLabel: _omit, ...legacyWithoutLabel } = legacy;
    const migrated = planSettingsMigration({}, legacyWithoutLabel)!;
    expect(migrated).not.toHaveProperty('deviceLabel');
    expect(migrated.deviceId).toBe('device-abc');
  });

  it('移行対象のキーをすべて引き継ぐ', () => {
    const migrated = planSettingsMigration({}, legacy)!;
    expect(Object.keys(migrated).sort()).toEqual([...MIGRATED_KEYS].sort());
  });

  it('KoeNote 側に既に設定があれば上書きしない', () => {
    expect(planSettingsMigration({ model: 'tiny' }, legacy)).toBeNull();
  });

  it('移行対象外のキーは引き継がない', () => {
    const migrated = planSettingsMigration({}, { ...legacy, windowBounds: { x: 1 } })!;
    expect(migrated).not.toHaveProperty('windowBounds');
  });

  it('旧設定に一部しか無くても、あるものだけ移行する', () => {
    expect(planSettingsMigration({}, { model: 'small' })).toEqual({ model: 'small' });
  });

  it('null / undefined の値は移行しない', () => {
    expect(planSettingsMigration({}, { model: 'small', gptUrl: null })).toEqual({ model: 'small' });
  });

  it('旧設定が無い・壊れている場合は移行しない', () => {
    for (const broken of [null, undefined, 'not json', 42, [], {}]) {
      expect(planSettingsMigration({}, broken)).toBeNull();
    }
  });

  it('移行できる値が1つも無ければ書き込ませない', () => {
    expect(planSettingsMigration({}, { windowBounds: { x: 1 } })).toBeNull();
  });

  it('現行設定が壊れている場合は移行しない（既存を壊さない）', () => {
    for (const broken of [null, undefined, 'x', 7, []]) {
      expect(planSettingsMigration(broken, legacy)).toBeNull();
    }
  });
});
