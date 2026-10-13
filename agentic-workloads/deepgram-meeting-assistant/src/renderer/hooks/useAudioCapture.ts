import { useState, useCallback, useRef } from 'react';
import type { AudioChunk } from '@shared/types';

const TARGET_SAMPLE_RATE = 16000;
const PROCESSOR_NAME = 'audio-capture-processor';
const WORKLET_MODULE_FILE = 'audio-processor.worklet.js';

async function loadAudioWorkletModule(audioContext: AudioContext): Promise<void> {
  const candidates = [
    `./${WORKLET_MODULE_FILE}`,
    `../${WORKLET_MODULE_FILE}`,
    `../../${WORKLET_MODULE_FILE}`,
    `../../../${WORKLET_MODULE_FILE}`,
  ];

  if (window.location.protocol !== 'file:') {
    candidates.unshift(`/${WORKLET_MODULE_FILE}`);
  }

  const urls = [...new Set(candidates.map((candidate) => new URL(candidate, window.location.href).toString()))];
  let lastError: unknown;

  for (const moduleUrl of urls) {
    try {
      await audioContext.audioWorklet.addModule(moduleUrl);
      return;
    } catch (error) {
      lastError = error;
    }
  }

  const reason = lastError instanceof Error ? lastError.message : String(lastError ?? 'unknown');
  throw new Error(`Unable to load worklet module (${WORKLET_MODULE_FILE}). Tried: ${urls.join(', ')}. Last error: ${reason}`);
}

interface AudioCaptureState {
  isCapturing: boolean;
  error: string | null;
  deviceId: string | null;
  isMuted: boolean;
}

export function useAudioCapture(
  onAudioChunk: (chunk: AudioChunk) => void
): {
  state: AudioCaptureState;
  startCapture: (deviceId: string) => Promise<void>;
  stopCapture: () => void;
  setMuted: (muted: boolean) => void;
} {
  const [state, setState] = useState<AudioCaptureState>({
    isCapturing: false,
    error: null,
    deviceId: null,
    isMuted: false,
  });

  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const outputGainRef = useRef<GainNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const mutedRef = useRef(false);

  const cleanup = useCallback(() => {
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }

    if (outputGainRef.current) {
      outputGainRef.current.disconnect();
      outputGainRef.current = null;
    }

    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
  }, []);

  const startCapture = useCallback(
    async (deviceId: string) => {
      try {
        cleanup();

        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: { exact: deviceId },
            sampleRate: { ideal: TARGET_SAMPLE_RATE },
            channelCount: { exact: 1 },
            echoCancellation: true,
            noiseSuppression: true,
          },
        });

        streamRef.current = stream;
        stream.getAudioTracks().forEach((track) => {
          track.enabled = !mutedRef.current;
        });

        const audioContext = new AudioContext({
          sampleRate: TARGET_SAMPLE_RATE,
        });
        audioContextRef.current = audioContext;

        if (audioContext.state === 'suspended') {
          await audioContext.resume();
        }

        await loadAudioWorkletModule(audioContext);

        const workletNode = new AudioWorkletNode(audioContext, PROCESSOR_NAME);
        workletNodeRef.current = workletNode;

        workletNode.port.onmessage = (event) => {
          if (event.data.type === 'audio-chunk') {
            onAudioChunk({
              data: event.data.data,
              timestamp: event.data.timestamp,
              sequenceNumber: event.data.sequenceNumber,
            });
          }
        };

        const source = audioContext.createMediaStreamSource(stream);
        sourceRef.current = source;

        source.connect(workletNode);
        const outputGain = audioContext.createGain();
        outputGain.gain.value = 0;
        outputGainRef.current = outputGain;
        // Keep the worklet processing chain alive without audible output.
        workletNode.connect(outputGain);
        outputGain.connect(audioContext.destination);

        setState({
          isCapturing: true,
          error: null,
          deviceId,
          isMuted: mutedRef.current,
        });
      } catch (error) {
        const message =
          error instanceof Error ? error.message : '오디오 캡처 실패';
        setState((prev) => ({ ...prev, error: message }));
        cleanup();
      }
    },
    [onAudioChunk, cleanup]
  );

  const stopCapture = useCallback(() => {
    cleanup();
    setState({
      isCapturing: false,
      error: null,
      deviceId: null,
      isMuted: mutedRef.current,
    });
  }, [cleanup]);

  const setMuted = useCallback((muted: boolean) => {
    mutedRef.current = muted;
    if (streamRef.current) {
      streamRef.current.getAudioTracks().forEach((track) => {
        track.enabled = !muted;
      });
    }
    setState((prev) => ({ ...prev, isMuted: muted }));
  }, []);

  return {
    state,
    startCapture,
    stopCapture,
    setMuted,
  };
}
