export interface TranscriptionSegment {
  id: string;
  meetingId: string;
  resultId: string;
  text: string;
  startTime: number;
  endTime: number;
  speakerLabel: string | null;
  confidence?: number;
  createdAt: Date;
}

export interface CorrectedSentence {
  id: string;
  meetingId: string;
  originalText: string;
  correctedText: string;
  translatedText?: string | null;
  segmentIds: string[];
  startTime: number;
  endTime: number;
  speakerLabel: string | null;
  modelId: string;
  correctedAt: Date;
}

export interface TranscriptionPartialEvent {
  type: 'partial';
  text: string;
  speakerLabel: string | null;
}

export interface TranscriptionFinalEvent {
  type: 'final';
  segment: TranscriptionSegment;
}

export interface CorrectionEvent {
  id: string;
  originalText: string;
  correctedText: string;
  translatedText?: string | null;
  segmentIds: string[];
  speakerLabel: string | null;
  startTime: number;
  endTime: number;
}

export interface BufferedSegment {
  id: string;
  text: string;
  startTime: number;
  endTime: number;
  speakerLabel: string | null;
}

export interface CompletedSentence {
  originalText: string;
  startTime: number;
  endTime: number;
  speakerLabel: string | null;
  segmentIds: string[];
}
