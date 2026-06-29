/**
 * Post-Meeting Agent — 도구 분류
 *
 * agent는 aws-sentral-mcp의 모든 도구 + 회의록 수정 로컬 도구 2종을 사용할 수 있다.
 * 도구는 두 부류로 나뉜다:
 *  - 읽기(read-only): search/get/fetch/list/check 등 조회 도구 → 컨펌 없이 자동 실행
 *    (agent가 조회 결과를 보고 이어서 추론하는 멀티스텝을 허용).
 *  - 쓰기(side-effect): create/update/add/delete/remove 등 + 회의록 수정 → 반드시
 *    사용자 컨펌 후 실행(confirm-gate). 회의록 수정도 쓰기로 분류된다.
 */

/**
 * 회의록을 수정하는 "로컬" 도구 이름. MCP가 아니라 main 프로세스가 직접 처리하며,
 * DB의 summary / conversationLog를 패치한다(agent-meeting-edit.service).
 */
export const LOCAL_AGENT_TOOLS = {
  UPDATE_SUMMARY: 'update_meeting_summary',
  UPDATE_CONVERSATION_LOG: 'update_conversation_log',
} as const;

export type LocalAgentToolName =
  (typeof LOCAL_AGENT_TOOLS)[keyof typeof LOCAL_AGENT_TOOLS];

const LOCAL_AGENT_TOOL_NAMES: readonly string[] = Object.values(LOCAL_AGENT_TOOLS);

/**
 * 읽기(조회) 도구로 간주하는 이름 접두사. aws-sentral-mcp의 도구 이름 규칙을 따른다
 * (search_*, get_*, fetch_*, list_*, check_*). 이 도구들은 부수효과가 없으므로
 * 컨펌 없이 자동 실행한다.
 */
const READ_ONLY_PREFIXES = ['search_', 'get_', 'fetch_', 'list_', 'check_'] as const;

/** 로컬 회의록 수정 도구인지. */
export function isLocalAgentTool(name: string): name is LocalAgentToolName {
  return LOCAL_AGENT_TOOL_NAMES.includes(name);
}

/** 읽기 전용(부수효과 없는 조회) 도구인지 — 컨펌 없이 자동 실행 대상. */
export function isReadOnlyTool(name: string): boolean {
  return READ_ONLY_PREFIXES.some((p) => name.startsWith(p));
}

/**
 * 도구 실행 정책:
 *  - 'auto'        : 읽기 도구. 컨펌 없이 즉시 실행하고 결과로 추론을 이어간다.
 *  - 'meeting_edit': 회의록 수정 로컬 도구. 사용자 컨펌 후 DB 패치.
 *  - 'sfdc_log'    : 그 외 모든 MCP 도구(쓰기/생성). 사용자 컨펌 후 MCP 실행.
 */
export type ToolPolicy = 'auto' | 'meeting_edit' | 'sfdc_log';

export function toolPolicy(name: string): ToolPolicy {
  if (isLocalAgentTool(name)) return 'meeting_edit';
  if (isReadOnlyTool(name)) return 'auto';
  return 'sfdc_log';
}
