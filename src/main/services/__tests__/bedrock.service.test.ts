import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BedrockService } from '../bedrock.service';
import { MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY } from '../../constants';

// Mock AWS SDK
vi.mock('@aws-sdk/client-bedrock-runtime', () => {
  return {
    BedrockRuntimeClient: class {
      send = vi.fn();
    },
    InvokeModelCommand: vi.fn(),
    ConverseCommand: vi.fn(),
  };
});

describe('BedrockService', () => {
  let service: BedrockService;
  let mockClient: any;

  const config = {
    region: 'us-east-1',
    credentials: { accessKeyId: 'test', secretAccessKey: 'test' },
    modelId: 'anthropic.claude-3-sonnet-20240229-v1:0', // Trigger isAnthropicModel = true
    maxTokens: 1000,
    temperature: 0,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    service = new BedrockService(config);
    // Access the private client property for mocking its behavior
    mockClient = (service as any).client;
  });

  describe('generateSummary', () => {
    it('returns empty summary immediately if transcript is too short', async () => {
      const shortTranscript = 'a'.repeat(MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY - 1);
      const result = await service.generateSummary(shortTranscript, 'en-US');

      expect(mockClient.send).not.toHaveBeenCalled();
      expect(result.mainTopics).toEqual([]);
      expect(result.confirmedActions).toEqual([]);
    });

    it('parses valid JSON response correctly', async () => {
      const transcript = 'a'.repeat(MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY + 1);
      const mockResponse = {
        mainTopics: ['Topic A'],
        topicDiscussions: [{ topic: 'Topic A', discussions: ['Discussion 1'], decisions: [] }],
        keyTakeaways: ['Takeaway 1'],
        confirmedActions: [{ task: 'Task 1', owner: 'Me', deadline: 'Today' }],
        pendingActions: [],
        followUps: [],
        openIssues: [],
      };

      // Mock the response structure expected by invokeAnthropic
      const responsePayload = {
        content: [{ text: JSON.stringify(mockResponse) }]
      };
      
      const body = new TextEncoder().encode(JSON.stringify(responsePayload));
      mockClient.send.mockResolvedValue({ body });

      const result = await service.generateSummary(transcript, 'en-US');

      expect(result.mainTopics).toEqual(['Topic A']);
      expect(result.confirmedActions).toHaveLength(1);
      expect(result.confirmedActions[0].task).toBe('Task 1');
    });

    it('handles invalid JSON gracefully', async () => {
        const transcript = 'a'.repeat(MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY + 1);
        
        // Mock invalid JSON
        const responsePayload = {
          content: [{ text: 'This is not JSON' }]
        };
        const body = new TextEncoder().encode(JSON.stringify(responsePayload));
        mockClient.send.mockResolvedValue({ body });
  
        const result = await service.generateSummary(transcript, 'en-US');
  
        expect(result.mainTopics).toEqual([]);
        expect(result.confirmedActions).toEqual([]);
    });

    it('handles partial/malformed JSON by returning empty default', async () => {
      const transcript = 'a'.repeat(MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY + 1);
      
      // Mock malformed JSON (valid JSON but missing fields)
      const mockResponse = {
        someRandomField: 'value'
      };

      const responsePayload = {
        content: [{ text: JSON.stringify(mockResponse) }]
      };
      
      const body = new TextEncoder().encode(JSON.stringify(responsePayload));
      mockClient.send.mockResolvedValue({ body });

      const result = await service.generateSummary(transcript, 'en-US');

      // Should return safe defaults (empty arrays)
      expect(result.mainTopics).toEqual([]);
      expect(result.confirmedActions).toEqual([]);
    });
  });

  describe('generateEnglishSuggestions', () => {
    it('parses object format with suggestions array', async () => {
        const mockSuggestions = {
            suggestions: [
                { en: 'Hello', ko: '안녕하세요' },
                { en: 'Bye', ko: '안녕히가세요' }
            ]
        };
        const responsePayload = {
            content: [{ text: JSON.stringify(mockSuggestions) }]
        };
        const body = new TextEncoder().encode(JSON.stringify(responsePayload));
        mockClient.send.mockResolvedValue({ body });

        const result = await service.generateEnglishSuggestions([], 5);
        expect(result.suggestions).toHaveLength(2);
        expect(result.suggestions[0].text).toBe('Hello');
        expect(result.suggestions[0].translatedText).toBe('안녕하세요');
    });

    it('parses array format directly', async () => {
        const mockSuggestions = [
            { en: 'Hello', ko: '안녕하세요' },
            { en: 'Bye', ko: '안녕히가세요' }
        ];
        const responsePayload = {
            content: [{ text: JSON.stringify(mockSuggestions) }]
        };
        const body = new TextEncoder().encode(JSON.stringify(responsePayload));
        mockClient.send.mockResolvedValue({ body });

        const result = await service.generateEnglishSuggestions([], 5);
        expect(result.suggestions).toHaveLength(2);
        expect(result.suggestions[0].text).toBe('Hello');
    });

    it('handles old format with text/translatedText keys', async () => {
        const mockSuggestions = {
            suggestions: [
                { text: 'Hello', translatedText: '안녕하세요' }
            ]
        };
        const responsePayload = {
            content: [{ text: JSON.stringify(mockSuggestions) }]
        };
        const body = new TextEncoder().encode(JSON.stringify(responsePayload));
        mockClient.send.mockResolvedValue({ body });

        const result = await service.generateEnglishSuggestions([], 5);
        expect(result.suggestions).toHaveLength(1);
        expect(result.suggestions[0].text).toBe('Hello');
        expect(result.suggestions[0].translatedText).toBe('안녕하세요');
    });
  });

  describe('runAgentTurn', () => {
    const tools = [
      { name: 'update_meeting_summary', description: 'edit summary', inputSchema: { type: 'object' } },
      { name: 'create_tech_activity', description: 'log SFDC', inputSchema: { type: 'object' } },
    ];

    it('returns assistant text only on end_turn (no tool use)', async () => {
      mockClient.send.mockResolvedValueOnce({
        stopReason: 'end_turn',
        output: { message: { role: 'assistant', content: [{ text: '네, 도와드릴게요.' }] } },
      });

      const result = await service.runAgentTurn({
        messages: [{ role: 'user', content: [{ text: '안녕' }] }],
        system: 'sys',
        tools,
      });

      expect(result.assistantText).toBe('네, 도와드릴게요.');
      expect(result.pendingActions).toEqual([]);
      // assistant 메시지가 히스토리에 누적된다.
      expect(result.updatedMessages).toHaveLength(2);
      expect(result.updatedMessages[1].role).toBe('assistant');
    });

    it('on tool_use: emits pendingAction WITHOUT executing, and closes history with a toolResult', async () => {
      mockClient.send.mockResolvedValueOnce({
        stopReason: 'tool_use',
        output: {
          message: {
            role: 'assistant',
            content: [
              { text: '액션 아이템을 추가하겠습니다.' },
              {
                toolUse: {
                  toolUseId: 'tu_1',
                  name: 'update_meeting_summary',
                  input: { field: 'confirmedActions', value: [] },
                },
              },
            ],
          },
        },
      });

      const result = await service.runAgentTurn({
        messages: [{ role: 'user', content: [{ text: '액션 추가해줘' }] }],
        system: 'sys',
        tools,
      });

      // pendingAction 1건, kind는 meeting_edit (로컬 도구).
      expect(result.pendingActions).toHaveLength(1);
      expect(result.pendingActions[0]).toMatchObject({
        toolUseId: 'tu_1',
        name: 'update_meeting_summary',
        kind: 'meeting_edit',
        args: { field: 'confirmedActions', value: [] },
      });
      expect(result.pendingActions[0].id).toBeTruthy();

      // 히스토리: [user, assistant(toolUse), user(toolResult)] — 마지막이 toolResult로 닫힘.
      const last = result.updatedMessages[result.updatedMessages.length - 1];
      expect(last.role).toBe('user');
      expect(last.content?.[0]).toHaveProperty('toolResult');
      expect((last.content?.[0] as any).toolResult.toolUseId).toBe('tu_1');
    });

    it('auto-executes a read-only tool (search_*) via mcpCallTool and continues to a final answer', async () => {
      // 1st round: 모델이 읽기 도구 호출. 2nd round: 결과를 받고 최종 답변.
      mockClient.send
        .mockResolvedValueOnce({
          stopReason: 'tool_use',
          output: {
            message: {
              role: 'assistant',
              content: [{ toolUse: { toolUseId: 'tu_r', name: 'search_opportunities', input: { q: 'ACME' } } }],
            },
          },
        })
        .mockResolvedValueOnce({
          stopReason: 'end_turn',
          output: { message: { role: 'assistant', content: [{ text: 'ACME opp 2건 찾았습니다.' }] } },
        });

      const mcpCallTool = vi.fn(async () => ({ content: [{ name: 'ACME opp' }], isError: false }));

      const result = await service.runAgentTurn({
        messages: [{ role: 'user', content: [{ text: 'ACME opp 찾아줘' }] }],
        system: 'sys',
        tools,
        mcpCallTool,
      });

      // 읽기 도구는 자동 실행되고(컨펌 없음), 최종 텍스트로 이어진다.
      expect(mcpCallTool).toHaveBeenCalledWith('search_opportunities', { q: 'ACME' });
      expect(result.pendingActions).toEqual([]);
      expect(result.assistantText).toContain('ACME opp 2건');
    });

    it('treats a non-read MCP tool (delete_*) as a side-effect pendingAction (sfdc_log)', async () => {
      mockClient.send.mockResolvedValueOnce({
        stopReason: 'tool_use',
        output: {
          message: {
            role: 'assistant',
            content: [{ toolUse: { toolUseId: 'tu_x', name: 'delete_meeting', input: {} } }],
          },
        },
      });

      const result = await service.runAgentTurn({
        messages: [{ role: 'user', content: [{ text: '회의 지워줘' }] }],
        system: 'sys',
        tools,
        mcpCallTool: vi.fn(),
      });

      // 쓰기 계열은 자동 실행하지 않고 컨펌 대기(sfdc_log)로 둔다.
      expect(result.pendingActions).toHaveLength(1);
      expect(result.pendingActions[0].kind).toBe('sfdc_log');
    });

    it('classifies a whitelisted SFDC tool as sfdc_log', async () => {
      mockClient.send.mockResolvedValueOnce({
        stopReason: 'tool_use',
        output: {
          message: {
            role: 'assistant',
            content: [
              { toolUse: { toolUseId: 'tu_2', name: 'create_tech_activity', input: { subject: 'Demo' } } },
            ],
          },
        },
      });

      const result = await service.runAgentTurn({
        messages: [{ role: 'user', content: [{ text: 'SFDC에 기록' }] }],
        system: 'sys',
        tools,
      });

      expect(result.pendingActions).toHaveLength(1);
      expect(result.pendingActions[0].kind).toBe('sfdc_log');
    });
  });
});
