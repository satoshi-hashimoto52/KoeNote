import { execFile } from 'node:child_process';

/** マイGPT URL として開いてよいホスト（任意ドメインは開かない）。 */
export const ALLOWED_GPT_HOSTS = ['chatgpt.com', 'chat.openai.com'];

export const CHROME_BUNDLE_ID = 'com.google.Chrome';

export function isAllowedGptUrl(raw: string): boolean {
  try {
    const url = new URL(String(raw ?? ''));
    if (url.protocol !== 'https:') return false;
    const host = url.hostname.toLowerCase();
    return ALLOWED_GPT_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
  } catch {
    return false;
  }
}

export type Opener = 'chrome' | 'default';

export interface OpenResult {
  ok: boolean;
  opener?: Opener;
  reason?: string;
}

export interface OpenDeps {
  /** Chrome をバンドル ID 指定で開く。成功なら true。 */
  openInChrome(url: string): Promise<boolean>;
  /** 既定ブラウザで開く。失敗時は throw。 */
  openDefault(url: string): Promise<void>;
}

/**
 * macOS で Chrome をバンドル ID 指定で開く。
 *
 * `execFile` へ引数を配列で渡す。シェルを介さないので URL の内容に関わらず
 * コマンドが組み立てられることはない（`shell: true` は使わない）。
 */
export function openInChromeMac(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile('open', ['-b', CHROME_BUNDLE_ID, url], (error) => resolve(!error));
  });
}

/**
 * 許可済み URL を Chrome で開く。開けなければ既定ブラウザへフォールバックする。
 *
 * 呼び出し側（Renderer）はフォールバックしたことを表示できるよう、
 * どちらで開いたかを `opener` で受け取る。
 */
export async function openGptUrl(url: string, deps: OpenDeps): Promise<OpenResult> {
  if (!isAllowedGptUrl(url)) {
    return { ok: false, reason: 'disallowed_domain' };
  }
  try {
    if (await deps.openInChrome(url)) {
      return { ok: true, opener: 'chrome' };
    }
  } catch {
    // Chrome の起動失敗はフォールバックで扱う。ここでは握り潰してよい。
  }
  try {
    await deps.openDefault(url);
    return { ok: true, opener: 'default' };
  } catch (error) {
    return {
      ok: false,
      reason: error instanceof Error ? error.message : 'open_failed'
    };
  }
}
