import { describe, it, expect } from 'vitest';
import { classifyExit, createSingleFlight } from './backend-lifecycle';

/** 解決タイミングを手で制御できる Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('classifyExit', () => {
  it('SIGKILL + code=null を OOM 相当として分類する', () => {
    expect(classifyExit(null, 'SIGKILL')).toBe('killed_possibly_oom');
  });

  it('その他のシグナルは crashed', () => {
    expect(classifyExit(null, 'SIGSEGV')).toBe('crashed');
    expect(classifyExit(null, 'SIGTERM')).toBe('crashed');
  });

  it('非ゼロ終了コードは crashed', () => {
    expect(classifyExit(1, null)).toBe('crashed');
  });

  it('正常終了は exited', () => {
    expect(classifyExit(0, null)).toBe('exited');
  });

  // 0012: 意図的に停止した旧 Backend を crashed として扱わない
  it('意図的停止は SIGTERM でも exited（crashed と二重通知しない）', () => {
    expect(classifyExit(null, 'SIGTERM', true)).toBe('exited');
    expect(classifyExit(null, 'SIGKILL', true)).toBe('exited');
    expect(classifyExit(1, null, true)).toBe('exited');
  });
});

describe('createSingleFlight（0012 の再起動排他）', () => {
  it('同時に 2 回呼んでも task は 1 回しか実行されない', async () => {
    const guard = createSingleFlight<boolean>();
    const gate = deferred<boolean>();
    let runs = 0;
    const task = () => {
      runs += 1;
      return gate.promise;
    };

    const a = guard.run(task);
    const b = guard.run(task);
    gate.resolve(true);

    expect(await a).toBe(true);
    expect(await b).toBe(true);
    expect(runs).toBe(1);
  });

  it('進行中の呼び出しは同じ Promise を共有する', () => {
    const guard = createSingleFlight<boolean>();
    const gate = deferred<boolean>();
    const a = guard.run(() => gate.promise);
    const b = guard.run(() => gate.promise);
    expect(a).toBe(b);
    gate.resolve(true);
  });

  it('合流したときだけ onJoin が呼ばれる', async () => {
    const guard = createSingleFlight<boolean>();
    const gate = deferred<boolean>();
    let joined = 0;
    const first = guard.run(() => gate.promise, () => { joined += 1; });
    expect(joined).toBe(0);
    const second = guard.run(() => gate.promise, () => { joined += 1; });
    expect(joined).toBe(1);
    gate.resolve(true);
    await Promise.all([first, second]);
  });

  it('完了後は新しい要求を受け付ける', async () => {
    const guard = createSingleFlight<boolean>();
    let runs = 0;
    const task = async () => { runs += 1; return true; };

    await guard.run(task);
    expect(guard.isRunning()).toBe(false);
    await guard.run(task);

    expect(runs).toBe(2);
  });

  it('失敗しても排他状態が解除され、次の要求を実行できる', async () => {
    const guard = createSingleFlight<boolean>();
    let runs = 0;
    await expect(
      guard.run(async () => { runs += 1; throw new Error('boom'); })
    ).rejects.toThrow('boom');

    expect(guard.isRunning()).toBe(false);

    await expect(guard.run(async () => { runs += 1; return true; })).resolves.toBe(true);
    expect(runs).toBe(2);
  });

  it('同期的に throw する task でも排他状態が解除される', async () => {
    const guard = createSingleFlight<boolean>();
    await expect(
      guard.run((() => { throw new Error('sync boom'); }) as () => Promise<boolean>)
    ).rejects.toThrow('sync boom');
    expect(guard.isRunning()).toBe(false);
  });

  it('進行中は isRunning が true、完了後は false', async () => {
    const guard = createSingleFlight<boolean>();
    const gate = deferred<boolean>();
    const running = guard.run(() => gate.promise);
    expect(guard.isRunning()).toBe(true);
    gate.resolve(true);
    await running;
    expect(guard.isRunning()).toBe(false);
  });
});
