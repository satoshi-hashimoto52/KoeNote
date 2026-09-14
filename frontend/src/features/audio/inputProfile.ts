/**
 * 入力モードと入力方式別プリセット（0019）。
 *
 * Backend `backend/services/input_profile.py` と同じ表を持つ。
 * 片方だけ変えると UI 表示と実処理がずれるため、必ず両方を更新すること。
 * 実際の音声処理は Backend が行い、ここは「何を送るか」と「何を見せるか」を決める。
 */

export type InputMode = 'auto' | 'mic' | 'loopback' | 'custom';
/** `auto` を解決したあとの実効モード。 */
export type ResolvedMode = 'mic' | 'loopback' | 'custom';
export type GainMode = 'auto' | 'none' | 'manual';
export type SilenceMode = 'relative' | 'absolute' | 'manual';

export const INPUT_MODES: readonly InputMode[] = ['auto', 'mic', 'loopback', 'custom'];
export const GAIN_MODES: readonly GainMode[] = ['auto', 'none', 'manual'];
export const SILENCE_MODES: readonly SilenceMode[] = ['relative', 'absolute', 'manual'];

/** 設計値。Backend の services/audio_levels.py と一致させる。 */
export const MAX_GAIN_DB = 24;
export const TARGET_SPEECH_DBFS = -24;
export const ABSOLUTE_SILENCE_RMS = 0.0002;
export const MIN_WARNING_SECONDS = 5;
export const MAX_WARNING_SECONDS = 120;
export const DEFAULT_WARNING_SECONDS = 20;

export const INPUT_MODE_LABELS: Record<InputMode, string> = {
  auto: '自動',
  mic: 'マイク',
  loopback: '内部音声',
  custom: 'カスタム'
};

export const INPUT_MODE_HINTS: Record<InputMode, string> = {
  auto: 'デバイス名から自動で判定します',
  mic: '遠い話者に合わせて音量を自動補正します',
  loopback: 'BlackHole 等。音声を一切加工しません',
  custom: '補正量や無音判定を手動で指定します'
};

/**
 * 仮想オーディオデバイス（ループバック）の既知の名前。
 * Backend の LOOPBACK_PATTERNS と同じ。
 */
const LOOPBACK_PATTERNS = [
  /blackhole/i,
  /soundflower/i,
  /loopback\s*audio/i,
  /vb[-\s]?cable/i,
  /vb[-\s]?audio/i,
  /existential\s*audio/i,
  /ishowu/i,
  /aggregate\s*device/i,
  /multi[-\s]?output/i,
  /virtual\s*(audio|cable|input)/i
];

/**
 * デバイス名から入力モードを自動判定する。
 *
 * 明確に判定できる仮想オーディオデバイスだけ `loopback`。
 * それ以外はすべて `mic`（判定に自信が無いときにマイク扱いにしておけば、
 * 補正が効くだけで音は壊れない）。
 */
export function detectMode(deviceLabel: string | null | undefined): 'mic' | 'loopback' {
  const label = String(deviceLabel ?? '').trim();
  if (!label) return 'mic';
  return LOOPBACK_PATTERNS.some((pattern) => pattern.test(label)) ? 'loopback' : 'mic';
}

export interface ResolvedInputMode {
  /** 実際に使うモード。 */
  mode: ResolvedMode;
  /** 自動判定の結果。UI へ必ず表示する。 */
  detected: 'mic' | 'loopback';
  /** 設定に保存されている選択。 */
  requested: InputMode;
  /** 自動判定をそのまま使っているか。 */
  isAuto: boolean;
}

/** 保存済みの選択とデバイス名から実効モードを決める。 */
export function resolveMode(
  requested: InputMode | string | null | undefined,
  deviceLabel: string | null | undefined
): ResolvedInputMode {
  const detected = detectMode(deviceLabel);
  const raw = String(requested ?? 'auto') as InputMode;
  const normalized: InputMode = (INPUT_MODES as readonly string[]).includes(raw) ? raw : 'auto';
  if (normalized === 'auto') {
    return { mode: detected, detected, requested: 'auto', isAuto: true };
  }
  return { mode: normalized as ResolvedMode, detected, requested: normalized, isAuto: false };
}

