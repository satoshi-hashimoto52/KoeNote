import type { BridgeApi } from '../types/bridge';

const FALLBACK_ORIGIN = 'http://127.0.0.1:8765';
const FALLBACK_WS = 'ws://127.0.0.1:8765';

export function getBridge(): BridgeApi | null {
  return typeof window !== 'undefined' && window.bridge ? window.bridge : null;
}

export function backendOrigin(): string {
  return getBridge()?.backendOrigin ?? FALLBACK_ORIGIN;
}

export function wsOrigin(): string {
  return getBridge()?.wsOrigin ?? FALLBACK_WS;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${backendOrigin()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) {
    let detail = 'リクエストに失敗しました';
    try {
      const payload = await res.json();
      detail = payload?.detail || detail;
    } catch {
      /* noop */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export interface OutputCheck {
  ok: boolean;
  exists: boolean;
  writable: boolean;
  free_bytes: number | null;
  path?: string;
  reason?: string;
}

export interface CreatedSession {
  session_dir: string;
  transcript_path: string;
  segments_path: string;
  session_json_path: string;
  diagnostics_path: string;
  transcript_filename: string;
  session: Record<string, unknown>;
}

export function checkOutput(outputBase: string): Promise<OutputCheck> {
  return postJson<OutputCheck>('/api/session/check_output', { output_base: outputBase });
}

export function createSession(params: {
  title: string;
  output_base: string;
  gpt_url: string;
}): Promise<CreatedSession> {
  return postJson<CreatedSession>('/api/session/create', params);
}

export function finalizeSession(sessionDir: string, status = 'done'): Promise<Record<string, unknown>> {
  return postJson('/api/session/finalize', { session_dir: sessionDir, status });
}

/** 異常停止の記録を diagnostics.log に残す。事後解析のための唯一の永続ログ。 */
/**
 * Backend 経由で diagnostics.log へ書く。
 * 異常記録には使わない（Backend 停止時に失敗するため。0010 で Electron のローカル I/O へ移行済み）。
 * Backend が生きている前提の用途のために残す。
 */
export function postDiagnostics(sessionDir: string, message: string): Promise<{ ok: boolean }> {
  return postJson('/api/session/diagnostics', { session_dir: sessionDir, message });
}

/** 強制終了でヘッダが古くなった recording.wav を実ファイル長から復旧する。 */
export function repairAudio(
  sessionDir: string
): Promise<{ ok: boolean; reason?: string; audio_path: string; seconds: number }> {
  return postJson('/api/session/repair_audio', { session_dir: sessionDir });
}

/** マイGPTへ渡す依頼文を生成する。設定で編集した本文があれば優先する。 */
export function buildRequestText(title: string, template?: string): string {
  const base =
    template && template.trim()
      ? template
      : `以下の会議／セミナーを、設定済みの形式でまとめてください。

タイトル：
{title}

添付ファイル：
・文字起こしテキスト

文字起こしには音声認識による誤字が含まれる可能性があります。
発言から確認できない内容を、推測で追加しないでください。`;
  return (
    base
      .replace('{title}', title)
      // 旧テンプレート互換: 資料機能の削除前に保存された requestTemplate に
      // {attachment_names} が残っていることがある。そのまま GPT へ渡さないよう空文字にする。
      // ユーザー設定は書き換えない（0007）。
      .replace(/\{attachment_names\}\n?/g, '')
  );
}
