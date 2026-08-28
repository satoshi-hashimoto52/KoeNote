/**
 * 文字起こし欄の高さ計算（0008）。
 *
 * 「ユーザーが希望した高さ」と「いまのウィンドウで実際に表示できる高さ」を分ける。
 *
 * - `preferredHeight`  : ユーザーがハンドルを操作したときだけ更新し、設定へ保存する
 * - `effectiveHeight`  : preferredHeight と利用可能領域から計算する表示値。保存しない
 *
 * ウィンドウを縮めて effectiveHeight が小さくなっても preferredHeight は変えない。
 * そのためウィンドウを広げれば元の希望高さへ戻る。
 */

export const TRANSCRIPT_HEIGHT_KEY = 'transcriptHeight';
export const DEFAULT_TRANSCRIPT_HEIGHT = 320;
export const MIN_TRANSCRIPT_HEIGHT = 180;
export const MAX_TRANSCRIPT_HEIGHT = 1200;
/** ウィンドウ高さに対して文字起こし欄が占めてよい上限。 */
export const VIEWPORT_RATIO = 0.7;

/**
 * 保存値・未設定値を希望高さへ正規化する。
 *
 * **ここではウィンドウサイズを一切見ない。** 見てしまうと、小さいウィンドウで
 * クランプした結果が希望高さとして保存され、二度と元に戻らなくなる。
 */
export function normalizePreferredHeight(raw: unknown): number {
  const parsed = typeof raw === 'number' ? raw : Number(raw);
  const height =
    Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_TRANSCRIPT_HEIGHT;
  return Math.round(Math.min(Math.max(height, MIN_TRANSCRIPT_HEIGHT), MAX_TRANSCRIPT_HEIGHT));
}

/** いまのウィンドウで文字起こし欄に割り当ててよい高さ。 */
export function availableHeightFor(viewportHeight: unknown): number {
  const h = typeof viewportHeight === 'number' ? viewportHeight : Number(viewportHeight);
  if (!Number.isFinite(h) || h <= 0) return MAX_TRANSCRIPT_HEIGHT;
  // 360 * 0.7 が 251.999… になるなど誤差が出るため floor ではなく round を使う。
  return Math.max(Math.round(h * VIEWPORT_RATIO), MIN_TRANSCRIPT_HEIGHT);
}

/**
 * 実際に表示する高さ。
 *
 * `clamp(preferredHeight, MIN, availableHeight)`。
 * 戻り値は表示にだけ使い、**保存してはいけない**。
 */
export function computeEffectiveHeight(preferredHeight: number, availableHeight: number): number {
  const limit = Math.max(availableHeight, MIN_TRANSCRIPT_HEIGHT);
  return Math.round(Math.min(Math.max(preferredHeight, MIN_TRANSCRIPT_HEIGHT), limit));
}

/**
 * ドラッグ量から希望高さを求める。横方向の移動は呼び出し側で無視する。
 *
 * 上へ大きく動かした結果が 0 以下になっても「不正値」ではないので、
 * 既定値へ飛ばさず最小値でクランプする（`normalizePreferredHeight` とは扱いが異なる）。
 */
export function heightFromDrag(startHeight: number, deltaY: number): number {
  const raw = startHeight + deltaY;
  if (!Number.isFinite(raw)) return DEFAULT_TRANSCRIPT_HEIGHT;
  return Math.round(Math.min(Math.max(raw, MIN_TRANSCRIPT_HEIGHT), MAX_TRANSCRIPT_HEIGHT));
}

/** 保存する価値のある変化か（1px 未満の揺れで書き込まない）。 */
export function shouldPersistHeight(previous: number, next: number): boolean {
  return Math.abs(previous - next) >= 1;
}
