/**
 * Post-Meeting Agent — 회의록 수정 적용 (로컬 도구)
 *
 * agent가 호출한 "회의록 수정" 도구(update_meeting_summary / update_conversation_log)를
 * 실제 DB에 반영한다. LLM 호출과 분리되어 있어(사용자 컨펌 후에만 호출됨) 순수한
 * DB 패치 + renderer 이벤트 재emit만 담당한다.
 *
 * 설계: summary는 필드 단위 부분 패치(전체 재생성 시 멀쩡한 필드 환각 파괴 방지),
 * conversationLog는 topics 통째 교체(구조가 단순). 적용 후 기존 요약/회의록 완료
 * 이벤트를 재emit해 렌더러 탭이 자동 갱신되게 한다.
 */
import { BrowserWindow } from 'electron';
import { z } from 'zod';
import { v4 as uuidv4 } from 'uuid';
import type { MeetingSummary, ConversationLog } from '../../shared/types/meeting';
import type { JsonSchema } from '../../shared/types/mcp';
import { IPC_CHANNELS } from '../../shared/constants/ipc-channels';
import { LOCAL_AGENT_TOOLS } from '../../shared/constants/agent-tools';
import {
  DatabaseService,
  ActionItemSchema,
  TopicDiscussionSchema,
  ConversationTopicSchema,
} from './database.service';
import { createLogger } from './logger.service';

const log = createLogger('agent-meeting-edit');

/** update_meeting_summary가 패치할 수 있는 필드. */
const SUMMARY_FIELDS = [
  'mainTopics',
  'topicDiscussions',
  'keyTakeaways',
  'confirmedActions',
  'pendingActions',
  'followUps',
  'openIssues',
] as const;
type SummaryField = (typeof SUMMARY_FIELDS)[number];

/**
 * 로컬 회의록 수정 도구의 JsonSchema (Bedrock toolSpec로 노출됨).
 * 중첩 구조(topicDiscussions/actions)는 JsonSchema 표현력 한계로 description에
 * 형태를 명시하고, 실제 적용 시 zod로 방어 검증한다.
 */
export const MEETING_EDIT_TOOL_SCHEMAS: Record<string, { description: string; inputSchema: JsonSchema }> = {
  [LOCAL_AGENT_TOOLS.UPDATE_SUMMARY]: {
    description:
      'AI 회의 요약(summary)의 한 필드를 통째로 교체한다. 사용자 승인 후에만 실제 반영된다. ' +
      'field는 다음 중 하나: mainTopics(string[]), keyTakeaways(string[]), followUps(string[]), ' +
      'openIssues(string[]), confirmedActions/pendingActions({task,owner,deadline}[]), ' +
      'topicDiscussions({topic,discussions:string[],decisions:string[]}[]). ' +
      'value는 해당 필드의 새 전체 값(기존 값을 덮어씀).',
    inputSchema: {
      type: 'object',
      properties: {
        field: {
          type: 'string',
          description: `교체할 요약 필드. 가능한 값: ${SUMMARY_FIELDS.join(', ')}`,
        },
        value: {
          type: 'array',
          description:
            '필드의 새 전체 값. 문자열 배열이거나 객체 배열(actions: {task,owner,deadline}, ' +
            'topicDiscussions: {topic,discussions,decisions}).',
        },
      },
      required: ['field', 'value'],
    },
  },
  [LOCAL_AGENT_TOOLS.UPDATE_CONVERSATION_LOG]: {
    description:
      '대화 요약(conversationLog)의 topics 전체를 교체한다. 사용자 승인 후에만 실제 반영된다. ' +
      'topics는 {title:string, points:string[]} 객체의 배열.',
    inputSchema: {
      type: 'object',
      properties: {
        topics: {
          type: 'array',
          description: '새 topics 전체. 각 항목은 {title, points:string[]}.',
        },
      },
      required: ['topics'],
    },
  },
};

/** field별 zod 검증 스키마. */
function validateSummaryField(field: SummaryField, value: unknown): unknown {
  switch (field) {
    case 'mainTopics':
    case 'keyTakeaways':
    case 'followUps':
    case 'openIssues':
      return z.array(z.string()).parse(value);
    case 'confirmedActions':
    case 'pendingActions':
      return z.array(ActionItemSchema).parse(value);
    case 'topicDiscussions':
      return z.array(TopicDiscussionSchema).parse(value);
  }
}

