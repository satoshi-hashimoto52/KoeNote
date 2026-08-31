/**
 * 画面上部に出す通知の分類と、自動消去の制御（0016）。
 *
 * 入力デバイスのフォールバック通知だけは、録音を続けられる情報通知なので
 * 一定時間で自動的に消す。マイク権限拒否・Backend 異常・録音開始失敗など、
 * ユーザーの操作が必要な通知は従来どおり残す。
 *
 * 文字列一致で判定すると文言を変えたときに壊れるため、kind を持たせる。
 */

/** フォールバック通知を自動で消すまでの時間。 */
export const FALLBACK_AUTO_DISMISS_MS = 8000;

/** 既存の banner クラス（`banner-error` / `banner-warn` / `banner-ok`）に対応する。 */
export type NoticeTone = 'error' | 'warn' | 'ok';

export type UiNotice =
  | { kind: 'input-device-fallback'; tone: 'warn'; message: string }
  | { kind: 'error'; tone: 'error'; message: string }
  | { kind: 'warn'; tone: 'warn'; message: string }
  | { kind: 'ok'; tone: 'ok'; message: string };

export function inputDeviceFallbackNotice(message: string): UiNotice {
  return { kind: 'input-device-fallback', tone: 'warn', message };
}
export function errorNotice(message: string): UiNotice {
  return { kind: 'error', tone: 'error', message };
}
export function warnNotice(message: string): UiNotice {
  return { kind: 'warn', tone: 'warn', message };
}
export function okNotice(message: string): UiNotice {
  return { kind: 'ok', tone: 'ok', message };
}

/** 自動で消してよい通知か。録音を続けられる情報通知だけを対象にする。 */
export function isAutoDismissible(notice: UiNotice | null | undefined): boolean {
  return notice?.kind === 'input-device-fallback';
}

export interface NoticeAutoDismiss {
  /** 表示中の通知を渡す。自動消去対象なら計時し直す。null で解除。 */
  schedule(notice: UiNotice | null): void;
  /** アンマウント時に呼ぶ。以後は発火しない。 */
  dispose(): void;
}

/**
 * 自動消去のタイマー管理。
 *
 * `schedule` のたびに前のタイマーを捨てるので、古いタイマーが後から来た通知を
 * 消すことはない。`dispose` 後は何をしても発火しない。
 */
export function createNoticeAutoDismiss(
  onExpire: (notice: UiNotice) => void,
  delayMs: number = FALLBACK_AUTO_DISMISS_MS
): NoticeAutoDismiss {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let scheduled: UiNotice | null = null;
  let disposed = false;

  const clear = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    scheduled = null;
  };

  return {
    schedule(notice) {
      if (disposed) return;
      // 同じ通知を計時中なら数え直さない（再レンダリングで消えなくなるのを防ぐ）。
      if (notice && scheduled === notice) return;
      clear();
      if (!isAutoDismissible(notice) || !notice) return;
      scheduled = notice;
      timer = setTimeout(() => {
        timer = null;
        const expired = scheduled;
        scheduled = null;
        if (!disposed && expired) onExpire(expired);
      }, delayMs);
    },
    dispose() {
      disposed = true;
      clear();
    }
  };
}
