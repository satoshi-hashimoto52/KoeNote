/**
 * 異常検知時の警告音。
 *
 * CSP が default-src 'self' なので音声アセットは持ち込まない。
 * WebAudio の OscillatorNode で合成すればリソース取得が発生しないため、
 * どのディレクティブにも触れない。
 */
let ctx: AudioContext | null = null;

function context(): AudioContext | null {
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return null;
  if (!ctx) ctx = new AudioCtx();
  return ctx;
}

/**
 * 録音開始ボタンのクリック内で呼び、自動再生ポリシーの「ユーザー操作」要件を満たす。
 * これを通しておかないと、いざ異常が起きたときに音が鳴らない。
 */
export function primeAlertTone(): void {
  const audio = context();
  if (audio && audio.state === 'suspended') audio.resume().catch(() => {});
}

export function playAlertTone(beeps = 3): void {
  const audio = context();
  if (!audio) return;
  if (audio.state === 'suspended') audio.resume().catch(() => {});

  const start = audio.currentTime;
  for (let i = 0; i < beeps; i += 1) {
    const at = start + i * 0.28;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = 'sine';
    osc.frequency.value = 880;
    // クリック音が出ないようエンベロープをかける。
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(0.25, at + 0.02);
    gain.gain.linearRampToValueAtTime(0, at + 0.18);
    osc.connect(gain);
    gain.connect(audio.destination);
    osc.start(at);
    osc.stop(at + 0.2);
  }
}
