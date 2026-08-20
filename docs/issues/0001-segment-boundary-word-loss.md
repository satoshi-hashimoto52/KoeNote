# Issue #0001: segment 境界でのテキスト欠落・重複

- 状態: 対応中
- 起点コミット: `e88a810c170733ec93b6d5d78b7b9bf746786805`
  （`fix: stabilize long-running transcription and add failure recovery`）
- 種別: bug（文字起こし内容の欠落）
- 影響: リアルタイム文字起こしの確定テキスト

> このリポジトリには git remote が設定されていないため、GitHub Issue の代わりに
> リポジトリ内の文書として管理する。remote を設定した後は
> `gh issue create --title ... --body-file docs/issues/0001-segment-boundary-word-loss.md`
> で移行できる。

## 根本原因

`stable_until`（確定線）が**次 window の開始時刻と数学的に一致する**。

```
window_start = window_end - chunk
stable_until = window_end - overlap
次windowの開始 = window_start + step = window_end - chunk + (chunk - overlap)
               = window_end - overlap = stable_until
```

したがって `stable_until` をまたぐ segment は

1. 確定されず保留に回る（`live_session.py:522` の else）
2. その開始が次 window より前なので、次 window では再認識できない
3. `live_session.py:535` で保留が置換され消える

`live_session.py:521` のコメント「境界をまたぐsegmentは途中切断せず、次のwindowで再評価する」は、
またぐ segment に対しては構造的に成立しない。

## 発生条件

`segment.start < stable_until < segment.end`

無音で区切られない長い発話ほど起きやすい。**息継ぎなく話し続ける場面が最悪ケース。**

## 実測

単語単位の忠実モデル（窓に含まれる音声しか文字化できない whisper を再現）による測定。

| プリセット | 欠落 | 重複 |
|---|---|---|
| 低遅延 8/2 | 1語 | 4語 |
| 標準 10/2 | 0 | 0 |
| 精度優先 12/3 | 0 | 4語 |
| 無音なし長文・標準 10/2 | **17語** | 0（pending残1） |

実音声（`say -v Kyoko`、model=small、chunk=10/overlap=2）では
「第一四半期の売上は前年比で十二パーセント増加しました。」が丸ごと欠落した。

レガシー webm 経路（`transcribe_chunk`）でも同一結果になるため、PCM 化による回帰ではない。

## 併発する重複

`flush_tail`（`live_session.py:333`、`e88a810` で追加）は `stable_until=window_end` で
1 窓処理するため、`segment.start < committed_until` の segment が保留に回り、
`finalize`（`:184`）が無条件に確定して既確定分を二重に書く。

ただし `flush_tail` は末尾の取りこぼし（標準プリセットで6語）を防ぐために必要で、
削除は後退になる。

## 採用方式: 方式G

word 単位確定 ＋ 系列アライメント重複除去 ＋ カーソル追従 ＋ 縮退再推論。

詳細は `README.md` の「リアルタイム文字起こしの確定アルゴリズム」節を参照。

## 受け入れ条件

- 全プリセット（8/2, 10/2, 12/3, 6/2）で欠落0・重複0
- 無音なし60語で欠落0・重複0
- 同一語の連続発話を維持
- drain 後に pending 0
- カーソル停滞なし
- 処理時間が step 未満
- 状態量が録音時間に比例しない
