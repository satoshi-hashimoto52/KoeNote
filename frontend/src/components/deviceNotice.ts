/**
 * 入力デバイス関連の通知と、設定モーダルの選択値（0016）。
 */

export interface NoticeGate {
  /** まだ出していない通知なら true。null は常に false。 */
  shouldShow(notice: string | null | undefined): boolean;
  /** 設定を変更したときなど、同じ通知を出し直したい場合に呼ぶ。 */
  reset(): void;
}

/** 同じ通知を録音のたびに繰り返さないためのゲート。 */
export function createNoticeGate(): NoticeGate {
  let shown = new Set<string>();
  return {
    shouldShow(notice) {
      if (!notice) return false;
      if (shown.has(notice)) return false;
      shown.add(notice);
      return true;
    },
    reset() {
      shown = new Set<string>();
    }
  };
}

/**
 * 設定モーダルの `<select>` に渡す値。
 *
 * 保存済み ID が現在の一覧に無い場合、空欄のままにすると「何も選ばれていない」
 * ように見える。`''`（＝既定の入力デバイス）を選択状態として返す。
 */
export function selectValueForMics(savedDeviceId: string, mics: readonly MediaDeviceInfo[]): string {
  const saved = String(savedDeviceId ?? '');
  if (!saved) return '';
  return (mics ?? []).some((m) => m.deviceId === saved) ? saved : '';
}
