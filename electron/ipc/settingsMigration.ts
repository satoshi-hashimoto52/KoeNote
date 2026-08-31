/**
 * BridgeLog から KoeNote への設定移行。
 *
 * 旧版 `~/Library/Application Support/BridgeLog/bridgelog-settings.json` を、
 * KoeNote 側にまだ設定が無い初回起動時だけ引き継ぐ。旧設定は読むだけで書き換えない。
 */

/** 移行する設定キー。ここに無いキーは引き継がない。 */
export const MIGRATED_KEYS = [
  'gptUrl',
  'saveFolder',
  'deviceId',
  // 0016 で追加。旧 BridgeLog 設定には無いが、あれば引き継ぐ。
  'deviceLabel',
  'model',
  'delayMode',
  'requestTemplate',
  'transcriptHeight',
  // 0018 で追加。旧 BridgeLog 設定にあれば引き継ぐ（無ければ 1.00 扱い）。
  'windowOpacity'
] as const;

export type MigratedKey = (typeof MIGRATED_KEYS)[number];

/**
 * 旧設定から移行対象キーだけを取り出す。
 *
 * `current` が空のときだけ移行する。KoeNote 側に既に設定があれば
 * （値が 1 つでもあれば）旧設定で上書きしてはいけないので null を返す。
 * 移行できるものが何も無い場合も null を返し、書き込みそのものを行わせない。
 */
export function planSettingsMigration(
  current: unknown,
  legacy: unknown
): Record<string, unknown> | null {
  if (!isPlainObject(current) || Object.keys(current).length > 0) return null;
  if (!isPlainObject(legacy)) return null;

  const migrated: Record<string, unknown> = {};
  for (const key of MIGRATED_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(legacy, key)) continue;
    const value = legacy[key];
    if (value === undefined || value === null) continue;
    migrated[key] = value;
  }
  return Object.keys(migrated).length > 0 ? migrated : null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
