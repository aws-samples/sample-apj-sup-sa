import { z } from 'zod';

export const PROTOCOL_VERSION = 1 as const;

const base = { v: z.literal(PROTOCOL_VERSION), meetingId: z.string() };

// Main → Server
export const ClientStartSchema = z.object({
  ...base,
  type: z.literal('start'),
  language: z.string(),
  targetLanguage: z.string().optional(),
  vocabularyName: z.string().optional(),
  enableCorrection: z.boolean(),
});
export const ClientAudioSchema = z.object({
  ...base, type: z.literal('audio'), seq: z.number().int(), data: z.string(),
});
export const ClientStopSchema = z.object({ ...base, type: z.literal('stop') });

export const ClientMessageSchema = z.discriminatedUnion('type', [
  ClientStartSchema, ClientAudioSchema, ClientStopSchema,
]);

// Server → Main
export const ServerReadySchema = z.object({ ...base, type: z.literal('ready') });
export const ServerPartialSchema = z.object({
  ...base, type: z.literal('partial'), text: z.string(), speakerLabel: z.string().nullish(),
});
export const ServerFinalSchema = z.object({
  ...base, type: z.literal('final'), resultId: z.string(), text: z.string(),
  startTime: z.number(), endTime: z.number(),
  speakerLabel: z.string().nullish(), confidence: z.number().nullish(),
});
export const ServerCorrectionSchema = z.object({
  ...base, type: z.literal('correction'), resultId: z.string(),
  original: z.string(), corrected: z.string(),
});
export const ServerStoppedSchema = z.object({ ...base, type: z.literal('stopped') });
export const ServerErrorSchema = z.object({
  v: z.literal(PROTOCOL_VERSION), type: z.literal('error'),
  meetingId: z.string().optional(), message: z.string(),
});

// Voice assistant (wake-word → LLM → TTS) 응답 스트림.
// assistant_start: wake word 감지, LLM 응답 시작 (query = 사용자가 던진 질문)
// assistant_text:  LLM 응답 텍스트 (스트리밍 조각; done=true면 마지막)
// assistant_audio: TTS 오디오 청크 (base64 PCM s16le, sampleRate Hz)
// assistant_end:   응답 종료
export const ServerAssistantStartSchema = z.object({
  ...base, type: z.literal('assistant_start'), query: z.string(),
});
export const ServerAssistantTextSchema = z.object({
  ...base, type: z.literal('assistant_text'), text: z.string(), done: z.boolean(),
});
export const ServerAssistantAudioSchema = z.object({
  ...base, type: z.literal('assistant_audio'), data: z.string(), sampleRate: z.number(),
});
export const ServerAssistantEndSchema = z.object({
  ...base, type: z.literal('assistant_end'),
});

export const ServerMessageSchema = z.discriminatedUnion('type', [
  ServerReadySchema, ServerPartialSchema, ServerFinalSchema,
  ServerCorrectionSchema, ServerStoppedSchema, ServerErrorSchema,
  ServerAssistantStartSchema, ServerAssistantTextSchema,
  ServerAssistantAudioSchema, ServerAssistantEndSchema,
]);

export type ClientMessage = z.infer<typeof ClientMessageSchema>;
export type ServerMessage = z.infer<typeof ServerMessageSchema>;

// main → renderer로 전달되는 정규화된 음성 어시스턴트 이벤트.
// (서버의 assistant_* 메시지를 bridge가 이 형태로 변환한다.)
export type AssistantEvent =
  | { kind: 'start'; query: string }
  | { kind: 'text'; text: string; done: boolean }
  | { kind: 'audio'; data: string; sampleRate: number }
  | { kind: 'end' };
