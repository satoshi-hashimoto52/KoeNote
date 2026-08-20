// AudioWorklet 検証用の使い捨てスクリプト。
// realtime の PCM キャプチャは AudioWorklet を blob: URL から読み込む。
// パッケージ後は file:// + CSP 下で動くため、その条件を本番と同じ形で再現して確認する。
// 音源には OscillatorNode を使うのでマイク権限は不要。
const { app, BrowserWindow } = require('electron');
const path = require('path');

const INDEX = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 800,
    height: 600,
    show: false,
    webPreferences: { offscreen: true }
  });
  await win.loadFile(INDEX);
  await sleep(600);

  const result = await win.webContents.executeJavaScript(`(async () => {
    const out = { origin: location.origin, href: location.href.slice(0, 60) };
    const SRC = \`
      class PcmProbe extends AudioWorkletProcessor {
        constructor() { super(); this.n = 0; }
        process(inputs) {
          const ch = inputs[0] && inputs[0][0];
          if (ch) { this.n += ch.length; this.port.postMessage(this.n); }
          return true;
        }
      }
      registerProcessor('pcm-probe', PcmProbe);
    \`;
    let ctx;
    try {
      ctx = new AudioContext({ sampleRate: 16000, latencyHint: 'playback' });
      await ctx.resume();
      out.sampleRate = ctx.sampleRate;
      out.state = ctx.state;
    } catch (e) { out.ctxError = String(e); return out; }

    const osc = ctx.createOscillator();
    osc.frequency.value = 440;

    // 1) blob: URL からの addModule（本番の主経路）
    try {
      const url = URL.createObjectURL(new Blob([SRC], { type: 'text/javascript' }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      out.addModuleBlob = 'ok';
    } catch (e) { out.addModuleBlob = 'FAILED: ' + String(e); }

    if (out.addModuleBlob === 'ok') {
      const node = new AudioWorkletNode(ctx, 'pcm-probe');
      let samples = 0;
      const t0 = performance.now();
      let firstMs = null;
      node.port.onmessage = (ev) => { samples = ev.data; if (firstMs === null) firstMs = performance.now() - t0; };
      const mute = ctx.createGain();
      mute.gain.value = 0;
      osc.connect(node); node.connect(mute); mute.connect(ctx.destination);
      osc.start();
      await new Promise((r) => setTimeout(r, 1500));
      osc.stop();
      out.mode = 'audioworklet';
      out.workletSamples = samples;
      out.firstFrameMs = firstMs === null ? null : Math.round(firstMs);
    }

    // 2) ScriptProcessorNode フォールバックも同時に確認しておく
    try {
      const ctx2 = new AudioContext({ sampleRate: 16000 });
      await ctx2.resume();
      const osc2 = ctx2.createOscillator();
      const sp = ctx2.createScriptProcessor(4096, 1, 1);
      let spSamples = 0;
      sp.onaudioprocess = (e) => { spSamples += e.inputBuffer.length; };
      const mute2 = ctx2.createGain();
      mute2.gain.value = 0;
      osc2.connect(sp); sp.connect(mute2); mute2.connect(ctx2.destination);
      osc2.start();
      await new Promise((r) => setTimeout(r, 1000));
      osc2.stop();
      out.scriptProcessorSamples = spSamples;
      await ctx2.close();
    } catch (e) { out.scriptProcessor = 'FAILED: ' + String(e); }

    await ctx.close();
    return out;
  })()`);

  console.log(JSON.stringify(result, null, 2));

  const ok = result.addModuleBlob === 'ok' && result.workletSamples > 0;
  console.log(ok ? '\nRESULT: AudioWorklet OK (主経路が使える)' : '\nRESULT: AudioWorklet 不可 -> ScriptProcessorNode フォールバックへ');
  app.exit(ok ? 0 : 1);
});
