/**
 * 文字起こし欄の高さの正規化（0008）。
 *
 * 設定に保存した値を復元するとき、異常値・古い値・極端に大きい値をそのまま
 * 使うと画面が破綻する。保存値より「画面内に収まること」を優先する。
 */

export const TRANSCRIPT_HEIGHT_KEY = 'transcriptHeight';
export const DEFAULT_TRANSCRIPT_HEIGHT = 320;
export const MIN_TRANSCRIPT_HEIGHT = 180;
export const MAX_TRANSCRIPT_HEIGHT = 1200;
/** ウィンドウ高さに対して文字起こし欄が占めてよい上限。 */
export const VIEWPORT_RATIO = 0.7;
/** 高さ変更を設定へ書き戻すまでの待ち時間。ドラッグ中の連続書き込みを避ける。 */
export const SAVE_DEBOUNCE_MS = 500;

/**
 * 保存値・未設定値を実際に使う高さへ正規化する。
 *
 * - 数値でない / NaN / 0 以下 → 既定値
 * - 最小・最大でクランプ
 * - ウィンドウが小さいときは `viewportHeight * VIEWPORT_RATIO` を優先する
 *   （ただし最小高さは下回らせない）
 */
export function normalizeTranscriptHeight(raw: unknown, viewportHeight?: number): number {
  const parsed = typeof raw === 'number' ? raw : Number(raw);
  let height =
    Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_TRANSCRIPT_HEIGHT;

  height = Math.min(Math.max(height, MIN_TRANSCRIPT_HEIGHT), MAX_TRANSCRIPT_HEIGHT);

  if (Number.isFinite(viewportHeight) && (viewportHeight as number) > 0) {
    const viewportLimit = Math.floor((viewportHeight as number) * VIEWPORT_RATIO);
    // 画面内に収めることを保存値より優先する。ただし最小は割らない。
    height = Math.max(Math.min(height, viewportLimit), MIN_TRANSCRIPT_HEIGHT);
  }
  return Math.round(height);
}

/** 保存する価値のある変化か（1px 未満の揺れで書き込まない）。 */
export function shouldPersistHeight(previous: number, next: number): boolean {
  return Math.abs(previous - next) >= 1;
}
