/**
 * 入力テスト（キャリブレーション）モーダル（0019）。
 *
 * 重要な制約:
 *   - **通常の録音セッションを開始しない。** `/api/session/create` を呼ばず、
 *     ユーザーの保存先にファイルを一切作らない
 *   - 測定した PCM はメモリ上（ArrayBuffer）だけで扱い、ディスクへ書かない。
 *     モーダルを閉じた時点で参照を捨て、マイクも解放する
 *   - 数値はすべて dBFS。物理音圧 dB SPL ではない
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  NOISE_STEP_SECONDS,
  SPEECH_STEP_SECONDS,
  STEP_INSTRUCTIONS,
  STEP_TITLES,
  TEST_SENTENCE,
  VERDICT_ADVICE,
  VERDICT_LABELS,
  VERDICT_TONES,
  applyRecommended,
  buildResultRows,
  compareRuns,
  describeImprovement,
  encodePcmChunks,
  remainingSeconds,
  stepProgress,
  type CalibrationResult,
  type CalibrationStep
} from '../features/audio/calibration';
import {
  toBackendPayload,
  type InputProfileSettings
} from '../features/audio/inputProfile';
import { buildMeterView, createLevelSmoother } from '../features/audio/levelMeter';
import { acquireInputStream, resolveInputDevice } from '../features/transcription/inputDevice';
import { startPcmCapture, type PcmCaptureHandle } from '../features/transcription/pcmCapture';
import { analyzeInput } from '../services/api';
import { InputLevelMeter } from './InputLevelMeter';

export interface CalibrationModalProps {
  open: boolean;
  deviceId: string;
  deviceLabel: string;
  settings: InputProfileSettings;
  onClose: () => void;
  /** 「推奨設定を適用」で確定した設定。 */
  onApply: (next: InputProfileSettings) => void;
}

interface Recorded {
  noise: ArrayBuffer[];
  speech: ArrayBuffer[];
}

