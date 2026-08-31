/**
 * ウィンドウ不透明度の設定値（0018）。
 *
 * Renderer（設定 UI）と Electron main（BrowserWindow.setOpacity の適用）の
 * 両方から使う。値の正当性はここ 1 箇所で決め、main 側でも必ず通してから
 * 反映する（Renderer から任意の値を渡されても安全にするため）。
 *
 * CSS の opacity で全体を薄くする方法は使わない（文字が読めなくなるため）。
 */

export const WINDOW_OPACITY_KEY = 'windowOpacity';
export const MIN_WINDOW_OPACITY = 0.7;
export const MAX_WINDOW_OPACITY = 1;
export const DEFAULT_WINDOW_OPACITY = 1;
export const WINDOW_OPACITY_STEP = 0.05;

/** 0.05 刻みの丸め。0.1+0.2 のような誤差を設定ファイルへ持ち込まない。 */
function roundToStep(value: number): number {
  return Math.round(value / WINDOW_OPACITY_STEP) * WINDOW_OPACITY_STEP;
}

/** 小数第 2 位までに正規化する（0.7000000000000001 を作らない）。 */
function toFixed2(value: number): number {
  return Number(value.toFixed(2));
}

/**
 * 任意の入力を 0.70〜1.00 の number にする。
 *
 * number 以外（文字列・null・オブジェクト等）と NaN / Infinity は拒否して
 * 既定値（1.00）を返す。範囲外は clamp する。
 */
export function normalizeWindowOpacity(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_WINDOW_OPACITY;
  const clamped = Math.min(MAX_WINDOW_OPACITY, Math.max(MIN_WINDOW_OPACITY, value));
  return toFixed2(roundToStep(clamped));
}

/** 設定オブジェクトから読む。未設定・壊れている場合は 1.00。 */
export function readWindowOpacity(settings: unknown): number {
  if (typeof settings !== 'object' || settings === null || Array.isArray(settings)) {
    return DEFAULT_WINDOW_OPACITY;
  }
  const raw = (settings as Record<string, unknown>)[WINDOW_OPACITY_KEY];
  if (raw === undefined) return DEFAULT_WINDOW_OPACITY;
  return normalizeWindowOpacity(raw);
}

/** 表示用のパーセント（70〜100）。 */
export function opacityToPercent(opacity: number): number {
  return Math.round(normalizeWindowOpacity(opacity) * 100);
}

/** スライダーのパーセント値から不透明度へ戻す。 */
export function percentToOpacity(percent: number): number {
  if (typeof percent !== 'number' || !Number.isFinite(percent)) return DEFAULT_WINDOW_OPACITY;
  return normalizeWindowOpacity(percent / 100);
}