/** 1 デバイス分の入力設定。 */
export interface InputProfileSettings {
  inputMode: InputMode;
  gainMode: GainMode;
  manualGainDb: number;
  maxGainDb: number;
  silenceMode: SilenceMode;
  manualSilenceRms: number;
  lowInputWarning: boolean;
  lowInputWarningSeconds: number;
}

/** 入力方式別プリセット。値の根拠は docs/issues/0019。 */
export const PRESETS: Record<'mic' | 'loopback', Omit<InputProfileSettings, 'inputMode'>> = {
  mic: {
    gainMode: 'auto',
    manualGainDb: 0,
    maxGainDb: MAX_GAIN_DB,
    silenceMode: 'relative',
    manualSilenceRms: ABSOLUTE_SILENCE_RMS,
    lowInputWarning: true,
    lowInputWarningSeconds: DEFAULT_WARNING_SECONDS
  },
  loopback: {
    // 既にアプリ側で正規化された音が来るので触らない。
    // 無音が厳密なデジタル 0 になりノイズフロアが 0 へ張り付くため、絶対判定のみ。
    gainMode: 'none',
    manualGainDb: 0,
    maxGainDb: 0,
    silenceMode: 'absolute',
    manualSilenceRms: ABSOLUTE_SILENCE_RMS,
    lowInputWarning: true,
    lowInputWarningSeconds: DEFAULT_WARNING_SECONDS
  }
};

export const DEFAULT_PROFILE: InputProfileSettings = {
  inputMode: 'auto',
  ...PRESETS.mic
};

