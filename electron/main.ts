import { app, BrowserWindow, dialog, ipcMain, Notification, powerSaveBlocker } from 'electron';
import { join } from 'node:path';
import {
  startBackend,
  stopBackend,
  waitForBackend,
  getBackendLogTail,
  BACKEND_PORT,
  type BackendExitInfo
} from './backend';
import { registerIpcHandlers } from './ipc/handlers';

const isDev = process.env.NODE_ENV === 'development';
let mainWindow: BrowserWindow | null = null;
let isRecording = false;
let quitting = false;
let powerSaveBlockerId: number | null = null;

process.env.KOENOTE_PORT = String(BACKEND_PORT);

// 長時間録音中に Chromium がタイマーを間引く/レンダラを背面化するのを止める。
// webPreferences.backgroundThrottling:false だけではオクルージョン起因の背面化を塞げない。
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
// 異常検知時の警告音を、ユーザー操作を待たずに鳴らせるようにする。
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

interface AnomalyInfo {
  reason: string;
  detail: string;
  sessionDir?: string | null;
  transcriptPath?: string | null;
  atSeconds?: number;
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    // 0015: 320px 幅を常用する前提の初期サイズ。手動での拡大・縮小は従来どおり。
    width: 320,
    height: 530,
    // 0009: 狭いウィンドウでも主要操作を使えるようにする。
    // Renderer 側は 380px / 340px のブレークポイントで縦積みへ切り替える。
    minWidth: 320,
    minHeight: 480,
    backgroundColor: '#09090B',
    titleBarStyle: 'hiddenInset',
    title: 'KoeNote',
    show: false,
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // ウィンドウが隠れても音声キャプチャとウォッチドッグを止めない。
      backgroundThrottling: false
    }
  });

  mainWindow.once('ready-to-show', () => mainWindow?.show());

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(join(__dirname, '..', '..', 'frontend', 'dist', 'index.html'));
  }

  // 録音中の意図しない終了を防ぐ確認。
  mainWindow.on('close', (event) => {
    if (isRecording && !quitting) {
      event.preventDefault();
      const choice = dialog.showMessageBoxSync(mainWindow!, {
        type: 'warning',
        buttons: ['録音を続ける', '停止して終了'],
        defaultId: 0,
        cancelId: 0,
        message: '録音中です。終了しますか？',
        detail: '終了すると、確定していない音声は最終確定されません。'
      });
      if (choice === 1) {
        quitting = true;
        mainWindow?.close();
      }
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/** 録音中はスリープ/App Nap を抑止する。停止で必ず解除する。 */
function applyPowerSaveBlocker(recording: boolean): void {
  if (recording && powerSaveBlockerId === null) {
    // 画面を強制点灯させたいわけではないので prevent-display-sleep は使わない。
    powerSaveBlockerId = powerSaveBlocker.start('prevent-app-suspension');
  } else if (!recording && powerSaveBlockerId !== null) {
    if (powerSaveBlocker.isStarted(powerSaveBlockerId)) powerSaveBlocker.stop(powerSaveBlockerId);
    powerSaveBlockerId = null;
  }
}

/** ウィンドウが隠れていてもユーザーに届く経路で知らせる。 */
function alertUser(title: string, body: string, buttons: string[]): Promise<number> {
  if (Notification.isSupported()) {
    new Notification({ title, body, urgency: 'critical' }).show();
  }
  app.dock?.bounce('critical');
  mainWindow?.flashFrame(true);
  const target = mainWindow;
  if (!target) {
    dialog.showMessageBox({ type: 'error', message: title, detail: body, buttons });
    return Promise.resolve(0);
  }
  // showMessageBoxSync はメインプロセスを止めてしまうため、必ず非同期版を使う。
  return dialog
    .showMessageBox(target, { type: 'error', message: title, detail: body, buttons, defaultId: 0 })
    .then((result) => result.response);
}

function handleBackendExit(info: BackendExitInfo): void {
  mainWindow?.webContents.send('backend:exited', info);
  if (!isRecording) return;

  const label =
    info.reason === 'killed_possibly_oom'
      ? 'Backend がメモリ不足で強制終了した可能性があります'
      : 'Backend が異常終了しました';
  alertUser(
    'KoeNote: 文字起こしが中断されました',
    `${label}\n(code=${info.code} signal=${info.signal})\n\n確定済みの文字起こしと録音音声は保存されています。`,
    ['OK', 'Backendを再起動']
  ).then((response) => {
    if (response === 1) {
      mainWindow?.webContents.send('backend:restartRequested');
    }
  });
}

ipcMain.on('app:recordingState', (_evt, recording: boolean) => {
  isRecording = Boolean(recording);
  applyPowerSaveBlocker(isRecording);
  mainWindow?.webContents.setBackgroundThrottling(!isRecording);
});

// renderer 側のウォッチドッグが検知した異常（Backend は生きているが進まない等）。
ipcMain.on('app:anomaly', (_evt, info: AnomalyInfo) => {
  alertUser(
    'KoeNote: 文字起こしが停止しました',
    `${info.detail}\n\n確定済みの文字起こしと録音音声は保存されています。`,
    ['OK']
  );
});

app.whenReady().then(async () => {
  registerIpcHandlers(() => mainWindow);
  await startBackend(handleBackendExit);
  const healthy = await waitForBackend(60000);
  if (!healthy) {
    dialog.showMessageBoxSync({
      type: 'error',
      message: 'Backend の起動に失敗しました',
      detail:
        'Python 依存関係が未インストールの可能性があります。README の手順で backend/requirements.txt をインストールしてください。\n\n' +
        getBackendLogTail(20)
    });
  }
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  quitting = true;
});

app.on('will-quit', async (event) => {
  // Backend（および子 Worker / ffmpeg）を確実に停止してから終了する。
  event.preventDefault();
  applyPowerSaveBlocker(false);
  await stopBackend();
  app.exit(0);
});
