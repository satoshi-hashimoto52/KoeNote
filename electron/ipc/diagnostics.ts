import { appendFile, mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';

export const DIAGNOSTICS_FILENAME = 'diagnostics.log';

/**
 * Backend の `session_store._now_iso()` と同じ表記を作る。
 * 例: 2026-08-28T09:42:35+09:00
 *
 * Date#toISOString() は UTC かつミリ秒付きなので使えない。
 * 同じファイルへ Backend と Electron の両方が追記しうるため、表記を必ず一致させる。
 */
export function localIsoSeconds(date: Date): string {
  const pad = (value: number) => String(Math.floor(Math.abs(value))).padStart(2, '0');
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? '+' : '-';
  const offset = `${sign}${pad(offsetMinutes / 60)}:${pad(offsetMinutes % 60)}`;
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${offset}`
  );
}

/** diagnostics.log の 1 行。Backend 側 `append_diagnostics` と同じ形式。 */
export function formatDiagnosticsLine(message: string, date: Date): string {
  return `[${localIsoSeconds(date)}] ${message}\n`;
}

export interface AppendResult {
  ok: boolean;
  reason?: string;
  path?: string;
}

/**
 * セッションフォルダの diagnostics.log へ 1 行追記する。
 *
 * Backend の HTTP API を経由しない。`reason=backend_exit` のように
 * 「Backend が死んだ」ことを記録する場面では、Backend 経由の経路は原理的に失敗するため
 * （docs/issues/0010）、記録は必ず main プロセスのローカル I/O で行う。
 */
export async function appendDiagnosticsLine(
  sessionDir: string,
  message: string,
  now: Date = new Date()
): Promise<AppendResult> {
  const dir = String(sessionDir ?? '').trim();
  if (!dir) return { ok: false, reason: 'empty_session_dir' };
  const target = join(dir, DIAGNOSTICS_FILENAME);
  try {
    // セッションフォルダごと未作成でも記録を落とさない。
    await mkdir(dirname(target), { recursive: true });
    await appendFile(target, formatDiagnosticsLine(String(message ?? ''), now), 'utf-8');
    return { ok: true, path: target };
  } catch (error) {
    return { ok: false, reason: error instanceof Error ? error.message : 'append_failed', path: target };
  }
}
