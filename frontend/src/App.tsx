import { useCallback, useEffect, useMemo, useState } from 'react';
import { TranscriptView } from './components/TranscriptView';
import {
  LIVE_PRESETS,
  useLiveTranscription,
  type LiveDelayMode,
  type LiveModel
} from './features/transcription/useLiveTranscription';
import {
  backendOrigin,
  buildRequestText,
  checkOutput,
  createSession,
  finalizeSession,
  getBridge,
  updateAttachments
} from './services/api';

interface Attachment {
  path: string;
  name: string;
  exists: boolean;
}

const GPT_URL_PREFIXES = ['https://chatgpt.com/', 'https://chat.openai.com/'];

function baseName(p: string): string {
  return p.split('/').pop() || p;
}

function isValidGptUrl(url: string): boolean {
  const value = url.trim();
  if (!value) return false;
  return GPT_URL_PREFIXES.some((prefix) => value.startsWith(prefix));
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
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [saveFolder, setSaveFolder] = useState('');
  const [model, setModel] = useState<LiveModel>('small');
  const [delayMode, setDelayMode] = useState<LiveDelayMode>('balanced');
  const [requestTemplate, setRequestTemplate] = useState('');
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState('');

  const [sessionDir, setSessionDir] = useState<string | null>(null);
  const [transcriptPath, setTranscriptPath] = useState<string | null>(null);
  const [transcriptReady, setTranscriptReady] = useState(false);
  const [banner, setBanner] = useState<{ tone: 'error' | 'warn' | 'ok'; text: string } | null>(null);
  const [handoffOpen, setHandoffOpen] = useState(false);
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

  // --- 資料 ---
  const addAttachments = useCallback(async () => {
    if (!bridge) {
      setBanner({ tone: 'warn', text: 'Electron 環境でのみ資料を追加できます' });
      return;
    }
    const picked = await bridge.pickAttachments();
    if (!picked.length) return;
    setAttachments((prev) => {
      const existingPaths = new Set(prev.map((a) => a.path));
      const additions = picked
        .filter((p) => !existingPaths.has(p)) // 同一ファイルの重複登録禁止
        .map((p) => ({ path: p, name: baseName(p), exists: true }));
      return [...prev, ...additions];
    });
  }, [bridge]);

  const removeAttachment = useCallback((path: string) => {
    setAttachments((prev) => prev.filter((a) => a.path !== path));
  }, []);

  const verifyAttachments = useCallback(async () => {
    if (!bridge || attachments.length === 0) return;
    const results = await bridge.pathExists(attachments.map((a) => a.path));
    const map = new Map(results.map((r) => [r.path, r.exists]));
    setAttachments((prev) => prev.map((a) => ({ ...a, exists: map.get(a.path) ?? a.exists })));
  }, [bridge, attachments]);

  useEffect(() => {
    verifyAttachments().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachments.length]);

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
      if (check.free_bytes !== null && check.free_bytes < 200 * 1024 * 1024) {
        setBanner({ tone: 'error', text: '保存先の空き容量が不足しています' });
        return;
      }
      const session = await createSession({
        title: title.trim(),
        output_base: saveFolder.trim(),
        gpt_url: gptUrl.trim(),
        attachments: attachments.map((a) => a.path)
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
    attachments,
    live,
    model,
    delayMode,
    chunkSeconds,
    overlapSeconds,
    deviceId,
    refreshMics
  ]);

  const stopRecording = useCallback(async () => {
    live.stop();
    if (sessionDir) {
      try {
        await finalizeSession(sessionDir, 'done');
      } catch {
        /* finalize 失敗でも文字起こしは保存済み */
      }
    }
    setTranscriptReady(true);
  }, [live, sessionDir]);

  // --- マイGPTへ渡す ---
  const gptUrlValid = isValidGptUrl(gptUrl);
  const canHandoff = transcriptReady && !recording && Boolean(transcriptPath) && title.trim().length > 0 && gptUrlValid;

  const copyTranscript = useCallback(async () => {
    const text = [live.committed, live.partial].filter(Boolean).join('\n');
    if (bridge) await bridge.writeClipboard(text);
    else await navigator.clipboard.writeText(text);
    setBanner({ tone: 'ok', text: '全文をコピーしました' });
  }, [bridge, live.committed, live.partial]);

  const openTranscript = useCallback(async () => {
    if (bridge && transcriptPath) await bridge.revealInFinder(transcriptPath);
  }, [bridge, transcriptPath]);

  const handoff = useCallback(async () => {
    setBanner(null);
    if (!bridge) {
      setBanner({ tone: 'warn', text: 'Electron 環境でのみ実行できます' });
      return;
    }
    if (!transcriptPath) return;
    // 1. 最終TXTの存在確認
    const [txtCheck] = await bridge.pathExists([transcriptPath]);
    if (!txtCheck?.exists) {
      setBanner({ tone: 'error', text: '最終TXTが見つかりません' });
      return;
    }
    // 2. 登録資料の存在確認（会議メタも更新）
    await verifyAttachments();
    if (sessionDir) await updateAttachments(sessionDir, attachments.map((a) => a.path)).catch(() => {});
    // 3. マイGPT URL を既定ブラウザで開く
    const opened = await bridge.openExternal(gptUrl.trim());
    if (!opened.ok) {
      setBanner({ tone: 'error', text: '許可されていない URL です（chatgpt.com のみ開けます）' });
      return;
    }
    // 4. 依頼文をクリップボードへ
    const text = buildRequestText(title.trim(), attachments.map((a) => a.name), requestTemplate);
    await bridge.writeClipboard(text);
    // 5. 添付一覧をダイアログ表示 + 6. Finder で TXT を表示
    setHandoffOpen(true);
    await bridge.revealInFinder(transcriptPath);
  }, [bridge, transcriptPath, verifyAttachments, sessionDir, attachments, gptUrl, title, requestTemplate]);

  const missingAttachments = attachments.filter((a) => !a.exists);
  const statusTone = useMemo(() => {
    if (live.status.includes('エラー')) return 'error';
    if (recording) return 'recording';
    if (transcriptReady) return 'done';
    return 'idle';
  }, [live.status, recording, transcriptReady]);

  return (
    <div className="app">
      <header className="titlebar">
        <div className="titlebar-drag">
          <span className="brand">BridgeLog</span>
        </div>
        <span className={`status-pill tone-${statusTone}`}>
          <span className="dot" /> {recording ? '録音中' : live.status}
        </span>
      </header>

      <main className="content">
        {banner ? <div className={`banner banner-${banner.tone}`}>{banner.text}</div> : null}
        {health && health.ffmpeg_ok === false ? (
          <div className="banner banner-error">
            Backend が ffmpeg/ffprobe を見つけられません。realtime のデコードができず文字起こしは空のままになります。
            Homebrew の ffmpeg をインストール（`brew install ffmpeg`）し、アプリを再起動してください。
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
          <div className="field-head">
            <label>資料</label>
            <button type="button" className="btn-ghost" onClick={addAttachments}>＋ 追加</button>
          </div>
          {attachments.length === 0 ? (
            <p className="hint">＋ から資料を追加できます（任意）</p>
          ) : (
            <ul className="attachments">
              {attachments.map((a) => (
                <li key={a.path} className={a.exists ? '' : 'missing'}>
                  <span className="att-name" title={a.path}>{a.name}</span>
                  {!a.exists ? <span className="att-warn">見つかりません</span> : null}
                  <span className="att-actions">
                    {bridge ? (
                      <button type="button" className="btn-mini" onClick={() => bridge.revealInFinder(a.path)}>
                        Finder
                      </button>
                    ) : null}
                    <button type="button" className="btn-mini" onClick={() => removeAttachment(a.path)}>×</button>
                  </span>
                </li>
              ))}
            </ul>
          )}
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
          <TranscriptView committed={live.committed} partial={live.partial} />
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
          <span>状態: {recording ? '録音中' : live.status}</span>
          {live.savedPath ? <span className="saved-path" title={live.savedPath}>保存: {baseName(live.savedPath)}</span> : null}
        </section>

        <section className="actions">
          <button type="button" className="btn-primary" onClick={startRecording} disabled={recording}>
            文字起こし開始
          </button>
          <button type="button" className="btn-danger" onClick={stopRecording} disabled={!recording}>
            停止
          </button>
          <button type="button" className="btn-accent" onClick={handoff} disabled={!canHandoff} title={!canHandoff ? '文字起こし完了・タイトル・マイGPT URL が必要です' : ''}>
            マイGPTへ渡す
          </button>
        </section>

        <details className="diag">
          <summary>診断ログ（Realtime パイプライン）</summary>
          <div className="diag-tools">
            <span>入力レベル(RMS): {live.inputLevel.toFixed(4)}{live.inputLevel < 0.001 && recording ? ' ⚠ ほぼ無音 — BlackHole のルーティングを確認' : ''}</span>
            <span>ffmpeg: {health ? (health.ffmpeg_ok ? `OK (${baseName(health.ffmpeg || '')})` : 'なし') : '不明'}</span>
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

      {handoffOpen ? (
        <div className="modal-backdrop" onClick={() => setHandoffOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>マイGPTへ渡す準備が整いました</h3>
            <p className="modal-note">
              依頼文をクリップボードへコピーし、マイGPT をブラウザで開きました。
              以下のファイルを ChatGPT の画面へ添付してください。
            </p>
            <ul className="modal-files">
              <li>
                <span>文字起こしテキスト</span>
                <button type="button" className="btn-mini" onClick={openTranscript}>Finder</button>
              </li>
              {attachments.map((a) => (
                <li key={a.path} className={a.exists ? '' : 'missing'}>
                  <span>{a.name}{!a.exists ? '（見つかりません）' : ''}</span>
                  {bridge ? <button type="button" className="btn-mini" onClick={() => bridge.revealInFinder(a.path)}>Finder</button> : null}
                </li>
              ))}
            </ul>
            {missingAttachments.length ? (
              <p className="hint hint-error">存在しない資料があります。ファイルを確認してください。</p>
            ) : null}
            <div className="modal-actions">
              <button type="button" className="btn-ghost" onClick={() => setHandoffOpen(false)}>閉じる</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
