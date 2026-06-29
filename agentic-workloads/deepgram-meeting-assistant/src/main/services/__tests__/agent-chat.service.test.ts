import { describe, it, expect, vi, beforeEach } from 'vitest';

// electron + better-sqlite3 mock (database.service / agent-meeting-edit 로드용)
vi.mock('electron', () => ({
  BrowserWindow: { getAllWindows: () => [] },
  app: { getPath: vi.fn(() => '/tmp'), getVersion: vi.fn(() => '1.0.0'), isPackaged: false },
}));
vi.mock('better-sqlite3', () => ({
  default: class {
    prepare = () => ({ run: () => ({}), get: () => undefined, all: () => [] });
    exec = () => {};
    pragma = () => {};
    transaction = (fn: (...a: unknown[]) => unknown) => fn;
    close = () => {};
  },
}));

import { AgentChatService } from '../agent-chat.service';
import type { AgentChatDeps } from '../agent-chat.service';
import type { MeetingDetail } from '../../../shared/types/meeting';

const fakeMeeting = (): MeetingDetail =>
  ({
    id: 'm1',
    type: 'agentic',
    title: 't',
    status: 'completed',
    language: 'ko-KR',
    startedAt: new Date(),
    duration: 0,
    metadata: {},
    createdAt: new Date(),
    updatedAt: new Date(),
    segments: [
      { id: 's1', meetingId: 'm1', resultId: 'r1', text: '안녕하세요', startTime: 0, endTime: 1, speakerLabel: null, createdAt: new Date() },
    ],
    correctedSentences: [],
    summary: undefined,
    conversationLog: undefined,
  }) as MeetingDetail;

function makeDeps(overrides: Partial<AgentChatDeps> = {}): AgentChatDeps {
  return {
    bedrockService: { runAgentTurn: vi.fn() } as never,
    db: { getMeeting: vi.fn(() => fakeMeeting()) } as never,
    tools: [],
    modelId: 'test-model',
    ...overrides,
  };
}

describe('AgentChatService', () => {
  let service: AgentChatService;
  beforeEach(() => {
    service = new AgentChatService();
    vi.clearAllMocks();
  });

  it('buildSystemPrompt embeds transcript and notes missing summary', () => {
    const prompt = service.buildSystemPrompt(fakeMeeting());
    expect(prompt).toContain('안녕하세요'); // 전사 포함
    expect(prompt).toContain('아직 생성된 요약이 없습니다');
  });

  it('sendMessage forwards to runAgentTurn and registers pendingActions', async () => {
    const pending = [{ id: 'a1', toolUseId: 'tu1', name: 'create_tech_activity', args: { x: 1 }, kind: 'sfdc_log' as const }];
    const deps = makeDeps();
    (deps.bedrockService.runAgentTurn as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      assistantText: '기록할게요',
      pendingActions: pending,
      updatedMessages: [{ role: 'user', content: [{ text: 'hi' }] }],
    });

    const result = await service.sendMessage('m1', 'SFDC에 기록해줘', deps);
    expect(result.assistantText).toBe('기록할게요');
    expect(result.pendingActions).toHaveLength(1);
    expect(deps.bedrockService.runAgentTurn).toHaveBeenCalledOnce();
  });

  it('resolveAction(sfdc_log, approved) calls mcpCallTool', async () => {
    const mcpCallTool = vi.fn(async () => ({ content: 'ok', isError: false }));
    const deps = makeDeps({ mcpCallTool });
    (deps.bedrockService.runAgentTurn as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      assistantText: '',
      pendingActions: [{ id: 'a1', toolUseId: 'tu1', name: 'create_tech_activity', args: { subject: 'Demo' }, kind: 'sfdc_log' }],
      updatedMessages: [],
    });
    await service.sendMessage('m1', 'x', deps);

    const r = await service.resolveAction('m1', 'a1', true, deps);
    expect(mcpCallTool).toHaveBeenCalledWith('create_tech_activity', { subject: 'Demo' });
    expect(r.applied).toBe(true);
  });

  it('resolveAction(approved=false) does NOT call mcpCallTool', async () => {
    const mcpCallTool = vi.fn(async () => ({ content: 'ok', isError: false }));
    const deps = makeDeps({ mcpCallTool });
    (deps.bedrockService.runAgentTurn as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      assistantText: '',
      pendingActions: [{ id: 'a1', toolUseId: 'tu1', name: 'create_tech_activity', args: {}, kind: 'sfdc_log' }],
      updatedMessages: [],
    });
    await service.sendMessage('m1', 'x', deps);

    const r = await service.resolveAction('m1', 'a1', false, deps);
    expect(mcpCallTool).not.toHaveBeenCalled();
    expect(r.applied).toBe(false);
  });

  it('resolveAction rejects an unknown actionId', async () => {
    const deps = makeDeps();
    const r = await service.resolveAction('m1', 'nope', true, deps);
    expect(r.applied).toBe(false);
  });

  it('resolveAction is one-shot: a second resolve of the same id is a no-op', async () => {
    const mcpCallTool = vi.fn(async () => ({ content: 'ok', isError: false }));
    const deps = makeDeps({ mcpCallTool });
    (deps.bedrockService.runAgentTurn as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      assistantText: '',
      pendingActions: [{ id: 'a1', toolUseId: 'tu1', name: 'create_tech_activity', args: {}, kind: 'sfdc_log' }],
      updatedMessages: [],
    });
    await service.sendMessage('m1', 'x', deps);

    await service.resolveAction('m1', 'a1', true, deps);
    const second = await service.resolveAction('m1', 'a1', true, deps);
    expect(mcpCallTool).toHaveBeenCalledOnce(); // 두 번째는 실행 안 됨
    expect(second.applied).toBe(false);
  });
});
