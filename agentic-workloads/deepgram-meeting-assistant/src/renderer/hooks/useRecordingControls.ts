import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import type { RecordingState } from '@shared/types';

interface UseRecordingControlsOptions {
  selectedDeviceId: string | null;
  isCapturing: boolean;
  startCapture: (deviceId: string) => Promise<void>;
  stopCapture: () => void;
  onStartRecording: () => Promise<void>;
  onPauseRecording: () => Promise<void>;
  onResumeRecording: () => Promise<void>;
  onEndRecording: () => Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }>;
  setRecordingState: Dispatch<SetStateAction<RecordingState>>;
  onStartSetup?: () => void;
  onStatusChange?: (status: RecordingState['status']) => void;
  onRecordingComplete?: () => void;
}

function useRecordingControls({
  selectedDeviceId,
  isCapturing,
  startCapture,
  stopCapture,
  onStartRecording,
  onPauseRecording,
  onResumeRecording,
  onEndRecording,
  setRecordingState,
  onStartSetup,
  onStatusChange,
  onRecordingComplete,
}: UseRecordingControlsOptions) {
  const durationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearDurationTimer = useCallback(() => {
    if (durationIntervalRef.current) {
      clearInterval(durationIntervalRef.current);
      durationIntervalRef.current = null;
    }
  }, []);

  const startDurationTimer = useCallback(() => {
    clearDurationTimer();
    durationIntervalRef.current = setInterval(() => {
      setRecordingState((prev) => ({
        ...prev,
        duration: prev.duration + 1,
      }));
    }, 1000);
  }, [clearDurationTimer, setRecordingState]);

  const handleStart = useCallback(async () => {
    if (!selectedDeviceId) {
      return;
    }

    onStartSetup?.();
    await onStartRecording();
    onStatusChange?.('recording');

    if (!isCapturing) {
      await startCapture(selectedDeviceId);
    }

    startDurationTimer();
  }, [
    selectedDeviceId,
    onStartSetup,
    onStartRecording,
    onStatusChange,
    isCapturing,
    startCapture,
    startDurationTimer,
  ]);

  const handleEnd = useCallback(async () => {
    clearDurationTimer();
    // 즉시 캡처 청크 전송을 멈춘다(stop RPC 대기 중 발화가 백엔드로 새는 race 방지).
    // onStatusChange는 동기적으로 recordingStatusRef를 갱신하므로 forwarding이 곧장 멎는다.
    onStatusChange?.('processing');
    const result = await onEndRecording(); // { completed, degraded, recoverable }
    if (!result?.completed && result?.recoverable) {
      // 복구 가능한 실패(스트림이 아직 살아있음): 캡처 유지하고 녹음 이어감(상태 복원 + 타이머 재가동).
      onStatusChange?.('recording');
      startDurationTimer();
      return;
    }
    // completed === true 이거나, 복구 불가(terminal) 실패: 로컬 teardown.
    stopCapture();
    onStatusChange?.('idle');
    if (result?.completed && !result.degraded) {
      onRecordingComplete?.(); // degraded가 아닐 때만 자동 요약
    }
  }, [clearDurationTimer, startDurationTimer, stopCapture, onEndRecording, onStatusChange, onRecordingComplete]);

  const handlePause = useCallback(async () => {
    clearDurationTimer();
    await onPauseRecording();
    onStatusChange?.('paused');
  }, [clearDurationTimer, onPauseRecording, onStatusChange]);

  const handleResume = useCallback(async () => {
    if (!selectedDeviceId) {
      return;
    }

    await onResumeRecording();
    onStatusChange?.('recording');

    if (!isCapturing) {
      await startCapture(selectedDeviceId);
    }

    startDurationTimer();
  }, [selectedDeviceId, onResumeRecording, isCapturing, startCapture, startDurationTimer, onStatusChange]);

  useEffect(() => {
    return () => {
      clearDurationTimer();
      stopCapture();
    };
  }, [clearDurationTimer, stopCapture]);

  return {
    handleStart,
    handlePause,
    handleResume,
    handleEnd,
  };
}

export default useRecordingControls;
