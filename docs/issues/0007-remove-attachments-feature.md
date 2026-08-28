# Issue #0007: 「資料」機能を削除する

- 状態: **解決**（4 層すべてから削除。Backend 5 件 + Vitest 6 件のテスト）
- 起点コミット: `50c36e4`
- 種別: removal
- 影響: Renderer / preload / main / Backend の 4 層

## 現状（調査済み）

資料を参照するファイルと件数。

| ファイル | 参照 | 資料専用か |
| --- | --- | --- |
| `frontend/src/App.tsx` | 26 | 大半が専用 |
| `frontend/src/services/api.ts` | 11 | `updateAttachments` は専用。`buildRequestText` は要判断 |
| `backend/services/session_store.py` | 11 | `write_attachments` / `ATTACHMENTS_FILENAME` は専用 |
| `backend/routes/session.py` | 9 | `POST /api/session/attachments` は専用 |
| `frontend/src/styles.css` | 4 | `.attachments` 系 3 セレクタ |
| `electron/ipc/handlers.ts` | 2 | `dialog:pickAttachments` / `ATTACHMENT_FILTERS` |
| `electron/preload.ts` | 1 | `pickAttachments` |
| `frontend/src/types/bridge.ts` | 1 | `pickAttachments()` |

Backend のテストに資料の参照はない。設定 `bridgelog-settings.json` にも資料項目はない。

## 確定方針

削除する。

- 資料 UI、専用 state / props / 型 / イベント
- `pickAttachments` と専用 IPC
- 資料専用の Backend API と保存処理
- 資料専用 CSS と文書
- **新規セッションでは `attachments` キーを出力しない**
- create API と内部引数から `attachments` を削除

**削除しない（共用・既存データ）**

`pickFolder` / `dialog:pickFolder` / `pathExists` / `pickAudioFile` / `AUDIO_FILTERS` /
保存先選択 / `gptUrl` / `revealInFinder` / セッション作成の中核処理

既存セッションの `attachments` キーと `attachments.json` は**削除・移行・書き換えしない**。
旧セッションに `attachments` があってもエラーにしない。**物理削除しない。**

## 依頼文テンプレート

- 既定テンプレートから資料関連の文章と `{attachment_names}` を削除する
- **保存済みの旧 `requestTemplate` に `{attachment_names}` が残っている場合は空文字へ置換する互換処理だけ残す**
- 互換処理には「旧テンプレート互換」とコメントする
- ユーザー設定を初期化しない

## 受入条件

- 資料関連の識別子が `rg` で残らない（共用処理を除く）
- 新規セッションの `session.json` に `attachments` キーがない
- 旧セッション（`attachments` あり）を読んでもエラーにならない
- 旧テンプレートの `{attachment_names}` が空文字へ置換される
- Backend テストが全件通る

## 削除した内容

| 層 | 削除 |
| --- | --- |
| Renderer | `Attachment` 型 / `attachments` state / `addAttachments` / `removeAttachment` / `verifyAttachments` と `useEffect` / 資料 UI セクション / create の `attachments` 引数 |
| preload | `pickAttachments` |
| main | `dialog:pickAttachments` ハンドラ / `ATTACHMENT_FILTERS` |
| 型 | `bridge.ts` の `pickAttachments()` / `api.ts` の `attachments` `attachments_json_path` |
| API | `updateAttachments()` / `POST /api/session/attachments` |
| Backend | `ATTACHMENTS_FILENAME` / `write_attachments()` / `create_meeting_directory` の `attachments` 引数 / `session.json` の `attachments` キー / `attachments_json_path` |
| CSS | `.attachments` 系 3 セレクタ（40 行） |
| 文書 | README の資料記述 4 箇所 |

## 維持した互換処理

- **既存セッションの `attachments` キーと `attachments.json` は削除・移行・書き換えしない。**
  旧セッションを読んでも `finalize` してもエラーにならず、値も保持される
- **依頼文の旧テンプレート互換**: 保存済み `requestTemplate` に `{attachment_names}` が
  残っている場合、そのまま GPT へ渡さないよう**空文字へ置換する**。
  ユーザー設定そのものは書き換えない

```ts
// 旧テンプレート互換: 資料機能の削除前に保存された requestTemplate に
// {attachment_names} が残っていることがある。そのまま GPT へ渡さないよう空文字にする。
// ユーザー設定は書き換えない（0007）。
.replace(/\{attachment_names\}\n?/g, '')
```

## 削除しなかった共用処理

`pickFolder` / `dialog:pickFolder` / `pathExists` / `pickAudioFile` / `AUDIO_FILTERS` /
`gptUrl` / `revealInFinder` / `create_meeting_directory`（中核）/ `baseName`（保存パス表示と ffmpeg 表示で使用）。
参照検索でいずれも資料以外の用途があることを確認した。

## テスト

- `backend/tests/test_attachments_removed.py`（5 件）: 新規 `session.json` に `attachments` キーがない /
  `attachments.json` を作らない / 引数なしで作成できる / **旧セッションを読んでも finalize しても壊れない** /
  `write_attachments` と `ATTACHMENTS_FILENAME` が存在しない
- `frontend/src/services/requestText.test.ts`（6 件）: 既定テンプレートに資料文言なし /
  保存済みテンプレート優先 / **旧テンプレートの `{attachment_names}` を空文字へ置換** / 複数出現 / 空文字 / 未指定
