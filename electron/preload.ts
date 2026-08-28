import { contextBridge, ipcRenderer } from 'electron';

// Renderer から Node API は使わせず、必要最小限の IPC だけを公開する。
const bridge = {
  backendOrigin: `http://127.0.0.1:${process.env.BRIDGELOG_PORT || 8000}`,
  wsOrigin: `ws://127.0.0.1:${process.env.BRIDGELOG_PORT || 8000}`,

  getSettings: (): Promise<Record<string, unknown>> => ipcRenderer.invoke('settings:get'),
  setSettings: (data: Record<string, unknown>): Promise<Record<string, unknown>> =>
    ipcRenderer.invoke('settings:set', data),

  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('dialog:pickFolder'),
  pickAudioFile: (): Promise<string | null> => ipcRenderer.invoke('dialog:pickAudioFile'),

  pathExists: (paths: string[]): Promise<Array<{ path: string; exists: boolean }>> =>
    ipcRenderer.invoke('fs:pathExists', paths),
  revealInFinder: (path: string): Promise<boolean> => ipcRenderer.invoke('shell:revealInFinder', path),
  openExternal: (url: string): Promise<{ ok: boolean; opener?: 'chrome' | 'default'; reason?: string }> =>
    ipcRenderer.invoke('shell:openExternal', url),
  writeClipboard: (text: string): Promise<boolean> => ipcRenderer.invoke('clipboard:write', text),
  getBackendLog: (): Promise<string> => ipcRenderer.invoke('backend:log'),
  restartBackend: (): Promise<{ ok: boolean }> => ipcRenderer.invoke('backend:restart'),

  // 異常停止時に文字起こしTXTへ中断マーカーを残す。TXT はユーザーが GPT へ渡す成果物で、
  // 無言で途切れるとそこでは中断が見えないため。
  appendTranscriptNotice: (path: string, text: string): Promise<{ ok: boolean; reason?: string }> =>
    ipcRenderer.invoke('transcript:appendNotice', path, text),

  // diagnostics.log への追記。Backend の死活に依存しない（0010）。
  appendDiagnostics: (sessionDir: string, text: string): Promise<{ ok: boolean; reason?: string }> =>
    ipcRenderer.invoke('diagnostics:append', sessionDir, text),

  // 録音中フラグを main に伝え、終了確認ダイアログ・スリープ抑止の判断に使う。
  setRecordingState: (recording: boolean): void => {
    ipcRenderer.send('app:recordingState', recording);
  },

  // renderer 側ウォッチドッグが検知した異常を main へ渡す。
  // ネイティブ通知/ダイアログは main しか出せず、renderer は隠れている場合もある。
  notifyAnomaly: (info: {
    reason: string;
    detail: string;
    sessionDir?: string | null;
    transcriptPath?: string | null;
    atSeconds?: number;
  }): void => {
    ipcRenderer.send('app:anomaly', info);
  },

  // main -> renderer の購読。生の IpcRendererEvent は渡さず、解除関数を返す。
  onBackendExited: (
    callback: (info: { code: number | null; signal: string | null; reason: string; logTail: string }) => void
  ): (() => void) => {
    const listener = (_event: unknown, info: Parameters<typeof callback>[0]) => callback(info);
    ipcRenderer.on('backend:exited', listener);
    return () => {
      ipcRenderer.removeListener('backend:exited', listener);
    };
  },

  onBackendRestartRequested: (callback: () => void): (() => void) => {
    const listener = () => callback();
    ipcRenderer.on('backend:restartRequested', listener);
    return () => {
      ipcRenderer.removeListener('backend:restartRequested', listener);
    };
  }
};

contextBridge.exposeInMainWorld('bridge', bridge);

export type BridgeApi = typeof bridge;