export function CalibrationModal({
  open,
  deviceId,
  deviceLabel,
  settings,
  onClose,
  onApply
}: CalibrationModalProps) {
  const [step, setStep] = useState<CalibrationStep>('idle');
  const [elapsed, setElapsed] = useState(0);
  const [meter, setMeter] = useState(() => buildMeterView(-60));
  const [result, setResult] = useState<CalibrationResult | null>(null);
  const [previous, setPrevious] = useState<CalibrationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);
  const [draft, setDraft] = useState<InputProfileSettings>(settings);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const captureRef = useRef<PcmCaptureHandle | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recordedRef = useRef<Recorded>({ noise: [], speech: [] });
  const phaseRef = useRef<'noise' | 'speech' | null>(null);
  const smootherRef = useRef(createLevelSmoother());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rafRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);

  /** マイクと測定バッファを完全に手放す。テスト音声はここで消える。 */
  const teardown = useCallback(async () => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const capture = captureRef.current;
    captureRef.current = null;
    if (capture) await capture.stop().catch(() => {});
    const stream = streamRef.current;
    streamRef.current = null;
    stream?.getTracks().forEach((track) => track.stop());
    // 測定した PCM を保持し続けない（ディスクへは元々書いていない）。
    recordedRef.current = { noise: [], speech: [] };
    phaseRef.current = null;
    smootherRef.current.reset();
  }, []);

  useEffect(() => {
    if (!open) {
      cancelledRef.current = true;
      void teardown();
      setStep('idle');
      setResult(null);
      setPrevious(null);
      setError(null);
      setApplied(false);
      setElapsed(0);
    } else {
      cancelledRef.current = false;
      setDraft(settings);
    }
    // アンマウント時も必ずマイクを解放する。
    return () => {
      cancelledRef.current = true;
      void teardown();
    };
  }, [open, settings, teardown]);

  const analyze = useCallback(async () => {
    setStep('analyzing');
    try {
      const payload = {
        speech_pcm: encodePcmChunks(recordedRef.current.speech),
        noise_pcm: recordedRef.current.noise.length
          ? encodePcmChunks(recordedRef.current.noise)
          : undefined,
        device_label: deviceLabel,
        input_profile: toBackendPayload(draft, deviceLabel) as unknown as Record<string, unknown>,
        sample_rate: captureRef.current?.sampleRate ?? 16000
      };
      await teardown();
      const analyzed = (await analyzeInput(payload)) as unknown as CalibrationResult;
      if (cancelledRef.current) return;
      setPrevious(result);
      setResult(analyzed);
      setStep('result');
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : '解析に失敗しました');
      setStep('error');
      await teardown();
    }
  }, [deviceLabel, draft, result, teardown]);

  const runSteps = useCallback(
    async (skipNoise: boolean) => {
      setError(null);
      setApplied(false);
      recordedRef.current = { noise: [], speech: [] };
      smootherRef.current.reset();

      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const resolution = resolveInputDevice(deviceId, deviceLabel, devices);
        if (!resolution.ok) throw new Error(resolution.notice ?? '入力デバイスがありません');
        const acquired = await acquireInputStream(
          (constraints) => navigator.mediaDevices.getUserMedia(constraints),
          resolution.effectiveDeviceId
        );
        if (cancelledRef.current) {
          acquired.stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = acquired.stream;

        captureRef.current = await startPcmCapture({
          stream: acquired.stream,
          onFrame: (frame) => {
            const phase = phaseRef.current;
            if (phase === 'noise') recordedRef.current.noise.push(frame.pcm);
            else if (phase === 'speech') recordedRef.current.speech.push(frame.pcm);
          },
          onFault: (fault) => {
            setError(fault.detail);
            setStep('error');
            void teardown();
          },
          onAnalyser: (analyser) => {
            const buffer = new Float32Array(analyser.fftSize);
            const tick = () => {
              analyser.getFloatTimeDomainData(buffer);
              let sum = 0;
              for (let i = 0; i < buffer.length; i += 1) sum += buffer[i] * buffer[i];
              setMeter(smootherRef.current.push(Math.sqrt(sum / buffer.length)));
              rafRef.current = requestAnimationFrame(tick);
            };
            rafRef.current = requestAnimationFrame(tick);
          }
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : 'マイクを取得できませんでした');
        setStep('error');
        await teardown();
        return;
      }

      const runPhase = (phase: 'noise' | 'speech', seconds: number) =>
        new Promise<void>((resolve) => {
          phaseRef.current = phase;
          setStep(phase);
          setElapsed(0);
          const startedAt = Date.now();
          timerRef.current = setInterval(() => {
            const passed = Date.now() - startedAt;
            setElapsed(passed);
            if (passed >= seconds * 1000) {
              if (timerRef.current !== null) clearInterval(timerRef.current);
              timerRef.current = null;
              phaseRef.current = null;
              resolve();
            }
          }, 100);
        });

      if (!skipNoise) await runPhase('noise', NOISE_STEP_SECONDS);
      if (cancelledRef.current) return;
      await runPhase('speech', SPEECH_STEP_SECONDS);
      if (cancelledRef.current) return;
      await analyze();
    },
    [analyze, deviceId, deviceLabel, teardown]
  );

  const applyRecommendedSettings = useCallback(async () => {
    if (!result) return;
    const next = applyRecommended(draft, result.recommended_settings as Record<string, unknown>);
    setDraft(next);
    onApply(next);
    setApplied(true);
    // 推奨設定の効果を確かめるため、環境音は省いて短く測り直す。
    await runSteps(true);
  }, [draft, onApply, result, runSteps]);

  const close = useCallback(() => {
    cancelledRef.current = true;
    void teardown();
    onClose();
  }, [onClose, teardown]);

  const rows = useMemo(() => (result ? buildResultRows(result) : []), [result]);
  const comparison = useMemo(
    () => (previous && result ? compareRuns(previous, result) : null),
    [previous, result]
  );

  if (!open) return null;

  const measuring = step === 'noise' || step === 'speech';
  const remaining = remainingSeconds(step, elapsed);
  const progress = stepProgress(step, elapsed);

  return (
    <div className="modal-backdrop" onClick={close}>
      <div
        className="modal calibration-modal"
        role="dialog"
        aria-modal="true"
        aria-label="入力テスト"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="modal-title">{STEP_TITLES[step]}</h2>

        {step === 'idle' ? (
          <>
            <p className="hint">{STEP_INSTRUCTIONS.idle}</p>
            <p className="hint">
              テスト中は録音セッションを開始しません。測定した音声は保存されません。
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-accent" onClick={() => void runSteps(false)}>
                テストを開始
              </button>
              <button type="button" className="btn-ghost" onClick={close}>
                閉じる
              </button>
            </div>
          </>
        ) : null}

        {measuring ? (
          <>
            <p className="hint">{STEP_INSTRUCTIONS[step]}</p>
            {step === 'speech' ? (
              <pre className="calibration-sentence">{TEST_SENTENCE}</pre>
            ) : null}
            <div className="calibration-progress">
              <span className="calibration-countdown mono">残り {remaining} 秒</span>
              <span className="calibration-bar">
                <span
                  className="calibration-bar-fill"
                  style={{ width: `${Math.round(progress * 100)}%` }}
                />
              </span>
            </div>
            <InputLevelMeter view={meter} mode="mic" isAuto={false} />
            <div className="modal-actions">
              <button type="button" className="btn-ghost" onClick={close}>
                キャンセル
              </button>
            </div>
          </>
        ) : null}

        {step === 'analyzing' ? <p className="hint">{STEP_INSTRUCTIONS.analyzing}</p> : null}

        {step === 'error' ? (
          <>
            <p className="hint hint-error">{error}</p>
            <div className="modal-actions">
              <button type="button" className="btn-accent" onClick={() => void runSteps(false)}>
                もう一度テスト
              </button>
              <button type="button" className="btn-ghost" onClick={close}>
                閉じる
              </button>
            </div>
          </>
        ) : null}

        {step === 'result' && result ? (
          <>
            <p className={`calibration-verdict banner-${VERDICT_TONES[result.verdict]}`}>
              <strong>{VERDICT_LABELS[result.verdict]}</strong>
              <span>{VERDICT_ADVICE[result.verdict]}</span>
            </p>
            {applied && comparison ? (
              <p className={`hint ${comparison.improved ? 'hint-ok' : 'hint-error'}`}>
                {comparison.improved ? '推奨設定で改善しました。' : '推奨設定でも大きな改善は見られません。'}
                {comparison.summary}
              </p>
            ) : (
              <p className="hint">{describeImprovement(result)}</p>
            )}

            <dl className="calibration-rows">
              {rows
                .filter((row) => !row.advanced || showAdvanced)
                .map((row) => (
                  <div className="calibration-row" key={row.label}>
                    <dt>{row.label}</dt>
                    <dd className="mono">{row.value}</dd>
                  </div>
                ))}
            </dl>
            <button
              type="button"
              className="btn-link"
              onClick={() => setShowAdvanced((value) => !value)}
            >
              {showAdvanced ? '詳細な数値を隠す' : '詳細な数値を表示'}
            </button>
            <p className="hint">
              数値はすべて dBFS（デジタルのフルスケール基準）です。騒音計の dB SPL とは異なります。
            </p>

            <div className="modal-actions">
              <button
                type="button"
                className="btn-accent"
                disabled={applied}
                onClick={() => void applyRecommendedSettings()}
              >
                推奨設定を適用
              </button>
              <button type="button" className="btn-ghost" onClick={() => void runSteps(false)}>
                もう一度テスト
              </button>
              <button type="button" className="btn-ghost" onClick={close}>
                閉じる
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
