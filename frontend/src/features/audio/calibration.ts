/**
 * 入力テスト（キャリブレーション）の進行と結果表示（0019）。
 *
 * 判定そのものは Backend（`/api/audio/analyze`）が録音時と同じコードで行う。
 * ここはステップ進行と、結果を人が読める形にする責務だけを持つ。
 *
 * 重要:
 *   - 通常の録音セッションを開始しない。ユーザーの保存先にファイルを残さない
 *   - 数値は必ず **dBFS**（フルスケール基準）と表記する。dB SPL ではない
 */

import type { InputProfileSettings } from './inputProfile';

export type CalibrationStep = 'idle' | 'noise' | 'speech' | 'analyzing' | 'result' | 'error';

/** ステップの想定時間。Backend の calibration.py と揃える。 */
export const NOISE_STEP_SECONDS = 5;
export const SPEECH_STEP_SECONDS = 18;

export const TEST_SENTENCE = [
  'これはKoeNoteのマイク入力テストです。',
  '会議中と同じ声量で、普段どおりに話しています。',
  '離れた位置からでも、音声が正しく認識されるか確認します。'
].join('\n');

export const STEP_TITLES: Record<CalibrationStep, string> = {
  idle: '入力テスト',
  noise: 'ステップ1 / 2　環境音の測定',
  speech: 'ステップ2 / 2　遠方発話の測定',
  analyzing: '解析中',
  result: 'テスト結果',
  error: '入力テストを実行できません'
};

export const STEP_INSTRUCTIONS: Record<CalibrationStep, string> = {
  idle: 'マイクの入力が文字起こしに十分かを測定します。所要 30 秒ほどです。',
  noise: '話さずにお待ちください。部屋の環境音を測定します。',
  speech:
    '実際の会議で最も遠い席へ移動し、普段どおりの声量で下の文を読んでください。',
  analyzing: '測定した音声を解析しています。',
  result: '',
  error: ''
};

/** 総合判定コード。Backend の classify_verdict と一致させる。 */
export type Verdict =
  | 'good'
  | 'usable'
  | 'needs_adjust'
  | 'too_quiet'
  | 'noisy'
  | 'clipping'
  | 'no_input';

export const VERDICT_LABELS: Record<Verdict, string> = {
  good: '良好',
  usable: '使用可能',
  needs_adjust: '要調整',
  too_quiet: '入力が小さすぎる',
  noisy: '環境音が大きすぎる',
  clipping: '音割れの可能性',
  no_input: '入力を検出できない'
};

export const VERDICT_TONES: Record<Verdict, 'ok' | 'warn' | 'error'> = {
  good: 'ok',
  usable: 'ok',
  needs_adjust: 'warn',
  too_quiet: 'error',
  noisy: 'warn',
  clipping: 'warn',
  no_input: 'error'
};

export const VERDICT_ADVICE: Record<Verdict, string> = {
  good: 'このまま録音できます。',
  usable: '自動補正により文字起こしできます。マイクを話者へ近づけると、さらに安定します。',
  needs_adjust: 'マイクの位置を話者へ近づけるか、推奨設定を適用してください。',
  too_quiet:
    '自動補正の上限でも文字起こしに必要なレベルへ届きません。マイクを話者へ近づけるか、外部マイクの使用を検討してください。',
  noisy:
    '話し声に対して環境音が大きすぎます。騒音源を減らすか、マイクを話者へ近づけてください。',
  clipping: '入力が大きすぎます。マイクを話者から離すか、入力音量を下げてください。',
  no_input:
    '入力を検出できません。マイクの接続、システム設定のマイク権限、入力デバイスの選択を確認してください。'
};

/** Backend `/api/audio/analyze` の応答。 */
export interface CalibrationResult {
  device_label: string;
  input_mode: string;
  detected_mode: string;
  unit: string;
  noise_floor_dbfs: number;
  speech_median_dbfs: number;
  speech_low_dbfs: number;
  speech_peak_dbfs: number;
  snr_db: number;
  speech_ratio: number;
  current_gain_db: number;
  current_pass_ratio: number;
  current_skip_ratio: number;
  recommended_gain_db: number;
  recommended_pass_ratio: number;
  recommended_skip_ratio: number;
  required_gain_db: number;
  max_gain_db: number;
  clipping_risk: 'low' | 'medium' | 'high';
  clip_ratio: number;
  verdict: Verdict;
  recommended_settings: Partial<InputProfileSettings> & Record<string, unknown>;
}

/** dBFS 表示。**必ず dBFS と書く**（物理音圧 dB SPL と混同させない）。 */
export function formatDbfs(value: number | null | undefined): string {
  const v = Number(value);
  if (!Number.isFinite(v) || v <= -119) return '−∞ dBFS';
  return `${v.toFixed(1)} dBFS`;
}

/** SNR は 2 点の差なので dB（相対量）で表記する。 */
export function formatDb(value: number | null | undefined): string {
  const v = Number(value);
  if (!Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)} dB`;
}

export function formatPercent(ratio: number | null | undefined): string {
  // Number(null) は 0 になるため、null/undefined は先に弾く。
  if (ratio === null || ratio === undefined) return '—';
  const v = Number(ratio);
  if (!Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(0)}%`;
}

export const CLIPPING_RISK_LABELS: Record<'low' | 'medium' | 'high', string> = {
  low: '低い',
  medium: '中',
  high: '高い'
};

export interface ResultRow {
  label: string;
  value: string;
  /** 詳細設定側にだけ出す行（一般利用者には数値を見せない）。 */
  advanced?: boolean;
}

