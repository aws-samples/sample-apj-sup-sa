import { contextBridge, ipcRenderer, IpcRendererEvent } from 'electron';
import type {
  AppSettings,
  AWSCredentials,
  MeetingType,
  MeetingMetadata,
  TranscribeLanguage,
  Meeting,
  MeetingDetail,
  MeetingSummary,
  ConversationLog,
  TranscriptionSegment,
  CorrectionEvent,
  TranslatedSuggestionResult,
  ConnectionStatus,
  McpResult,
  McpTool,
  McpToolResult,
  MeetingPrepData,
  Vocabulary,
  VocabularyEntry,
  VocabularyStatus,
  VocabularyLanguage,
  CreateVocabularyRequest,
  UpdateVocabularyRequest,
  CreateVocabularyEntryRequest,
  UpdateVocabularyEntryRequest,
  VocabularySyncResult,
} from '../shared/types';
import type { InterviewSuggestionResult, LeadershipPrinciple } from '../shared/types/interview';
import type { AssistantEvent } from '../shared/types/pipecat-protocol';
import type { AgentChatResult, AgentResolveResult } from '../shared/types/agent';
import { IPC_CHANNELS } from '../shared/constants/ipc-channels';

/** main 핸들러의 공통 응답 래퍼 ({success, data?, error?}). */
type AgentInvokeResult<T> = { success: true; data: T } | { success: false; error: string };

