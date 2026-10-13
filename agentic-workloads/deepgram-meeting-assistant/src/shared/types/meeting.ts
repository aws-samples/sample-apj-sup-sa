import type { TranscribeLanguage } from './settings';
import type { TranscriptionSegment, CorrectedSentence } from './transcription';

export type MeetingType =
  | 'interview'
  | 'english'
  | 'translated'
  | 'client'
  | 'weekly'
  | 'agentic';

export type MeetingStatus = 'recording' | 'paused' | 'completed' | 'cancelled';

// ============================================
// 미팅 타입별 메타 정보 인터페이스
// ============================================

/** Client Meeting 메타 정보 */
export interface ClientMeetingMetadata {
  company?: string;
  meetingDate?: string;
  meetingTopic?: string;
  note?: string;
}

/** Interview Question 아이템 */
export interface InterviewQuestion {
  question: string;
  answer?: string;
  score?: number;
}

/** Interview Meeting 메타 정보 */
export interface InterviewMeetingMetadata {
  candidateName?: string;
  position?: string;
  questions?: InterviewQuestion[];
  overallScore?: number;
}

/** Translated Meeting AI 제안 아이템 */
export interface TranslatedSuggestion {
  text: string;
  translatedText?: string;
}

/** Translated Meeting 메타 정보 */
export interface TranslatedMeetingMetadata {
  suggestions?: TranslatedSuggestion[];
}

/** @deprecated Use TranslatedSuggestion instead */
export type EnglishSuggestion = TranslatedSuggestion;

/** @deprecated Use TranslatedMeetingMetadata instead */
export type EnglishMeetingMetadata = TranslatedMeetingMetadata;

/** Weekly/Quick Meeting 메타 정보 */
export interface WeeklyMeetingMetadata {
  weekNumber?: number;
  agenda?: string[];
}

/** 모든 미팅 메타 정보 Union Type */
export type MeetingMetadata =
  | ClientMeetingMetadata
  | InterviewMeetingMetadata
  | TranslatedMeetingMetadata
  | WeeklyMeetingMetadata
  | Record<string, never>; // 빈 객체 허용

/** 미팅 타입별 메타 정보 매핑 */
export type MeetingMetadataMap = {
  client: ClientMeetingMetadata;
  interview: InterviewMeetingMetadata;
  english: TranslatedMeetingMetadata;
  translated: TranslatedMeetingMetadata;
  weekly: WeeklyMeetingMetadata;
  agentic: Record<string, never>;
};

export interface MeetingTypeConfig {
  id: MeetingType;
  label: string;
  description: string;
  bgColor: string;
  textColor: string;
  icon: string;
}

export const MEETING_TYPES: MeetingTypeConfig[] = [
  {
    id: 'client',
    label: 'Client Meeting',
    description: 'Action items, CRM sync, and external focus',
    bgColor: 'bg-green-50',
    textColor: 'text-green-600',
    icon: 'handshake',
  },
  {
    id: 'weekly',
    label: 'Quick Meeting',
    description: 'Internal syncs, task tracking and follow-ups',
    bgColor: 'bg-purple-50',
    textColor: 'text-purple-600',
    icon: 'calendar_view_week',
  },
  {
    id: 'translated',
    label: 'Translated Meeting',
    description: 'Real-time language help and global translation',
    bgColor: 'bg-amber-50',
    textColor: 'text-amber-600',
    icon: 'translate',
  },
  {
    id: 'interview',
    label: 'Amazon Interview',
    description: 'Structured Q&A tracking and candidate scoring',
    bgColor: 'bg-blue-50',
    textColor: 'text-blue-600',
    icon: 'record_voice_over',
  },
  {
    id: 'agentic',
    label: 'Agentic Meeting',
    description: 'Pipecat pipeline: real-time STT + LLM via local server',
    bgColor: 'bg-indigo-50',
    textColor: 'text-indigo-600',
    icon: 'smart_toy',
  },
];

export interface Meeting {
  id: string;
  type: MeetingType;
  title: string;
  status: MeetingStatus;
  language: TranscribeLanguage;
  vocabularyId?: string; // 사용된 용어집 ID
  startedAt: Date;
  endedAt?: Date;
  duration: number;
  metadata?: MeetingMetadata;
  createdAt: Date;
  updatedAt: Date;
}

export interface ActionItem {
  task: string;
  owner: string;
  deadline: string;
}

export interface TopicDiscussion {
  topic: string;
  discussions: string[];
  decisions: string[];
}

export interface MeetingSummary {
  id: string;
  meetingId: string;
  mainTopics: string[];
  topicDiscussions: TopicDiscussion[];
  keyTakeaways: string[];
  confirmedActions: ActionItem[];
  pendingActions: ActionItem[];
  followUps: string[];
  openIssues: string[];
  generatedAt: Date;
  modelId: string;
}

/** 대화 로그 주제 */
export interface ConversationTopic {
  title: string;  // 주제 제목 (15자 이내)
  points: string[];  // 핵심 내용 (3~6개, 각 50자 이내)
}

/** 대화 로그 - 전사 내용을 주제별로 분절해 정리 */
export interface ConversationLog {
  id: string;
  meetingId: string;
  topics: ConversationTopic[];
  generatedAt: Date;
  modelId: string;
}

export interface MeetingDetail extends Meeting {
  segments: TranscriptionSegment[];
  correctedSentences: CorrectedSentence[];
  summary?: MeetingSummary;
  conversationLog?: ConversationLog;
}

export type RecordingStatus = 'idle' | 'recording' | 'paused' | 'processing' | 'completed';

export interface RecordingState {
  status: RecordingStatus;
  meetingId: string | null;
  meetingType: MeetingType | null;
  language: TranscribeLanguage;  // Source language (what's being spoken)
  targetLanguage: TranscribeLanguage;  // Target language for translation
  vocabularyId: string | null; // 선택된 용어집 ID
  startTime: Date | null;
  duration: number;
}

export type NavItem = 'home' | 'settings';