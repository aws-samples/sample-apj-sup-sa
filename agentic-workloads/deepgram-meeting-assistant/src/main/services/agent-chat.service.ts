/**
 * Post-Meeting Agent — 채팅 세션 + KB 컨텍스트 오케스트레이션 (싱글톤)
 *
 * 미팅별 대화 히스토리(Bedrock Message[])와 보류 중인 부수효과(pendingActions)를
 * 메모리에 유지한다. 회의록(전사+요약)을 system 프롬프트로 주입해 KB로 삼고,
 * BedrockService.runAgentTurn(tool use, 자동 실행 없음)을 구동한다.
 *
 * 부수효과(회의록 수정 / SFDC 로깅)는 즉시 실행하지 않고 pendingAction으로 올려
 * 사용자 컨펌 후 resolveAction에서 실행한다(confirm-gate). LLM/MCP/DB 의존성은
 * 호출부에서 deps로 주입해 서비스가 자격증명·모델을 직접 들고 있지 않게 한다.
 */
import type { Message } from '@aws-sdk/client-bedrock-runtime';
import type { BedrockService } from './bedrock.service';
import type { DatabaseService } from './database.service';
import type { MeetingDetail } from '../../shared/types/meeting';
import type { AgentToolSpec, AgentPendingAction } from '../../shared/types/agent';
import { isLocalAgentTool, isReadOnlyTool } from '../../shared/constants/agent-tools';
import { applyMeetingEdit } from './agent-meeting-edit.service';
import { MAX_TRANSCRIPT_LENGTH } from '../constants';
import { createLogger } from './logger.service';

const log = createLogger('agent-chat');

const SYSTEM_GUIDELINES = `당신은 회의가 끝난 뒤 회의록을 함께 다듬고 후속 작업을 돕는 어시스턴트입니다.
- 아래에 제공된 회의 전사와 현재 회의록(요약/대화 요약)을 지식 베이스로 사용하세요.
- 회의록을 고치거나 Salesforce에 기록하는 등 "부수효과"가 필요한 작업은 반드시 제공된 도구를 호출해 "제안"하세요. 실제 반영은 사용자가 승인한 뒤에만 일어납니다.
- 도구를 호출하기 전에, 무엇을 어떻게 바꿀지 한국어로 간단히 설명하세요.
- 전사에서 확실히 알 수 없는 내용은 추측하지 말고 사용자에게 물어보세요.
- 답변은 한국어로 간결하게 하세요.
- 답변은 마크다운이 아닌 일반 텍스트(plain text)로 작성하세요. **굵게**, #제목, - 목록 기호 같은 마크다운 문법을 쓰지 마세요. 목록이 필요하면 줄바꿈과 "·" 또는 숫자(1. 2.)로만 표현하세요.`;

interface AgentSession {
  messages: Message[];
  pending: Map<string, AgentPendingAction>;
}

/** sendMessage/resolveAction에 주입하는 의존성. */
export interface AgentChatDeps {
  bedrockService: BedrockService;
  db: DatabaseService;
  /** 이 턴에 노출할 도구(회의록 로컬 2종 + SFDC 화이트리스트 교집합). */
  tools: AgentToolSpec[];
  /** MCP 도구 실행기(미연결이면 생략). */
  mcpCallTool?: (name: string, args: Record<string, unknown>) => Promise<{ content: unknown; isError: boolean }>;
  /** 저장 시 기록할 modelId. */
  modelId: string;
}

class AgentChatService {
  private sessions = new Map<string, AgentSession>();

  private getOrCreate(meetingId: string): AgentSession {
    let s = this.sessions.get(meetingId);
    if (!s) {
      s = { messages: [], pending: new Map() };
      this.sessions.set(meetingId, s);
    }
    return s;
  }

  /** 회의록(전사 + 현재 요약)을 KB로 묶은 system 프롬프트를 만든다. */
  buildSystemPrompt(meeting: MeetingDetail): string {
    // 전사: correctedSentences 우선, 없으면 segments. meeting.handlers의 패턴과 동일.
    const lines =
      meeting.correctedSentences.length > 0
        ? meeting.correctedSentences.map((s) => `[${s.speakerLabel || 'Speaker'}] ${s.correctedText}`)
        : meeting.segments.map((s) => `[${s.speakerLabel || 'Speaker'}] ${s.text}`);
    let transcript = lines.join('\n');
    if (transcript.length > MAX_TRANSCRIPT_LENGTH) {
      // 최근 발화를 우선 보존(끝에서부터 자름).
      transcript = transcript.slice(-MAX_TRANSCRIPT_LENGTH);
    }

    const summaryText = meeting.summary
      ? JSON.stringify(
          {
            mainTopics: meeting.summary.mainTopics,
            keyTakeaways: meeting.summary.keyTakeaways,
            confirmedActions: meeting.summary.confirmedActions,
            pendingActions: meeting.summary.pendingActions,
            followUps: meeting.summary.followUps,
            openIssues: meeting.summary.openIssues,
            topicDiscussions: meeting.summary.topicDiscussions,
          },
          null,
          2
        )
      : '(아직 생성된 요약이 없습니다)';

    const logText = meeting.conversationLog
      ? JSON.stringify(meeting.conversationLog.topics, null, 2)
      : '(아직 생성된 대화 요약이 없습니다)';

    return [
      SYSTEM_GUIDELINES,
      '',
      '=== 회의 전사 ===',
      transcript || '(전사 없음)',
      '',
      '=== 현재 요약(summary) ===',
      summaryText,
      '',
      '=== 현재 대화 요약(conversationLog) ===',
      logText,
    ].join('\n');
  }