/**
 * 結果画面に並べる行。
 *
 * 最大値・最小値だけでは判定していないことが読み取れるよう、
 * 中央値・パーセンタイル・比率を明示する。
 */
export function buildResultRows(result: CalibrationResult): ResultRow[] {
  return [
    { label: '入力デバイス', value: result.device_label || '既定の入力デバイス' },
    { label: '判定された入力モード', value: describeMode(result) },
    { label: 'ノイズフロア', value: formatDbfs(result.noise_floor_dbfs), advanced: true },
    { label: '発話レベル（中央値）', value: formatDbfs(result.speech_median_dbfs) },
    { label: '小さい発話（下位20%）', value: formatDbfs(result.speech_low_dbfs) },
    { label: '発話ピーク', value: formatDbfs(result.speech_peak_dbfs), advanced: true },
    { label: 'SNR', value: formatDb(result.snr_db) },
    { label: '現在設定での推定通過率', value: formatPercent(result.current_pass_ratio) },
    { label: '推定スキップ率', value: formatPercent(result.current_skip_ratio) },
    { label: '必要な推奨ゲイン', value: formatDb(result.recommended_gain_db) },
    {
      label: 'クリッピングリスク',
      value: CLIPPING_RISK_LABELS[result.clipping_risk] ?? '—'
    }
  ];
}

function describeMode(result: CalibrationResult): string {
  const labels: Record<string, string> = { mic: 'マイク', loopback: '内部音声', custom: 'カスタム' };
  const mode = labels[result.input_mode] ?? result.input_mode;
  if (result.input_mode === result.detected_mode) return `${mode}（自動判定）`;
  const detected = labels[result.detected_mode] ?? result.detected_mode;
  return `${mode}（手動選択・自動判定は ${detected}）`;
}

/** 推奨設定を適用したときの改善見込み。再測定前でも見せる。 */
export function describeImprovement(result: CalibrationResult): string {
  const before = formatPercent(result.current_pass_ratio);
  const after = formatPercent(result.recommended_pass_ratio);
  if (result.recommended_pass_ratio <= result.current_pass_ratio + 1e-9) {
    return `現在の設定で既に推定通過率 ${before} です。`;
  }
  return `推定通過率 ${before} → ${after} に改善する見込みです。`;
}

/** 再測定の結果が改善したかを判定する。 */
export function compareRuns(
  before: CalibrationResult,
  after: CalibrationResult
): { improved: boolean; summary: string } {
  const passUp = after.current_pass_ratio > before.current_pass_ratio + 1e-9;
  const clipSafe = riskRank(after.clipping_risk) <= riskRank(before.clipping_risk);
  const improved = passUp && clipSafe;
  const parts = [
    `推定通過率 ${formatPercent(before.current_pass_ratio)} → ${formatPercent(after.current_pass_ratio)}`,
    `クリッピングリスク ${CLIPPING_RISK_LABELS[before.clipping_risk]} → ${CLIPPING_RISK_LABELS[after.clipping_risk]}`
  ];
  return { improved, summary: parts.join(' / ') };
}

function riskRank(risk: 'low' | 'medium' | 'high'): number {
  return { low: 0, medium: 1, high: 2 }[risk] ?? 0;
}

/** 推奨設定を現在の設定へ反映する。未知のキーは無視する。 */
export function applyRecommended(
  current: InputProfileSettings,
  recommended: Record<string, unknown>
): InputProfileSettings {
  const next: InputProfileSettings = { ...current };
  if (typeof recommended.inputMode === 'string') {
    next.inputMode = recommended.inputMode as InputProfileSettings['inputMode'];
  }
  if (typeof recommended.gainMode === 'string') {
    next.gainMode = recommended.gainMode as InputProfileSettings['gainMode'];
  }
  if (Number.isFinite(Number(recommended.maxGainDb))) {
    next.maxGainDb = Number(recommended.maxGainDb);
  }
  if (Number.isFinite(Number(recommended.manualGainDb))) {
    next.manualGainDb = Number(recommended.manualGainDb);
  }
  if (typeof recommended.lowInputWarning === 'boolean') {
    next.lowInputWarning = recommended.lowInputWarning;
  }
  return next;
}

/** ステップの残り秒数から進捗 0..1 を作る。 */
export function stepProgress(step: CalibrationStep, elapsedMs: number): number {
  const total =
    step === 'noise' ? NOISE_STEP_SECONDS : step === 'speech' ? SPEECH_STEP_SECONDS : 0;
  if (total <= 0) return 0;
  return Math.min(1, Math.max(0, elapsedMs / (total * 1000)));
}

/** カウントダウン表示（秒）。 */
export function remainingSeconds(step: CalibrationStep, elapsedMs: number): number {
  const total =
    step === 'noise' ? NOISE_STEP_SECONDS : step === 'speech' ? SPEECH_STEP_SECONDS : 0;
  if (total <= 0) return 0;
  return Math.max(0, Math.ceil(total - elapsedMs / 1000));
}

/** PCM16 の ArrayBuffer 群を base64 へ。Backend へはこの形で送る。 */
export function encodePcmChunks(chunks: ArrayBuffer[]): string {
  let total = 0;
  for (const chunk of chunks) total += chunk.byteLength;
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(new Uint8Array(chunk), offset);
    offset += chunk.byteLength;
  }
  // 大きい配列を一度に apply するとスタックが溢れるので分割する。
  let binary = '';
  const STEP = 0x8000;
  for (let i = 0; i < merged.length; i += STEP) {
    binary += String.fromCharCode(...merged.subarray(i, i + STEP));
  }
  return btoa(binary);
}
