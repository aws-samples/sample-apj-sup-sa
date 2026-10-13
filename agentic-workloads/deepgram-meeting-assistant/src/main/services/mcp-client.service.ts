/**
 * MCP Client Service
 * 
 * MCP 서버와의 연결을 관리하는 싱글톤 서비스입니다.
 * main 프로세스에서 실행되며, IPC를 통해 renderer에서 호출됩니다.
 * 
 * Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.3, 3.1, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3
 */

import { Client } from '@modelcontextprotocol/sdk/client';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import type {
  ConnectionStatus,
  McpResult,
  McpTool,
  McpToolResult,
} from '../../shared/types/mcp';
import { createLogger } from './logger.service';

const log = createLogger('mcp-client');

export class McpClientService {
  private static instance: McpClientService | null = null;
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;
  private status: ConnectionStatus = 'disconnected';

  private constructor() {
    // 싱글톤 패턴: private constructor
  }

  /**
   * 싱글톤 인스턴스 반환
   */
  static getInstance(): McpClientService {
    if (!McpClientService.instance) {
      McpClientService.instance = new McpClientService();
    }
    return McpClientService.instance;
  }

  /**
   * MCP 서버에 연결
   * Requirements: 1.1, 1.2, 1.3, 1.4
   */
  async connect(): Promise<McpResult<void>> {
    // 이미 연결된 경우
    if (this.client && this.status === 'connected') {
      return { success: true };
    }

    // 연결 시도 중 상태로 변경
    this.status = 'connecting';

    try {
      // 기존 연결이 있으면 정리
      await this.cleanupConnection();

      // 새 클라이언트 생성
      this.client = new Client(
        {
          name: 'meeting-assistant',
          version: '1.0.0',
        },
        { capabilities: {} }
      );

      // StdioClientTransport 생성
      this.transport = new StdioClientTransport({
        command: 'crm-mcp-server',
      });

      // 연결 시도
      await this.client.connect(this.transport);

      // 연결 성공
      this.status = 'connected';
      log.info('MCP server connected');
      return { success: true };
    } catch (error) {
      // 연결 실패
      this.status = 'error';
      await this.cleanupConnection();

      const errorMessage = error instanceof Error 
        ? error.message 
        : 'MCP 서버 연결에 실패했습니다.';
      
      log.error({ error: errorMessage }, 'MCP server connection failed');
      return { success: false, error: errorMessage };
    }
  }

  /**
   * MCP 서버 연결 해제
   * Requirements: 2.1, 2.3
   */
  async disconnect(): Promise<McpResult<void>> {
    try {
      await this.cleanupConnection();
      this.status = 'disconnected';
      log.info('MCP server disconnected');
      return { success: true };
    } catch (error) {
      // 에러가 발생해도 상태는 disconnected로 변경
      this.status = 'disconnected';
      
      const errorMessage = error instanceof Error 
        ? error.message 
        : 'MCP 서버 연결 해제 중 에러가 발생했습니다.';
      
      log.error({ error: errorMessage }, 'MCP server disconnect error');
      return { success: true }; // 에러가 발생해도 성공으로 처리 (상태는 disconnected)
    }
  }

  /**
   * 현재 연결 상태 반환
   * Requirements: 3.1
   */
  getStatus(): ConnectionStatus {
    return this.status;
  }

  /**
   * 사용 가능한 도구 목록 조회
   * Requirements: 4.1, 4.2, 4.3
   */
  async listTools(): Promise<McpResult<McpTool[]>> {
    // 연결되지 않은 상태 체크
    if (this.status !== 'connected' || !this.client) {
      return { 
        success: false, 
        error: 'MCP 서버에 연결되어 있지 않습니다.' 
      };
    }

    try {
      const result = await this.client.listTools();
      
      const tools: McpTool[] = result.tools.map((tool) => ({
        name: tool.name,
        description: tool.description || '',
        inputSchema: tool.inputSchema as McpTool['inputSchema'],
      }));

      return { success: true, data: tools };
    } catch (error) {
      const errorMessage = error instanceof Error 
        ? error.message 
        : '도구 목록 조회에 실패했습니다.';
      
      log.error({ error: errorMessage }, 'Failed to list MCP tools');
      return { success: false, error: errorMessage };
    }
  }

  /**
   * 도구 실행
   * Requirements: 5.1, 5.2, 5.3
   */
  async callTool(
    name: string, 
    args: Record<string, unknown>
  ): Promise<McpResult<McpToolResult>> {
    // 연결되지 않은 상태 체크
    if (this.status !== 'connected' || !this.client) {
      return { 
        success: false, 
        error: 'MCP 서버에 연결되어 있지 않습니다.' 
      };
    }

    try {
      const result = await this.client.callTool({
        name,
        arguments: args,
      });

      const toolResult: McpToolResult = {
        content: result.content,
        isError: typeof result.isError === 'boolean' ? result.isError : false,
      };

      return { success: true, data: toolResult };
    } catch (error) {
      const errorMessage = error instanceof Error 
        ? error.message 
        : `도구 '${name}' 실행에 실패했습니다.`;
      
      log.error({ error: errorMessage }, 'Failed to execute MCP tool');
      return { 
        success: false, 
        error: errorMessage,
        data: { content: null, isError: true }
      };
    }
  }

  /**
   * 내부 연결 정리
   */
  private async cleanupConnection(): Promise<void> {
    if (this.client) {
      try {
        await this.client.close();
      } catch {
        // 클라이언트 종료 에러 무시
      }
      this.client = null;
    }

    if (this.transport) {
      try {
        await this.transport.close();
      } catch {
        // 트랜스포트 종료 에러 무시
      }
      this.transport = null;
    }
  }
}

// 싱글톤 인스턴스 export
export const mcpClientService = McpClientService.getInstance();
