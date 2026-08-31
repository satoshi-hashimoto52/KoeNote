/**
 * 入力デバイスの解決と取得（0016）。
 *
 * Chromium は `MediaDeviceInfo.deviceId` を origin ごとに異なる値へソルトする。
 * 開発版（`http://localhost:5173`）で保存した ID はパッケージ版（`file://`）には
 * 存在せず、`exact` 指定すると必ず `OverconstrainedError` になる。
 *
 * そのため保存値をそのまま使わず、必ず現在のデバイス一覧と照合してから使う。
 * `groupId` も origin 依存の可能性があるため、安定識別子として前提にしない。
 */

/** 保存済み ID がそのまま使えた / ラベルで再解決した / 既定入力へ落ちた。 */
export type DeviceMatch = 'deviceId' | 'label' | 'default';

/** 既定入力へ落ちた、またはラベルで解決し直した理由。 */
export type DeviceFallbackReason = 'device_id_not_found' | 'label_ambiguous' | 'no_input_device';

export const FALLBACK_NOTICE =
  '保存された入力デバイスが見つからないため、既定の入力デバイスを使用しました。必要に応じて設定から選び直してください。';
export const RELABEL_NOTICE = '入力デバイスの識別情報を現在の環境に更新しました。';
export const NO_INPUT_NOTICE = '利用できる入力デバイスがありません。マイクの接続とマイク権限を確認してください。';

/** 「OS 既定入力を使う」ことを表す予約値。ブラウザが返す `default` と同じ扱い。 */
export const DEFAULT_DEVICE_ID = 'default';

export interface ResolvedInputDevice {
  /** 入力デバイスが 1 つも無い場合だけ false。 */
  ok: boolean;
  /** `getUserMedia` へ exact 指定する ID。null なら指定しない（OS 既定入力）。 */
  effectiveDeviceId: string | null;
  matchedBy: DeviceMatch;
  fallbackReason: DeviceFallbackReason | null;
  /** ユーザーへ出す非ブロッキング通知。不要なら null。 */
  notice: string | null;
  /** 診断ログ用の要約。完全な deviceId は含めない。 */
  logSummary: string;
}

function audioInputs(devices: readonly MediaDeviceInfo[]): MediaDeviceInfo[] {
  return (devices ?? []).filter((d) => d && d.kind === 'audioinput');
}

function summarize(matchedBy: DeviceMatch, reason: DeviceFallbackReason | null, count: number): string {
  // deviceId は個体識別に使えるため、完全な値はログへ出さない。
  return `input_device matchedBy=${matchedBy} fallbackReason=${reason ?? 'none'} candidates=${count}`;
}

/**
 * 保存済みの ID / ラベルと、現在のデバイス一覧から、実際に使う制約を決める。
 *
 * 判定順序:
 *   1. 入力デバイスが 0 件 → エラー
 *   2. 保存 ID が未設定 / `default` → OS 既定入力
 *   3. 保存 ID が一覧に存在 → その ID を exact 指定
 *   4. ID は無効だがラベル完全一致が 1 件だけ → 現 origin の ID へ再解決
 *   5. それ以外 → OS 既定入力
 */
export function resolveInputDevice(
  savedDeviceId: string | null | undefined,
  savedDeviceLabel: string | null | undefined,
  devices: readonly MediaDeviceInfo[]
): ResolvedInputDevice {
  const inputs = audioInputs(devices);
  if (inputs.length === 0) {
    return {
      ok: false,
      effectiveDeviceId: null,
      matchedBy: 'default',
      fallbackReason: 'no_input_device',
      notice: NO_INPUT_NOTICE,
      logSummary: summarize('default', 'no_input_device', 0)
    };
  }

  const savedId = String(savedDeviceId ?? '').trim();
  const savedLabel = String(savedDeviceLabel ?? '').trim();

  // 未設定と `default` はどちらも「OS 既定入力」。通知は出さない（ユーザーの意図どおり）。
  if (!savedId || savedId === DEFAULT_DEVICE_ID) {
    return {
      ok: true,
      effectiveDeviceId: null,
      matchedBy: 'default',
      fallbackReason: null,
      notice: null,
      logSummary: summarize('default', null, inputs.length)
    };
  }

  if (inputs.some((d) => d.deviceId === savedId)) {
    return {
      ok: true,
      effectiveDeviceId: savedId,
      matchedBy: 'deviceId',
      fallbackReason: null,
      notice: null,
      logSummary: summarize('deviceId', null, inputs.length)
    };
  }

  // ID が無効。同じ物理デバイスをラベルで引き当てられるなら、そちらを使う。
  if (savedLabel) {
    const byLabel = inputs.filter((d) => d.label === savedLabel && d.deviceId !== DEFAULT_DEVICE_ID);
    if (byLabel.length === 1) {
      return {
        ok: true,
        effectiveDeviceId: byLabel[0].deviceId,
        matchedBy: 'label',
        fallbackReason: 'device_id_not_found',
        notice: RELABEL_NOTICE,
        logSummary: summarize('label', 'device_id_not_found', inputs.length)
      };
    }
    if (byLabel.length > 1) {
      // 同名が複数。勝手に選ぶと誤った音源を録りかねないので既定入力にする。
      return {
        ok: true,
        effectiveDeviceId: null,
        matchedBy: 'default',
        fallbackReason: 'label_ambiguous',
        notice: FALLBACK_NOTICE,
        logSummary: summarize('default', 'label_ambiguous', inputs.length)
      };
    }
  }

  return {
    ok: true,
    effectiveDeviceId: null,
    matchedBy: 'default',
    fallbackReason: 'device_id_not_found',
    notice: FALLBACK_NOTICE,
    logSummary: summarize('default', 'device_id_not_found', inputs.length)
  };
}

