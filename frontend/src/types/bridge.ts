export interface BridgeApi {
  backendOrigin: string;
  wsOrigin: string;
  getSettings(): Promise<Record<string, unknown>>;
  setSettings(data: Record<string, unknown>): Promise<Record<string, unknown>>;
  pickFolder(): Promise<string | null>;
  pickAttachments(): Promise<string[]>;
  pickAudioFile(): Promise<string | null>;
  pathExists(paths: string[]): Promise<Array<{ path: string; exists: boolean }>>;
  revealInFinder(path: string): Promise<boolean>;
  openExternal(url: string): Promise<{ ok: boolean; reason?: string }>;
  writeClipboard(text: string): Promise<boolean>;
  getBackendLog(): Promise<string>;
  setRecordingState(recording: boolean): void;
}

declare global {
  interface Window {
    bridge: BridgeApi;
  }
}

export {};
