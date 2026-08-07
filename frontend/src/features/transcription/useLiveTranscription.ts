import { useCallback, useEffect, useRef, useState } from 'react';
import { wsOrigin } from '../../services/api';

export type LiveDelayMode = 'low_latency' | 'balanced' | 'accuracy';
export type LiveModel = 'tiny' | 'base' | 'small' | 'medium';

export const LIVE_PRESETS: Record<LiveDelayMode, { chunk: number; overlap: number; label: string }> = {
  low_latency: { chunk: 8, overlap: 2, label: '低遅延' },
  balanced: { chunk: 10, overlap: 2, label: '標準' },
  accuracy: { chunk: 12, overlap: 3, label: '精度優先' }
};

type LiveMessage = {
  type: 'ready' | 'update' | 'session_final' | 'error' | 'log';
  result_id?: string;
  committed_text?: string;
  partial_text?: string;
  text?: string;
  message?: string;
  saved_path?: string | null;
};

export interface StartOptions {
  model: LiveModel;
  delayMode: LiveDelayMode;
  chunkSeconds: number;
  overlapSeconds: number;
  deviceId?: string;
  outputFolder: string;
  outputFilename: string;
  writeToFile: boolean;
}

export interface LiveState {
  committed: string;
  partial: string;
  status: string;
  recording: boolean;
  savedPath: string | null;
  deviceLabel: string;
  logText: string;
  inputLevel: number;
  start: (opts: StartOptions) => Promise<void>;
  stop: () => void;
  reset: () => void;
}

function wsStateName(ws: WebSocket | null): string {
  if (!ws) return 'NONE';
  return ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][ws.readyState] ?? String(ws.readyState);
}