/** `getUserMedia` へ渡す audio 制約。deviceId が null なら指定を外す。 */
export function buildAudioConstraints(deviceId: string | null): MediaTrackConstraints {
  return {
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    // BlackHole 等のループバック入力を音声強調で消さない。
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: { ideal: 1 }
  };
}

export interface GetUserMediaFailure {
  /** deviceId 指定を外して 1 回だけ再試行してよいか。 */
  retryWithoutDevice: boolean;
  /** ユーザーへ出す説明。必ず非空。 */
  message: string;
}

/**
 * `getUserMedia` の例外を、ユーザーに意味の分かる説明へ変換する。
 *
 * `OverconstrainedError` は `message` が空文字のことがあり、そのまま表示すると
 * 「エラーバナーが空欄」になる。name ごとに必ず文言を用意する。
 */
export function describeGetUserMediaError(error: unknown): GetUserMediaFailure {
  const name = error instanceof Error ? error.name : '';
  const raw = error instanceof Error ? error.message : '';

  switch (name) {
    case 'OverconstrainedError':
    case 'NotFoundError':
      return {
        retryWithoutDevice: true,
        message: '指定された入力デバイスが見つかりませんでした。既定の入力デバイスで再試行します。'
      };
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return {
        retryWithoutDevice: false,
        message:
          'マイクの権限が拒否されています。システム設定 →「プライバシーとセキュリティ」→「マイク」で KoeNote を許可してください。'
      };
    case 'NotReadableError':
      return {
        retryWithoutDevice: false,
        message: '入力デバイスを開けませんでした。他のアプリが使用中か、OS から取得できない可能性があります。'
      };
    case 'SecurityError':
      return {
        retryWithoutDevice: false,
        message: 'セキュリティ設定によりマイクを利用できません。'
      };
    case 'AbortError':
      return {
        retryWithoutDevice: false,
        message: '入力デバイスの開始が中断されました。もう一度お試しください。'
      };
    default:
      return {
        retryWithoutDevice: false,
        message: `マイクの取得に失敗しました（${name || '不明なエラー'}${raw ? `: ${raw}` : ''}）`
      };
  }
}

export type GetUserMediaFn = (constraints: MediaStreamConstraints) => Promise<MediaStream>;

export interface AcquiredStream {
  stream: MediaStream;
  /** deviceId 指定を外して取り直したか。 */
  retried: boolean;
}

/**
 * 入力ストリームを取得する。
 *
 * 一覧照合のあとにデバイスが外れる競合があるため、`OverconstrainedError` /
 * `NotFoundError` のときだけ deviceId 指定を外して **1 回だけ** 再試行する。
 * 無制限の再試行はしない。
 */
export async function acquireInputStream(
  getUserMedia: GetUserMediaFn,
  deviceId: string | null
): Promise<AcquiredStream> {
  try {
    return { stream: await getUserMedia({ audio: buildAudioConstraints(deviceId) }), retried: false };
  } catch (error) {
    const failure = describeGetUserMediaError(error);
    // deviceId を指定していなければ、同じ呼び出しを繰り返すだけなので再試行しない。
    if (!failure.retryWithoutDevice || !deviceId) {
      throw new Error(failure.message);
    }
    try {
      return { stream: await getUserMedia({ audio: buildAudioConstraints(null) }), retried: true };
    } catch (retryError) {
      throw new Error(describeGetUserMediaError(retryError).message);
    }
  }
}
