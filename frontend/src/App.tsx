import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { TranscriptView } from './components/TranscriptView';
import { TRANSCRIPT_HEIGHT_KEY } from './components/transcriptHeight';
import { SettingsModal } from './components/SettingsModal';
import { GearIcon } from './components/GearIcon';
import { InfoTip } from './components/InfoTip';
import {
  DEFAULT_WINDOW_OPACITY,
  normalizeWindowOpacity,
  readWindowOpacity
} from './components/windowOpacity';
import { resolveRecordButton } from './components/recordButton';
import { createNoticeGate } from './components/deviceNotice';
import { createSessionCleanup } from './components/sessionCleanup';
import {
  createNoticeAutoDismiss,
  errorNotice,
  inputDeviceFallbackNotice,
  okNotice,
  warnNotice,
  type UiNotice
} from './components/uiNotice';
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
  // 設定モーダル（0015）
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 開始処理中フラグ。連打と「開始中…」表示に使う（0015）。
  const [starting, setStarting] = useState(false);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState('');
  // 0016: origin が変わると deviceId が無効になるため、ラベルでも引き当てられるようにする。
  const [deviceLabel, setDeviceLabel] = useState('');
  // 0018: ウィンドウ不透明度。CSS ではなく BrowserWindow.setOpacity で反映する。
  const [windowOpacity, setWindowOpacity] = useState(DEFAULT_WINDOW_OPACITY);

  const [sessionDir, setSessionDir] = useState<string | null>(null);
  // Backend 再起動の多重実行を防ぐ（0012）。
  const restartingRef = useRef(false);
  // マイGPT の連打で複数タブを開かない（0006）。
  const openingGptRef = useRef(false);
  // 0016: 同じデバイス通知を録音のたびに繰り返さない。
  const noticeGateRef = useRef(createNoticeGate());
  // 0016: 開始に失敗したセッションを status: recording のまま残さない。
  // diagnostics は Backend の死活に依存しない Electron 側の経路を使う（0010）。
  const cleanupSessionRef = useRef(
    createSessionCleanup({
      finalize: (dir, status) => finalizeSession(dir, status),
      diagnostics: async (dir, text) => {
        const b = getBridge();
        if (!b) return { ok: false };
        return b.appendDiagnostics(dir, text);
      }
    })
  );
  const [transcriptPath, setTranscriptPath] = useState<string | null>(null);
  const [transcriptReady, setTranscriptReady] = useState(false);
  const [banner, setBanner] = useState<UiNotice | null>(null);
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
      // 旧設定には deviceLabel が無い。無ければ空のままにする（0016）。
      if (typeof s.deviceLabel === 'string') setDeviceLabel(s.deviceLabel);
      // 未設定・壊れている場合は 1.00。main 側は起動時に同じ値を適用済み（0018）。
      setWindowOpacity(readWindowOpacity(s));
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
    bridge
      .setSettings({
        gptUrl, saveFolder, deviceId, deviceLabel, model, delayMode, requestTemplate, windowOpacity
      })
      .catch(() => {});
  }, [
    bridge, settingsLoaded, gptUrl, saveFolder, deviceId, deviceLabel,
    model, delayMode, requestTemplate, windowOpacity
  ]);

  /** ウィンドウへ即時反映する（ライブプレビューと復元の共通経路）。失敗しても続行する。 */
  const applyOpacity = useCallback(
    (value: number) => {
      bridge?.setWindowOpacity(normalizeWindowOpacity(value)).catch(() => {});
    },
    [bridge]
  );

  // 0016: 入力デバイスの解決結果を通知し、診断ログへも残す。
  // 録音開始は妨げない（バナー表示のみ）。同じ通知は繰り返さない。
  const { deviceResolution } = live;
  useEffect(() => {
    if (!deviceResolution) return;
    if (deviceResolution.notice && noticeGateRef.current.shouldShow(deviceResolution.notice)) {
      // 既定入力へ落ちた通知だけは録音を続けられる情報通知なので、8 秒で自動的に消す。
      setBanner(
        deviceResolution.matchedBy === 'default' && deviceResolution.fallbackReason
          ? inputDeviceFallbackNotice(deviceResolution.notice)
          : warnNotice(deviceResolution.notice)
      );
    }
    // 完全な deviceId は含めない要約だけを書く。
    if (sessionDir && bridge) {
      bridge.appendDiagnostics(sessionDir, deviceResolution.logSummary).catch(() => {});
    }
  }, [deviceResolution, sessionDir, bridge]);

  // 0016: フォールバック通知だけを 8 秒で自動的に消す。
  // アンマウント時に必ず解除し、古い timer が新しい通知を消さないようにする。
  const autoDismissRef = useRef(
    createNoticeAutoDismiss((expired) => {
      setBanner((cur) => (cur === expired ? null : cur));
    })
  );
  useEffect(() => {
    autoDismissRef.current.schedule(banner);
  }, [banner]);
  useEffect(() => {
    const controller = autoDismissRef.current;
    return () => controller.dispose();
  }, []);

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

  // --- 録音開始/停止 ---
  const chunkSeconds = LIVE_PRESETS[delayMode].chunk;
  const overlapSeconds = LIVE_PRESETS[delayMode].overlap;

  const startRecording = useCallback(async () => {
    setBanner(null);
    // 異常時に確実に警告音を鳴らせるよう、ユーザー操作の中で AudioContext を起こす。
    primeAlertTone();
    if (!title.trim()) {
      setBanner(errorNotice('タイトルを入力してください'));
      return;
    }
    if (!saveFolder.trim()) {
      setBanner(errorNotice('文字起こし保存先を指定してください'));
      return;
    }
    // 開始処理中は「開始中…」を出し、連打を止める（0015）。
    setStarting(true);
    try {
      const check = await checkOutput(saveFolder);
      if (!check.exists && !check.writable) {
        setBanner(errorNotice('保存先が存在せず、作成もできません'));
        return;
      }
      // 録音音声(16kHz mono PCM)は約115MB/時。2時間ぶん + 余裕を要求する。
      if (check.free_bytes !== null && check.free_bytes < 600 * 1024 * 1024) {
        setBanner(errorNotice('保存先の空き容量が不足しています（録音2時間で約230MB必要）'));
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
      try {
        await live.start({
          model,
          delayMode,
          chunkSeconds,
          overlapSeconds,
          deviceId: deviceId || undefined,
          deviceLabel: deviceLabel || undefined,
          outputFolder: session.session_dir,
          outputFilename: session.transcript_filename,
          writeToFile: true
        });
      } catch (startError) {
        // マイク取得・録音初期化の失敗。作成済みセッションを recording のまま残さない（0016）。
        // 後処理は投げない設計なので、元のエラーは必ずユーザーへ届く。
        const reason = startError instanceof Error ? startError.message : '録音開始に失敗しました';
        await cleanupSessionRef.current(session.session_dir, reason);
        setSessionDir(null);
        setTranscriptPath(null);
        throw startError;
      }
      await refreshMics();
    } catch (error) {
      const message = error instanceof Error ? error.message : '録音開始に失敗しました';
      setBanner(errorNotice(message));
    } finally {
      setStarting(false);
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
    deviceLabel,
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
          setBanner(warnNotice(`diagnostics.log へ記録できませんでした（${written.reason ?? 'unknown'}）`));
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
    setBanner(warnNotice('Backend を再起動しています…'));
    try {
      const result = await bridge.restartBackend().catch(() => ({ ok: false }));
      if (!result.ok) {
        setBanner(errorNotice('Backend の再起動に失敗しました。アプリを再起動してください。'));
        return;
      }
      setBanner(okNotice('Backend を再起動しました。再接続します。'));
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
    setBanner(okNotice('全文をコピーしました'));
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
      setBanner(errorNotice('有効な chatgpt.com のURLを入力してください'));
      return;
    }
    if (!bridge) {
      setBanner(warnNotice('Electron 環境でのみブラウザを開けます'));
      return;
    }
    // 連打で複数タブを開かない。
    if (openingGptRef.current) return;
    openingGptRef.current = true;
    try {
      // Main プロセス側で許可ドメインを検証し、Chrome → 既定ブラウザの順に開く（0006）。
      const opened = await bridge.openExternal(gptUrl.trim());
      if (!opened.ok) {
        setBanner(
          errorNotice(
            opened.reason === 'disallowed_domain'
              ? '許可されていない URL です（chatgpt.com のみ開けます）'
              : `ブラウザを開けませんでした（${opened.reason ?? 'unknown'}）`
          )
        );
        return;
      }
      if (opened.opener === 'default') {
        setBanner(warnNotice('Google Chrome が見つからないため、既定のブラウザで開きました'));
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

  // 開始／停止を統合したボタンの状態（0015）。
  const recordButton = resolveRecordButton({
    recording,
    starting,
    finalizing,
    anomaly: Boolean(live.anomaly)
  });

  return (
    <div className="app">
      <header className="titlebar">
        {/* 左: macOS の traffic lights 用の余白。文字は置かない。 */}
        <div className="titlebar-lead" aria-hidden="true" />
        {/* 中央: タイトル。省略しない。 */}
        <div className="titlebar-center">
          <span className="brand">KoeNote</span>
        </div>
        <div className="titlebar-trail">
          {/* 狭幅では CSS で隠す。録音進捗パネルに同じ情報がある。 */}
          <span className={`status-pill tone-${statusTone}`}>
            <span className="dot" /> {phaseLabel}
          </span>
          <button
            type="button"
            className="icon-btn gear"
            onClick={() => setSettingsOpen(true)}
            disabled={recording}
            aria-label="設定を開く"
            title={recording ? '録音中は設定を変更できません' : '設定'}
          >
            <GearIcon />
          </button>
        </div>
      </header>

      <main className="content">
        {banner ? (
          <div
            // 自動消去される通知は浮かせて出し、主要ボタンを押し出さない（0016）。
            className={`banner banner-${banner.tone}${
              banner.kind === 'input-device-fallback' ? ' banner-floating' : ''
            }`}
            role={banner.tone === 'error' ? 'alert' : 'status'}
          >
            {banner.message}
          </div>
        ) : null}
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

        {/* 320px 幅を優先し、表示ラベルと「必須」バッジは置かない（0015）。
            必須である旨は placeholder で示し、アクセシビリティ名は aria-label に残す。 */}
        <section className="title-row">
          <input
            id="title"
            type="text"
            aria-label="会議／セミナータイトル（必須）"
            aria-required="true"
            placeholder="タイトルを入力（必須）"
            value={title}
            disabled={recording}
            onChange={(e) => setTitle(e.target.value)}
          />
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
            preferredHeight={transcriptHeight}
            onPreferredHeightChange={(h) => {
              setTranscriptHeight(h);
              bridge?.setSettings({ [TRANSCRIPT_HEIGHT_KEY]: h }).catch(() => {});
            }}
          />
        </section>

        {/* 3 行構成のコンパクト表示（0015）。情報は削除せず、長い値は省略して title で補う。 */}
        <section className="statusbar">
          <div className="status-line">
            <span className="mono">{formatElapsed(elapsedSec)}</span>
            <span className="ellipsis" title={`入力デバイス: ${live.deviceLabel}`}>入力: {live.deviceLabel}</span>
          </div>
          <div className="status-line">
            <span className="level-meter" title={`入力レベル(RMS)=${live.inputLevel.toFixed(4)}`}>
              <span className="level-track">
                <span
                  className={`level-fill ${live.inputLevel < 0.001 && recording ? 'silent' : ''}`}
                  style={{ width: `${Math.min(100, live.inputLevel * 400)}%` }}
                />
              </span>
            </span>
          </div>
          {/* 0017: 録音状態をこの領域で最も目立つ 1 つの表示にまとめる。
              「録音」「中」に割れないよう nowrap。 */}
          <div className={`status-state tone-${statusTone}`}>
            <span className="state-dot" aria-hidden="true" />
            <span className="state-text">{phaseLabel}</span>
          </div>

          {/* 0017: 時刻はラベルを省略しない。広ければ横並び、狭ければ 2 行へ自動で切り替える。 */}
          <dl className="status-times">
            <dt>
              音声
              <InfoTip text="Backend が最後に音声を受信した時刻" />
            </dt>
            <dd className="mono">{formatIsoTime(live.progress.lastAudioReceivedAt)}</dd>
            <dt>
              文字起こし
              <InfoTip text="最後に文字起こしが完了した時刻" />
            </dt>
            <dd className="mono">{formatIsoTime(live.progress.lastTranscriptionAt)}</dd>
          </dl>

          {/* 0017: 保存先は時刻と同じ行に詰め込まず、独立した行にする（優先度は一段下げる）。 */}
          {live.savedPath ? (
            <div className="status-saved">
              <span className="saved-key">保存先</span>
              <span className="saved-path ellipsis" title={live.savedPath}>
                {baseName(live.savedPath)}
              </span>
            </div>
          ) : null}
        </section>

        {/* 開始／停止・クリア・マイGPT を 1 行に並べる（0015）。 */}
        <section className="actions-main">
          <button
            type="button"
            className={recordButton.tone === 'danger' ? 'btn-danger' : 'btn-primary'}
            onClick={() => {
              if (recordButton.action === 'start') void startRecording();
              else if (recordButton.action === 'stop') void stopRecording();
            }}
            disabled={recordButton.disabled}
            aria-label={recordButton.ariaLabel}
            title={recordButton.ariaLabel}
          >
            {recordButton.label}
          </button>
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
            aria-label="マイGPTをGoogle Chromeで開く"
            title={
              !gptUrlValid
                ? '設定でマイGPTのURLを入力してください'
                : 'マイGPTをGoogle Chromeで開く'
            }
          >
            マイGPT
          </button>
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
      <SettingsModal
        open={settingsOpen}
        current={{ gptUrl, saveFolder, deviceId, deviceLabel, model, delayMode, windowOpacity }}
        onPreviewOpacity={applyOpacity}
        mics={mics}
        recording={recording}
        onPickFolder={async () => (bridge ? bridge.pickFolder() : null)}
        onCheckFolder={async (path) => {
          if (!bridge) return undefined;
          const results = await bridge.pathExists([path]);
          return results[0]?.exists;
        }}
        onSave={(next) => {
          setGptUrl(next.gptUrl);
          setSaveFolder(next.saveFolder);
          // 0016: ユーザーが保存したときだけ deviceId / deviceLabel を永続化する。
          // フォールバックしただけでは書き換えない（USB の一時的な取り外しを恒久化しないため）。
          setDeviceId(next.deviceId);
          setDeviceLabel(next.deviceLabel);
          setWindowOpacity(next.windowOpacity);
          noticeGateRef.current.reset();
          setModel(next.model);
          setDelayMode(next.delayMode);
        }}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
