import { spawn, ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { app } from 'electron';
import http from 'node:http';

export const BACKEND_HOST = '127.0.0.1';
export const BACKEND_PORT = Number(process.env.BRIDGELOG_PORT || 8000);
export const BACKEND_ORIGIN = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

let backendProcess: ChildProcess | null = null;
const logBuffer: string[] = [];
const MAX_LOG_LINES = 500;

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

function pushLog(line: string): void {
  logBuffer.push(line);
  if (logBuffer.length > MAX_LOG_LINES) logBuffer.splice(0, logBuffer.length - MAX_LOG_LINES);
}

export function getBackendLog(): string {
  return logBuffer.join('');
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

export async function startBackend(): Promise<void> {
  // 既に外部で起動済みなら再利用する（開発時の二重起動防止）。
  if (await healthCheckOnce()) {
    pushLog('[backend] 既存のBackendを検出したため再利用します。\n');
    return;
  }
  if (backendProcess) return;

  const python = resolvePython();
  const cwd = backendDir();
  pushLog(`[backend] 起動: ${python} -m uvicorn main:app (cwd=${cwd})\n`);
  backendProcess = spawn(
    python,
    ['-m', 'uvicorn', 'main:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)],
    { cwd, env: { ...process.env }, stdio: ['ignore', 'pipe', 'pipe'] }
  );

  backendProcess.stdout?.on('data', (d) => pushLog(`[backend] ${d}`));
  backendProcess.stderr?.on('data', (d) => pushLog(`[backend] ${d}`));
  backendProcess.on('exit', (code, signal) => {
    pushLog(`[backend] 終了 code=${code} signal=${signal}\n`);
    backendProcess = null;
  });
}

export async function stopBackend(): Promise<void> {
  const proc = backendProcess;
  if (!proc || proc.killed) {
    backendProcess = null;
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
}
