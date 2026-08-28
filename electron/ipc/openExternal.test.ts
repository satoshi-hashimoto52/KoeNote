import { describe, it, expect, vi } from 'vitest';
import { CHROME_BUNDLE_ID, isAllowedGptUrl, openGptUrl } from './openExternal';

const URL_OK = 'https://chatgpt.com/g/g-abc';

function deps(over: Partial<Parameters<typeof openGptUrl>[1]> = {}) {
  return {
    openInChrome: vi.fn(async () => true),
    openDefault: vi.fn(async () => undefined),
    ...over
  };
}

describe('isAllowedGptUrl（既存の許可リストを維持）', () => {
  it('chatgpt.com / chat.openai.com を許可する', () => {
    expect(isAllowedGptUrl('https://chatgpt.com/c/1')).toBe(true);
    expect(isAllowedGptUrl('https://chat.openai.com/g/x')).toBe(true);
    expect(isAllowedGptUrl('https://sub.chatgpt.com/g/x')).toBe(true);
  });

  it('https 以外と他ドメインを拒否する', () => {
    expect(isAllowedGptUrl('http://chatgpt.com/')).toBe(false);
    expect(isAllowedGptUrl('https://evil.com/')).toBe(false);
    expect(isAllowedGptUrl('https://chatgpt.com.evil.com/')).toBe(false);
    expect(isAllowedGptUrl('not a url')).toBe(false);
    expect(isAllowedGptUrl('')).toBe(false);
  });
});

describe('0006: openGptUrl', () => {
  it('Chrome で開けたら opener=chrome を返し、既定ブラウザは呼ばない', async () => {
    const d = deps();
    await expect(openGptUrl(URL_OK, d)).resolves.toEqual({ ok: true, opener: 'chrome' });
    expect(d.openInChrome).toHaveBeenCalledWith(URL_OK);
    expect(d.openDefault).not.toHaveBeenCalled();
  });

  it('Chrome が開けなければ既定ブラウザへフォールバックする', async () => {
    const d = deps({ openInChrome: vi.fn(async () => false) });
    await expect(openGptUrl(URL_OK, d)).resolves.toEqual({ ok: true, opener: 'default' });
    expect(d.openDefault).toHaveBeenCalledWith(URL_OK);
  });

  it('Chrome が例外を投げてもフォールバックする', async () => {
    const d = deps({ openInChrome: vi.fn(async () => { throw new Error('spawn failed'); }) });
    await expect(openGptUrl(URL_OK, d)).resolves.toEqual({ ok: true, opener: 'default' });
    expect(d.openDefault).toHaveBeenCalledTimes(1);
  });

  it('両方失敗したら ok:false と理由を返す', async () => {
    const d = deps({
      openInChrome: vi.fn(async () => false),
      openDefault: vi.fn(async () => { throw new Error('no browser'); })
    });
    await expect(openGptUrl(URL_OK, d)).resolves.toEqual({ ok: false, reason: 'no browser' });
  });

  it('許可外 URL はどちらも呼ばず disallowed_domain を返す', async () => {
    const d = deps();
    await expect(openGptUrl('https://evil.com/', d)).resolves.toEqual({
      ok: false,
      reason: 'disallowed_domain'
    });
    expect(d.openInChrome).not.toHaveBeenCalled();
    expect(d.openDefault).not.toHaveBeenCalled();
  });

  it('bundle ID は com.google.Chrome', () => {
    expect(CHROME_BUNDLE_ID).toBe('com.google.Chrome');
  });
});

describe('0006: 連打防止（呼び出し側のガード）', () => {
  /** App.tsx の openingGptRef と同じ構造。 */
  function makeGuardedOpener(open: () => Promise<unknown>) {
    let inFlight = false;
    return async () => {
      if (inFlight) return 'skipped';
      inFlight = true;
      try {
        await open();
        return 'opened';
      } finally {
        inFlight = false;
      }
    };
  }

  it('進行中の再クリックは無視され、開くのは 1 回だけ', async () => {
    let calls = 0;
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const guarded = makeGuardedOpener(async () => { calls += 1; await gate; });

    const first = guarded();
    const second = guarded();
    const third = guarded();
    release();
    const results = await Promise.all([first, second, third]);

    expect(calls).toBe(1);
    expect(results.filter((r) => r === 'opened')).toHaveLength(1);
    expect(results.filter((r) => r === 'skipped')).toHaveLength(2);
  });

  it('完了後は再び開ける', async () => {
    let calls = 0;
    const guarded = makeGuardedOpener(async () => { calls += 1; });
    await guarded();
    await guarded();
    expect(calls).toBe(2);
  });
});

describe('0006: execFile へ引数配列で渡す（シェルを介さない）', () => {
  it('openInChromeMac は execFile を配列引数で呼び、shell オプションを使わない', async () => {
    vi.resetModules();
    const execFile = vi.fn((_cmd: string, _args: string[], cb: (e: Error | null) => void) => cb(null));
    vi.doMock('node:child_process', () => ({ execFile }));
    const mod = await import('./openExternal');
    await expect(mod.openInChromeMac('https://chatgpt.com/x')).resolves.toBe(true);
    expect(execFile).toHaveBeenCalledTimes(1);
    const [cmd, args, cb] = execFile.mock.calls[0];
    expect(cmd).toBe('open');
    expect(args).toEqual(['-b', 'com.google.Chrome', 'https://chatgpt.com/x']);
    expect(typeof cb).toBe('function');
    vi.doUnmock('node:child_process');
    vi.resetModules();
  });

  it('execFile が失敗したら false を返す', async () => {
    vi.resetModules();
    const execFile = vi.fn((_c: string, _a: string[], cb: (e: Error | null) => void) => cb(new Error('x')));
    vi.doMock('node:child_process', () => ({ execFile }));
    const mod = await import('./openExternal');
    await expect(mod.openInChromeMac('https://chatgpt.com/x')).resolves.toBe(false);
    vi.doUnmock('node:child_process');
    vi.resetModules();
  });
});
