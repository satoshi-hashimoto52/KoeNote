/**
 * 設定モーダルの下書き（0015）。
 *
 * ひとまとまりの下書きとして扱い、「保存」を押したときだけ反映する。
 * キャンセル・Escape・背景クリックでは破棄する。
 * 保存キー: `gptUrl` / `saveFolder` / `deviceId` / `deviceLabel` / `model` /
 * `delayMode` / `windowOpacity`。
 * `deviceLabel` は 0016 で追加。origin が変わって `deviceId` が無効になっても
 * 同じ物理デバイスを引き当て直すために使う。
 */

import { normalizeWindowOpacity } from './windowOpacity';

export type LiveModelValue = 'tiny' | 'base' | 'small' | 'medium';
export type LiveDelayValue = 'low_latency' | 'balanced' | 'accuracy';

export interface CaptureSettings {
  gptUrl: string;
  saveFolder: string;
  deviceId: string;
  /** 選択時のデバイス名。0016 で追加。旧設定には無いので空文字を許容する。 */
  deviceLabel: string;
  model: LiveModelValue;
  delayMode: LiveDelayValue;
  /** ウィンドウの不透明度 0.70〜1.00。0018 で追加。旧設定に無ければ 1.00。 */
  windowOpacity: number;
}

/** マイGPT として許可する URL の接頭辞。既存の検証をそのまま使う。 */
export const GPT_URL_PREFIXES = ['https://chatgpt.com/', 'https://chat.openai.com/'];

export function createDraft(current: CaptureSettings): CaptureSettings {
  return { ...current };
}

/** 下書きへ 1 項目を反映する（元の値は変更しない）。 */
export function updateDraft<K extends keyof CaptureSettings>(
  draft: CaptureSettings,
  key: K,
  value: CaptureSettings[K]
): CaptureSettings {
  return { ...draft, [key]: value };
}

/** 保存すべき変更があるか。無駄な書き込みを避けるために使う。 */
export function hasChanges(current: CaptureSettings, draft: CaptureSettings): boolean {
  return (
    normalizeGptUrl(current.gptUrl) !== normalizeGptUrl(draft.gptUrl) ||
    current.saveFolder.trim() !== draft.saveFolder.trim() ||
    current.deviceId !== draft.deviceId ||
    current.deviceLabel !== draft.deviceLabel ||
    current.model !== draft.model ||
    current.delayMode !== draft.delayMode ||
    current.windowOpacity !== draft.windowOpacity
  );
}

/** 前後の空白を落とす。保存時は必ずこれを通す。 */
export function normalizeGptUrl(url: string): string {
  return String(url ?? '').trim();
}

export interface DraftErrors {
  gptUrl?: string;
  saveFolder?: string;
}

/**
 * 保存前の検証。
 *
 * `folderExists` は呼び出し側が `pathExists` で調べた結果を渡す。
 * 未確認（undefined）のときは存在チェックを保留する。
 */
export function validateDraft(
  draft: CaptureSettings,
  folderExists?: boolean
): DraftErrors {
  const errors: DraftErrors = {};

  const url = normalizeGptUrl(draft.gptUrl);
  if (!url) {
    errors.gptUrl = 'マイGPTのURLを入力してください';
  } else if (!GPT_URL_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    errors.gptUrl = 'chatgpt.com のURLを入力してください';
  }

  const folder = String(draft.saveFolder ?? '').trim();
  if (!folder) {
    errors.saveFolder = '保存先を指定してください';
  } else if (folderExists === false) {
    errors.saveFolder = '保存先が見つかりません';
  }

  return errors;
}

export function hasErrors(errors: DraftErrors): boolean {
  return Boolean(errors.gptUrl || errors.saveFolder);
}

/**
 * 保存時に確定する値。
 *
 * 録音中は変更を受け付けず現在値を返す。検証エラーがあっても現在値を返す
 * （呼び出し側は `hasErrors` で先に弾くこと）。
 */
export function commitDraft(
  current: CaptureSettings,
  draft: CaptureSettings,
  recording: boolean,
  errors: DraftErrors = {}
): CaptureSettings {
  if (recording || hasErrors(errors)) return { ...current };
  return {
    ...draft,
    gptUrl: normalizeGptUrl(draft.gptUrl),
    saveFolder: String(draft.saveFolder ?? '').trim(),
    // 不正値が設定ファイルへ入らないよう、保存時にも必ず通す。
    windowOpacity: normalizeWindowOpacity(draft.windowOpacity)
  };
}

/** キャンセル時。常に現在値へ戻す。 */
export function discardDraft(current: CaptureSettings): CaptureSettings {
  return { ...current };
}

/**
 * フォルダ選択の結果を下書きへ反映する。
 * ダイアログをキャンセル（null）した場合は下書きを変更しない。
 */
export function applyPickedFolder(
  draft: CaptureSettings,
  picked: string | null
): CaptureSettings {
  if (!picked) return draft;
  return updateDraft(draft, 'saveFolder', picked);
}
