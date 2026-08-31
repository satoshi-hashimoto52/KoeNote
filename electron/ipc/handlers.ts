import { app, BrowserWindow, clipboard, dialog, ipcMain, shell } from 'electron';
import { existsSync, readFileSync } from 'node:fs';
import { writeFile, mkdir, appendFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { getBackendLog, restartBackend } from '../backend';
import { appendDiagnosticsLine } from './diagnostics';
import { isAllowedGptUrl, openGptUrl, openInChromeMac } from './openExternal';
import { planSettingsMigration } from './settingsMigration';

const AUDIO_FILTERS = [
  { name: '音声/動画', extensions: ['mp4', 'mov', 'm4v', 'mkv', 'avi', 'webm', 'mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'opus'] }
];

function settingsPath(): string {
  return join(app.getPath('userData'), 'koenote-settings.json');
}

/** 旧 BridgeLog の設定ファイル。読むだけで、書き換えも削除もしない。 */
function legacySettingsPath(): string {
  return join(app.getPath('appData'), 'BridgeLog', 'bridgelog-settings.json');
}

function readJsonFile(path: string): unknown {
  try {
    if (!existsSync(path)) return null;
    return JSON.parse(readFileSync(path, 'utf-8')) as unknown;
  } catch {
    // 壊れていても移行を諦めるだけ。旧ファイルには一切触れない。
    return null;
  }
}

/**
 * KoeNote 側に設定がまだ無い初回起動時だけ、旧 BridgeLog の設定を引き継ぐ。
 * 失敗しても既定値で起動を続ける（旧設定は破壊しない）。
 */
async function migrateLegacySettingsOnce(): Promise<Record<string, unknown>> {
  const current = readSettings();
  const migrated = planSettingsMigration(current, readJsonFile(legacySettingsPath()));
  if (!migrated) return current;
  try {
    await writeSettingsAtomic(migrated);
    return migrated;
  } catch {
    return current;
  }
}

function readSettings(): Record<string, unknown> {
  try {
    if (existsSync(settingsPath())) {
      return JSON.parse(readFileSync(settingsPath(), 'utf-8')) as Record<string, unknown>;
    }
  } catch {
    /* 壊れていても既定へフォールバック */
  }
  return {};
}

async function writeSettingsAtomic(data: Record<string, unknown>): Promise<void> {
  const target = settingsPath();
  await mkdir(dirname(target), { recursive: true });
  const tmp = `${target}.tmp`;
  await writeFile(tmp, JSON.stringify(data, null, 2), 'utf-8');
  const { rename } = await import('node:fs/promises');
  await rename(tmp, target);
}

export function registerIpcHandlers(getWindow: () => BrowserWindow | null): void {
  ipcMain.handle('settings:get', () => migrateLegacySettingsOnce());

  ipcMain.handle('settings:set', async (_evt, data: Record<string, unknown>) => {
    const merged = { ...readSettings(), ...(data || {}) };
    await writeSettingsAtomic(merged);
    return merged;
  });

  ipcMain.handle('dialog:pickFolder', async () => {
    const win = getWindow();
    const result = await dialog.showOpenDialog(win!, {
      title: '文字起こし保存先フォルダを選択',
      properties: ['openDirectory', 'createDirectory']
    });
    return result.canceled ? null : result.filePaths[0];
  });

  ipcMain.handle('dialog:pickAudioFile', async () => {
    const win = getWindow();
    const result = await dialog.showOpenDialog(win!, {
      title: '音声/動画ファイルを選択',
      properties: ['openFile'],
      filters: AUDIO_FILTERS
    });
    return result.canceled ? null : result.filePaths[0];
  });

  ipcMain.handle('fs:pathExists', (_evt, paths: string[]) => {
    return (paths || []).map((p) => ({ path: p, exists: existsSync(p) }));
  });

  ipcMain.handle('shell:revealInFinder', (_evt, path: string) => {
    if (path && existsSync(path)) {
      shell.showItemInFolder(path);
      return true;
    }
    return false;
  });

  // マイGPT は Google Chrome で開く。無ければ既定ブラウザへフォールバックする（0006）。
  ipcMain.handle('shell:openExternal', (_evt, url: string) =>
    openGptUrl(url, {
      openInChrome: openInChromeMac,
      openDefault: (target) => shell.openExternal(target)
    })
  );

  ipcMain.handle('clipboard:write', (_evt, text: string) => {
    clipboard.writeText(String(text ?? ''));
    return true;
  });

  ipcMain.handle('backend:log', () => getBackendLog());

  ipcMain.handle('backend:restart', async () => {
    const ok = await restartBackend();
    return { ok };
  });

  // Backend が死んでいても記録できるよう、diagnostics.log はローカル I/O で書く。
  // Backend の HTTP API 経由だと reason=backend_exit / no_heartbeat では必ず失敗する（0010）。
  ipcMain.handle('diagnostics:append', async (_evt, sessionDir: string, text: string) => {
    const result = await appendDiagnosticsLine(sessionDir, text);
    if (!result.ok) {
      // 握り潰さない。Electron のログに残して、後から追えるようにする。
      console.error(`[diagnostics] 追記に失敗しました path=${result.path ?? sessionDir} reason=${result.reason}`);
    }
    return result;
  });

  ipcMain.handle('transcript:appendNotice', async (_evt, path: string, text: string) => {
    const target = String(path ?? '');
    if (!target || !existsSync(target)) return { ok: false, reason: 'not_found' };
    try {
      await appendFile(target, `${String(text ?? '')}\n`, 'utf-8');
      return { ok: true };
    } catch (error) {
      return { ok: false, reason: error instanceof Error ? error.message : 'append_failed' };
    }
  });
}

export { isAllowedGptUrl };
