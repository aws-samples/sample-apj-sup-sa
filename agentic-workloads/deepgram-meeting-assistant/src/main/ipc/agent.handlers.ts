/**
 * Post-Meeting Agent IPC Handlers
 *
 * 회의록 대화 agent(텍스트 채팅 + 회의록 수정 + CRM 로깅)의 IPC.
 *  - agent:chat-send     사용자 메시지 → agent 한 턴 → {assistantText, pendingActions}
 *  - agent:resolve-action 사용자 컨펌 → 부수효과 실행/취소 → {resultText, applied}
 *  - agent:reset          미팅 채팅 세션 정리
 *
 * agent에 노출하는 도구 = 회의록 로컬 도구 2종 + CRM 로깅 화이트리스트 교집합.
 * read-only 도구는 노출하지 않으므로 "도구 호출 = 부수효과 = 컨펌 게이트"가 성립.
 */
import { ipcMain } from 'electron';
import { z } from 'zod';
import { IPC_CHANNELS } from '../../shared/constants/ipc-channels';
import {
  agentChatService,
  meetingStreamingService,
  mcpClientService,
} from '../services';
import type { AgentChatDeps } from '../services';
import { meetingCorrectionService } from '../services';
import { MEETING_EDIT_TOOL_SCHEMAS } from '../services/agent-meeting-edit.service';
import type { AgentToolSpec } from '../../shared/types/agent';
import { createLogger } from '../services/logger.service';
import { rateLimiter } from '../services/rate-limiter.service';

const log = createLogger('agent-handlers');

const AGENT_RATE_KEY = 'agent:chat-send';
rateLimiter.register(AGENT_RATE_KEY, { windowMs: 60 * 1000, maxRequests: 20 });

interface CredentialsInput {
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
}
interface SettingsInput {
  bedrock: { summaryModelId: string; temperature: number };
}

const ChatSendSchema = z.object({
  meetingId: z.string().uuid(),
  text: z.string().min(1).max(4000),
});
const ResolveActionSchema = z.object({
  meetingId: z.string().uuid(),
  actionId: z.string().min(1),
  approved: z.boolean(),
});
const ResetSchema = z.object({ meetingId: z.string().uuid() });

/** 로컬 회의록 수정 도구 2종을 AgentToolSpec로. */
function localTools(): AgentToolSpec[] {
  return Object.entries(MEETING_EDIT_TOOL_SCHEMAS).map(([name, def]) => ({
    name,
    description: def.description,
    inputSchema: def.inputSchema,
  }));
}

/** MCP 연결 시 CRM의 모든 도구를 AgentToolSpec로 노출(읽기/쓰기 무관).
 *  읽기는 자동 실행, 쓰기는 컨펌 — 분류는 toolPolicy가 런타임에 판단한다. */
async function mcpTools(): Promise<{ tools: AgentToolSpec[]; connected: boolean }> {
  if (mcpClientService.getStatus() !== 'connected') {
    return { tools: [], connected: false };
  }
  const result = await mcpClientService.listTools();
  if (!result.success || !result.data) {
    return { tools: [], connected: true };
  }
  const tools = result.data.map((t) => ({
    name: t.name,
    description: t.description,
    inputSchema: t.inputSchema ?? { type: 'object', properties: {} },
  }));
  return { tools, connected: true };
}

export function registerAgentHandlers(
  getCredentials: () => Promise<CredentialsInput | null>,
  getSettings: () => Promise<SettingsInput>
): void {
  ipcMain.handle(IPC_CHANNELS.AGENT_CHAT_SEND, async (_event, payload: unknown) => {
    const parsed = ChatSendSchema.safeParse(payload);
    if (!parsed.success) {
      return { success: false, error: `Invalid params: ${parsed.error.message}` };
    }
    if (!rateLimiter.tryRequest(AGENT_RATE_KEY)) {
      const retryAfter = Math.ceil(rateLimiter.getRetryAfterMs(AGENT_RATE_KEY) / 1000);
      return { success: false, error: `요청이 너무 많습니다. ${retryAfter}초 후 다시 시도하세요.` };
    }

    const credentials = await getCredentials();
    if (!credentials) {
      return { success: false, error: 'AWS 자격증명이 설정되지 않았습니다.' };
    }
    const settings = await getSettings();

    try {
      const bedrockService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.summaryModelId,
        8000,
        settings.bedrock.temperature
      );
      const { tools: serverTools, connected } = await mcpTools();
      const tools = [...localTools(), ...serverTools];

      const deps: AgentChatDeps = {
        bedrockService,
        db: meetingCorrectionService.ensureDatabase(),
        tools,
        modelId: settings.bedrock.summaryModelId,
        mcpCallTool: connected
          ? async (name, args) => {
              const r = await mcpClientService.callTool(name, args);
              if (!r.success || !r.data) {
                return { content: r.success ? null : r.error, isError: true };
              }
              return { content: r.data.content, isError: r.data.isError };
            }
          : undefined,
      };

      const { assistantText, pendingActions } = await agentChatService.sendMessage(
        parsed.data.meetingId,
        parsed.data.text,
        deps
      );
      return { success: true, data: { assistantText, pendingActions, mcpConnected: connected } };
    } catch (err) {
      log.error({ err: String(err) }, 'agent chat-send failed');
      return { success: false, error: String(err) };
    }
  });

  ipcMain.handle(IPC_CHANNELS.AGENT_RESOLVE_ACTION, async (_event, payload: unknown) => {
    const parsed = ResolveActionSchema.safeParse(payload);
    if (!parsed.success) {
      return { success: false, error: `Invalid params: ${parsed.error.message}` };
    }
    const credentials = await getCredentials();
    if (!credentials) {
      return { success: false, error: 'AWS 자격증명이 설정되지 않았습니다.' };
    }
    const settings = await getSettings();

    try {
      // resolveAction은 meeting_edit(DB)·crm_log(MCP)만 수행하므로 bedrock 호출은
      // 없지만, deps 형태를 맞추기 위해 동일하게 구성한다.
      const bedrockService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.summaryModelId,
        8000,
        settings.bedrock.temperature
      );
      const connected = mcpClientService.getStatus() === 'connected';
      const deps: AgentChatDeps = {
        bedrockService,
        db: meetingCorrectionService.ensureDatabase(),
        tools: [],
        modelId: settings.bedrock.summaryModelId,
        mcpCallTool: connected
          ? async (name, args) => {
              const r = await mcpClientService.callTool(name, args);
              if (!r.success || !r.data) {
                return { content: r.success ? null : r.error, isError: true };
              }
              return { content: r.data.content, isError: r.data.isError };
            }
          : undefined,
      };

      const result = await agentChatService.resolveAction(
        parsed.data.meetingId,
        parsed.data.actionId,
        parsed.data.approved,
        deps
      );
      return { success: true, data: result };
    } catch (err) {
      log.error({ err: String(err) }, 'agent resolve-action failed');
      return { success: false, error: String(err) };
    }
  });

  ipcMain.handle(IPC_CHANNELS.AGENT_RESET, (_event, payload: unknown) => {
    const parsed = ResetSchema.safeParse(payload);
    if (!parsed.success) {
      return { success: false, error: `Invalid params: ${parsed.error.message}` };
    }
    agentChatService.clearSession(parsed.data.meetingId);
    return { success: true };
  });
}
