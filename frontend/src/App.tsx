import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { TranscriptView } from './components/TranscriptView';
import { TRANSCRIPT_HEIGHT_KEY } from './components/transcriptHeight';
import { useLiveTranscription } from './features/transcription/useLiveTranscription';
import { primeAlertTone } from './features/transcription/alertTone';
import {
  ERROR_REASON_LABELS,
  LIVE_PRESETS,
  statusLabel,
  statusTone as toneForStatus,
  type LiveDelayMode,
  type LiveModel
} from './features/transcription/liveTypes';
import {
  backendOrigin,
  checkOutput,
  createSession,
  finalizeSession,
  getBridge,
  repairAudio
} from './services/api';

const GPT_URL_PREFIXES = ['https://chatgpt.com/', 'https://chat.openai.com/'];

function baseName(p: string): string {
  return p.split('/').pop() || p;
}

function isValidGptUrl(url: string): boolean {
  const value = url.trim();
  if (!value) return false;
  return GPT_URL_PREFIXES.some((prefix) => value.startsWith(prefix));
}

function clockTime(): string {
  return new Date().toLocaleTimeString('ja-JP', { hour12: false });
}

function formatIsoTime(value: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString('ja-JP', { hour12: false });
}

function formatElapsed(sec: number): string {
  const h = String(Math.floor(sec / 3600)).padStart(2, '0');
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
  const s = String(sec % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

export default function App() {
  const bridge = getBridge();
  const live = useLiveTranscription();

  const [title, setTitle] = useState('');
  const [gptUrl, setGptUrl] = useState('');
  const [saveFolder, setSaveFolder] = useState('');
  const [model, setModel] = useState<LiveModel>('small');
  const [delayMode, setDelayMode] = useState<LiveDelayMode>('balanced');
  const [requestTemplate, setRequestTemplate] = useState('');
  // 文字起こし欄の高さ（0008）。設定から復元し、変更時に保存する。
  const [transcriptHeight, setTranscriptHeight] = useState<number | null>(null);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState('');

  const [sessionDir, setSessionDir] = useState<string | null>(null);
  // Backend 再起動の多重実行を防ぐ（0012）。
  const restartingRef = useRef(false);
  // マイGPT の連打で複数タブを開かない（0006）。
  const openingGptRef = useRef(false);
  const [transcriptPath, setTranscriptPath] = useState<string | null>(null);
  const [transcriptReady, setTranscriptReady] = useState(false);
  const [banner, setBanner] = useState<{ tone: 'error' | 'warn' | 'ok'; text: string } | null>(null);
  // 停止押下から session_final / TXT保存完了までの確定処理中フラグ（この間はクリア禁止）。
  const [finalizing, setFinalizing] = useState(false);
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [health, setHealth] = useState<{ ffmpeg_ok?: boolean; ffmpeg?: string | null } | null>(null);
  const [backendLog, setBackendLog] = useState('');

  const { recording } = live;

  // --- 設定の読み込み・保存 ---
  useEffect(() => {
    (async () => {
      if (!bridge) {
        setSettingsLoaded(true);
        return;
      }
      const s = await bridge.getSettings();
      if (typeof s.gptUrl === 'string') setGptUrl(s.gptUrl);
      if (typeof s.saveFolder === 'string') setSaveFolder(s.saveFolder);
      if (typeof s.deviceId === 'string') setDeviceId(s.deviceId);
      if (s.model === 'tiny' || s.model === 'base' || s.model === 'small' || s.model === 'medium') setModel(s.model);
      if (s.delayMode === 'low_latency' || s.delayMode === 'balanced' || s.delayMode === 'accuracy')
        setDelayMode(s.delayMode);
      if (typeof s.requestTemplate === 'string') setRequestTemplate(s.requestTemplate);
      // 高さは正規化を TranscriptView 側で行う。ここでは生値を渡すだけ（0008）。
      if (s[TRANSCRIPT_HEIGHT_KEY] !== undefined) setTranscriptHeight(Number(s[TRANSCRIPT_HEIGHT_KEY]));
      setSettingsLoaded(true);
    })();
  }, [bridge]);

  useEffect(() => {
    if (!bridge || !settingsLoaded) return;
    bridge.setSettings({ gptUrl, saveFolder, deviceId, model, delayMode, requestTemplate }).catch(() => {});
  }, [bridge, settingsLoaded, gptUrl, saveFolder, deviceId, model, delayMode, requestTemplate]);

  // --- マイク一覧・録音経過・録音状態通知 ---
  const refreshMics = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const devices = await navigator.mediaDevices.enumerateDevices();
    setMics(devices.filter((d) => d.kind === 'audioinput'));
  }, []);

  useEffect(() => {
    refreshMics().catch(() => {});
  }, [refreshMics]);

  // Backend の ffmpeg 解決状況を取得（realtime デコード失敗の一次切り分け）。
  useEffect(() => {
    fetch(`${backendOrigin()}/api/health`)
      .then((r) => r.json())
      .then((h) => setHealth(h))
      .catch(() => setHealth(null));
  }, []);

  const loadBackendLog = useCallback(async () => {
    if (!bridge) return;
    setBackendLog(await bridge.getBackendLog());
  }, [bridge]);

  useEffect(() => {
    bridge?.setRecordingState(recording);
    if (!recording) return;
    const timer = window.setInterval(() => setElapsedSec((p) => p + 1), 1000);
    return () => window.clearInterval(timer);
  }, [bridge, recording]);

  // --- 保存先 ---
  const pickFolder = useCallback(async () => {
    if (!bridge) {
      setBanner({ tone: 'warn', text: 'Electron 環境でのみフォルダを選択できます' });
      return;
    }
    const folder = await bridge.pickFolder();
    if (folder) setSaveFolder(folder);
  }, [bridge]);

  // --- 録音開始/停止 ---
  const chunkSeconds = LIVE_PRESETS[delayMode].chunk;
  const overlapSeconds = LIVE_PRESETS[delayMode].overlap;

  const startRecording = useCallback(async () => {
    setBanner(null);
    // 異常時に確実に警告音を鳴らせるよう、ユーザー操作の中で AudioContext を起こす。
    primeAlertTone();
    if (!title.trim()) {
      setBanner({ tone: 'error', text: 'タイトルを入力してください' });
      return;
    }
    if (!saveFolder.trim()) {
      setBanner({ tone: 'error', text: '文字起こし保存先を指定してください' });
      return;
    }
    try {
      const check = await checkOutput(saveFolder);
      if (!check.exists && !check.writable) {
        setBanner({ tone: 'error', text: '保存先が存在せず、作成もできません' });
        return;
      }
      // 録音音声(16kHz mono PCM)は約115MB/時。2時間ぶん + 余裕を要求する。
      if (check.free_bytes !== null && check.free_bytes < 600 * 1024 * 1024) {
        setBanner({ tone: 'error', text: '保存先の空き容量が不足しています（録音2時間で約230MB必要）' });
        return;
      }
      const session = await createSession({
        title: title.trim(),
        output_base: saveFolder.trim(),
        gpt_url: gptUrl.trim()
      });
      setSessionDir(session.session_dir);
      setTranscriptPath(session.transcript_path);
      setTranscriptReady(false);
      setElapsedSec(0);
      await live.start({
        model,
        delayMode,
        chunkSeconds,
        overlapSeconds,
        deviceId: deviceId || undefined,
        outputFolder: session.session_dir,
        outputFilename: session.transcript_filename,
        writeToFile: true
      });
      await refreshMics();
    } catch (error) {
      const message = error instanceof Error ? error.message : '録音開始に失敗しました';
      setBanner({ tone: 'error', text: message });
    }
  }, [
    title,
    saveFolder,
    gptUrl,
    live,
    model,
    delayMode,
    chunkSeconds,
    overlapSeconds,
    deviceId,
    refreshMics
  ]);

  const stopRecording = useCallback(async () => {
    // recording -> finalizing。session_final と TXT保存が確定するまでクリアを禁止する。
    setFinalizing(true);
    await live.stop();
    if (sessionDir) {
      try {
        await finalizeSession(sessionDir, 'done');
      } catch {
        /* finalize 失敗でも文字起こしは保存済み */
      }
      // 強制終了していた場合に備えて録音ファイルのヘッダを整える。
      try {
        await repairAudio(sessionDir);
      } catch {
        /* 修復失敗でも PCM 本体はディスク上に残っている */
      }
    }
    setTranscriptReady(true);
    setFinalizing(false);
  }, [live, sessionDir]);

  /** 異常停止を、ユーザーが後から辿れる場所すべてに残す。 */
  const recordInterruption = useCallback(
    async (reason: string, detail: string) => {
      const line = `[中断] ${clockTime()} 文字起こしが異常終了しました (reason=${reason}) ${detail}`;
      // TXT はユーザーが GPT へ渡す成果物。無言で途切れると中断が見えない。
      if (bridge && transcriptPath) {
        await bridge.appendTranscriptNotice(transcriptPath, line).catch(() => undefined);
      }
      // diagnostics.log は Backend の HTTP API を使わない。
      // reason=backend_exit / no_heartbeat では Backend が死んでおり、
      // API 経由の記録は原理的に失敗するため（0010）、main のローカル I/O で書く。
      if (bridge && sessionDir) {
        const written = await bridge
          .appendDiagnostics(sessionDir, line)
          .catch((error: unknown) => ({
            ok: false,
            reason: error instanceof Error ? error.message : 'ipc_failed'
          }));
        // 失敗を握り潰さない。記録が残らなかったことを利用者に見せる。
        if (!written.ok) {
          setBanner({ tone: 'warn', text: `diagnostics.log へ記録できませんでした（${written.reason ?? 'unknown'}）` });
        }
      }
    },
    [bridge, transcriptPath, sessionDir]
  );

  /** 異常ポップアップの「録音を終了して保存」。 */
  const stopFromAnomaly = useCallback(async () => {
    live.dismissAnomaly();
    await stopRecording();
  }, [live, stopRecording]);

  const restartBackendAndReconnect = useCallback(async () => {
    if (!bridge) {
      live.reconnect();
      return;
    }
    // main 側にも排他があるが（0012）、UI からの連打をここでも止めて
    // 「再起動しています…」の表示が二重に走らないようにする。
    if (restartingRef.current) return;
    restartingRef.current = true;
    setBanner({ tone: 'warn', text: 'Backend を再起動しています…' });
    try {
      const result = await bridge.restartBackend().catch(() => ({ ok: false }));
      if (!result.ok) {
        setBanner({ tone: 'error', text: 'Backend の再起動に失敗しました。アプリを再起動してください。' });
        return;
      }
      setBanner({ tone: 'ok', text: 'Backend を再起動しました。再接続します。' });
      live.reconnect();
    } finally {
      restartingRef.current = false;
    }
  }, [bridge, live]);

  // --- マイGPTを開く（URL をブラウザで開くだけ。データ送信はしない） ---
  const gptUrlValid = isValidGptUrl(gptUrl);

  const copyTranscript = useCallback(async () => {
    const text = [live.committed, live.partial].filter(Boolean).join('\n');
    if (bridge) await bridge.writeClipboard(text);
    else await navigator.clipboard.writeText(text);
    setBanner({ tone: 'ok', text: '全文をコピーしました' });
  }, [bridge, live.committed, live.partial]);

  const openTranscript = useCallback(async () => {
    if (bridge && transcriptPath) await bridge.revealInFinder(transcriptPath);
  }, [bridge, transcriptPath]);

  const openAudio = useCallback(async () => {
    if (bridge && live.audioPath) await bridge.revealInFinder(live.audioPath);
  }, [bridge, live.audioPath]);

  const openGpt = useCallback(async () => {
    setBanner(null);
    if (!gptUrlValid) {
      setBanner({ tone: 'error', text: '有効な chatgpt.com のURLを入力してください' });
      return;
    }
    if (!bridge) {
      setBanner({ tone: 'warn', text: 'Electron 環境でのみブラウザを開けます' });
      return;
    }
    // 連打で複数タブを開かない。
    if (openingGptRef.current) return;
    openingGptRef.current = true;
    try {
      // Main プロセス側で許可ドメインを検証し、Chrome → 既定ブラウザの順に開く（0006）。
      const opened = await bridge.openExternal(gptUrl.trim());
      if (!opened.ok) {
        setBanner({
          tone: 'error',
          text:
            opened.reason === 'disallowed_domain'
              ? '許可されていない URL です（chatgpt.com のみ開けます）'
              : `ブラウザを開けませんでした（${opened.reason ?? 'unknown'}）`
        });
        return;
      }
      if (opened.opener === 'default') {
        setBanner({ tone: 'warn', text: 'Google Chrome が見つからないため、既定のブラウザで開きました' });
      }
    } finally {
      openingGptRef.current = false;
    }
  }, [bridge, gptUrl, gptUrlValid]);

  // --- クリア（画面状態のリセットのみ。保存済みファイルは削除しない） ---
  const canClear = !recording && !finalizing;

  const doClear = useCallback(() => {
    setTitle('');
    live.reset(); // committed/partial/final表示・savedPath・診断ログ・session/result 追跡をリセット
    setElapsedSec(0);
    setSessionDir(null);
    setTranscriptPath(null);
    setTranscriptReady(false);
    setBanner(null);
    setClearConfirmOpen(false);
  }, [live]);

  const onClearClick = useCallback(() => {
    if (!canClear) return;
    // 文字起こしテキストがある場合だけ確認する。
    if (live.committed || live.partial) {
      setClearConfirmOpen(true);
    } else {
      doClear();
    }
  }, [canClear, live.committed, live.partial, doClear]);

  // --- Backend 異常終了の検知（main プロセスからの通知）---
  useEffect(() => {
    if (!bridge?.onBackendExited) return;
    const offExit = bridge.onBackendExited((info) => {
      live.noteBackendExit(`code=${info.code} signal=${info.signal} reason=${info.reason}`);
    });
    const offRestart = bridge.onBackendRestartRequested?.(() => {
      void restartBackendAndReconnect();
    });
    return () => {
      offExit();
      offRestart?.();
    };
  }, [bridge, live, restartBackendAndReconnect]);

  // 異常を検知したら、TXT と diagnostics.log に必ず痕跡を残す。
  const loggedAnomalyRef = useRef(0);
  useEffect(() => {
    const current = live.anomaly;
    if (!current || loggedAnomalyRef.current === current.at) return;
    loggedAnomalyRef.current = current.at;
    void recordInterruption(current.reason, current.detail);
  }, [live.anomaly, recordInterruption]);

  const statusTone = useMemo(() => {
    if (finalizing) return 'recording';
    if (live.status.kind === 'idle' && transcriptReady) return 'done';
    return toneForStatus(live.status);
  }, [live.status, finalizing, transcriptReady]);

  const phaseLabel = finalizing ? '確定処理中…' : statusLabel(live.status);

  return (
    <div className="app">
      <header className="titlebar">
        <div className="titlebar-drag">
          <span className="brand">BridgeLog</span>
        </div>
        <span className={`status-pill tone-${statusTone}`}>
          <span className="dot" /> {phaseLabel}
        </span>
      </header>

      <main className="content">
        {banner ? <div className={`banner banner-${banner.tone}`}>{banner.text}</div> : null}
        {live.warning ? (
          <div className="banner banner-warn">
            {live.warning.message}
            <button type="button" className="btn-mini banner-btn" onClick={live.dismissWarning}>閉じる</button>
          </div>
        ) : null}
        {live.droppedClientSeconds > 0 ? (
          <div className="banner banner-warn">
            接続が不安定だったため、文字起こしへ送れなかった音声が
            {live.droppedClientSeconds.toFixed(1)} 秒あります（録音ファイルには残っています）。
          </div>
        ) : null}
        {health && health.ffmpeg_ok === false ? (
          <div className="banner banner-error">
            Backend が ffmpeg/ffprobe を見つけられません。リアルタイム文字起こしは ffmpeg を使わないため影響しませんが、
            音声ファイルからの文字起こしはできません。Homebrew の ffmpeg をインストール（`brew install ffmpeg`）してください。
          </div>
        ) : null}

        <section className="field">
          <label htmlFor="title">会議／セミナータイトル<span className="req">必須</span></label>
          <input
            id="title"
            type="text"
            placeholder="例: AIモデル、そのままデバイスに載せていませんか？"
            value={title}
            disabled={recording}
            onChange={(e) => setTitle(e.target.value)}
          />
        </section>

        <section className="field">
          <label htmlFor="gpturl">マイGPTのURL</label>
          <input
            id="gpturl"
            type="text"
            placeholder="https://chatgpt.com/g/g-xxxxxxxx"
            value={gptUrl}
            className={gptUrl && !gptUrlValid ? 'invalid' : ''}
            onChange={(e) => setGptUrl(e.target.value)}
          />
          {gptUrl && !gptUrlValid ? <p className="hint hint-error">chatgpt.com のURLを入力してください</p> : null}
        </section>

        <section className="field">
          <label htmlFor="folder">文字起こしファイル保存先</label>
          <div className="inline">
            <input
              id="folder"
              type="text"
              placeholder="/Users/you/Documents/BridgeLog"
              value={saveFolder}
              disabled={recording}
              onChange={(e) => setSaveFolder(e.target.value)}
            />
            <button type="button" className="btn-ghost" onClick={pickFolder} disabled={recording}>選択</button>
          </div>
        </section>

        <section className="field row-3">
          <div>
            <label htmlFor="mic">入力デバイス</label>
            <select id="mic" value={deviceId} onChange={(e) => setDeviceId(e.target.value)} disabled={recording}>
              <option value="">既定入力</option>
              {mics.map((m, i) => (
                <option key={m.deviceId || i} value={m.deviceId}>{m.label || `マイク ${i + 1}`}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="model">モデル</label>
            <select id="model" value={model} onChange={(e) => setModel(e.target.value as LiveModel)} disabled={recording}>
              <option value="tiny">tiny</option>
              <option value="base">base</option>
              <option value="small">small（推奨）</option>
              <option value="medium">medium</option>
            </select>
          </div>
          <div>
            <label htmlFor="delay">遅延モード</label>
            <select id="delay" value={delayMode} onChange={(e) => setDelayMode(e.target.value as LiveDelayMode)} disabled={recording}>
              <option value="low_latency">低遅延 (8s/2s)</option>
              <option value="balanced">標準 (10s/2s)</option>
              <option value="accuracy">精度優先 (12s/3s)</option>
            </select>
          </div>
        </section>

        <section className="field">
          <div className="field-head">
            <label>文字起こしテキスト</label>
            <span className="transcript-tools">
              <button type="button" className="btn-mini" onClick={copyTranscript} disabled={!live.committed && !live.partial}>全文コピー</button>
              <button type="button" className="btn-mini" onClick={openTranscript} disabled={!transcriptPath || !bridge}>保存TXTを開く</button>
            </span>
          </div>
          <TranscriptView
            committed={live.committed}
            partial={live.partial}
            savedHeight={transcriptHeight}
            onHeightChange={(h) => {
              setTranscriptHeight(h);
              bridge?.setSettings({ [TRANSCRIPT_HEIGHT_KEY]: h }).catch(() => {});
            }}
          />
        </section>

        <section className="statusbar">
          <span>{formatElapsed(elapsedSec)}</span>
          <span>入力: {live.deviceLabel}</span>
          <span className="level-meter" title={`入力レベル(RMS)=${live.inputLevel.toFixed(4)}`}>
            レベル
            <span className="level-track">
              <span
                className={`level-fill ${live.inputLevel < 0.001 && recording ? 'silent' : ''}`}
                style={{ width: `${Math.min(100, live.inputLevel * 400)}%` }}
              />
            </span>
          </span>
          <span>状態: {phaseLabel}</span>
          <span title="Backend が最後に音声を受信した時刻">音声: {formatIsoTime(live.progress.lastAudioReceivedAt)}</span>
          <span title="最後に文字起こしが完了した時刻">文字起こし: {formatIsoTime(live.progress.lastTranscriptionAt)}</span>
          {live.savedPath ? <span className="saved-path" title={live.savedPath}>保存: {baseName(live.savedPath)}</span> : null}
        </section>

        <section className="actions">
          <div className="actions-left">
            <button type="button" className="btn-primary" onClick={startRecording} disabled={recording || finalizing}>
              文字起こし開始
            </button>
            <button type="button" className="btn-danger" onClick={stopRecording} disabled={!recording}>
              停止
            </button>
          </div>
          <div className="actions-right">
            <button
              type="button"
              className="btn-ghost"
              onClick={onClearClick}
              disabled={!canClear}
              title={!canClear ? '録音中・確定処理中はクリアできません' : '画面表示をリセット（保存ファイルは削除しません）'}
            >
              クリア
            </button>
            <button
              type="button"
              className="btn-accent"
              onClick={openGpt}
              disabled={!gptUrlValid}
              title={!gptUrlValid ? '有効な chatgpt.com のURLを入力してください' : 'マイGPT をブラウザで開く'}
            >
              マイGPTを開く
            </button>
          </div>
        </section>

        <details className="diag" onToggle={(e) => live.setDebug((e.currentTarget as HTMLDetailsElement).open)}>
          <summary>診断ログ（Realtime パイプライン）</summary>
          <div className="diag-tools">
            <span>入力レベル(RMS): {live.inputLevel.toFixed(4)}{live.inputLevel < 0.001 && recording ? ' ⚠ ほぼ無音 — BlackHole のルーティングを確認' : ''}</span>
            <span>キャプチャ経路: {live.capturePath ?? '-'}</span>
            <span>録音: {live.progress.recordedSeconds.toFixed(0)}s</span>
            <span>処理済み: {live.progress.processedAudioSeconds.toFixed(0)}s</span>
            <span title="文字起こしが録音より何秒遅れているか">遅延: {live.progress.lagSeconds.toFixed(1)}s</span>
            <span>破棄: {live.progress.droppedSeconds.toFixed(1)}s</span>
            <span>窓: {live.progress.windowIndex}</span>
            <span>ffmpeg: {health ? (health.ffmpeg_ok ? `OK (${baseName(health.ffmpeg || '')})` : 'なし') : '不明'}</span>
            {live.audioPath ? (
              <button type="button" className="btn-mini" onClick={openAudio} disabled={!bridge}>録音ファイルを開く</button>
            ) : null}
            {bridge ? <button type="button" className="btn-mini" onClick={loadBackendLog}>Backendログ取得</button> : null}
          </div>
          <pre className="diag-log">{live.logText || 'ログなし'}</pre>
          {backendLog ? (
            <>
              <h4 className="diag-h">Backendログ</h4>
              <pre className="diag-log">{backendLog}</pre>
            </>
          ) : null}
        </details>
      </main>

      {live.anomaly ? (
        <div className="modal-backdrop">
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>⚠ 文字起こしが停止しました</h3>
            <p className="modal-note">
              {ERROR_REASON_LABELS[live.anomaly.reason]}
              {'\n'}{live.anomaly.detail}
              {'\n\n'}確定済みの文字起こしは保存済みです。録音音声も別ファイルに残っています。
              {live.savedPath ? `\n文字起こし: ${live.savedPath}` : ''}
              {live.audioPath ? `\n録音音声: ${live.audioPath}` : ''}
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn-accent"
                onClick={live.anomaly.reason === 'backend_exit' ? restartBackendAndReconnect : live.reconnect}
                disabled={!live.anomaly.recoverable}
                title={live.anomaly.recoverable ? '同じセッションに再接続して続きから記録します' : '再接続できない状態です'}
              >
                再接続
              </button>
              <button type="button" className="btn-danger" onClick={stopFromAnomaly}>
                録音を終了して保存
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {clearConfirmOpen ? (
        <div className="modal-backdrop" onClick={() => setClearConfirmOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>現在の表示内容をクリアしますか？</h3>
            <p className="modal-note">
              会議／セミナータイトル、文字起こしテキスト、録音進捗が初期化されます。
              {'\n'}マイGPTのURL・保存先・入力設定は保持されます。
              {'\n\n'}保存済みの文字起こしファイルは削除されません。
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-ghost" onClick={() => setClearConfirmOpen(false)}>キャンセル</button>
              <button type="button" className="btn-accent" onClick={doClear}>クリア</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
