import { describe, it, expect, vi } from 'vitest';
import { FAILED_SESSION_STATUS, createSessionCleanup } from './sessionCleanup';

describe('createSessionCleanup', () => {
  it('失敗時に session.json を failed へ確定し、理由を診断ログへ残す', async () => {
    const finalize = vi.fn().mockResolvedValue({});
    const diagnostics = vi.fn().mockResolvedValue({ ok: true });
    const cleanup = createSessionCleanup({ finalize, diagnostics });

    await cleanup('/tmp/s1', 'マイクの取得に失敗しました');

    expect(finalize).toHaveBeenCalledWith('/tmp/s1', FAILED_SESSION_STATUS);
    expect(diagnostics).toHaveBeenCalledTimes(1);
    expect(diagnostics.mock.calls[0][0]).toBe('/tmp/s1');
    expect(diagnostics.mock.calls[0][1]).toContain('マイクの取得に失敗しました');
  });

  it('status は recording のままにしない', () => {
    expect(FAILED_SESSION_STATUS).not.toBe('recording');
    expect(FAILED_SESSION_STATUS).not.toBe('done');
  });

  it('同じセッションへ複数回呼んでも1回しか確定しない（冪等）', async () => {
    const finalize = vi.fn().mockResolvedValue({});
    const diagnostics = vi.fn().mockResolvedValue({ ok: true });
    const cleanup = createSessionCleanup({ finalize, diagnostics });

    await cleanup('/tmp/s1', 'x');
    await cleanup('/tmp/s1', 'x');
    await cleanup('/tmp/s1', 'y');

    expect(finalize).toHaveBeenCalledTimes(1);
  });

  it('別セッションはそれぞれ確定する', async () => {
    const finalize = vi.fn().mockResolvedValue({});
    const cleanup = createSessionCleanup({ finalize, diagnostics: vi.fn().mockResolvedValue({ ok: true }) });
    await cleanup('/tmp/s1', 'x');
    await cleanup('/tmp/s2', 'x');
    expect(finalize).toHaveBeenCalledTimes(2);
  });

  it('finalize が失敗しても例外を投げない（元のマイクエラーを隠さない）', async () => {
    const cleanup = createSessionCleanup({
      finalize: vi.fn().mockRejectedValue(new Error('backend down')),
      diagnostics: vi.fn().mockResolvedValue({ ok: true })
    });
    await expect(cleanup('/tmp/s1', 'x')).resolves.toBeUndefined();
  });

  it('診断ログの書き込みが失敗しても例外を投げない', async () => {
    const cleanup = createSessionCleanup({
      finalize: vi.fn().mockResolvedValue({}),
      diagnostics: vi.fn().mockRejectedValue(new Error('no disk'))
    });
    await expect(cleanup('/tmp/s1', 'x')).resolves.toBeUndefined();
  });

  it('sessionDir が空なら何もしない', async () => {
    const finalize = vi.fn();
    const cleanup = createSessionCleanup({ finalize, diagnostics: vi.fn() });
    await cleanup('', 'x');
    await cleanup(null, 'x');
    expect(finalize).not.toHaveBeenCalled();
  });

  it('フォルダやファイルを削除する経路を持たない', () => {
    const cleanup = createSessionCleanup({ finalize: vi.fn(), diagnostics: vi.fn() });
    expect(String(cleanup)).not.toMatch(/rmdir|unlink|remove|delete/i);
  });
});
