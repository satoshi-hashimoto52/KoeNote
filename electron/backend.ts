import { spawn, ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { app } from 'electron';
import http from 'node:http';

export const BACKEND_HOST = '127.0.0.1';
export const BACKEND_PORT = Number(process.env.BRIDGELOG_PORT || 8000);
export const BACKEND_ORIGIN = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

export type BackendExitReason = 'exited' | 'crashed' | 'killed_possibly_oom';

export interface BackendExitInfo {
  code: number | null;
  signal: string | null;
  reason: BackendExitReason;
  logTail: string;
}

let backendProcess: ChildProcess | null = null;
let exitListener: ((info: BackendExitInfo) => void) | null = null;
let stoppingIntentionally = false;
const logBuffer: string[] = [];
// realtime のログは窓ごと1行に絞ったので、この行数で数十分ぶんの履歴が残る。
const MAX_LOG_LINES = 2000;
const EXIT_LOG_TAIL_LINES = 40;

function projectRoot(): string {
  // dev: electron/dist/backend.cjs -> ルートは ../../ 。 packaged: resources/ 配下。
  return app.isPackaged ? process.resourcesPath : join(__dirname, '..', '..');
}

function backendDir(): string {
  return join(projectRoot(), 'backend');
}

function resolvePython(): string {
  // .venv があれば優先。なければ system python3。
  const venvPython = join(projectRoot(), '.venv', 'bin', 'python');
  if (existsSync(venvPython)) return venvPython;
  const backendVenv = join(backendDir(), '.venv', 'bin', 'python');
  if (existsSync(backendVenv)) return backendVenv;
  return process.env.BRIDGELOG_PYTHON || 'python3';
}

// GUI から起動した Electron は PATH が最小構成になり Homebrew の ffmpeg/ffprobe を
// 見つけられないことがある。子 Backend が必ず ffmpeg を解決できるよう PATH を補い、
// 見つかれば BRIDGELOG_FFMPEG_DIR も設定する（realtime のデコード失敗＝空文字化を防ぐ）。
const FFMPEG_CANDIDATE_DIRS = ['/opt/homebrew/bin', '/usr/local/bin'];

function detectFfmpegDir(): string | null {
  for (const dir of FFMPEG_CANDIDATE_DIRS) {
    if (existsSync(join(dir, 'ffmpeg')) && existsSync(join(dir, 'ffprobe'))) return dir;
  }
  return null;
}

function buildBackendEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  const existing = (env.PATH || '').split(':').filter(Boolean);
  const merged = [...FFMPEG_CANDIDATE_DIRS, '/usr/bin', '/bin', '/usr/sbin', '/sbin', ...existing];
  env.PATH = Array.from(new Set(merged)).join(':');
  const ffmpegDir = detectFfmpegDir();
  if (ffmpegDir && !env.BRIDGELOG_FFMPEG_DIR) {
    env.BRIDGELOG_FFMPEG_DIR = ffmpegDir;
  }
  pushLog(`[backend] PATH=${env.PATH}\n[backend] ffmpeg_dir=${env.BRIDGELOG_FFMPEG_DIR || '(PATH解決)'}\n`);
  return env;
}

function pushLog(line: string): void {
  logBuffer.push(line);
  if (logBuffer.length > MAX_LOG_LINES) logBuffer.splice(0, logBuffer.length - MAX_LOG_LINES);
}

// リングバッファは UI から見るためのもので、Electron の stdout には出ない。
// 事後解析に不可欠なライフサイクル/エラー行だけは端末にも出す。
function pushImportantLog(line: string): void {
  pushLog(line);
  process.stdout.write(line.endsWith('\n') ? line : `${line}\n`);
}

export function getBackendLog(): string {
  return logBuffer.join('');
}

export function getBackendLogTail(lines = EXIT_LOG_TAIL_LINES): string {
  return logBuffer.slice(-lines).join('');
}

export function isBackendRunning(): boolean {
  return backendProcess !== null && backendProcess.exitCode === null;
}

