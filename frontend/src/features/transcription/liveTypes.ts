export type LiveDelayMode = 'low_latency' | 'balanced' | 'accuracy';
export type LiveModel = 'tiny' | 'base' | 'small' | 'medium';

export const LIVE_PRESETS: Record<LiveDelayMode, { chunk: number; overlap: number; label: string }> = {
  low_latency: { chunk: 8, overlap: 2, label: '低遅延' },
  balanced: { chunk: 10, overlap: 2, label: '標準' },
  accuracy: { chunk: 12, overlap: 3, label: '精度優先' }
};

/** 異常停止の理由。正常停止と必ず区別できるようにするための列挙。 */
export type LiveErrorReason =
  | 'backend_exit'
  | 'no_heartbeat'
  | 'capture_stalled'
  | 'transcription_stalled'
  | 'mic_lost'
  | 'ws_closed'
  | 'ws_error'
  | 'server_error'
  | 'reconnect_failed';

export const ERROR_REASON_LABELS: Record<LiveErrorReason, string> = {
  backend_exit: 'Backend が異常終了しました',
  no_heartbeat: 'Backend から応答がありません',
  capture_stalled: 'マイク入力が停止しました',
  transcription_stalled: '文字起こしが進んでいません',
  mic_lost: '入力デバイスが切断されました',
  ws_closed: '接続が切断されました',
  ws_error: '接続エラーが発生しました',
  server_error: 'Backend でエラーが発生しました',
  reconnect_failed: '再接続に失敗しました'
};

/** UI へ常時表示する状態。場当たり的な文字列ではなく判別可能な形にする。 */
export type LiveStatus =
  | { kind: 'idle' }
  | { kind: 'connecting' }
  | { kind: 'recording' }
  | { kind: 'reconnecting'; attempt: number; maxAttempts: number }
  | { kind: 'stopping' }
  | { kind: 'stopped'; unconfirmed: boolean }
  | { kind: 'error'; reason: LiveErrorReason; detail: string; code?: number };

export function statusLabel(status: LiveStatus): string {
  switch (status.kind) {
    case 'idle':
      return '待機中';
    case 'connecting':
      return '接続中';
    case 'recording':
      return '録音中';
    case 'reconnecting':
      return `再接続中 (${status.attempt}/${status.maxAttempts})`;
    case 'stopping':
      return '確定処理中…';
    case 'stopped':
      return status.unconfirmed ? '停止（未確定分あり）' : '停止（正常）';
    case 'error':
      return `エラー: ${ERROR_REASON_LABELS[status.reason]}`;
  }
}

export function statusTone(status: LiveStatus): 'idle' | 'recording' | 'reconnecting' | 'done' | 'error' {
  switch (status.kind) {
    case 'idle':
      return 'idle';
    case 'connecting':
    case 'recording':
    case 'stopping':
      return 'recording';
    case 'reconnecting':
      return 'reconnecting';
    case 'stopped':
      return status.unconfirmed ? 'error' : 'done';
    case 'error':
      return 'error';
  }
}

export interface LiveAnomaly {
  reason: LiveErrorReason;
  detail: string;
  at: number;
  /** 再接続を試せる状態か（不可なら「録音を終了して保存」のみ）。 */
  recoverable: boolean;
}

export interface LiveWarning {
  code: string;
  message: string;
  at: number;
}

/** heartbeat から受け取るサーバ側の進捗。UI に常時表示する。 */
export interface LiveProgress {
  recordedSeconds: number;
  receivedAudioSeconds: number;
  processedAudioSeconds: number;
  lagSeconds: number;
  droppedSeconds: number;
  committedLength: number;
  windowIndex: number;
  lastAudioReceivedAt: string | null;
  lastTranscriptionAt: string | null;
}

export const EMPTY_PROGRESS: LiveProgress = {
  recordedSeconds: 0,
  receivedAudioSeconds: 0,
  processedAudioSeconds: 0,
  lagSeconds: 0,
  droppedSeconds: 0,
  committedLength: 0,
  windowIndex: 0,
  lastAudioReceivedAt: null,
  lastTranscriptionAt: null
};

export interface StartOptions {
  model: LiveModel;
  delayMode: LiveDelayMode;
  chunkSeconds: number;
  overlapSeconds: number;
  deviceId?: string;
  /** 保存時のデバイス名。deviceId が無効な場合の再解決に使う（0016）。 */
  deviceLabel?: string;
  outputFolder: string;
  outputFilename: string;
  writeToFile: boolean;
  debug?: boolean;
}