export function useLiveTranscription(): LiveState {
  const [committed, setCommitted] = useState('');
  const [partial, setPartial] = useState('');
  const [status, setStatus] = useState('待機中');
  const [recording, setRecording] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [deviceLabel, setDeviceLabel] = useState('-');
  const [logText, setLogText] = useState('');
  const [inputLevel, setInputLevel] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const blobBufferRef = useRef<Blob[]>([]);
  const sendTimerRef = useRef<number | null>(null);
  const processedIdsRef = useRef<string[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(0);
  const chunkCountRef = useRef<number>(0);
  const sendCountRef = useRef<number>(0);

  const appendLog = useCallback((message: string) => {
    const time = new Date().toLocaleTimeString();
    setLogText((prev) => `${prev}${prev ? '\n' : ''}[${time}] ${message}`.slice(-20000));
  }, []);

  const cleanup = useCallback(() => {
    if (sendTimerRef.current !== null) {
      window.clearInterval(sendTimerRef.current);
      sendTimerRef.current = null;
    }
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      try {
        recorderRef.current.stop();
      } catch {
        /* noop */
      }
    }
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    analyserRef.current = null;
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    blobBufferRef.current = [];
    setInputLevel(0);
  }, []);

  useEffect(() => {
    return () => {
      cleanup();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [cleanup]);

  const reset = useCallback(() => {
    setCommitted('');
    setPartial('');
    setSavedPath(null);
    setLogText('');
    setStatus('待機中');
    processedIdsRef.current = [];
  }, []);

  const start = useCallback(
    async (opts: StartOptions) => {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('この環境ではマイク録音を利用できません');
      }
      setStatus('マイク権限確認中');
      setCommitted('');
      setPartial('');
      setSavedPath(null);
      setLogText('');
      processedIdsRef.current = [];
      blobBufferRef.current = [];

      const audioConstraints: MediaTrackConstraints = {
        ...(opts.deviceId ? { deviceId: { exact: opts.deviceId } } : {}),
        // BlackHole 等のループバック入力を音声強調で消さない。
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: { ideal: 2 }
      };

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
      } catch (error) {
        cleanup();
        setRecording(false);
        const message = error instanceof Error ? error.message : 'マイク権限の取得に失敗しました';
        setStatus(message);
        throw new Error(message);
      }
      streamRef.current = stream;
      const track = stream.getAudioTracks()[0];
      const trackSettings = track?.getSettings();
      setDeviceLabel(track?.label || '既定入力');
      appendLog(
        `入力デバイス=${track?.label || '不明'} channels=${trackSettings?.channelCount || '-'} sampleRate=${trackSettings?.sampleRate || '-'}`
      );

      // 入力レベルメーター（AnalyserNode の RMS）。Whisper とは独立に、
      // BlackHole から実際に音が来ているか（rms>0）を即座に確認できる。
      try {
        const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const audioCtx = new AudioCtx();
        audioCtxRef.current = audioCtx;
        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 2048;
        source.connect(analyser);
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
      } catch (err) {
        appendLog(`入力レベルメーター初期化失敗: ${err instanceof Error ? err.message : String(err)}`);
      }

      chunkCountRef.current = 0;
      sendCountRef.current = 0;
      startTimeRef.current = Date.now();

      const socket = new WebSocket(`${wsOrigin()}/ws/live`);
      socketRef.current = socket;
      socket.binaryType = 'arraybuffer';

      socket.onopen = () => {
        const preferred = 'audio/webm;codecs=opus';
        const fallback = 'audio/webm';
        const mimeType = MediaRecorder.isTypeSupported(preferred)
          ? preferred
          : MediaRecorder.isTypeSupported(fallback)
            ? fallback
            : '';
        socket.send(
          JSON.stringify({
            type: 'config',
            model: opts.model,
            delay_mode: opts.delayMode,
            chunk_seconds: opts.chunkSeconds,
            overlap_seconds: opts.overlapSeconds,
            write_to_file: opts.writeToFile,
            output_folder: opts.outputFolder,
            output_filename: opts.outputFilename,
            mime_type: mimeType || fallback,
            send_mode: 'full'
          })
        );

        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        recorderRef.current = recorder;
        recorder.ondataavailable = (event) => {
          chunkCountRef.current += 1;
          const elapsed = ((Date.now() - startTimeRef.current) / 1000).toFixed(1);
          if (!event.data || event.data.size === 0) {
            appendLog(`[RT] chunk=${chunkCountRef.current} size=0 (空) t=${elapsed}s ws=${wsStateName(socket)}`);
            return;
          }
          appendLog(
            `[RT] chunk=${chunkCountRef.current} size=${event.data.size} type=${event.data.type || '-'} t=${elapsed}s ws=${wsStateName(socket)}`
          );
          // 送信済み音声を無制限に保持せず、次送信で使う最新Blob群のみ保持する。
          blobBufferRef.current.push(event.data);
        };
        recorder.onerror = () => {
          setStatus('録音エラー');
          appendLog('MediaRecorder でエラーが発生しました');
        };
        // MediaRecorder.start(1000) 相当の安定経路。
        recorder.start(1000);
        appendLog(`MediaRecorder MIME=${mimeType || 'browser-default'} timeslice=1000ms`);

        sendTimerRef.current = window.setInterval(() => {
          if (socket.readyState !== WebSocket.OPEN) {
            appendLog(`[RT] 送信スキップ ws=${wsStateName(socket)} buffered_chunks=${blobBufferRef.current.length}`);
            return;
          }
          if (blobBufferRef.current.length === 0) return;
          // send_mode:'full' はヘッダ保持のため先頭からの全体を1Blobで送る。
          const blob = new Blob(blobBufferRef.current, { type: mimeType || fallback });
          if (blob.size > 0) {
            sendCountRef.current += 1;
            socket.send(blob);
            appendLog(
              `[RT] send=${sendCountRef.current} bytes=${blob.size} chunks=${blobBufferRef.current.length} ws=${wsStateName(socket)} bufferedAmount=${socket.bufferedAmount}`
            );
          }
        }, 1000);

        setRecording(true);
        setStatus('録音中');
        appendLog('リアルタイム文字起こしを開始しました');
      };

      socket.onmessage = (event) => {
        let data: LiveMessage;
        try {
          data = JSON.parse(event.data) as LiveMessage;
        } catch {
          return;
        }
        if (data.result_id) {
          const messageId = `${data.type}:${data.result_id}`;
          if (processedIdsRef.current.includes(messageId)) return;
          processedIdsRef.current = [...processedIdsRef.current.slice(-199), messageId];
        }
        if (data.type === 'ready') {
          setSavedPath(data.saved_path ?? null);
          appendLog('WebSocket ready');
        } else if (data.type === 'update') {
          // committed はスナップショット置換、partial は置換。
          appendLog(
            `[RT] recv update committed_len=${(data.committed_text ?? '').length} partial_len=${(data.partial_text ?? '').length} result_id=${data.result_id ?? '-'}`
          );
          setCommitted(data.committed_text ?? '');
          setPartial(data.partial_text ?? '');
        } else if (data.type === 'session_final') {
          appendLog(
            `[RT] recv session_final committed_len=${(data.committed_text ?? data.text ?? '').length}`
          );
          setCommitted(data.committed_text ?? data.text ?? '');
          setPartial(data.partial_text ?? '');
          setSavedPath(data.saved_path ?? null);
        } else if (data.type === 'error') {
          setStatus('エラー');
          appendLog(data.message || 'リアルタイム文字起こしでエラーが発生しました');
        } else if (data.type === 'log') {
          appendLog(data.message || 'サーバー処理中');
        }
      };

      socket.onerror = () => {
        setStatus('WebSocket接続エラー');
        appendLog('WebSocket 接続に失敗しました');
      };

      socket.onclose = () => {
        cleanup();
        setRecording(false);
        setStatus((prev) => (prev.includes('エラー') ? prev : '停止'));
      };
    },
    [appendLog, cleanup]
  );

  const stop = useCallback(() => {
    setRecording(false);
    cleanup();
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'stop' }));
      window.setTimeout(() => socket.close(), 500);
    } else {
      socket?.close();
    }
    socketRef.current = null;
    setStatus('停止');
    appendLog('リアルタイム文字起こしを停止しました');
  }, [appendLog, cleanup]);

  return { committed, partial, status, recording, savedPath, deviceLabel, logText, inputLevel, start, stop, reset };
}
