# Issue #0012: Backend 再起動要求が二重実行される

- 状態: **解決**（`653a9db` で single-flight を追加。2026-08-28 の再試験で再起動要求・spawn とも 1 回を確認）
- 起点コミット: `ca0997b`
- 種別: 調査 / 堅牢性（排他制御の不足）
- 影響: 復旧中の Backend が停止され、復旧が遅れる。最悪の場合、復旧に失敗しうる
- 発見経緯: 2026-08-28 の C-3 / E-2 試験
- 関連: [#0010](0010-diagnostics-log-lost-on-backend-exit.md)（同じ異常経路）

## 観測事実

Electron の stdout（`backend.ts` の `pushImportantLog` は stdout へも書く）。

```
30: [backend] 終了 code=null signal=SIGKILL reason=killed_possibly_oom   ← kill -9
33: [backend] 再起動を要求されました。
34: [backend] 起動: ...
35: [backend] 再起動を要求されました。                    ← 2 回目
36: [backend] 終了 code=null signal=SIGTERM reason=crashed  ← 34 で起動した Backend を停止
37: [backend] 起動: ...
42: [backend] 再起動結果 healthy=true
45: [backend] 再起動結果 healthy=true
```

**`restartBackend()` が 2 回走り、1 回目で起動した Backend が 2 回目の
`stopBackend()` によって SIGTERM で停止されている。** 最終的には復旧した。

試験者は「ネイティブダイアログの『Backendを再起動』を 1 回だけ押し、
アプリ内ポップアップの『再接続』は押していない」と報告している。
**操作の重複と実装の重複ディスパッチのどちらであるかは未確定。**

## 副次的な観測

`36` 行目の `reason=crashed` は、`restartBackend()` 内の意図的な `stopBackend()` に対する分類である。
`stoppingIntentionally` により異常通知は抑止されているが（`backend.ts:174`）、
**ログ上は意図的停止が `crashed` と表示される**。紛らわしいので併せて見直したい。

## 調査対象

### 経路1：ネイティブダイアログ

`electron/main.ts:117-134`

```ts
alertUser(..., ['OK', 'Backendを再起動']).then((response) => {
  if (response === 1) {
    mainWindow?.webContents.send('backend:restartRequested');
  }
});
```

### 経路2：アプリ内ポップアップ

`frontend/src/App.tsx:624-632`

```tsx
onClick={live.anomaly.reason === 'backend_exit' ? restartBackendAndReconnect : live.reconnect}
```

`reason === 'backend_exit'` のとき、**この「再接続」ボタンも `restartBackendAndReconnect` を呼ぶ**。
ダイアログとポップアップの両方が同じ処理へ通じている。

### 経路3：IPC リスナの登録

`frontend/src/App.tsx:366-379`

```tsx
useEffect(() => {
  const offExit = bridge.onBackendExited(...);
  const offRestart = bridge.onBackendRestartRequested?.(() => { void restartBackendAndReconnect(); });
  return () => { offExit(); offRestart?.(); };
}, [bridge, live, restartBackendAndReconnect]);
```

`preload.ts:58-64` の登録・解除は同一参照で `removeListener` しており、実装上は正しい。
ただし **依存配列に `live` が含まれ、`live` は毎レンダーで新しいオブジェクトになる**ため、
レンダーのたびに解除と再登録が走る。イベント到着とレンダーが競合した場合の挙動を確認する必要がある。

### 経路4：`restartBackend()` 自体に排他がない

`electron/backend.ts:219-225`

```ts
export async function restartBackend(): Promise<boolean> {
  pushImportantLog('[backend] 再起動を要求されました。\n');
  await stopBackend();
  await startBackend();
  const healthy = await waitForBackend(30000);
  return healthy;
}
```

**進行中かどうかを見ていない。** 呼ばれた回数だけ `stopBackend` → `startBackend` を繰り返す。

## 修正候補

1. **`restartBackend()` に排他ガードを入れる。**
   進行中の Promise を保持し、再入時は同じ Promise を返す（重複起動しない）
2. Renderer 側でも `restartBackendAndReconnect` の多重実行を防ぐ（実行中フラグ＋ボタン disabled）
3. ダイアログとポップアップのどちらか一方に経路を集約する。
   ネイティブダイアログを出したら、アプリ内ポップアップの「再接続」は
   `backend_exit` のとき無効化する等
4. `useEffect` の依存配列から `live` を外す（必要な関数のみ参照する）
5. `classifyExit` が意図的停止を `crashed` と分類しないようにする

**排他は main 側（案1）を主とすべき。** Renderer 側の対策だけでは、
複数経路や将来の呼び出し追加に対して漏れる。

## 必要なテスト

- `restartBackend()` を連続 2 回呼んでも、Backend の起動が 1 回だけであること
- 進行中の再起動が完了するまで、2 回目の呼び出しが同じ結果を待つこと
- 再起動中にさらに Backend が死んだ場合の挙動が定義されていること
- ダイアログとポップアップの両方を押しても二重起動しないこと
- 正常系（1 回の要求）で従来どおり復旧すること
- 意図的停止がログ上 `crashed` と表示されないこと

## 注意

A-1 で 68 分の安定性を確認した長時間処理アルゴリズムには触れない。
本 Issue は Backend のライフサイクル管理と異常時 UI の経路に閉じる。
