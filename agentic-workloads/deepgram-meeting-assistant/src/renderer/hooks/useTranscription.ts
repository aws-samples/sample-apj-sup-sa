import { useState, useCallback, useEffect } from 'react';
import type {
  TranscriptionSegment,
  CorrectedSentence,
  CorrectionEvent,
} from '@shared/types';
import { normalizeErrorMessage } from '../utils/normalize-error';

interface TranscriptionState {
  partialText: string;
  partialSpeaker: string | null;
  segments: TranscriptionSegment[];
  correctedSentences: CorrectedSentence[];
  error: string | null;
}

export function useTranscription() {
  const [state, setState] = useState<TranscriptionState>({
    partialText: '',
    partialSpeaker: null,
    segments: [],
    correctedSentences: [],
    error: null,
  });

  useEffect(() => {
    if (!window.electronAPI) {
      return;
    }

    const unsubPartial = window.electronAPI.onTranscriptionPartial((data) => {
      setState((prev) => ({
        ...prev,
        partialText: data.text,
        partialSpeaker: data.speakerLabel,
      }));
    });

    const unsubFinal = window.electronAPI.onTranscriptionFinal((data) => {
      setState((prev) => ({
        ...prev,
        partialText: '',
        partialSpeaker: null,
        segments: [...prev.segments, data.segment],
      }));
    });

    const unsubCorrected = window.electronAPI.onTranscriptionCorrected((data: CorrectionEvent) => {
      setState((prev) => ({
        ...prev,
        correctedSentences: [
          ...prev.correctedSentences,
          {
            id: data.id,
            meetingId: '',
            originalText: data.originalText,
            correctedText: data.correctedText,
            translatedText: data.translatedText ?? null,
            segmentIds: data.segmentIds ?? [],
            startTime: data.startTime,
            endTime: data.endTime,
            speakerLabel: data.speakerLabel,
            modelId: '',
            correctedAt: new Date(),
          },
        ],
      }));
    });

    const unsubError = window.electronAPI.onTranscriptionError((data) => {
      setState((prev) => ({
        ...prev,
        error: normalizeErrorMessage(data.error),
      }));
    });

    return () => {
      unsubPartial();
      unsubFinal();
      unsubCorrected();
      unsubError();
    };
  }, []);

  const clearTranscription = useCallback(() => {
    setState({
      partialText: '',
      partialSpeaker: null,
      segments: [],
      correctedSentences: [],
      error: null,
    });
  }, []);

  return {
    ...state,
    clearTranscription,
  };
}
