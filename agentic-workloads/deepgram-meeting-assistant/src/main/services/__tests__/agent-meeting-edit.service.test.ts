import { describe, it, expect, vi, beforeEach } from 'vitest';

// electron mock — BrowserWindow(이벤트 재emit) + app(logger/database 로드 시 사용)
vi.mock('electron', () => ({
  BrowserWindow: { getAllWindows: () => [] },
  app: {
    getPath: vi.fn(() => '/tmp'),
    getVersion: vi.fn(() => '1.0.0'),
    isPackaged: false,
  },
}));

// better-sqlite3 — agent-meeting-edit가 database.service를 import하면 따라온다.
// 실제 DB는 fake로 주입하므로 생성자만 무해하게 통과시키면 된다.
vi.mock('better-sqlite3', () => ({
  default: class {
    prepare = () => ({ run: () => ({}), get: () => undefined, all: () => [] });
    exec = () => {};
    pragma = () => {};
    transaction = (fn: (...a: unknown[]) => unknown) => fn;
    close = () => {};
  },
}));

import { applyMeetingEdit } from '../agent-meeting-edit.service';
import { LOCAL_AGENT_TOOLS } from '../../../shared/constants/agent-tools';
import type { MeetingSummary, ConversationLog } from '../../../shared/types/meeting';

/** applyMeetingEdit이 쓰는 DatabaseService 메서드만 갖춘 fake. */
function makeFakeDb(initialSummary?: MeetingSummary, initialLog?: ConversationLog) {
  let summary: MeetingSummary | null = initialSummary ?? null;
  let convLog: ConversationLog | null = initialLog ?? null;
  return {
    getSummaryByMeeting: vi.fn(() => summary),
    saveSummary: vi.fn((s: Omit<MeetingSummary, 'generatedAt'>) => {
      summary = { ...s, generatedAt: new Date() } as MeetingSummary;
    }),
    getConversationLogByMeeting: vi.fn(() => convLog),
    saveConversationLog: vi.fn((c: Omit<ConversationLog, 'generatedAt'>) => {
      convLog = { ...c, generatedAt: new Date() } as ConversationLog;
    }),
    _peek: () => ({ summary, convLog }),
  };
}

const fullSummary = (): MeetingSummary => ({
  id: 'sum-1',
  meetingId: 'm1',
  mainTopics: ['원래 주제'],
  topicDiscussions: [{ topic: 'T', discussions: ['d'], decisions: [] }],
  keyTakeaways: ['원래 takeaway'],
  confirmedActions: [{ task: '기존 액션', owner: 'A', deadline: '내일' }],
  pendingActions: [],
  followUps: ['원래 followup'],
  openIssues: [],
  generatedAt: new Date(),
  modelId: 'old-model',
});

describe('applyMeetingEdit', () => {
  beforeEach(() => vi.clearAllMocks());

  it('patches a single summary field and preserves the others', () => {
    const db = makeFakeDb(fullSummary());

    const result = applyMeetingEdit(
      db as never,
      'm1',
      LOCAL_AGENT_TOOLS.UPDATE_SUMMARY,
      { field: 'confirmedActions', value: [{ task: '새 액션', owner: 'B', deadline: '금요일' }] },
      'new-model'
    );

    expect(result.ok).toBe(true);
    const { summary } = db._peek();
    // 변경된 필드만 교체
    expect(summary?.confirmedActions).toEqual([{ task: '새 액션', owner: 'B', deadline: '금요일' }]);
    // 나머지 필드는 보존
    expect(summary?.mainTopics).toEqual(['원래 주제']);
    expect(summary?.keyTakeaways).toEqual(['원래 takeaway']);
    expect(summary?.followUps).toEqual(['원래 followup']);
    // id는 유지(INSERT OR REPLACE 정합성)
    expect(summary?.id).toBe('sum-1');
  });

  it('rejects an unknown summary field', () => {
    const db = makeFakeDb(fullSummary());
    const result = applyMeetingEdit(
      db as never,
      'm1',
      LOCAL_AGENT_TOOLS.UPDATE_SUMMARY,
      { field: 'bogusField', value: [] },
      'm'
    );
    expect(result.ok).toBe(false);
    expect(db.saveSummary).not.toHaveBeenCalled();
  });

  it('rejects a value that fails zod validation (action items missing fields)', () => {
    const db = makeFakeDb(fullSummary());
    const result = applyMeetingEdit(
      db as never,
      'm1',
      LOCAL_AGENT_TOOLS.UPDATE_SUMMARY,
      { field: 'confirmedActions', value: [{ task: 'only task' }] }, // owner/deadline 누락
      'm'
    );
    expect(result.ok).toBe(false);
    expect(db.saveSummary).not.toHaveBeenCalled();
  });

  it('creates a fresh summary when none exists yet', () => {
    const db = makeFakeDb(); // 요약 없음
    const result = applyMeetingEdit(
      db as never,
      'm1',
      LOCAL_AGENT_TOOLS.UPDATE_SUMMARY,
      { field: 'keyTakeaways', value: ['첫 takeaway'] },
      'm'
    );
    expect(result.ok).toBe(true);
    const { summary } = db._peek();
    expect(summary?.keyTakeaways).toEqual(['첫 takeaway']);
    expect(summary?.mainTopics).toEqual([]); // 나머지는 빈 골격
  });

  it('replaces conversation log topics wholesale', () => {
    const db = makeFakeDb();
    const result = applyMeetingEdit(
      db as never,
      'm1',
      LOCAL_AGENT_TOOLS.UPDATE_CONVERSATION_LOG,
      { topics: [{ title: '주제1', points: ['p1', 'p2'] }] },
      'm'
    );
    expect(result.ok).toBe(true);
    const { convLog } = db._peek();
    expect(convLog?.topics).toEqual([{ title: '주제1', points: ['p1', 'p2'] }]);
  });
});
