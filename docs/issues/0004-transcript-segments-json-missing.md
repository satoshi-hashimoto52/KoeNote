# Issue #0004: session.json の segments_path が指すファイルが生成されない

- 状態: **解決** — [#0019](0019-input-gain-calibration-and-low-volume-detection.md) へ統合して実装
- 起点コミット: `ca0997b`（この時点で再現。導入時期は未特定）
- 種別: 参照整合性の疑い（仕様未確定のため bug と断定しない）
- 影響: `session.json` を読む外部処理・将来の機能
- 発見経緯: A-1 の保存データ確認中に気付いた。長時間録音の修正とは無関係

## 概要

リアルタイムセッションが書く `session.json` には次のフィールドがある。

```json
"segments_path": "transcript_segments.json"
```

しかし `transcript_segments.json` はセッションフォルダに生成されない。

## 再現状況

2026-08-25 の 3 セッションすべてで再現した。

| セッション | 録音 | status | `transcript_segments.json` |
| --- | --- | --- | --- |
| `20260825_テスト` | 38 分 51 秒 | `done` | なし |
| `20260825_テスト_02` | 26 分 20 秒 | `recording`（強制終了） | なし |
| `20260825_20260825_テスト_03` | 68 分 00 秒 | `done` | なし |

正常停止（`done`）でも生成されない。異常終了だけの問題ではない。

## 確定していないこと

次のどちらなのか判断できていない。

1. **リアルタイム経路では segments を保存しない仕様**であり、
   `segments_path` はファイル文字起こし（File Trans）経路と共通の
   メタデータ構造として書かれているだけ
2. **生成漏れ**であり、書き出すべきものが書かれていない

`session.json` に存在しないファイルへのパスが書かれている状態そのものは、
どちらの結論でも整合性の問題として残る。

## 調査すべき点

- `session_store` が `segments_path` を書く条件と、実際に書き出す経路の有無
- ファイル文字起こし経路では生成されるのか
- `segments_path` を読む側が存在するか（現状の消費者の有無）
- 期待仕様: リアルタイムでも生成すべきか、`segments_path` を省略すべきか、
  生成しない場合に `null` を入れるべきか

## 修正候補（仕様確定後）

- 生成する場合: 確定 word のタイムスタンプ付き segments を保存する。
  ただし 68 分で 27,703 文字規模になるため、ファイルサイズと書き込み頻度の設計が必要
- 生成しない場合: リアルタイムセッションでは `segments_path` を書かない、
  または明示的に `null` にする

## 必要なテスト

- リアルタイムセッションの `session.json` に、実在しないパスが書かれていない
- ファイル文字起こし経路での `segments_path` の扱いが定義どおり
- 既存の `session.json` を読む処理が壊れない（後方互換）

## 注意

**今回の長時間録音・末尾回収の修正には混ぜない。** 独立した Issue として扱う。

---

## 結論（2026-09-08 / #0019 へ統合）

`transcript_segments.json` は **#0019「入力音量の自動補正・事前テスト・低音量検知」**
の副原因3として調査・実装した。#0019 の障害解析と救済処理で
**タイムスタンプ付きセグメントが決定的に有効だった**ため、
本 Issue を単独で進めず #0019 に統合した。

追跡先: [`docs/issues/0019-input-gain-calibration-and-low-volume-detection.md`](0019-input-gain-calibration-and-low-volume-detection.md)
（「副原因3」と「9. transcript_segments.json」の節）

### 「確定していないこと」への回答

本 Issue が挙げた 2 択のうち、**2.（生成漏れ）** で確定した。

| # | 候補 | 判定 |
|---|---|---|
| 1 | realtime では segments を保存しない仕様 | **否**。File Trans 経路は `services/exporter.py:write_segments_json` で `<timestamp>_segments.json` を既に出力しており、「保存しない仕様」ではない |
| 2 | 生成漏れ | **是**。`session_store.py` は `SEGMENTS_FILENAME` を定義して `session.json` に書くだけで、realtime 経路に書き出すコードが存在しなかった |

### 「調査すべき点」への回答

| 調査項目 | 結果 |
|---|---|
| `session_store` が `segments_path` を書く条件と、実際に書き出す経路の有無 | `create_meeting_directory()` が無条件に書く。realtime 側に書き出す実装は**無かった** |
| ファイル文字起こし経路では生成されるのか | **される**（`exporter.export_transcription_files` → `write_segments_json`）。ファイル名は `<job_timestamp>_segments.json` で realtime とは別系統 |
| `segments_path` を読む側が存在するか | 実装上の消費者は無し。`frontend/src/services/api.ts` の `CreatedSession` 型が受け取るだけ。ただし**事後解析での価値が #0019 で実証された**ため、生成する方を採った |
| 期待仕様 | **realtime でも生成する**。`segments_path` の省略・`null` 化は採らない |

### 実装（#0019 に含む）

`backend/services/segments_writer.py` を新設した。

- 確定のたび `transcript_segments.jsonl` へ **1 行 1 JSON** で追記（O(1)）
- 20 行ごとに `fsync`。強制終了時の欠損はここまでに収まる
- セッション確定時に `transcript_segments.json`（配列形式）へまとめ直す
- `rebuild_from_jsonl()` で、`finalize()` を呼べずに落ちたセッションからも復旧できる
- 壊れた行（強制終了で途中まで書かれた最終行など）は捨てて読める分だけ復元する

本 Issue が懸念した「68 分で 27,703 文字規模になるためファイルサイズと
書き込み頻度の設計が必要」は、**全件をメモリに保持しない逐次追記**で解決した。
確定は word 単位だが 1 語 1 行では実用にならないため、
**1 回の確定バッチ = 1 レコード**とし、`transcript.txt` への追記単位と一致させている
（2 つのファイルの内容がずれない）。

### 「必要なテスト」への対応

| 本 Issue が求めたテスト | 実装したテスト |
|---|---|
| realtime の `session.json` に実在しないパスが書かれていない | `backend/tests/test_live_ws_raw_audio.py::test_segments_json_is_written`（実 WS 経路で実ファイルの存在を確認） |
| 既存の `session.json` を読む処理が壊れない（後方互換） | `session.json` のスキーマは変更していない。`segments_path` の値も従来どおり `"transcript_segments.json"` |
| 宣言と実ファイル名の一致 | `backend/tests/test_segments_writer.py::SessionStoreDeclarationTest` |
| 逐次保存・復旧・メモリ非保持 | `backend/tests/test_segments_writer.py`（12 件） |

> ファイル文字起こし経路の `segments_path` の扱いは**変更していない**。
> realtime と File Trans は別系統のままとする。

### 注意書きについて

本 Issue 末尾の「今回の長時間録音・末尾回収の修正には混ぜない」は、
`ca0997b` 当時の #0001〜#0003 に対する注意である。
その修正群とは混ぜておらず、#0019 という別の Issue として実装した。
