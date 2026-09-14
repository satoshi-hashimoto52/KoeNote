import { mkdtemp, readFile, readdir, rm, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { pendingWriteCount, writeJsonAtomic } from './atomicJson';

let dir = '';

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'koenote-atomic-'));
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
});

describe('writeJsonAtomic', () => {
  it('JSON を書き込める', async () => {
    const target = join(dir, 'settings.json');
    await writeJsonAtomic(target, { gptUrl: 'https://chatgpt.com/g/x', maxGainDb: 24 });
    expect(JSON.parse(await readFile(target, 'utf-8'))).toEqual({
      gptUrl: 'https://chatgpt.com/g/x',
      maxGainDb: 24
    });
  });

  it('同時に書き込んでも ENOENT にならない（0019 の再現）', async () => {
    // 旧実装は一時ファイル名が固定だったため、この並列書き込みで
    // 後発の rename が ENOENT になった。
    const target = join(dir, 'settings.json');
    const writes = Array.from({ length: 30 }, (_, i) =>
      writeJsonAtomic(target, { seq: i })
    );
    await expect(Promise.all(writes)).resolves.toBeDefined();
    const parsed = JSON.parse(await readFile(target, 'utf-8'));
    expect(typeof parsed.seq).toBe('number');
  });

  it('最後の書き込みが残る（到着順に直列化される）', async () => {
    const target = join(dir, 'settings.json');
    await Promise.all([
      writeJsonAtomic(target, { seq: 1 }),
      writeJsonAtomic(target, { seq: 2 }),
      writeJsonAtomic(target, { seq: 3 })
    ]);
    expect(JSON.parse(await readFile(target, 'utf-8'))).toEqual({ seq: 3 });
  });

  it('一時ファイルを残さない', async () => {
    const target = join(dir, 'settings.json');
    await Promise.all(
      Array.from({ length: 10 }, (_, i) => writeJsonAtomic(target, { seq: i }))
    );
    const files = await readdir(dir);
    expect(files.filter((f) => f.endsWith('.tmp'))).toEqual([]);
    expect(files).toEqual(['settings.json']);
  });

  it('親ディレクトリが無くても作る', async () => {
    const target = join(dir, 'nested', 'deep', 'settings.json');
    await writeJsonAtomic(target, { ok: true });
    expect(JSON.parse(await readFile(target, 'utf-8'))).toEqual({ ok: true });
  });

  it('別ファイルへの書き込みは互いに干渉しない', async () => {
    const a = join(dir, 'a.json');
    const b = join(dir, 'b.json');
    await Promise.all([writeJsonAtomic(a, { who: 'a' }), writeJsonAtomic(b, { who: 'b' })]);
    expect(JSON.parse(await readFile(a, 'utf-8'))).toEqual({ who: 'a' });
    expect(JSON.parse(await readFile(b, 'utf-8'))).toEqual({ who: 'b' });
  });

  it('書き込み失敗は呼び出し元へ伝わる', async () => {
    // 書き込めないディレクトリを作る
    const locked = join(dir, 'locked');
    await writeJsonAtomic(join(locked, 'seed.json'), {});
    await chmod(locked, 0o500);
    try {
      await expect(writeJsonAtomic(join(locked, 'settings.json'), { x: 1 })).rejects.toThrow();
    } finally {
      await chmod(locked, 0o700);
    }
  });

  it('1 回失敗しても後続の書き込みは実行される', async () => {
    const locked = join(dir, 'locked2');
    await writeJsonAtomic(join(locked, 'seed.json'), {});
    await chmod(locked, 0o500);
    const failing = writeJsonAtomic(join(locked, 'settings.json'), { x: 1 }).catch(() => 'failed');
    expect(await failing).toBe('failed');
    await chmod(locked, 0o700);

    // 同じパスへの次の書き込みは成功すること（失敗が伝播し続けない）
    await writeJsonAtomic(join(locked, 'settings.json'), { x: 2 });
    expect(JSON.parse(await readFile(join(locked, 'settings.json'), 'utf-8'))).toEqual({ x: 2 });
  });

  it('書き込みが落ち着けばキューは空になる（状態量が増え続けない）', async () => {
    const target = join(dir, 'settings.json');
    await Promise.all(
      Array.from({ length: 20 }, (_, i) => writeJsonAtomic(target, { seq: i }))
    );
    // マイクロタスクを 1 巡させてから確認する
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(pendingWriteCount()).toBe(0);
  });
});