function sendToRenderer(channel: string, data: unknown): void {
  const windows = BrowserWindow.getAllWindows();
  if (windows.length > 0) {
    windows[0].webContents.send(channel, data);
  }
}

/** 빈 요약 골격(요약이 아직 없는 미팅에서 첫 수정 시). */
function emptySummary(meetingId: string, modelId: string): Omit<MeetingSummary, 'generatedAt'> {
  return {
    id: uuidv4(),
    meetingId,
    mainTopics: [],
    topicDiscussions: [],
    keyTakeaways: [],
    confirmedActions: [],
    pendingActions: [],
    followUps: [],
    openIssues: [],
    modelId,
  };
}

export interface MeetingEditResult {
  ok: boolean;
  /** 사람용 결과 설명(채팅 system 메시지로 표시). */
  message: string;
}

/**
 * agent의 회의록 수정 도구 호출 1건을 DB에 반영한다.
 *
 * @param db        DatabaseService 인스턴스(테스트 시 주입)
 * @param meetingId 대상 미팅
 * @param toolName  LOCAL_AGENT_TOOLS 중 하나
 * @param args      도구 인자(검증 전 raw)
 * @param modelId   저장 시 기록할 modelId
 */
export function applyMeetingEdit(
  db: DatabaseService,
  meetingId: string,
  toolName: string,
  args: Record<string, unknown>,
  modelId: string
): MeetingEditResult {
  try {
    if (toolName === LOCAL_AGENT_TOOLS.UPDATE_SUMMARY) {
      const field = String(args.field ?? '') as SummaryField;
      if (!SUMMARY_FIELDS.includes(field)) {
        return { ok: false, message: `알 수 없는 요약 필드입니다: ${String(args.field)}` };
      }
      const validated = validateSummaryField(field, args.value);

      const existing = db.getSummaryByMeeting(meetingId);
      const base: Omit<MeetingSummary, 'generatedAt'> = existing
        ? {
            id: existing.id,
            meetingId: existing.meetingId,
            mainTopics: existing.mainTopics,
            topicDiscussions: existing.topicDiscussions,
            keyTakeaways: existing.keyTakeaways,
            confirmedActions: existing.confirmedActions,
            pendingActions: existing.pendingActions,
            followUps: existing.followUps,
            openIssues: existing.openIssues,
            modelId,
          }
        : emptySummary(meetingId, modelId);

      const updated = { ...base, [field]: validated };
      db.saveSummary(updated);

      const summary = db.getSummaryByMeeting(meetingId);
      sendToRenderer(IPC_CHANNELS.SUMMARY_COMPLETE, { meetingId, summary });
      log.info({ meetingId, field }, 'Applied summary edit');
      return { ok: true, message: `요약의 "${field}" 필드를 수정했습니다.` };
    }

    if (toolName === LOCAL_AGENT_TOOLS.UPDATE_CONVERSATION_LOG) {
      const topics = z.array(ConversationTopicSchema).parse(args.topics);
      const existing = db.getConversationLogByMeeting(meetingId);
      const updated: Omit<ConversationLog, 'generatedAt'> = {
        id: existing?.id ?? uuidv4(),
        meetingId,
        topics,
        modelId,
      };
      db.saveConversationLog(updated);

      const conversationLog = db.getConversationLogByMeeting(meetingId);
      sendToRenderer(IPC_CHANNELS.CONVERSATION_LOG_COMPLETE, { meetingId, conversationLog });
      log.info({ meetingId, topicCount: topics.length }, 'Applied conversation log edit');
      return { ok: true, message: `대화 요약을 ${topics.length}개 주제로 수정했습니다.` };
    }

    return { ok: false, message: `지원하지 않는 회의록 수정 도구입니다: ${toolName}` };
  } catch (err) {
    log.error({ err: String(err), meetingId, toolName }, 'Meeting edit failed');
    return { ok: false, message: `회의록 수정에 실패했습니다: ${String(err)}` };
  }
}
