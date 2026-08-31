export interface BackendExitInfo {
  code: number | null;
  signal: string | null;
  reason: string;
  logTail: string;
}

export interface AnomalyInfo {
  reason: string;
  detail: string;
  sessionDir?: string | null;
  transcriptPath?: string | null;
  atSeconds?: number;
}

export interface BridgeApi {
  backendOrigin: string;
  wsOrigin: string;
  getSettings(): Promise<Record<string, unknown>>;
  setSettings(data: Record<string, unknown>): Promise<Record<string, unknown>>;
  pickFolder(): Promise<string | null>;
  pickAudioFile(): Promise<string | null>;
  pathExists(paths: string[]): Promise<Array<{ path: string; exists: boolean }>>;
  revealInFinder(path: string): Promise<boolean>;
  openExternal(url: string): Promise<{ ok: boolean; opener?: 'chrome' | 'default'; reason?: string }>;
  writeClipboard(text: string): Promise<boolean>;
  getBackendLog(): Promise<string>;
  /** ウィンドウの不透明度を 0.70〜1.00 で設定する（0018）。main 側で clamp される。 */
  setWindowOpacity(value: number): Promise<{ ok: boolean; opacity: number }>;
  restartBackend(): Promise<{ ok: boolean }>;
  appendTranscriptNotice(path: string, text: string): Promise<{ ok: boolean; reason?: string }>;
  appendDiagnostics(sessionDir: string, text: string): Promise<{ ok: boolean; reason?: string }>;
  setRecordingState(recording: boolean): void;
  notifyAnomaly(info: AnomalyInfo): void;
  onBackendExited(callback: (info: BackendExitInfo) => void): () => void;
  onBackendRestartRequested(callback: () => void): () => void;
}

declare global {
  interface Window {
    bridge: BridgeApi;
  }
}

export {};
