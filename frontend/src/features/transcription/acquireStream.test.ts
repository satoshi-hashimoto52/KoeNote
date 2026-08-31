import { describe, it, expect, vi } from 'vitest';
import { acquireInputStream } from './inputDevice';

const OK = { id: 'stream' } as unknown as MediaStream;
const err = (name: string, message = '') => Object.assign(new Error(message), { name });

describe('acquireInputStream', () => {
  it('exact 指定が通ればそのまま返し、再試行しない', async () => {
    const gum = vi.fn().mockResolvedValue(OK);
    const r = await acquireInputStream(gum, 'aaa111');
    expect(r.stream).toBe(OK);
    expect(r.retried).toBe(false);
    expect(gum).toHaveBeenCalledTimes(1);
    expect((gum.mock.calls[0][0].audio as MediaTrackConstraints).deviceId).toEqual({ exact: 'aaa111' });
  });

  it('列挙後にデバイスが外れて OverconstrainedError → deviceId なしで1回だけ再試行する', async () => {
    const gum = vi.fn().mockRejectedValueOnce(err('OverconstrainedError')).mockResolvedValueOnce(OK);
    const r = await acquireInputStream(gum, 'aaa111');
    expect(r.stream).toBe(OK);
    expect(r.retried).toBe(true);
    expect(gum).toHaveBeenCalledTimes(2);
    expect('deviceId' in (gum.mock.calls[1][0].audio as MediaTrackConstraints)).toBe(false);
  });

  it('NotFoundError でも1回だけ再試行する', async () => {
    const gum = vi.fn().mockRejectedValueOnce(err('NotFoundError')).mockResolvedValueOnce(OK);
    const r = await acquireInputStream(gum, 'aaa111');
    expect(r.retried).toBe(true);
    expect(gum).toHaveBeenCalledTimes(2);
  });

  it('再試行も失敗したら無限ループせずエラーを投げる', async () => {
    const gum = vi.fn().mockRejectedValue(err('OverconstrainedError'));
    await expect(acquireInputStream(gum, 'aaa111')).rejects.toThrow();
    expect(gum).toHaveBeenCalledTimes(2); // 初回 + 再試行1回だけ
  });

  it('deviceId 未指定なら再試行しない（同じ呼び出しの繰り返しを避ける）', async () => {
    const gum = vi.fn().mockRejectedValue(err('OverconstrainedError'));
    await expect(acquireInputStream(gum, null)).rejects.toThrow();
    expect(gum).toHaveBeenCalledTimes(1);
  });

  it('NotAllowedError は再試行せず権限エラーを投げる', async () => {
    const gum = vi.fn().mockRejectedValue(err('NotAllowedError'));
    await expect(acquireInputStream(gum, 'aaa111')).rejects.toThrow(/権限/);
    expect(gum).toHaveBeenCalledTimes(1);
  });

  it('NotReadableError も再試行しない', async () => {
    const gum = vi.fn().mockRejectedValue(err('NotReadableError'));
    await expect(acquireInputStream(gum, 'aaa111')).rejects.toThrow();
    expect(gum).toHaveBeenCalledTimes(1);
  });

  it('投げるエラーの message は空にならない', async () => {
    const gum = vi.fn().mockRejectedValue(err('OverconstrainedError', ''));
    await expect(acquireInputStream(gum, null)).rejects.toThrow(/.+/);
  });
});
