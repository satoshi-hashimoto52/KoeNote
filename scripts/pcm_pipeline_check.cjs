// レンダラ -> Backend のリアルタイム経路を、本番と同じ条件で通しで検証する使い捨てスクリプト。
//   - 実際の file:// + 実際の CSP（built index.html を読み込む）
//   - blob: URL からの AudioWorklet 読み込み
//   - OscillatorNode を MediaStream に変換して「マイク」の代わりにする（権限不要）
//   - 実際の ws://127.0.0.1:PORT/ws/live へ PCM16 を送る
// 事前に Backend を起動しておくこと。
const { app, BrowserWindow } = require('electron');
const path = require('path');

const INDEX = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');
const PORT = Number(process.env.KOENOTE_PORT || 8765);
const SECONDS = Number(process.env.CHECK_SECONDS || 14);

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: 800, height: 600, show: false, webPreferences: { offscreen: true } });
  await win.loadFile(INDEX);
  await new Promise((r) => setTimeout(r, 500));

  const result = await win.webContents.executeJavaScript(`(async () => {
    const out = { origin: location.origin, frames: 0, sent: 0, messages: {}, errors: [] };
    const FRAME = 2048;
    const WORKLET = \`
      class Cap extends AudioWorkletProcessor {
        constructor(){ super(); this.b=new Int16Array(\${FRAME}); this.f=0; this.t=0; }
        process(inputs){
          const ch = inputs[0] && inputs[0][0];
          if (ch) for (let i=0;i<ch.length;i++){
            let s=ch[i]; if(s>1)s=1; else if(s<-1)s=-1;
            this.b[this.f]= s<0 ? s*32768 : s*32767;
            this.f++; this.t++;
            if(this.f===\${FRAME}){ const o=this.b; this.b=new Int16Array(\${FRAME}); this.f=0;
              this.port.postMessage({pcm:o.buffer, totalSamples:this.t},[o.buffer]); }
          }
          return true;
        }
      }
      registerProcessor('cap', Cap);
    \`;

    // 16kHz の AudioContext。マイクの代わりに Oscillator を MediaStream 化する。
    const ctx = new AudioContext({ sampleRate: 16000, latencyHint: 'playback' });
    await ctx.resume();
    out.sampleRate = ctx.sampleRate;

    const osc = ctx.createOscillator();
    osc.type = 'sine'; osc.frequency.value = 180;
    const amp = ctx.createGain(); amp.gain.value = 0.3;
    const dest = ctx.createMediaStreamDestination();
    osc.connect(amp); amp.connect(dest); osc.start();
    const stream = dest.stream;
    out.streamTracks = stream.getAudioTracks().length;

    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }));
    try { await ctx.audioWorklet.addModule(url); out.workletLoaded = true; }
    catch (e) { out.workletLoaded = false; out.errors.push('addModule: ' + e); return out; }
    URL.revokeObjectURL(url);

    const node = new AudioWorkletNode(ctx, 'cap');
    const src = ctx.createMediaStreamSource(stream);
    const mute = ctx.createGain(); mute.gain.value = 0;
    src.connect(node); node.connect(mute); mute.connect(ctx.destination);

    const ws = new WebSocket('ws://127.0.0.1:${PORT}/ws/live');
    ws.binaryType = 'arraybuffer';
    const bump = (k) => { out.messages[k] = (out.messages[k] || 0) + 1; };
    let committed = '';
    let ready = false;

    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      bump(m.type);
      if (m.type === 'ready') { ready = true; out.sessionId = m.session_id; out.serverSampleRate = m.sample_rate; }
      if (m.type === 'heartbeat') { out.lastHeartbeat = m; }
      if (m.type === 'update') {
        if (committed.length !== m.committed_length_before) { out.errors.push('delta base mismatch'); }
        committed += m.committed_delta || '';
        out.lastUpdate = { before: m.committed_length_before, after: m.committed_length, window: m.window_index };
      }
      if (m.type === 'session_final') { out.final = { len: (m.committed_text||'').length, recorded: m.recorded_seconds, dropped: m.dropped_seconds }; }
      if (m.type === 'error') out.errors.push('server: ' + m.message);
    };
    ws.onerror = () => out.errors.push('ws error');
    ws.onclose = (e) => { out.close = { code: e.code, clean: e.wasClean }; };

    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      setTimeout(() => reject(new Error('ws open timeout')), 5000);
    }).catch((e) => out.errors.push(String(e)));

    ws.send(JSON.stringify({
      type: 'config', send_mode: 'pcm16', sample_rate: ctx.sampleRate,
      model: 'tiny', delay_mode: 'balanced', write_to_file: false, debug: true
    }));

    let stopping = false;
    node.port.onmessage = (ev) => {
      if (!stopping) out.frames += 1;
      if (ws.readyState === 1 && !stopping) { ws.send(ev.data.pcm); out.sent += 1; }
      out.totalSamples = ev.data.totalSamples;
    };

    await new Promise((r) => setTimeout(r, ${SECONDS} * 1000));
    stopping = true;
    ws.send(JSON.stringify({ type: 'stop' }));
    await new Promise((r) => setTimeout(r, 3000));
    osc.stop(); await ctx.close();
    out.committedLocal = committed.length;
    return out;
  })()`);

  console.log(JSON.stringify(result, null, 2));

  const ok =
    result.workletLoaded === true &&
    result.sampleRate === 16000 &&
    result.frames > 0 &&
    result.sent === result.frames &&
    (result.messages.heartbeat || 0) >= 3 &&
    (result.messages.update || 0) >= 1 &&
    result.final &&
    result.errors.length === 0;
  console.log(ok ? '\nRESULT: renderer -> backend の PCM 経路 OK' : '\nRESULT: NG');
  app.exit(ok ? 0 : 1);
});
