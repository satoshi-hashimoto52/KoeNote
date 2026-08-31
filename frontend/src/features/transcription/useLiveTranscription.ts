import { useCallback, useEffect, useRef, useState } from 'react';
import { wsOrigin } from '../../services/api';
import { playAlertTone } from './alertTone';
import {
  NO_MARK,
  beginSession,
  isTranscriptionStalled,
  stalledForMs,
  updateProcessedMark,
  type ProcessedMark
} from './watchdog';
import {
  CAPTURE_SAMPLE_RATE,
  FRAME_SAMPLES,
  startPcmCapture,
  type CaptureFault,
  type CapturePath,
  type PcmCaptureHandle
} from './pcmCapture';
import {
  EMPTY_PROGRESS,
  ERROR_REASON_LABELS,
  type LiveAnomaly,
  type LiveErrorReason,
  type LiveProgress,
  type LiveStatus,
  type LiveWarning,
  type StartOptions
} from './liveTypes';

import {
  FALLBACK_NOTICE,
  acquireInputStream,
  resolveInputDevice,
  type ResolvedInputDevice
} from './inputDevice';

export { LIVE_PRESETS } from './liveTypes';
export type { LiveDelayMode, LiveModel, StartOptions } from './liveTypes';

/** 異常切断からの再接続。合計 15.5 秒ぶん粘る。 */
const RECONNECT_BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
/** 再接続中に保持する音声の上限。60 秒 = 約 1.9MB。超えたら古い方から捨てる。 */
const PENDING_CAP_SAMPLES = 60 * CAPTURE_SAMPLE_RATE;
/** socket に溜まってよい量。超えたら送信を控え、renderer のメモリを守る。 */
const SEND_BUFFERED_LIMIT = 4 * 1024 * 1024;

/** heartbeat は 2 秒間隔。4 回落ちたら異常と判定する。 */
const HEARTBEAT_TIMEOUT_MS = 8000;
/** worklet は 128ms ごとにフレームを出す。3 秒来なければキャプチャが死んでいる。 */
const CAPTURE_TIMEOUT_MS = 3000;
/** 文字起こしの進捗停止。無音でも processed は進むので誤検知しない。 */
const TRANSCRIPTION_STALL_MS = 60000;
/** モデル初回読み込みに数秒かかるので、開始直後は判定しない。 */
const STARTUP_GRACE_MS = 15000;
const WATCHDOG_TICK_MS = 1000;
const SESSION_FINAL_TIMEOUT_MS = 10000;
const LOG_FLUSH_MS = 500;

type ServerMessage = {
  type:
    | 'ready'
    | 'resumed'
    | 'update'
    | 'snapshot'
    | 'session_final'
    | 'heartbeat'
    | 'warning'
    | 'metrics'
    | 'error'
    | 'log';
  result_id?: string;
  session_id?: string;
  committed_delta?: string;
  committed_length_before?: number;
  committed_length?: number;
  needs_snapshot?: boolean;
  committed_text?: string;
  partial_text?: string;
  text?: string;
  message?: string;
  code?: string;
  saved_path?: string | null;
  audio_path?: string | null;
  server_total_samples?: number;
  recorded_seconds?: number;
  received_audio_seconds?: number;
  processed_audio_seconds?: number;
  lag_seconds?: number;
  dropped_seconds?: number;
  window_index?: number;
  last_audio_received_at?: string | null;
  last_transcription_at?: string | null;
  [key: string]: unknown;
};

export interface LiveState {
  committed: string;
  partial: string;
  status: LiveStatus;
  recording: boolean;
  savedPath: string | null;
  audioPath: string | null;
  deviceLabel: string;
  /** 入力デバイスをどう解決したか（0016）。開始前は null。 */
  deviceResolution: ResolvedInputDevice | null;
  capturePath: CapturePath | null;
  logText: string;
  inputLevel: number;
  progress: LiveProgress;
  anomaly: LiveAnomaly | null;
  warning: LiveWarning | null;
  droppedClientSeconds: number;
  start: (opts: StartOptions) => Promise<void>;
  stop: () => Promise<void>;
  reconnect: () => void;
  dismissAnomaly: () => void;
  dismissWarning: () => void;
  reset: () => void;
  noteBackendExit: (detail: string) => void;
  setDebug: (value: boolean) => void;
}

