/**
 * 開始／停止を 1 つに統合したボタンの状態（0015）。
 *
 * 見た目だけを統合すると、処理中や異常時に誤った操作を呼びかねない。
 * どの状態でどちらの処理を呼ぶかをここで決め、UI はその結果を描くだけにする。
 */

export type RecordAction = 'start' | 'stop' | 'none';

export interface RecordButtonState {
  label: string;
  /** 押したときに呼ぶ処理。`none` は押せない状態。 */
  action: RecordAction;
  disabled: boolean;
  /** ボタンの見た目。`danger` は赤系。 */
  tone: 'primary' | 'danger';
  /**
   * スクリーンリーダーと title 属性で使う説明。
   * 画面上のラベルは短縮するので、ここで役割を具体的に書く（0015）。
   */
  ariaLabel: string;
}

export interface RecordButtonInput {
  /** 録音中か（WS 接続後）。 */
  recording: boolean;
  /** 開始処理中（接続確立まで）。 */
  starting: boolean;
  /** 停止処理中（最終確定まで）。 */
  finalizing: boolean;
  /** 異常が発生してポップアップが出ている。 */
  anomaly: boolean;
}

/**
 * 状態から表示と呼び出す処理を決める。
 *
 * 異常発生中は「通常停止」と混同させない。録音状態は続いているので
 * ラベルは停止のままにし、復旧操作は異常ポップアップ側に任せる。
 * 開始ボタンへ戻して異常を隠すことはしない。
 */
export function resolveRecordButton(input: RecordButtonInput): RecordButtonState {
  const { recording, starting, finalizing, anomaly } = input;

  if (finalizing) {
    return {
      label: '停止中…',
      action: 'none',
      disabled: true,
      tone: 'danger',
      ariaLabel: '文字起こしを停止しています'
    };
  }
  if (starting) {
    return {
      label: '開始中…',
      action: 'none',
      disabled: true,
      tone: 'primary',
      ariaLabel: '文字起こしを開始しています'
    };
  }
  if (recording) {
    return {
      label: '停止',
      action: 'stop',
      disabled: false,
      tone: 'danger',
      ariaLabel: anomaly
        ? '文字起こしを停止して保存（異常が発生しています）'
        : '文字起こしを停止'
    };
  }
  if (anomaly) {
    // 録音は止まっているが異常が未解決。開始で上書きせず、保存へ誘導する。
    // ラベルは「停止して保存」。異常ポップアップの同名の操作と対応させる。
    return {
      label: '停止して保存',
      action: 'stop',
      disabled: false,
      tone: 'danger',
      ariaLabel: '異常が発生しています。録音を終了して保存'
    };
  }
  return {
    label: '開始',
    action: 'start',
    disabled: false,
    tone: 'primary',
    ariaLabel: '文字起こしを開始'
  };
}
