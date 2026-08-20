/**
 * realtime 文字起こし用の PCM16 キャプチャ。
 *
 * MediaRecorder(webm/opus) をやめて 16kHz PCM を直接取り出す。理由:
 *  - 送信量が経過時間に依存しなくなる（32KB/秒 固定）
 *  - リアルタイム経路から ffmpeg を排除できる
 *  - 送信がタイマー駆動ではなくオーディオスレッド駆動になり、ウィンドウが
 *    隠れてもタイマー間引きで止まらない
 */

export const CAPTURE_SAMPLE_RATE = 16000;
/** 1 フレーム = 2048 サンプル = 128ms = 4KB。uvicorn の上限より遥かに小さい。 */
export const FRAME_SAMPLES = 2048;
const SCRIPT_PROCESSOR_BUFFER = 4096;

export type CapturePath = 'audioworklet' | 'scriptprocessor';

export interface PcmFrame {
  /** PCM16LE mono。転送済みなので受け取り側が所有する。 */
  pcm: ArrayBuffer;
  /** このフレーム末尾時点の累計サンプル数（絶対時刻の基準）。 */
  totalSamples: number;
}

export interface CaptureFault {
  reason: 'context_suspended' | 'track_ended' | 'track_muted' | 'worklet_error';
  detail: string;
}

export interface PcmCaptureHandle {
  path: CapturePath;
  sampleRate: number;
  stop: () => Promise<void>;
  /** 実際に取り込めた累計サンプル数。サーバ側との差分検出に使う。 */
  totalSamples: () => number;
}

export interface StartCaptureOptions {
  stream: MediaStream;
  onFrame: (frame: PcmFrame) => void;
  onFault: (fault: CaptureFault) => void;
  /** レベルメーター用。キャプチャと同じ AudioContext を共有する。 */
  onAnalyser?: (analyser: AnalyserNode) => void;
}

// AudioWorklet のモジュールは blob: URL から読み込む。
// パッケージ版は file:// で動くため、アセットパス方式では module fetch が CORS で弾かれる。
// blob: はドキュメント origin を継承するので dev と packaged で同一の経路になる。
const WORKLET_SOURCE = `
class BridgelogPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(${FRAME_SAMPLES});
    this.filled = 0;
    this.total = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i += 1) {
        let sample = channel[i];
        if (sample > 1) sample = 1;
        else if (sample < -1) sample = -1;
        this.buffer[this.filled] = sample < 0 ? sample * 32768 : sample * 32767;
        this.filled += 1;
        this.total += 1;
        if (this.filled === ${FRAME_SAMPLES}) {
          const out = this.buffer;
          this.buffer = new Int16Array(${FRAME_SAMPLES});
          this.filled = 0;
          this.port.postMessage({ pcm: out.buffer, totalSamples: this.total }, [out.buffer]);
        }
      }
    }
    // 入力が途切れてもノードを生かし続ける。
    return true;
  }
}
registerProcessor('bridgelog-pcm-capture', BridgelogPcmCapture);
`;

function floatToPcm16(input: Float32Array): Int16Array<ArrayBuffer> {
  const out = new Int16Array(new ArrayBuffer(input.length * 2));
  for (let i = 0; i < input.length; i += 1) {
    let sample = input[i];
    if (sample > 1) sample = 1;
    else if (sample < -1) sample = -1;
    out[i] = sample < 0 ? sample * 32768 : sample * 32767;
  }
  return out;
}

export async function startPcmCapture(options: StartCaptureOptions): Promise<PcmCaptureHandle> {
  const { stream, onFrame, onFault, onAnalyser } = options;
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AudioCtx({ sampleRate: CAPTURE_SAMPLE_RATE, latencyHint: 'playback' });
  await ctx.resume();

  const source = ctx.createMediaStreamSource(stream);

  // レベルメーターは同じ context に相乗りさせる（旧実装は別 context を作っていた）。
  if (onAnalyser) {
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    onAnalyser(analyser);
  }

  // Chromium のグラフは destination からの pull 駆動。下流が無いノードは確実に
  // レンダリングされないため、gain=0 で destination まで必ず繋ぐ。
  // 併せてオーディオデバイスが開いたままになり、renderer の keep-awake にもなる。
  const mute = ctx.createGain();
  mute.gain.value = 0;
  mute.connect(ctx.destination);

  let totalSamples = 0;
  let stopped = false;
  let path: CapturePath = 'audioworklet';
  let node: AudioNode;
  let blobUrl: string | null = null;

  try {
    blobUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'text/javascript' }));
    await ctx.audioWorklet.addModule(blobUrl);
    const worklet = new AudioWorkletNode(ctx, 'bridgelog-pcm-capture');
    worklet.port.onmessage = (event: MessageEvent<PcmFrame>) => {
      if (stopped) return;
      totalSamples = event.data.totalSamples;
      onFrame(event.data);
    };
    worklet.onprocessorerror = () => {
      onFault({ reason: 'worklet_error', detail: 'AudioWorklet の処理が停止しました' });
    };
    node = worklet;
  } catch (error) {
    // モジュール読み込みが失敗しても録音は続けられるようにする。
    // ScriptProcessorNode はメインスレッド動作で deprecated なので主経路にはしない。
    path = 'scriptprocessor';
    const processor = ctx.createScriptProcessor(SCRIPT_PROCESSOR_BUFFER, 1, 1);
    processor.onaudioprocess = (event) => {
      if (stopped) return;
      const pcm = floatToPcm16(event.inputBuffer.getChannelData(0));
      totalSamples += pcm.length;
      onFrame({ pcm: pcm.buffer, totalSamples });
    };
    node = processor;
  } finally {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
  }

  source.connect(node);
  node.connect(mute);

  // 「勝手に止まった」の独立した原因: context が suspend される / 入力デバイスが消える。
  ctx.onstatechange = () => {
    if (stopped || ctx.state === 'running') return;
    ctx.resume().catch(() => {
      onFault({ reason: 'context_suspended', detail: `AudioContext が ${ctx.state} になりました` });
    });
  };
  const track = stream.getAudioTracks()[0];
  if (track) {
    track.onended = () => {
      if (!stopped) onFault({ reason: 'track_ended', detail: '入力デバイスが切断されました' });
    };
    track.onmute = () => {
      if (!stopped) onFault({ reason: 'track_muted', detail: '入力デバイスが無音になりました' });
    };
  }

  return {
    path,
    sampleRate: ctx.sampleRate,
    totalSamples: () => totalSamples,
    stop: async () => {
      stopped = true;
      ctx.onstatechange = null;
      if (track) {
        track.onended = null;
        track.onmute = null;
      }
      try {
        source.disconnect();
        node.disconnect();
        mute.disconnect();
      } catch {
        /* noop */
      }
      if ('port' in node) (node as AudioWorkletNode).port.onmessage = null;
      await ctx.close().catch(() => {});
    }
  };
}