  /**
   * 사용자 메시지를 보내 agent 한 턴을 실행한다. 부수효과는 pendingAction으로만
   * 돌아오며 실행되지 않는다(컨펌 대기).
   */
  async sendMessage(
    meetingId: string,
    text: string,
    deps: AgentChatDeps
  ): Promise<{ assistantText: string; pendingActions: AgentPendingAction[] }> {
    const meeting = deps.db.getMeeting(meetingId);
    if (!meeting) {
      throw new Error('미팅을 찾을 수 없습니다.');
    }
    const session = this.getOrCreate(meetingId);
    const system = this.buildSystemPrompt(meeting);

    session.messages.push({ role: 'user', content: [{ text }] });

    const { assistantText, pendingActions, updatedMessages } = await deps.bedrockService.runAgentTurn({
      messages: session.messages,
      system,
      tools: deps.tools,
      // 읽기 도구는 루프 안에서 자동 실행한다(멀티스텝). 쓰기/회의록 수정은
      // runAgentTurn이 pendingAction으로만 모아 컨펌 대기로 둔다.
      mcpCallTool: deps.mcpCallTool,
    });

    session.messages = updatedMessages;
    for (const action of pendingActions) {
      session.pending.set(action.id, action);
    }

    log.info({ meetingId, pendingCount: pendingActions.length }, 'agent turn complete');
    return { assistantText, pendingActions };
  }

  /**
   * pendingAction을 승인/거절한다. 승인 시에만 실제 부수효과를 실행한다.
   * 회의록 수정은 applyMeetingEdit(DB 패치 + 이벤트 재emit), SFDC 로깅은 mcpCallTool.
   */
  async resolveAction(
    meetingId: string,
    actionId: string,
    approved: boolean,
    deps: AgentChatDeps
  ): Promise<{ resultText: string; applied: boolean }> {
    const session = this.sessions.get(meetingId);
    const action = session?.pending.get(actionId);
    if (!session || !action) {
      return { resultText: '해당 작업을 찾을 수 없습니다(이미 처리되었을 수 있습니다).', applied: false };
    }
    // 일회성: 조회 직후 제거(중복 실행 방지).
    session.pending.delete(actionId);

    if (!approved) {
      return { resultText: `작업을 취소했습니다: ${action.name}`, applied: false };
    }

    if (action.kind === 'meeting_edit') {
      if (!isLocalAgentTool(action.name)) {
        return { resultText: `허용되지 않은 회의록 도구입니다: ${action.name}`, applied: false };
      }
      const r = applyMeetingEdit(deps.db, meetingId, action.name, action.args, deps.modelId);
      return { resultText: r.message, applied: r.ok };
    }

    // sfdc_log — 방어 검증. 읽기 도구는 자동 실행 대상이므로 컨펌 경로로 오면 안 된다.
    if (isReadOnlyTool(action.name) || isLocalAgentTool(action.name)) {
      return { resultText: `잘못된 작업 분류입니다: ${action.name}`, applied: false };
    }
    if (!deps.mcpCallTool) {
      return { resultText: 'SFDC(MCP)에 연결되어 있지 않아 실행할 수 없습니다.', applied: false };
    }
    try {
      const result = await deps.mcpCallTool(action.name, action.args);
      if (result.isError) {
        return { resultText: `SFDC 로깅 실패 (${action.name}): ${stringifyContent(result.content)}`, applied: false };
      }
      return { resultText: `SFDC에 기록했습니다 (${action.name}).`, applied: true };
    } catch (err) {
      log.error({ err: String(err), meetingId, tool: action.name }, 'SFDC tool call failed');
      return { resultText: `SFDC 로깅 중 오류: ${String(err)}`, applied: false };
    }
  }

  clearSession(meetingId: string): void {
    this.sessions.delete(meetingId);
  }

  resetAll(): void {
    this.sessions.clear();
  }
}

function stringifyContent(content: unknown): string {
  if (typeof content === 'string') return content;
  try {
    return JSON.stringify(content).slice(0, 200);
  } catch {
    return String(content);
  }
}

export const agentChatService = new AgentChatService();
export { AgentChatService };