interface PendingFrame {
  pcm: ArrayBuffer;
  samples: number;
}

export function useLiveTranscription(): LiveState {
  const [committed, setCommitted] = useState('');
  const [partial, setPartial] = useState('');
  const [status, setStatus] = useState<LiveStatus>({ kind: 'idle' });
  const [recording, setRecording] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [audioPath, setAudioPath] = useState<string | null>(null);
  const [deviceLabel, setDeviceLabel] = useState('-');
  // 0016: どう解決したか（通知と診断ログに使う）。
  const [deviceResolution, setDeviceResolution] = useState<ResolvedInputDevice | null>(null);
  const [capturePath, setCapturePath] = useState<CapturePath | null>(null);
  const [logText, setLogText] = useState('');
  const [inputLevel, setInputLevel] = useState(0);
  const [progress, setProgress] = useState<LiveProgress>(EMPTY_PROGRESS);
  const [anomaly, setAnomaly] = useState<LiveAnomaly | null>(null);
  const [warning, setWarning] = useState<LiveWarning | null>(null);
  const [droppedClientSeconds, setDroppedClientSeconds] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<PcmCaptureHandle | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);

  const optionsRef = useRef<StartOptions | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  // Backend が落ちて再起動した場合、再接続先は「別セッション」になる（レジストリは
  // プロセス内にあるため）。そのとき画面のテキストを失わないよう、
  // 「以前のセッションから持ち越したぶん」と「今のセッションぶん」を分けて持つ。
  const carriedRef = useRef('');
  const sessionCommittedRef = useRef('');
  // 差分更新なので、同じ result_id を二度適用すると二重追記になる。この排除は必須。
  const processedIdsRef = useRef<string[]>([]);

  const pendingRef = useRef<PendingFrame[]>([]);
  const pendingSamplesRef = useRef(0);
  const sentSamplesRef = useRef(0);

  const stopIntentRef = useRef(false);
  const watchdogReasonRef = useRef<LiveErrorReason | null>(null);
  const backendExitAtRef = useRef(0);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const lastHeartbeatAtRef = useRef(0);
  const lastFrameAtRef = useRef(0);
  const lastProcessedRef = useRef<ProcessedMark>(NO_MARK);
  const watchdogTimerRef = useRef<number | null>(null);
  // 異常は 1 回だけ通知する。毎tick鳴らすと警告音とOS通知が毎秒繰り返される。
  const anomalyRaisedRef = useRef(false);
  const sessionFinalTimerRef = useRef<number | null>(null);
  const finalResolveRef = useRef<(() => void) | null>(null);

  const logLinesRef = useRef<string[]>([]);
  const logTimerRef = useRef<number | null>(null);

  // 1 行ごとに setState すると 20KB の <pre> を毎秒何度も再描画してしまう。
  // ref に溜めて一定間隔でまとめて反映する。
  const appendLog = useCallback((message: string) => {
    logLinesRef.current.push(`[${new Date().toLocaleTimeString()}] ${message}`);
    if (logTimerRef.current !== null) return;
    logTimerRef.current = window.setTimeout(() => {
      logTimerRef.current = null;
      const lines = logLinesRef.current;
      logLinesRef.current = [];
      if (lines.length === 0) return;
      setLogText((prev) => `${prev}${prev ? '\n' : ''}${lines.join('\n')}`.slice(-20000));
    }, LOG_FLUSH_MS);
  }, []);

  /** 画面に出す確定テキスト。持ち越し + 現セッション。 */
  const renderCommitted = useCallback(
    () => `${carriedRef.current}${sessionCommittedRef.current}`,
    []
  );

  const clearTimers = useCallback(() => {
    for (const ref of [watchdogTimerRef, reconnectTimerRef, sessionFinalTimerRef]) {
      if (ref.current !== null) {
        window.clearTimeout(ref.current);
        window.clearInterval(ref.current);
        ref.current = null;
      }
    }
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const teardownCapture = useCallback(async () => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    analyserRef.current = null;
    const capture = captureRef.current;
    captureRef.current = null;
    if (capture) await capture.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setInputLevel(0);
  }, []);

  // ---------------------------------------------------------------
  // 送信
  // ---------------------------------------------------------------

  const flushPending = useCallback((socket: WebSocket) => {
    while (pendingRef.current.length > 0) {
      if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > SEND_BUFFERED_LIMIT) return;
      const frame = pendingRef.current.shift();
      if (!frame) return;
      pendingSamplesRef.current -= frame.samples;
      socket.send(frame.pcm);
      sentSamplesRef.current += frame.samples;
    }
  }, []);

  const enqueueFrame = useCallback(
    (pcm: ArrayBuffer, samples: number) => {
      pendingRef.current.push({ pcm, samples });
      pendingSamplesRef.current += samples;
      // 上限を超えたら古い方から捨てる。表示中の最新音声を優先し、
      // 落ちた分は必ず数えてユーザーに伝える（録音ファイルには残っている）。
      let dropped = 0;
      while (pendingSamplesRef.current > PENDING_CAP_SAMPLES && pendingRef.current.length > 0) {
        const oldest = pendingRef.current.shift();
        if (!oldest) break;
        pendingSamplesRef.current -= oldest.samples;
        dropped += oldest.samples;
      }
      if (dropped > 0) {
        setDroppedClientSeconds((prev) => prev + dropped / CAPTURE_SAMPLE_RATE);
        appendLog(`[RT] 再接続バッファ上限のため ${(dropped / CAPTURE_SAMPLE_RATE).toFixed(1)}s を破棄しました`);
      }
    },
    [appendLog]
  );

  // ---------------------------------------------------------------
  // 異常判定
  // ---------------------------------------------------------------

  const raiseAnomaly = useCallback(
    (reason: LiveErrorReason, detail: string, recoverable: boolean) => {
      if (anomalyRaisedRef.current) return;
      anomalyRaisedRef.current = true;
      // 判定が続いても鳴り続けないよう、ウォッチドッグは止める。
      // 以降は再接続か停止のどちらかをユーザーが選ぶ。
      if (watchdogTimerRef.current !== null) {
        window.clearInterval(watchdogTimerRef.current);
        watchdogTimerRef.current = null;
      }
      setAnomaly({ reason, detail, at: Date.now(), recoverable });
      setStatus({ kind: 'error', reason, detail });
      appendLog(`[異常] ${ERROR_REASON_LABELS[reason]}: ${detail}`);
      playAlertTone();
      const options = optionsRef.current;
      window.bridge?.notifyAnomaly?.({
        reason,
        detail: `${ERROR_REASON_LABELS[reason]}\n${detail}`,
        sessionDir: options?.outputFolder ?? null,
        transcriptPath: savedPath,
        atSeconds: Math.round((Date.now() - startedAtRef.current) / 1000)
      });
    },
    [appendLog, savedPath]
  );

  const scheduleReconnect = useCallback(
    (reason: LiveErrorReason, detail: string) => {
      if (stopIntentRef.current) return;
      const attempt = reconnectAttemptRef.current;
      if (attempt >= RECONNECT_BACKOFF_MS.length) {
        raiseAnomaly('reconnect_failed', `${detail}（${attempt} 回試行しました）`, true);
        return;
      }
      reconnectAttemptRef.current = attempt + 1;
      const delay = RECONNECT_BACKOFF_MS[attempt];
      setStatus({ kind: 'reconnecting', attempt: attempt + 1, maxAttempts: RECONNECT_BACKOFF_MS.length });
      appendLog(`[RT] ${detail} -> ${delay}ms 後に再接続します (${attempt + 1}/${RECONNECT_BACKOFF_MS.length})`);
      // 再接続中もキャプチャは止めない。音声は pendingRef に溜め続ける。
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        openSocketRef.current?.(true, reason);
      }, delay);
    },
    [appendLog, raiseAnomaly]
  );

  // openSocket は自分自身を再接続で呼ぶため ref 経由にする。
  const openSocketRef = useRef<((isReconnect: boolean, reason?: LiveErrorReason) => void) | null>(null);

  const startWatchdog = useCallback(() => {
    anomalyRaisedRef.current = false;
    if (watchdogTimerRef.current !== null) window.clearInterval(watchdogTimerRef.current);
    watchdogTimerRef.current = window.setInterval(() => {
      if (stopIntentRef.current) return;
      // 壁時計で比較するので、タイマーが間引かれても検知できる。
      const now = Date.now();
      if (now - startedAtRef.current < STARTUP_GRACE_MS) return;

      if (lastFrameAtRef.current > 0 && now - lastFrameAtRef.current > CAPTURE_TIMEOUT_MS) {
        watchdogReasonRef.current = 'capture_stalled';
        raiseAnomaly(
          'capture_stalled',
          `${Math.round((now - lastFrameAtRef.current) / 1000)} 秒間マイクから音声が届いていません`,
          true
        );
        return;
      }
      if (lastHeartbeatAtRef.current > 0 && now - lastHeartbeatAtRef.current > HEARTBEAT_TIMEOUT_MS) {
        watchdogReasonRef.current = 'no_heartbeat';
        raiseAnomaly(
          'no_heartbeat',
          `${Math.round((now - lastHeartbeatAtRef.current) / 1000)} 秒間 Backend から応答がありません`,
          true
        );
        return;
      }
      // 無音でも processed_audio_seconds は進むので、これが止まるのは本当の停止。
      // セッション切替と初回進捗前の扱いは watchdog.ts で判定する（0011）。
      if (isTranscriptionStalled(lastProcessedRef.current, now, TRANSCRIPTION_STALL_MS)) {
        watchdogReasonRef.current = 'transcription_stalled';
        const stalledFor = stalledForMs(lastProcessedRef.current, now);
        raiseAnomaly('transcription_stalled', `${Math.round(stalledFor / 1000)} 秒間 文字起こしが進んでいません`, true);
      }
    }, WATCHDOG_TICK_MS);
  }, [raiseAnomaly]);

  // ---------------------------------------------------------------
  // メッセージ処理
  // ---------------------------------------------------------------

  const applyMessage = useCallback(
    (socket: WebSocket, data: ServerMessage) => {
      if (data.result_id) {
        const messageId = `${data.type}:${data.result_id}`;
        if (processedIdsRef.current.includes(messageId)) return;
        processedIdsRef.current = [...processedIdsRef.current.slice(-199), messageId];
      }

      switch (data.type) {
        case 'ready':
        case 'resumed': {
          const wantedResume = Boolean(sessionIdRef.current) && data.type === 'ready';
          if (wantedResume) {
            // 再接続を求めたのに新規セッションが返った = Backend が再起動している。
            // 既に確定した分は TXT にも残っているので、持ち越し扱いにして画面から失わない。
            carriedRef.current = renderCommitted();
            sessionCommittedRef.current = '';
            sentSamplesRef.current = 0;
            appendLog('[RT] Backend が再起動していたため新しいセッションで続行します（既存の文字起こしは保持）');
          }
          sessionIdRef.current = data.session_id ?? null;
          setSavedPath(data.saved_path ?? null);
          setAudioPath(data.audio_path ?? null);
          reconnectAttemptRef.current = 0;
          watchdogReasonRef.current = null;
          anomalyRaisedRef.current = false;
          lastHeartbeatAtRef.current = Date.now();
          setStatus({ kind: 'recording' });
          setRecording(true);
          if (data.type === 'resumed') {
            const serverSamples = Number(data.server_total_samples ?? 0);
            // サーバが持っている位置より進んでいる分は欠落なので、無音で埋めるよう伝える。
            // 絶対時刻を壁時計に合わせ続けるため。
            const firstPendingStart = sentSamplesRef.current;
            if (firstPendingStart > serverSamples) {
              socket.send(JSON.stringify({ type: 'gap', samples: firstPendingStart - serverSamples }));
            }
            sentSamplesRef.current = Math.max(sentSamplesRef.current, serverSamples);
            appendLog(`[RT] 再接続しました session=${data.session_id} server_samples=${serverSamples}`);
          } else {
            appendLog(`[RT] 接続しました session=${data.session_id}`);
          }
          flushPending(socket);
          break;
        }
        case 'snapshot': {
          sessionCommittedRef.current = data.committed_text ?? '';
          setCommitted(renderCommitted());
          setPartial(data.partial_text ?? '');
          appendLog(`[RT] 全文同期 length=${sessionCommittedRef.current.length}`);
          break;
        }
        case 'update': {
          const expected = data.committed_length_before ?? 0;
          if (sessionCommittedRef.current.length !== expected) {
            // 取りこぼしがあると差分を積めないので、全文同期を要求する。
            appendLog(
              `[RT] 差分の基準がずれています (local=${sessionCommittedRef.current.length} server=${expected})`
            );
            socket.send(JSON.stringify({ type: 'resync' }));
            break;
          }
          sessionCommittedRef.current += data.committed_delta ?? '';
          setCommitted(renderCommitted());
          setPartial(data.partial_text ?? '');
          break;
        }
        case 'session_final': {
          sessionCommittedRef.current = data.committed_text ?? data.text ?? '';
          setCommitted(renderCommitted());
          setPartial(data.partial_text ?? '');
          setSavedPath(data.saved_path ?? null);
          setAudioPath(data.audio_path ?? null);
          appendLog(
            `[RT] 確定しました length=${renderCommitted().length} ` +
              `録音=${(data.recorded_seconds ?? 0).toFixed(1)}s`
          );
          if (sessionFinalTimerRef.current !== null) {
            window.clearTimeout(sessionFinalTimerRef.current);
            sessionFinalTimerRef.current = null;
          }
          finalResolveRef.current?.();
          finalResolveRef.current = null;
          break;
        }
        case 'heartbeat': {
          lastHeartbeatAtRef.current = Date.now();
          const processed = Number(data.processed_audio_seconds ?? 0);
          lastProcessedRef.current = updateProcessedMark(
            lastProcessedRef.current,
            sessionIdRef.current,
            processed,
            Date.now()
          );
          setProgress({
            recordedSeconds: Number(data.recorded_seconds ?? 0),
            receivedAudioSeconds: Number(data.received_audio_seconds ?? 0),
            processedAudioSeconds: processed,
            lagSeconds: Number(data.lag_seconds ?? 0),
            droppedSeconds: Number(data.dropped_seconds ?? 0),
            committedLength: Number(data.committed_length ?? 0),
            windowIndex: Number(data.window_index ?? 0),
            lastAudioReceivedAt: (data.last_audio_received_at as string | null) ?? null,
            lastTranscriptionAt: (data.last_transcription_at as string | null) ?? null
          });
          break;
        }
        case 'warning': {
          setWarning({ code: String(data.code ?? 'warning'), message: data.message ?? '', at: Date.now() });
          appendLog(`[警告] ${data.code}: ${data.message}`);
          break;
        }
        case 'metrics': {
          appendLog(
            `[metrics] window=${data.window_index} ` +
              `range=${data.window_start}-${data.window_end}s ` +
              `infer=${data.inference_ms}ms lag=${data.lag_seconds}s ` +
              `committed=${data.committed_length} rms=${data.rms}`
          );
          break;
        }
        case 'error': {
          appendLog(`[RT] サーバエラー: ${data.message}`);
          watchdogReasonRef.current = 'server_error';
          break;
        }
        case 'log': {
          appendLog(data.message || 'サーバ処理中');
          break;
        }
      }
    },
    [appendLog, flushPending, renderCommitted]
  );

  // ---------------------------------------------------------------
  // 接続
  // ---------------------------------------------------------------

  const openSocket = useCallback(
    (isReconnect: boolean, _reason?: LiveErrorReason) => {
      const options = optionsRef.current;
      if (!options || stopIntentRef.current) return;

      if (!isReconnect) setStatus({ kind: 'connecting' });
      const socket = new WebSocket(`${wsOrigin()}/ws/live`);
      socketRef.current = socket;
      socket.binaryType = 'arraybuffer';

      socket.onopen = () => {
        socket.send(
          JSON.stringify({
            type: 'config',
            send_mode: 'pcm16',
            sample_rate: captureRef.current?.sampleRate ?? CAPTURE_SAMPLE_RATE,
            capture_path: captureRef.current?.path ?? null,
            model: options.model,
            delay_mode: options.delayMode,
            chunk_seconds: options.chunkSeconds,
            overlap_seconds: options.overlapSeconds,
            write_to_file: options.writeToFile,
            output_folder: options.outputFolder,
            output_filename: options.outputFilename,
            debug: Boolean(options.debug),
            ...(isReconnect && sessionIdRef.current ? { resume_session_id: sessionIdRef.current } : {})
          })
        );
      };

      socket.onmessage = (event) => {
        let data: ServerMessage;
        try {
          data = JSON.parse(event.data as string) as ServerMessage;
        } catch {
          return;
        }
        applyMessage(socket, data);
      };

      socket.onerror = () => {
        appendLog('[RT] WebSocket エラー');
        if (!watchdogReasonRef.current) watchdogReasonRef.current = 'ws_error';
      };

      socket.onclose = (event) => {
        socketRef.current = null;
        if (stopIntentRef.current) return;

        // 正常停止と異常切断を必ず区別する。close code を捨てると
        // ユーザーには「停止」と見分けがつかなくなる。
        const backendDied = Date.now() - backendExitAtRef.current < 5000;
        const reason: LiveErrorReason = backendDied
          ? 'backend_exit'
          : (watchdogReasonRef.current ?? 'ws_closed');
        const detail = `code=${event.code} reason=${event.reason || '-'} clean=${event.wasClean}`;
        appendLog(`[RT] 切断 ${detail}`);
        scheduleReconnect(reason, detail);
      };
    },
    [appendLog, applyMessage, scheduleReconnect]
  );

  openSocketRef.current = openSocket;

  // ---------------------------------------------------------------
  // 開始 / 停止
  // ---------------------------------------------------------------

  const start = useCallback(
    async (opts: StartOptions) => {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('この環境ではマイク録音を利用できません');
      }
      stopIntentRef.current = false;
      watchdogReasonRef.current = null;
      anomalyRaisedRef.current = false;
      reconnectAttemptRef.current = 0;
      backendExitAtRef.current = 0;
      sessionIdRef.current = null;
      carriedRef.current = '';
      sessionCommittedRef.current = '';
      processedIdsRef.current = [];
      pendingRef.current = [];
      pendingSamplesRef.current = 0;
      sentSamplesRef.current = 0;
      lastHeartbeatAtRef.current = 0;
      lastFrameAtRef.current = 0;
      lastProcessedRef.current = NO_MARK;
      startedAtRef.current = Date.now();
      optionsRef.current = opts;

      setCommitted('');
      setPartial('');
      setSavedPath(null);
      setAudioPath(null);
      setLogText('');
      setProgress(EMPTY_PROGRESS);
      setAnomaly(null);
      setWarning(null);
      setDroppedClientSeconds(0);
      setStatus({ kind: 'connecting' });

      // 保存済み deviceId は origin が変わると無効になる（0016）。
      // 必ず現在の一覧と照合してから使う。
      let resolved: ResolvedInputDevice;
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        resolved = resolveInputDevice(opts.deviceId ?? '', opts.deviceLabel ?? '', devices);
      } catch {
        // 列挙できなくても既定入力で開始を試みる。
        resolved = {
          ok: true,
          effectiveDeviceId: null,
          matchedBy: 'default',
          fallbackReason: null,
          notice: null,
          logSummary: 'input_device matchedBy=default fallbackReason=enumerate_failed candidates=0'
        };
      }
      if (!resolved.ok) {
        setStatus({ kind: 'idle' });
        setRecording(false);
        throw new Error(resolved.notice ?? '利用できる入力デバイスがありません');
      }
      setDeviceResolution(resolved);

      let stream: MediaStream;
      try {
        // 列挙後にデバイスが外れる競合に備え、最大 1 回だけ deviceId なしで取り直す。
        const acquired = await acquireInputStream(
          (c) => navigator.mediaDevices.getUserMedia(c),
          resolved.effectiveDeviceId
        );
        stream = acquired.stream;
        if (acquired.retried) {
          setDeviceResolution({
            ...resolved,
            effectiveDeviceId: null,
            matchedBy: 'default',
            fallbackReason: 'device_id_not_found',
            notice: FALLBACK_NOTICE,
            logSummary: 'input_device matchedBy=default fallbackReason=lost_after_enumerate candidates=0'
          });
        }
      } catch (error) {
        setStatus({ kind: 'idle' });
        setRecording(false);
        // describeGetUserMediaError が必ず非空の文言を用意する（0016）。
        throw error instanceof Error ? error : new Error('マイクの取得に失敗しました');
      }
      streamRef.current = stream;
      const track = stream.getAudioTracks()[0];
      setDeviceLabel(track?.label || '既定入力');

      const onFault = (fault: CaptureFault) => {
        const reason: LiveErrorReason =
          fault.reason === 'track_ended' || fault.reason === 'track_muted' ? 'mic_lost' : 'capture_stalled';
        watchdogReasonRef.current = reason;
        raiseAnomaly(reason, fault.detail, fault.reason !== 'track_ended');
      };

      try {
        captureRef.current = await startPcmCapture({
          stream,
          onFrame: (frame) => {
            lastFrameAtRef.current = Date.now();
            const socket = socketRef.current;
            const samples = frame.pcm.byteLength / 2;
            if (socket && socket.readyState === WebSocket.OPEN && pendingRef.current.length === 0) {
              if (socket.bufferedAmount > SEND_BUFFERED_LIMIT) {
                enqueueFrame(frame.pcm, samples);
                return;
              }
              socket.send(frame.pcm);
              sentSamplesRef.current += samples;
              return;
            }
            enqueueFrame(frame.pcm, samples);
            if (socket && socket.readyState === WebSocket.OPEN) flushPending(socket);
          },
          onFault,
          onAnalyser: (analyser) => {
            analyserRef.current = analyser;
            const buffer = new Float32Array(analyser.fftSize);
            const tick = () => {
              const node = analyserRef.current;
              if (!node) return;
              node.getFloatTimeDomainData(buffer);
              let sum = 0;
              for (let i = 0; i < buffer.length; i += 1) sum += buffer[i] * buffer[i];
              setInputLevel(Math.sqrt(sum / buffer.length));
              rafRef.current = requestAnimationFrame(tick);
            };
            rafRef.current = requestAnimationFrame(tick);
          }
        });
      } catch (error) {
        await teardownCapture();
        setStatus({ kind: 'idle' });
        setRecording(false);
        throw new Error(error instanceof Error ? error.message : '音声キャプチャの初期化に失敗しました');
      }

      setCapturePath(captureRef.current.path);
      appendLog(
        `入力デバイス=${track?.label || '不明'} 経路=${captureRef.current.path} ` +
          `sampleRate=${captureRef.current.sampleRate} frame=${FRAME_SAMPLES}samples`
      );
      if (captureRef.current.path === 'scriptprocessor') {
        appendLog('AudioWorklet を読み込めなかったため ScriptProcessorNode で動作しています');
      }

      openSocket(false);
      startWatchdog();
    },
    [appendLog, enqueueFrame, flushPending, openSocket, raiseAnomaly, startWatchdog, teardownCapture]
  );

  const stop = useCallback(async () => {
    // 最初に意図を立てる。以降の onclose は「正常停止」として扱われる。
    stopIntentRef.current = true;
    clearTimers();
    setStatus({ kind: 'stopping' });
    await teardownCapture();

    const socket = socketRef.current;
    let unconfirmed = false;
    if (socket && socket.readyState === WebSocket.OPEN) {
      // session_final を待ってから閉じる。待たずに閉じると最終確定を取りこぼす。
      const waitForFinal = new Promise<void>((resolve) => {
        finalResolveRef.current = resolve;
        sessionFinalTimerRef.current = window.setTimeout(() => {
          sessionFinalTimerRef.current = null;
          unconfirmed = true;
          resolve();
        }, SESSION_FINAL_TIMEOUT_MS);
      });
      socket.send(JSON.stringify({ type: 'stop' }));
      await waitForFinal;
      socket.close();
    } else {
      unconfirmed = Boolean(socket);
      socket?.close();
    }
    socketRef.current = null;
    finalResolveRef.current = null;
    setRecording(false);
    setStatus({ kind: 'stopped', unconfirmed });
    appendLog(unconfirmed ? 'リアルタイム文字起こしを停止しました（未確定分あり）' : 'リアルタイム文字起こしを停止しました');
  }, [appendLog, clearTimers, teardownCapture]);

  const reconnect = useCallback(() => {
    if (!optionsRef.current) return;
    setAnomaly(null);
    anomalyRaisedRef.current = false;
    stopIntentRef.current = false;
    reconnectAttemptRef.current = 0;
    watchdogReasonRef.current = null;
    lastHeartbeatAtRef.current = Date.now();
    // 旧セッションの値を持ち越さない。Backend 再起動で processed は 0 から数え直しになる（0011）。
    lastProcessedRef.current = beginSession(sessionIdRef.current, Date.now());
    startedAtRef.current = Date.now();
    appendLog('[RT] 手動で再接続します');
    socketRef.current?.close();
    socketRef.current = null;
    setStatus({ kind: 'reconnecting', attempt: 1, maxAttempts: RECONNECT_BACKOFF_MS.length });
    openSocket(true);
    startWatchdog();
  }, [appendLog, openSocket, startWatchdog]);

  const noteBackendExit = useCallback(
    (detail: string) => {
      backendExitAtRef.current = Date.now();
      watchdogReasonRef.current = 'backend_exit';
      appendLog(`[RT] Backend が終了しました: ${detail}`);
      if (recording || socketRef.current) raiseAnomaly('backend_exit', detail, true);
    },
    [appendLog, raiseAnomaly, recording]
  );

  const setDebug = useCallback((value: boolean) => {
    if (optionsRef.current) optionsRef.current.debug = value;
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'set_debug', value }));
    }
  }, []);

  const reset = useCallback(() => {
    setCommitted('');
    setPartial('');
    setSavedPath(null);
    setAudioPath(null);
    setLogText('');
    setStatus({ kind: 'idle' });
    setProgress(EMPTY_PROGRESS);
    setAnomaly(null);
    setWarning(null);
    setDroppedClientSeconds(0);
    setCapturePath(null);
    carriedRef.current = '';
    sessionCommittedRef.current = '';
    processedIdsRef.current = [];
    sessionIdRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      stopIntentRef.current = true;
      clearTimers();
      void teardownCapture();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [clearTimers, teardownCapture]);

  return {
    committed,
    partial,
    status,
    recording,
    savedPath,
    audioPath,
    deviceLabel,
    deviceResolution,
    capturePath,
    logText,
    inputLevel,
    progress,
    anomaly,
    warning,
    droppedClientSeconds,
    start,
    stop,
    reconnect,
    dismissAnomaly: () => {
      setAnomaly(null);
      anomalyRaisedRef.current = false;
    },
    dismissWarning: () => setWarning(null),
    reset,
    noteBackendExit,
    setDebug
  };
}
