/**
 * MCP IPC Handlers
 *
 * MCP Client Service와 renderer 프로세스 간의 IPC 통신을 처리합니다.
 * 5개 채널에 대한 핸들러를 등록합니다.
 *
 * Requirements: 6.1, 6.2
 */

import { ipcMain } from 'electron';
import { z } from 'zod';
import { IPC_CHANNELS } from '../../shared/constants/ipc-channels';
import { mcpClientService } from '../services/mcp-client.service';

/**
 * MCP 관련 IPC 핸들러 등록
 */
export function registerMcpHandlers(): void {
  // mcp:connect - MCP 서버 연결
  ipcMain.handle(IPC_CHANNELS.MCP_CONNECT, async () => {
    return mcpClientService.connect();
  });

  // mcp:disconnect - MCP 서버 연결 해제
  ipcMain.handle(IPC_CHANNELS.MCP_DISCONNECT, async () => {
    return mcpClientService.disconnect();
  });

  // mcp:getStatus - 연결 상태 조회
  ipcMain.handle(IPC_CHANNELS.MCP_GET_STATUS, () => {
    return mcpClientService.getStatus();
  });

  // mcp:listTools - 도구 목록 조회
  ipcMain.handle(IPC_CHANNELS.MCP_LIST_TOOLS, async () => {
    return mcpClientService.listTools();
  });

  // mcp:callTool - 도구 실행
  ipcMain.handle(
    IPC_CHANNELS.MCP_CALL_TOOL,
    async (_event, name: unknown, args: unknown) => {
      // P1-2: Validate tool execution inputs
      const schema = z.object({
        name: z.string().min(1),
        args: z.record(z.string(), z.unknown()),
      });

      const result = schema.safeParse({ name, args });
      if (!result.success) {
        return {
          success: false,
          error: `Invalid tool call parameters: ${result.error.message}`,
        };
      }

      return mcpClientService.callTool(result.data.name, result.data.args);
    }
  );
}