import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtemp, mkdir, readFile, rm, writeFile, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { appendDiagnosticsLine, formatDiagnosticsLine, localIsoSeconds } from './diagnostics';

let base: string;

beforeEach(async () => {
  base = await mkdtemp(join(tmpdir(), 'koenote-diag-'));
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true }).catch(() => undefined);
});

describe('localIsoSeconds', () => {
  it('Backend の _now_iso と同じ表記（秒精度・オフセット付き）', () => {
    const line = localIsoSeconds(new Date(2026, 7, 28, 9, 42, 35));
    // 例: 2026-08-28T09:42:35+09:00
    expect(line).toMatch(/^2026-08-28T09:42:35[+-]\d{2}:\d{2}$/);
  });

  it('ミリ秒や UTC 表記（末尾 Z）を含まない', () => {
    const line = localIsoSeconds(new Date(2026, 0, 2, 3, 4, 5, 678));
    expect(line).not.toContain('.');
    expect(line.endsWith('Z')).toBe(false);
  });

  it('各要素がゼロ詰めされる', () => {
    expect(localIsoSeconds(new Date(2026, 0, 2, 3, 4, 5))).toMatch(/^2026-01-02T03:04:05/);
  });
});

describe('formatDiagnosticsLine', () => {
  it('[時刻] 本文 の 1 行で、改行で終わる', () => {
    const line = formatDiagnosticsLine('[中断] test', new Date(2026, 7, 28, 9, 42, 35));
    expect(line).toMatch(/^\[2026-08-28T09:42:35[+-]\d{2}:\d{2}\] \[中断\] test\n$/);
  });
});

describe('appendDiagnosticsLine（0010）', () => {
  // Backend の HTTP API を一切使わないことが要件。この関数は fetch を持たない。
  it('Backend が利用不能でも diagnostics.log へ書き込める', async () => {
    const dir = join(base, 'session');
    await mkdir(dir);
    const result = await appendDiagnosticsLine(dir, '[中断] backend_exit', new Date(2026, 7, 28, 9, 39, 33));
    expect(result.ok).toBe(true);
    const body = await readFile(join(dir, 'diagnostics.log'), 'utf-8');
    expect(body).toContain('[中断] backend_exit');
  });

  it('既存ファイルへ追記する（上書きしない）', async () => {
    const dir = join(base, 'session');
    await mkdir(dir);
    await writeFile(join(dir, 'diagnostics.log'), '[2026-08-28T00:00:00+09:00] 既存行\n', 'utf-8');

    await appendDiagnosticsLine(dir, '追記1');
    await appendDiagnosticsLine(dir, '追記2');

    const lines = (await readFile(join(dir, 'diagnostics.log'), 'utf-8')).trimEnd().split('\n');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toContain('既存行');
    expect(lines[1]).toContain('追記1');
    expect(lines[2]).toContain('追記2');
  });

  it('セッションフォルダが未作成でも安全に作成して書き込む', async () => {
    const dir = join(base, 'not-created-yet', 'nested');
    const result = await appendDiagnosticsLine(dir, '[中断] backend_exit');
    expect(result.ok).toBe(true);
    expect(await readFile(join(dir, 'diagnostics.log'), 'utf-8')).toContain('backend_exit');
  });

  it('同じ本文を 1 回呼べば 1 行だけ（呼び出し側が重複しない限り二重記録しない）', async () => {
    const dir = join(base, 'session');
    await mkdir(dir);
    await appendDiagnosticsLine(dir, '[中断] once');
    const lines = (await readFile(join(dir, 'diagnostics.log'), 'utf-8')).trimEnd().split('\n');
    expect(lines).toHaveLength(1);
  });

  it('書き込み失敗を握り潰さず ok:false と理由を返す', async () => {
    const dir = join(base, 'readonly');
    await mkdir(dir);
    await chmod(dir, 0o500); // 書き込み不可
    const result = await appendDiagnosticsLine(dir, '[中断] backend_exit');
    await chmod(dir, 0o700);
    expect(result.ok).toBe(false);
    expect(result.reason).toBeTruthy();
    expect(result.path).toContain('diagnostics.log');
  });

  it('sessionDir が空なら書き込まず理由を返す', async () => {
    const result = await appendDiagnosticsLine('', 'x');
    expect(result).toEqual({ ok: false, reason: 'empty_session_dir' });
  });
});
