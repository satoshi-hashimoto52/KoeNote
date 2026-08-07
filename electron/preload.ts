import { contextBridge, ipcRenderer } from 'electron';

// Renderer から Node API は使わせず、必要最小限の IPC だけを公開する。
const bridge = {
  backendOrigin: `http://127.0.0.1:${process.env.BRIDGELOG_PORT || 8000}`,
  wsOrigin: `ws://127.0.0.1:${process.env.BRIDGELOG_PORT || 8000}`,

  getSettings: (): Promise<Record<string, unknown>> => ipcRenderer.invoke('settings:get'),
  setSettings: (data: Record<string, unknown>): Promise<Record<string, unknown>> =>
    ipcRenderer.invoke('settings:set', data),

  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('dialog:pickFolder'),
  pickAttachments: (): Promise<string[]> => ipcRenderer.invoke('dialog:pickAttachments'),
  pickAudioFile: (): Promise<string | null> => ipcRenderer.invoke('dialog:pickAudioFile'),

  pathExists: (paths: string[]): Promise<Array<{ path: string; exists: boolean }>> =>
    ipcRenderer.invoke('fs:pathExists', paths),
  revealInFinder: (path: string): Promise<boolean> => ipcRenderer.invoke('shell:revealInFinder', path),
  openExternal: (url: string): Promise<{ ok: boolean; reason?: string }> =>
    ipcRenderer.invoke('shell:openExternal', url),
  writeClipboard: (text: string): Promise<boolean> => ipcRenderer.invoke('clipboard:write', text),
  getBackendLog: (): Promise<string> => ipcRenderer.invoke('backend:log'),

  // 録音中フラグを main に伝え、終了確認ダイアログの判断に使う。
  setRecordingState: (recording: boolean): void => {
    ipcRenderer.send('app:recordingState', recording);
  }
};

contextBridge.exposeInMainWorld('bridge', bridge);

export type BridgeApi = typeof bridge;
