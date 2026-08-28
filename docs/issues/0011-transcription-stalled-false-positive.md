# Issue #0011: transcription_stalled の疑わしい発火を調査する

- 状態: 未調査
- 起点コミット: `ca0997b`
- 種別: 調査（誤検知の疑い。bug と断定しない）
- 影響: 誤検知なら、正常な録音中に警告音・OS 通知・ポップアップが出て録音が中断されうる
- 発見経緯: 2026-08-28 の C-3 / E-2 試験中に別セッションで観測
- 関連: 受け入れ試験 B-1（同じウォッチドッグの判定）

## 観測事実

セッション `20260828_20260828_02`。

```
started_at: 2026-08-28T09:41:33+09:00
ended_at:   2026-08-28T09:42:46+09:00   status: done
```

`diagnostics.log`

```
[2026-08-28T09:42:35+09:00] [中断] 9:42:35 文字起こしが異常終了しました
  (reason=transcription_stalled) 60 秒間 文字起こしが進んでいません
```

| 事実 | 値 |
| --- | --- |
| 発火までの経過 | 録音開始から **約 62 秒**（閾値 60 秒とほぼ一致） |
| `transcript.txt` | `[中断]` 行の **前後どちらにも文字起こしテキストが存在する** |
| wav | 66.56 秒（16,000 Hz / 1ch / Int16） |

**文字起こしが進んでいたにもかかわらず「60 秒間進んでいません」と判定された可能性がある。**
実際の停止だったのか誤検知だったのかは、この記録だけでは確定できない。

## 調査対象

判定は `frontend/src/features/transcription/useLiveTranscription.ts:332-336`。

```ts
const stalledFor = now - lastProcessedRef.current.at;
if (lastProcessedRef.current.at > 0 && stalledFor > TRANSCRIPTION_STALL_MS) { ... }
```

`lastProcessedRef` の更新は同ファイル `:432-436`。

```ts
const processed = Number(data.processed_audio_seconds ?? 0);
if (processed > lastProcessedRef.current.value) {
  lastProcessedRef.current = { value: processed, at: Date.now() };
} else if (lastProcessedRef.current.at === 0) {
  lastProcessedRef.current = { value: processed, at: Date.now() };
}
```

**`at` は `processed` が増えたときだけ更新される。**

### 仮説A：セッション跨ぎで値が持ち越される

`reconnect()`（`:712`）は値を持ち越して時刻だけ更新する。

```ts
lastProcessedRef.current = { value: lastProcessedRef.current.value, at: Date.now() };
```

Backend が再起動すると**新しい LiveSession になり `processed_audio_seconds` は 0 から始まる**。
持ち越した `value`（前セッションの大きな値）を新セッションの小さな `processed` が超えられず、
`at` が更新されないまま 60 秒経過して発火する、という筋道が成立しうる。

`start()`（`:570`）では `{ value: 0, at: 0 }` にリセットされるため、
**通常の新規録音では起きない。再接続・Backend 再起動を経た場合に限る。**

本件のセッションは Backend 再起動（09:41:03）の直後に開始されており、
再接続経路を通ったかどうかの確認が必要。

### 仮説B：実際に推論が 60 秒進まなかった

Backend 再起動直後でモデルのロードが走り、推論が遅れた可能性。
`[中断]` 行の後にテキストが続いているため、遅れて追いついたとも読める。

## 追跡すべき値の経路

| 値 | 生成 | 伝達 | 消費 |
| --- | --- | --- | --- |
| `processed_samples` | `live_session.py:610`（`_process_window`。確定テキストの有無に依存しない） | — | — |
| `processed_audio_seconds` | `live_session.py:611` | `progress_snapshot`（`:268-274`）→ heartbeat | Renderer `:431` |
| `last_transcription_at` | `live_session.py:612` | `progress_snapshot` | UI 表示 |
| `lastProcessedRef` | Renderer `:432-436` / `:570` / `:712` | — | ウォッチドッグ `:332-336` |

**セッションが切り替わったときに `lastProcessedRef` をどう扱うべきかが論点。**

## 確認方法

1. Backend を `kill -9` → 再起動 → 再接続し、**60 秒以上録音を継続**して発火するか
2. 発火した場合、診断ログの「処理済み」が実際に増えているかを同時に目視
3. 通常の新規録音（再接続なし）では発火しないことを対照確認

## 修正候補（原因確定後）

- 新しい `session_id` を受け取ったら `lastProcessedRef` を `{ value: 0, at: Date.now() }` にリセットする
- `processed` が減少した場合（＝セッションが変わった場合）も `at` を更新する
- 判定を「値の増加」ではなく「heartbeat の受信」と「値の非減少」で行う

## B-1 への影響

B-1（90 秒無音で誤検知しない）は 2026-08-26 に合格している。
本件は**再接続を経た場合の別経路**である可能性が高く、B-1 の合格は取り消さない。
ただし同じウォッチドッグの判定であるため、原因確定後に B-1 の前提を再確認すること。
