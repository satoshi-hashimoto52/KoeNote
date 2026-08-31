/**
 * 開始に失敗したセッションの後処理（0016）。
 *
 * セッションフォルダはマイク取得より前に作られるため、マイク取得や録音初期化が
 * 失敗すると `status: "recording"` のまま中身の無いフォルダが残る。
 * ここで `status` を確定させ、理由を診断ログへ残す。
 *
 * 方針:
 *   - フォルダもファイルも**削除しない**（ユーザーの成果物を消さない）
 *   - 何度呼んでも 1 度しか確定しない（冪等）
 *   - 後処理自体が失敗しても投げない。元のマイクエラーを隠さないため
 */

/** 開始に失敗したセッションの status。`recording` のままにも `done` にもしない。 */
export const FAILED_SESSION_STATUS = 'failed';

export interface SessionCleanupDeps {
  /** `/api/session/finalize` 相当。status を確定し ended_at を設定する。 */
  finalize: (sessionDir: string, status: string) => Promise<unknown>;
  /** diagnostics.log への追記。Backend の死活に依存しない経路を渡すこと（0010）。 */
  diagnostics: (sessionDir: string, text: string) => Promise<unknown>;
}

export type SessionCleanup = (sessionDir: string | null | undefined, reason: string) => Promise<void>;

export function createSessionCleanup(deps: SessionCleanupDeps): SessionCleanup {
  const finalized = new Set<string>();

  return async function cleanup(sessionDir, reason) {
    const dir = String(sessionDir ?? '').trim();
    if (!dir || finalized.has(dir)) return;
    finalized.add(dir);

    // 診断ログを先に書く。Backend が落ちていても理由だけは残る（0010）。
    try {
      await deps.diagnostics(dir, `[start_failed] ${reason}`);
    } catch {
      /* 記録できなくても続行する */
    }
    try {
      await deps.finalize(dir, FAILED_SESSION_STATUS);
    } catch {
      /* 確定できなくても、元のエラーを隠さないために投げない */
    }
  };
}