function healthCheckOnce(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_ORIGIN}/api/health`, { timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

export async function waitForBackend(timeoutMs = 60000): Promise<boolean> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await healthCheckOnce()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function classifyExit(code: number | null, signal: string | null): BackendExitReason {
  // macOS の OOM kill は SIGKILL + code=null で現れる。
  if (signal === 'SIGKILL' && code === null) return 'killed_possibly_oom';
  if (signal !== null) return 'crashed';
  if (code !== null && code !== 0) return 'crashed';
  return 'exited';
}

export async function startBackend(onExit?: (info: BackendExitInfo) => void): Promise<void> {
  if (onExit) exitListener = onExit;
  // 既に外部で起動済みなら再利用する（開発時の二重起動防止）。
  if (await healthCheckOnce()) {
    pushImportantLog('[backend] 既存のBackendを検出したため再利用します。\n');
    return;
  }
  if (backendProcess) return;

  const python = resolvePython();
  const cwd = backendDir();
  pushImportantLog(`[backend] 起動: ${python} -m uvicorn main:app (cwd=${cwd})\n`);
  backendProcess = spawn(
    python,
    [
      '-m',
      'uvicorn',
      'main:app',
      '--host',
      BACKEND_HOST,
      '--port',
      String(BACKEND_PORT),
      // PCM フレームは 4KB 程度。既定の 16MiB より遥かに小さい上限にしておくことで、
      // 将来また「全音声を送り直す」実装が入っても 25 分後の時間爆弾ではなく即座に失敗する。
      '--ws-max-size',
      '1048576',
      '--ws-max-queue',
      '256',
      // 推論は別タスクへ分離したので PONG は常に読まれるが、余裕を持たせておく。
      '--ws-ping-interval',
      '20',
      '--ws-ping-timeout',
      '60',
      // PCM は圧縮が効かないうえ CPU を食うだけなので per-message deflate は切る。
      '--ws-per-message-deflate',
      'false',
      '--timeout-keep-alive',
      '30'
    ],
    { cwd, env: buildBackendEnv(), stdio: ['ignore', 'pipe', 'pipe'] }
  );

  backendProcess.stdout?.on('data', (d) => pushLog(`[backend] ${d}`));
  backendProcess.stderr?.on('data', (d) => pushImportantLog(`[backend] ${d}`));
  backendProcess.on('exit', (code, signal) => {
    const reason = classifyExit(code, signal);
    pushImportantLog(`[backend] 終了 code=${code} signal=${signal} reason=${reason}\n`);
    backendProcess = null;
    // 明示的な停止（アプリ終了・再起動）は異常として通知しない。
    if (stoppingIntentionally) return;
    exitListener?.({ code, signal, reason, logTail: getBackendLogTail() });
  });
}

export async function stopBackend(): Promise<void> {
  const proc = backendProcess;
  stoppingIntentionally = true;
  if (!proc || proc.killed) {
    backendProcess = null;
    stoppingIntentionally = false;
    return;
  }
  await new Promise<void>((resolve) => {
    let settled = false;
    const done = () => {
      if (!settled) {
        settled = true;
        resolve();
      }
    };
    proc.once('exit', done);
    try {
      proc.kill('SIGTERM');
    } catch {
      done();
      return;
    }
    // 猶予後に SIGKILL で確実に回収する（Worker/ffmpeg 残留防止）。
    setTimeout(() => {
      if (!settled) {
        try {
          proc.kill('SIGKILL');
        } catch {
          /* noop */
        }
        done();
      }
    }, 5000);
  });
  backendProcess = null;
  stoppingIntentionally = false;
}

export async function restartBackend(): Promise<boolean> {
  pushImportantLog('[backend] 再起動を要求されました。\n');
  await stopBackend();
  await startBackend();
  const healthy = await waitForBackend(30000);
  pushImportantLog(`[backend] 再起動結果 healthy=${healthy}\n`);
  return healthy;
}