function clampNumber(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  const raw = String(value ?? '');
  return (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
}

/**
 * 保存値を安全な設定へ正規化する。
 *
 * - 未設定は既定値で埋める
 * - 不正値・範囲外は安全な値へ落とす
 * - `custom` 以外はプリセットを優先する
 *   （モードを選び直したときに前のモードの値が残らないようにする）
 * - 警告の ON/OFF と待ち時間だけはモードを問わずユーザー設定を尊重する
 */
export function normalizeProfile(
  raw: Partial<InputProfileSettings> | Record<string, unknown> | null | undefined,
  deviceLabel: string | null | undefined
): InputProfileSettings {
  const source = (raw ?? {}) as Record<string, unknown>;
  const inputMode = pick<InputMode>(source.inputMode, INPUT_MODES, 'auto');
  const { mode } = resolveMode(inputMode, deviceLabel);

  const warningSeconds = clampNumber(
    source.lowInputWarningSeconds,
    DEFAULT_WARNING_SECONDS,
    MIN_WARNING_SECONDS,
    MAX_WARNING_SECONDS
  );
  const lowInputWarning =
    typeof source.lowInputWarning === 'boolean' ? source.lowInputWarning : true;

  if (mode === 'custom') {
    return {
      inputMode,
      gainMode: pick<GainMode>(source.gainMode, GAIN_MODES, 'auto'),
      manualGainDb: clampNumber(source.manualGainDb, 0, 0, MAX_GAIN_DB),
      maxGainDb: clampNumber(source.maxGainDb, MAX_GAIN_DB, 0, MAX_GAIN_DB),
      silenceMode: pick<SilenceMode>(source.silenceMode, SILENCE_MODES, 'relative'),
      manualSilenceRms: clampNumber(source.manualSilenceRms, ABSOLUTE_SILENCE_RMS, 0, 0.05),
      lowInputWarning,
      lowInputWarningSeconds: warningSeconds
    };
  }

  const preset = PRESETS[mode];
  return {
    inputMode,
    ...preset,
    lowInputWarning,
    lowInputWarningSeconds: warningSeconds
  };
}

/** デバイスラベル -> 設定 の保存。ラベルが安定識別子（deviceId は origin で変わる）。 */
export type InputProfileMap = Record<string, Partial<InputProfileSettings>>;

/** 保存された map を安全な形へ整える。壊れた値は捨てるが、未知のキーは残さない。 */
export function normalizeProfileMap(raw: unknown): InputProfileMap {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const out: InputProfileMap = {};
  for (const [label, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!label || typeof value !== 'object' || value === null || Array.isArray(value)) continue;
    out[label] = value as Partial<InputProfileSettings>;
  }
  return out;
}

/**
 * デバイスに対応する設定を解決する。
 *
 * **フォールバック時に旧デバイスの設定を引き継がない。**
 * 保存が無ければ、そのデバイス名で判定したプリセットを使う。
 * BlackHole 用の「補正なし」を本体マイクへ持ち込むと 0019 が再発する。
 */
export function profileForDevice(
  profiles: InputProfileMap,
  deviceLabel: string | null | undefined,
  fallback?: Partial<InputProfileSettings>
): InputProfileSettings {
  const label = String(deviceLabel ?? '').trim();
  const saved = label ? profiles[label] : undefined;
  if (saved) return normalizeProfile(saved, label);
  // 保存が無いデバイスでは、渡された既定ではなく必ずデバイス判定から作り直す。
  const seed = fallback && !label ? fallback : { inputMode: 'auto' as InputMode };
  return normalizeProfile(seed, label);
}

/** デバイス別設定を書き戻す。既存の他デバイスの設定は触らない。 */
export function rememberProfile(
  profiles: InputProfileMap,
  deviceLabel: string | null | undefined,
  settings: InputProfileSettings
): InputProfileMap {
  const label = String(deviceLabel ?? '').trim();
  if (!label) return profiles;
  return { ...profiles, [label]: { ...settings } };
}

export interface ActualDeviceResolution {
  settings: InputProfileSettings;
  /** 実デバイスが保存済みラベルと異なり、プリセットを解決し直したか。 */
  relabeled: boolean;
  /** 実際に使うデバイス名。Backend の自動判定はこの名前で行わせる。 */
  deviceLabel: string;
}

/**
 * **録音開始直前に、実際に開けたデバイスでプリセットを解決し直す（0019）。**
 *
 * 設定は保存済みデバイス名で読み込むが、`getUserMedia` が実際に開くデバイスは
 * フォールバックで別物になりうる（USB の抜き差し、origin 変更、既定入力への後退）。
 * このとき保存済み設定をそのまま使うと、たとえば BlackHole 用の
 * 「補正なし・絶対無音判定」が本体マイクへ適用され、0019 の欠落条件が再現する。
 *
 * 実デバイス名が保存済みと異なるときは、保存済み設定を捨て、
 * **実デバイス名で `inputProfiles` を引き直す**（無ければデバイス名から判定し直す）。
 */
export function resolveProfileForActualDevice(
  savedLabel: string | null | undefined,
  actualLabel: string | null | undefined,
  savedProfile: InputProfileSettings,
  profiles: InputProfileMap
): ActualDeviceResolution {
  const saved = String(savedLabel ?? '').trim();
  const actual = String(actualLabel ?? '').trim();

  // 実デバイス名が取れない（権限やブラウザの都合でラベルが空）ときは、
  // 判断材料が無いので保存済み設定をそのまま使う。捨てる側へ倒さない。
  if (!actual || actual === saved) {
    return { settings: savedProfile, relabeled: false, deviceLabel: actual || saved };
  }
  return {
    settings: profileForDevice(profiles, actual),
    relabeled: true,
    deviceLabel: actual
  };
}

/** Backend の `input_profile` payload。WebSocket の config へそのまま載せる。 */
export function toBackendPayload(settings: InputProfileSettings, deviceLabel: string): {
  mode: InputMode;
  device_label: string;
  gain_mode: GainMode;
  manual_gain_db: number;
  max_gain_db: number;
  silence_mode: SilenceMode;
  manual_silence_rms: number;
  low_input_warning: boolean;
  low_input_warning_seconds: number;
} {
  return {
    mode: settings.inputMode,
    device_label: deviceLabel,
    gain_mode: settings.gainMode,
    manual_gain_db: settings.manualGainDb,
    max_gain_db: settings.maxGainDb,
    silence_mode: settings.silenceMode,
    manual_silence_rms: settings.manualSilenceRms,
    low_input_warning: settings.lowInputWarning,
    low_input_warning_seconds: settings.lowInputWarningSeconds
  };
}

/** 自動判定の結果を UI に出す短い説明。 */
export function describeResolution(resolution: ResolvedInputMode, deviceLabel: string): string {
  const device = deviceLabel.trim() || '既定の入力デバイス';
  if (!resolution.isAuto) {
    return `${device} → ${INPUT_MODE_LABELS[resolution.requested]}（手動で選択中）`;
  }
  return `${device} → ${INPUT_MODE_LABELS[resolution.detected]}（自動判定）`;
}
