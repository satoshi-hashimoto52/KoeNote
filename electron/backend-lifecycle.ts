/**
 * Backend のライフサイクル判定のうち、Electron に依存しない部分。
 * `backend.ts` は `electron` を import するためユニットテストしづらいので、
 * 純粋なロジックだけをここへ置いてテスト対象にする。
 */

export type BackendExitReason = 'exited' | 'crashed' | 'killed_possibly_oom';

/**
 * 子プロセスの終了理由を分類する。
 *
 * `intentional` はアプリ終了・再起動による停止。SIGTERM で落としているため、
 * これを crashed と分類すると意図的停止が異常終了として記録され紛らわしい（0012）。
 */
export function classifyExit(
  code: number | null,
  signal: string | null,
  intentional = false
): BackendExitReason {
  if (intentional) return 'exited';
  // macOS の OOM kill は SIGKILL + code=null で現れる。
  if (signal === 'SIGKILL' && code === null) return 'killed_possibly_oom';
  if (signal !== null) return 'crashed';
  if (code !== null && code !== 0) return 'crashed';
  return 'exited';
}

export interface SingleFlight<T> {
  /** 進行中なら同じ Promise を返し、task を再実行しない。 */
  run(task: () => Promise<T>, onJoin?: () => void): Promise<T>;
  isRunning(): boolean;
}

/**
 * 同じ処理の同時実行を 1 本に束ねる。
 *
 * Backend の再起動はネイティブダイアログ・アプリ内ポップアップ・IPC の
 * 複数経路から要求されうる。ガードがないと stopBackend -> startBackend が
 * 二重に走り、起動直後の Backend を自分で停止してしまう（0012）。
 */
export function createSingleFlight<T>(): SingleFlight<T> {
  let inFlight: Promise<T> | null = null;
  return {
    isRunning: () => inFlight !== null,
    run(task: () => Promise<T>, onJoin?: () => void): Promise<T> {
      if (inFlight) {
        onJoin?.();
        return inFlight;
      }
      const started = (async () => task())();
      inFlight = started;
      // 成功・失敗のどちらでも必ず解除する。解除し損ねると以後の要求を受け付けられない。
      const release = () => {
        if (inFlight === started) inFlight = null;
      };
      started.then(release, release);
      return started;
    }
  };
}

/** KoeNote の /api/health が名乗るアプリ名。 */
export const BACKEND_APP_NAME = 'KoeNote';

/**
 * /api/health の応答が「KoeNote 自身のBackend」かどうかを判定する。
 *
 * 同じポートを別プロジェクトが占有していることは実際に起きる。status だけを見ると
 * 無関係のサーバへ録音を送ってしまうため、app 名まで一致した場合だけ再利用する。
 */
export function isOwnBackendHealth(statusCode: number | undefined, body: string): boolean {
  if (statusCode !== 200) return false;
  try {
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed !== 'object' || parsed === null) return false;
    return (parsed as { app?: unknown }).app === BACKEND_APP_NAME;
  } catch {
    return false;
  }
}
