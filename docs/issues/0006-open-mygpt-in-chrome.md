# Issue #0006: マイGPT を Google Chrome で開く

- 状態: **解決**（`electron/ipc/openExternal.ts` を新設。Vitest 12 件）
- 起点コミット: `50c36e4`
- 種別: enhancement
- 影響: 「マイGPTを開く」ボタンのみ。録音・文字起こしには影響しない

## 現状（調査済み）

| 層 | 場所 | 処理 |
| --- | --- | --- |
| Renderer | `App.tsx` `openGpt()` | URL 検証 → `bridge.openExternal()` |
| preload | `preload.ts:19-20` | `ipcRenderer.invoke('shell:openExternal', url)` |
| main | `handlers.ts` | `isAllowedGptUrl()` → `shell.openExternal(url)` |

Renderer から直接 OS コマンドを実行しない構造は既にできている。
許可ホストは `ALLOWED_GPT_HOSTS = ['chatgpt.com', 'chat.openai.com']`。

## 確定要件

- Chrome があれば Chrome で開く。macOS では **bundle ID `com.google.Chrome`** を使う
- **`child_process.execFile` で `open` へ引数配列として渡す**。シェル文字列を組み立てない（`shell: true` を使わない）
- 既存の URL 許可リストを維持する
- Renderer → preload → IPC → main の構造を維持する
- Chrome 起動に失敗したら `shell.openExternal` で既定ブラウザへフォールバックし、**その旨を表示する**
- 両方失敗したらエラーを表示する
- **ボタン連打で複数タブを開かない**
- 録音状態へ影響しない

戻り値を `{ ok, opener: 'chrome' | 'default', reason? }` へ拡張する。

## 受入条件

- `execFile('open', ['-b', 'com.google.Chrome', url])` の形で呼ばれる（引数配列・`shell` 未使用）
- Chrome 起動失敗時に `shell.openExternal` が呼ばれ `opener: 'default'` を返す
- 許可外 URL は `{ ok: false, reason: 'disallowed_domain' }` で、どちらも呼ばれない
- 連打しても 1 回しか開かない
- Vitest で上記を検証する

## 実装

`electron/ipc/openExternal.ts`（新規）に純関数として置き、`handlers.ts` は薄く呼ぶ。

| 関数 | 役割 |
| --- | --- |
| `isAllowedGptUrl(url)` | 許可リスト判定（既存実装をそのまま移設。https のみ・ホスト小文字化） |
| `openInChromeMac(url)` | `execFile('open', ['-b', 'com.google.Chrome', url])`。**shell を介さない** |
| `openGptUrl(url, deps)` | 許可判定 → Chrome → 既定ブラウザの順。`deps` 注入でテスト可能 |

Renderer は `openingGptRef` で連打を止め、`opener === 'default'` のときに
「Google Chrome が見つからないため、既定のブラウザで開きました」を表示する。

## テスト（12 件）

許可リスト 2 件 / Chrome 成功・失敗・例外・両方失敗・許可外 5 件 / bundle ID 1 件 /
連打防止 2 件 / `execFile` の引数配列と失敗 2 件。
`vi.doMock('node:child_process')` で `execFile` の呼び出し形を直接検証している。
