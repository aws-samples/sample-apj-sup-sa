import type { RecordingState, TranscriptionSegment, CorrectedSentence, MeetingSummary } from '@shared/types';

export interface BaseMeetingViewProps {
  recordingState: RecordingState;
  segments: TranscriptionSegment[];
  correctedSentences: CorrectedSentence[];
  partialText: string;
  partialSpeaker: string | null;
  summary?: MeetingSummary | null;
  fullScript: string;
  transcriptionError?: string | null;
  audioError?: string | null;
  summaryError?: string | null;
}

export type QuickMeetingTab = 'conversation' | 'script' | 'summary';
