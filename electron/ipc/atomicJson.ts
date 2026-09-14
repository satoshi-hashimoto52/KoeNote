/**
 * JSON ファイルの原子的な書き込み（0019）。
 *
 * 旧実装は一時ファイル名を `${target}.tmp` と**固定**していたため、
 * 書き込みが同時に走ると次の順序で必ず失敗した。
 *
 * ```
 * A: write  target.tmp
 * B: write  target.tmp             （A の内容を上書き）
 * A: rename target.tmp -> target   （tmp が消える）
 * B: rename target.tmp -> target   -> ENOENT
 * ```
 *
 * 設定画面の保存と文字起こし欄の高さ保存など、独立した経路から
 * `settings:set` が近接して呼ばれると起きる。0019 で入力音声の設定が増え、
 * 保存契機が増えたことで実際に再現した（`Error occurred in handler for 'settings:set'`）。
 *
 * 対策は 2 つ。
 *   1. 一時ファイル名を呼び出しごとに一意にする
 *   2. 同一パスへの書き込みを直列化する（到着順に適用し、最後の書き込みが残る）
 */
import { mkdir, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

/** パスごとの直列化キュー。プロセス内の同時書き込みだけを対象にする。 */
const queues = new Map<string, Promise<void>>();

let counter = 0;

function uniqueTempPath(target: string): string {
  counter += 1;
  // pid + 連番 + 乱数。同一プロセス内でも別プロセスからでも衝突しない。
  return `${target}.${process.pid}.${counter}.${Math.random().toString(36).slice(2, 8)}.tmp`;
}

async function writeOnce(target: string, data: unknown): Promise<void> {
  await mkdir(dirname(target), { recursive: true });
  const tmp = uniqueTempPath(target);
  try {
    await writeFile(tmp, JSON.stringify(data, null, 2), 'utf-8');
    await rename(tmp, target);
  } catch (error) {
    // 中途半端な一時ファイルを残さない。削除の失敗で元のエラーを隠さない。
    await unlink(tmp).catch(() => {});
    throw error;
  }
}

/**
 * `data` を JSON として `target` へ原子的に書き込む。
 *
 * 同じ `target` への呼び出しは到着順に直列化される。
 * 直前の書き込みが失敗しても後続は実行する（1 回の失敗で以後すべてが
 * 保存できなくなる方が害が大きい）。
 */
export async function writeJsonAtomic(target: string, data: unknown): Promise<void> {
  const previous = queues.get(target) ?? Promise.resolve();
  const task = previous.then(
    () => writeOnce(target, data),
    () => writeOnce(target, data)
  );
  // キューには「失敗しても次へ進む」版を積む。呼び出し元へは本来の結果を返す。
  queues.set(
    target,
    task.then(
      () => undefined,
      () => undefined
    )
  );
  try {
    await task;
  } finally {
    // 自分が最後の書き込みなら Map から外す。状態量を無限に増やさない。
    const current = queues.get(target);
    if (current !== undefined) {
      void current.then(() => {
        if (queues.get(target) === current) queues.delete(target);
      });
    }
  }
}

/** テスト用。処理待ちのパス数。書き込みが落ち着けば 0 に戻る。 */
export function pendingWriteCount(): number {
  return queues.size;
}
