import { app, BrowserWindow, dialog, ipcMain } from 'electron';
import { join } from 'node:path';
import { startBackend, stopBackend, waitForBackend, BACKEND_PORT } from './backend';
import { registerIpcHandlers } from './ipc/handlers';

const isDev = process.env.NODE_ENV === 'development';
let mainWindow: BrowserWindow | null = null;
let isRecording = false;
let quitting = false;

process.env.BRIDGELOG_PORT = String(BACKEND_PORT);

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 960,
    height: 900,
    minWidth: 460,
    minHeight: 650,
    backgroundColor: '#09090B',
    titleBarStyle: 'hiddenInset',
    title: 'BridgeLog',
    show: false,
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
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

ipcMain.on('app:recordingState', (_evt, recording: boolean) => {
  isRecording = Boolean(recording);
});

app.whenReady().then(async () => {
  registerIpcHandlers(() => mainWindow);
  await startBackend();
  const healthy = await waitForBackend(60000);
  if (!healthy) {
    dialog.showMessageBoxSync({
      type: 'error',
      message: 'Backend の起動に失敗しました',
      detail:
        'Python 依存関係が未インストールの可能性があります。README の手順で backend/requirements.txt をインストールしてください。'
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
  await stopBackend();
  app.exit(0);
});
