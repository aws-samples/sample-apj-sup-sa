/**
 * Post-Meeting Agent 공용 타입.
 *
 * main(BedrockService.runAgentTurn / AgentChatService)과 renderer(useAgentChat)가
 * 공유한다. Bedrock Converse API의 메시지 히스토리는 main 내부 구현 디테일이므로
 * 여기서는 SDK 타입에 의존하지 않는 직렬화 가능한 형태만 노출한다.
 */
import type { JsonSchema } from './mcp';

/** agent에 노출할 도구 1개의 스펙 (회의록 로컬 도구 또는 CRM 로깅 MCP 도구). */
export interface AgentToolSpec {
  name: string;
  description: string;
  inputSchema: JsonSchema;
}

/**
 * agent가 호출하려는 부수효과 동작 1건. 즉시 실행하지 않고 renderer로 올려
 * 사용자 컨펌을 받는다(confirm-gate). 승인 시 agent:resolve-action으로 실행.
 */
export interface AgentPendingAction {
  /** 이 pending action의 고유 id(컨펌 매칭용, uuid). */
  id: string;
  /** Bedrock tool_use 블록의 toolUseId (히스토리 정합성용). */
  toolUseId: string;
  /** 호출 도구 이름. */
  name: string;
  /** 도구 인자(JSON object). */
  args: Record<string, unknown>;
  /** 회의록 수정인지 CRM 로깅인지. */
  kind: 'meeting_edit' | 'crm_log';
}

/** renderer 채팅 UI에 표시되는 메시지 1개. */
export interface AgentChatMessage {
  role: 'user' | 'assistant' | 'system';
  text: string;
}

/** agent:chat-send 응답. */
export interface AgentChatResult {
  /** assistant가 낸 텍스트(없을 수 있음 — 도구만 호출한 경우). */
  assistantText: string;
  /** 사용자 컨펌이 필요한 동작들(없으면 빈 배열). */
  pendingActions: AgentPendingAction[];
  /** MCP(CRM) 연결 여부 — 미연결이면 로깅 도구가 비활성. */
  mcpConnected: boolean;
}

/** agent:resolve-action 응답. */
export interface AgentResolveResult {
  /** 실행/취소 결과를 설명하는 사람용 텍스트(채팅에 system 메시지로 표시). */
  resultText: string;
  /** 실제 부수효과가 일어났는지(승인+성공). */
  applied: boolean;
}