contextBridge.exposeInMainWorld('electronAPI', {
  createMeeting: (params: { type: MeetingType; language: TranscribeLanguage; title?: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_CREATE, params),
  startMeeting: (params: { meetingId: string; language?: TranscribeLanguage; targetLanguage?: TranscribeLanguage; vocabularyId?: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_START, params),
  pauseMeeting: () => ipcRenderer.invoke(IPC_CHANNELS.MEETING_PAUSE),
  resumeMeeting: () => ipcRenderer.invoke(IPC_CHANNELS.MEETING_RESUME),
  stopMeeting: () => ipcRenderer.invoke(IPC_CHANNELS.MEETING_STOP),
  getMeeting: (params: { id: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_GET, params),
  listMeetings: (params?: { limit?: number; offset?: number }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_LIST, params),
  deleteMeeting: (params: { id: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_DELETE, params),
  deleteAllMeetings: () =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_DELETE_ALL),
  updatePrepData: (params: { prepData: MeetingPrepData | null }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_UPDATE_PREP_DATA, params),
  updateMeetingMetadata: (params: { id: string; metadata: MeetingMetadata }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_UPDATE_METADATA, params),
  getMeetingMetadata: (params: { id: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.MEETING_GET_METADATA, params),

  sendAudioChunk: (chunk: { data: string }) =>
    ipcRenderer.send(IPC_CHANNELS.AUDIO_CHUNK, chunk),

  generateSummary: (params: { meetingId: string; prepData?: MeetingPrepData | null }) =>
    ipcRenderer.invoke(IPC_CHANNELS.SUMMARY_GENERATE, params),
  generateConversationLog: (params: { meetingId: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.CONVERSATION_LOG_GENERATE, params),
  generateEnglishSuggestions: (params: { meetingId: string; count?: number }) =>
    ipcRenderer.invoke(IPC_CHANNELS.ENGLISH_SUGGESTIONS, params),
  translateEnglishText: (params: { meetingId?: string | null; text: string }) =>
    ipcRenderer.invoke(IPC_CHANNELS.ENGLISH_TRANSLATE, params),
  generateInterviewSuggestions: (params: { meetingId: string; lpIds: LeadershipPrinciple[]; count?: number }) =>
    ipcRenderer.invoke(IPC_CHANNELS.INTERVIEW_SUGGESTIONS, params),

  onTranscriptionPartial: (callback: (data: { text: string; speakerLabel: string | null }) => void) => {
    const handler = (_event: IpcRendererEvent, data: { text: string; speakerLabel: string | null }) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.TRANSCRIPTION_PARTIAL, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.TRANSCRIPTION_PARTIAL, handler);
  },
  onTranscriptionFinal: (callback: (data: { segment: TranscriptionSegment }) => void) => {
    const handler = (_event: IpcRendererEvent, data: { segment: TranscriptionSegment }) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.TRANSCRIPTION_FINAL, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.TRANSCRIPTION_FINAL, handler);
  },
  onTranscriptionCorrected: (callback: (data: CorrectionEvent) => void) => {
    const handler = (_event: IpcRendererEvent, data: CorrectionEvent) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.TRANSCRIPTION_CORRECTED, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.TRANSCRIPTION_CORRECTED, handler);
  },
  onTranscriptionError: (callback: (data: { error: string }) => void) => {
    const handler = (_event: IpcRendererEvent, data: { error: string }) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.TRANSCRIPTION_ERROR, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.TRANSCRIPTION_ERROR, handler);
  },
  onAssistantEvent: (callback: (data: AssistantEvent) => void) => {
    const handler = (_event: IpcRendererEvent, data: AssistantEvent) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.ASSISTANT_EVENT, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.ASSISTANT_EVENT, handler);
  },
  onSummaryComplete: (callback: (data: { meetingId: string; summary: MeetingSummary }) => void) => {
    const handler = (_event: IpcRendererEvent, data: { meetingId: string; summary: MeetingSummary }) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.SUMMARY_COMPLETE, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.SUMMARY_COMPLETE, handler);
  },
  onConversationLogComplete: (callback: (data: { meetingId: string; conversationLog: ConversationLog }) => void) => {
    const handler = (_event: IpcRendererEvent, data: { meetingId: string; conversationLog: ConversationLog }) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.CONVERSATION_LOG_COMPLETE, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.CONVERSATION_LOG_COMPLETE, handler);
  },
  onMeetingTitleUpdated: (callback: (data: { meetingId: string; title: string }) => void) => {
    const handler = (_event: IpcRendererEvent, data: { meetingId: string; title: string }) => callback(data);
    ipcRenderer.on(IPC_CHANNELS.MEETING_TITLE_UPDATED, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.MEETING_TITLE_UPDATED, handler);
  },

  saveSettings: (settings: AppSettings) =>
    ipcRenderer.invoke(IPC_CHANNELS.SETTINGS_SAVE, settings),
  loadSettings: () => ipcRenderer.invoke(IPC_CHANNELS.SETTINGS_LOAD),
  clearSettings: () => ipcRenderer.invoke(IPC_CHANNELS.SETTINGS_CLEAR),
  getAWSCredentials: () => ipcRenderer.invoke(IPC_CHANNELS.SETTINGS_GET_AWS_CREDENTIALS),

  mcp: {
    connect: () => ipcRenderer.invoke(IPC_CHANNELS.MCP_CONNECT),
    disconnect: () => ipcRenderer.invoke(IPC_CHANNELS.MCP_DISCONNECT),
    getStatus: () => ipcRenderer.invoke(IPC_CHANNELS.MCP_GET_STATUS),
    listTools: () => ipcRenderer.invoke(IPC_CHANNELS.MCP_LIST_TOOLS),
    callTool: (name: string, args: Record<string, unknown>) =>
      ipcRenderer.invoke(IPC_CHANNELS.MCP_CALL_TOOL, name, args),
  },

  agent: {
    chatSend: (params: { meetingId: string; text: string }) =>
      ipcRenderer.invoke(IPC_CHANNELS.AGENT_CHAT_SEND, params),
    resolveAction: (params: { meetingId: string; actionId: string; approved: boolean }) =>
      ipcRenderer.invoke(IPC_CHANNELS.AGENT_RESOLVE_ACTION, params),
    reset: (params: { meetingId: string }) =>
      ipcRenderer.invoke(IPC_CHANNELS.AGENT_RESET, params),
  },

  vocabulary: {
    list: (languageCode?: VocabularyLanguage) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_LIST, languageCode),
    get: (id: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_GET, id),
    create: (request: CreateVocabularyRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_CREATE, request),
    update: (id: string, updates: UpdateVocabularyRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_UPDATE, id, updates),
    delete: (id: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_DELETE, id),
    listEntries: (vocabularyId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_ENTRY_LIST, vocabularyId),
    addEntry: (request: CreateVocabularyEntryRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_ENTRY_ADD, request),
    updateEntry: (entryId: string, updates: UpdateVocabularyEntryRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_ENTRY_UPDATE, entryId, updates),
    removeEntry: (entryId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_ENTRY_REMOVE, entryId),
    setDefault: (vocabularyId: string, languageCode: VocabularyLanguage) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_SET_DEFAULT, vocabularyId, languageCode),
    getDefault: (languageCode: VocabularyLanguage) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_GET_DEFAULT, languageCode),
    clearDefault: (languageCode: VocabularyLanguage) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_CLEAR_DEFAULT, languageCode),
    syncToAws: (vocabularyId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_SYNC_TO_AWS, vocabularyId),
    checkStatus: (vocabularyId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_CHECK_STATUS, vocabularyId),
    generateFile: (vocabularyId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.VOCABULARY_GENERATE_FILE, vocabularyId),
  },

  platform: process.platform,
});

export interface ElectronAPI {
  createMeeting: (params: { type: MeetingType; language: TranscribeLanguage; title?: string }) =>
    Promise<{ success: boolean; meeting?: Meeting; error?: string }>;
  startMeeting: (params: { meetingId: string; language?: TranscribeLanguage; targetLanguage?: TranscribeLanguage; vocabularyId?: string }) =>
    Promise<{ success: boolean; error?: string }>;
  pauseMeeting: () => Promise<{ success: boolean; error?: string }>;
  resumeMeeting: () => Promise<{ success: boolean; error?: string }>;
  stopMeeting: () => Promise<{ success: boolean; error?: string; degraded?: boolean; streamStillActive?: boolean }>;
  getMeeting: (params: { id: string }) =>
    Promise<{ success: boolean; meeting?: MeetingDetail; error?: string }>;
  listMeetings: (params?: { limit?: number; offset?: number }) =>
    Promise<{ success: boolean; meetings?: Meeting[]; error?: string }>;
  deleteMeeting: (params: { id: string }) =>
    Promise<{ success: boolean; error?: string }>;
  deleteAllMeetings: () =>
    Promise<{ success: boolean; deletedCount?: number; error?: string }>;
  updatePrepData: (params: { prepData: MeetingPrepData | null }) =>
    Promise<{ success: boolean; error?: string }>;
  updateMeetingMetadata: (params: { id: string; metadata: MeetingMetadata }) =>
    Promise<{ success: boolean; error?: string }>;
  getMeetingMetadata: (params: { id: string }) =>
    Promise<{ success: boolean; metadata?: MeetingMetadata; error?: string }>;

