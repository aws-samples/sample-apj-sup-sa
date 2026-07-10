/**
 * MCP (Model Context Protocol) 관련 타입 정의
 * Requirements: 3.2, 6.2
 */

/**
 * MCP 서버 연결 상태
 * - disconnected: 연결되지 않음
 * - connecting: 연결 시도 중
 * - connected: 연결됨
 * - error: 에러 발생
 */
export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

/**
 * MCP 작업 결과를 나타내는 제네릭 타입
 * 모든 IPC 응답에서 일관된 형식으로 사용됨
 */
export interface McpResult<T> {
  success: boolean;
  data?: T;
  error?: string;
}

/**
 * JSON Schema 타입 정의
 */
export interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  description?: string;
}

export interface JsonSchemaProperty {
  type?: string;
  description?: string;
  enum?: string[];
  default?: unknown;
  items?: JsonSchemaProperty;
}

/**
 * MCP 서버가 제공하는 도구 정보
 */
export interface McpTool {
  name: string;
  description: string;
  inputSchema?: JsonSchema;
}

/**
 * MCP 도구 실행 결과
 */
export interface McpToolResult {
  content: unknown;
  isError: boolean;
}

/**
 * MCP 도구 호출 파라미터
 */
export interface McpCallToolParams {
  name: string;
  args: Record<string, unknown>;
}