  sendAudioChunk: (chunk: { data: string }) => void;

  generateSummary: (params: { meetingId: string; prepData?: MeetingPrepData | null }) =>
    Promise<{ success: boolean; summary?: MeetingSummary; error?: string }>;
  generateConversationLog: (params: { meetingId: string }) =>
    Promise<{ success: boolean; conversationLog?: ConversationLog; error?: string }>;
  generateEnglishSuggestions: (params: { meetingId: string; count?: number }) =>
    Promise<{ success: boolean; suggestions?: TranslatedSuggestionResult; error?: string }>;
  translateEnglishText: (params: { meetingId?: string | null; text: string }) =>
    Promise<{ success: boolean; translatedText?: string; error?: string }>;
  generateInterviewSuggestions: (params: { meetingId: string; lpIds: LeadershipPrinciple[]; count?: number }) =>
    Promise<{ success: boolean; suggestions?: InterviewSuggestionResult; error?: string }>;

  onTranscriptionPartial: (callback: (data: { text: string; speakerLabel: string | null }) => void) => () => void;
  onTranscriptionFinal: (callback: (data: { segment: TranscriptionSegment }) => void) => () => void;
  onTranscriptionCorrected: (callback: (data: CorrectionEvent) => void) => () => void;
  onTranscriptionError: (callback: (data: { error: string }) => void) => () => void;
  onAssistantEvent: (callback: (data: AssistantEvent) => void) => () => void;
  onSummaryComplete: (callback: (data: { meetingId: string; summary: MeetingSummary }) => void) => () => void;
  onConversationLogComplete: (callback: (data: { meetingId: string; conversationLog: ConversationLog }) => void) => () => void;
  onMeetingTitleUpdated: (callback: (data: { meetingId: string; title: string }) => void) => () => void;

  saveSettings: (settings: AppSettings) => Promise<{ success: boolean; error?: string }>;
  loadSettings: () => Promise<{ success: boolean; settings: AppSettings }>;
  clearSettings: () => Promise<{ success: boolean; error?: string }>;
  getAWSCredentials: () => Promise<{
    success: boolean;
    credentials: AWSCredentials | null;
    isConfigured: boolean;
  }>;

  mcp: {
    connect: () => Promise<McpResult<void>>;
    disconnect: () => Promise<McpResult<void>>;
    getStatus: () => Promise<ConnectionStatus>;
    listTools: () => Promise<McpResult<McpTool[]>>;
    callTool: (name: string, args: Record<string, unknown>) => Promise<McpResult<McpToolResult>>;
  };

  agent: {
    chatSend: (params: { meetingId: string; text: string }) => Promise<AgentInvokeResult<AgentChatResult>>;
    resolveAction: (params: { meetingId: string; actionId: string; approved: boolean }) => Promise<AgentInvokeResult<AgentResolveResult>>;
    reset: (params: { meetingId: string }) => Promise<{ success: boolean; error?: string }>;
  };

  vocabulary: {
    list: (languageCode?: VocabularyLanguage) => Promise<Vocabulary[]>;
    get: (id: string) => Promise<Vocabulary | null>;
    create: (request: CreateVocabularyRequest) => Promise<Vocabulary>;
    update: (id: string, updates: UpdateVocabularyRequest) => Promise<Vocabulary | null>;
    delete: (id: string) => Promise<boolean>;
    listEntries: (vocabularyId: string) => Promise<VocabularyEntry[]>;
    addEntry: (request: CreateVocabularyEntryRequest) => Promise<VocabularyEntry>;
    updateEntry: (entryId: string, updates: UpdateVocabularyEntryRequest) => Promise<VocabularyEntry | null>;
    removeEntry: (entryId: string) => Promise<boolean>;
    setDefault: (vocabularyId: string, languageCode: VocabularyLanguage) => Promise<void>;
    getDefault: (languageCode: VocabularyLanguage) => Promise<Vocabulary | null>;
    clearDefault: (languageCode: VocabularyLanguage) => Promise<void>;
    syncToAws: (vocabularyId: string) => Promise<VocabularySyncResult>;
    checkStatus: (vocabularyId: string) => Promise<VocabularyStatus>;
    generateFile: (vocabularyId: string) => Promise<string>;
  };

  platform: NodeJS.Platform;
}

declare global {
  interface Window {
    electronAPI: ElectronAPI;
  }
}
