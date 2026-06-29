import {
  BedrockRuntimeClient,
  ConverseCommand,
  InvokeModelCommand,
} from '@aws-sdk/client-bedrock-runtime';
import type { Message, Tool } from '@aws-sdk/client-bedrock-runtime';
import { v4 as uuidv4 } from 'uuid';
import type { MeetingType, ActionItem, TopicDiscussion, ConversationTopic } from '../../shared/types/meeting';
import type { AgentToolSpec, AgentPendingAction } from '../../shared/types/agent';
import { toolPolicy } from '../../shared/constants/agent-tools';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { TranslatedSuggestionItem, TranslatedSuggestionResult } from '../../shared/types/translated';
import type { InterviewSuggestionItem, InterviewSuggestionResult, LeadershipPrinciple } from '../../shared/types/interview';
import { LEADERSHIP_PRINCIPLES } from '../../shared/constants/interview-questions';
import { ANTHROPIC_API_VERSION, SUMMARY_MAX_TOKENS, MAX_TRANSCRIPT_LENGTH, MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY } from '../constants';

export interface BedrockServiceConfig {
  region: string;
  credentials: { accessKeyId: string; secretAccessKey: string };
  modelId: string;
  maxTokens: number;
  temperature: number;
}

interface AnthropicPayload {
  anthropic_version: string;
  max_tokens: number;
  temperature: number;
  messages: Array<{
    role: string;
    content: Array<{ type: string; text: string }>;
  }>;
}

const JSON_SYSTEM_INSTRUCTIONS =
  'You are a strict JSON generator.\n' +
  'Output exactly one JSON object and nothing else.\n' +
  'No markdown, no code fences, no explanations, no extra text.\n' +
  'Use double quotes for all keys/strings.\n' +
  'Do not include trailing commas.\n' +
  'Always include all required keys.\n' +
  'Return JSON on a single line.\n' +
  '\n' +
  'CRITICAL: Before responding, mentally verify:\n' +
  '1. Every opening brace { has a matching closing brace }\n' +
  '2. Every opening bracket [ has a matching closing bracket ]\n' +
  '3. All strings are properly quoted with "\n' +
  '4. Commas are placed correctly between array/object elements\n' +
  '5. No extra braces, brackets, or commas at the end\n' +
  '6. The JSON is valid and can be parsed\n';

export class BedrockService {
  private client: BedrockRuntimeClient;
  private config: BedrockServiceConfig;

  constructor(config: BedrockServiceConfig) {
    this.config = config;
    this.client = new BedrockRuntimeClient({
      region: config.region,
      credentials: config.credentials,
    });
  }

  getModelId(): string {
    return this.config.modelId;
  }

  /**
   * Post-Meeting Agent의 한 턴을 실행한다(tool use 지원).
   *
   * 도구 정책(toolPolicy)에 따라 두 갈래:
   *  - 읽기 도구('auto'): mcpCallTool로 **즉시 실행**하고 결과를 toolResult로 넣어
   *    모델을 재호출한다(멀티스텝 추론). 조회→판단→다음 행동을 한 번의 사용자
   *    메시지 안에서 이어간다. maxToolRounds로 라운드를 제한해 무한 루프를 막는다.
   *  - 쓰기 도구('sfdc_log') / 회의록 수정('meeting_edit'): **실행하지 않고**
   *    AgentPendingAction으로 모은 뒤, "사용자 확인 대기 중" toolResult로 히스토리를
   *    닫고 루프를 종료한다(confirm-gate). 실제 실행은 resolveAction이 한다.
   *
   * Bedrock(Anthropic)은 tool_use 직후 다음 user 메시지에 모든 toolUseId의
   * toolResult를 요구하므로, 어느 경로든 호출된 모든 toolUse에 toolResult를 채운다
   * (안 하면 다음 턴 400).
   */
  async runAgentTurn(params: {
    messages: Message[];
    system: string;
    tools: AgentToolSpec[];
    maxTokens?: number;
    /** 읽기 도구 자동 실행기. 없으면 읽기 도구도 컨펌 대기로 처리한다. */
    mcpCallTool?: (
      name: string,
      args: Record<string, unknown>
    ) => Promise<{ content: unknown; isError: boolean }>;
    /** 읽기 도구 자동 실행 최대 라운드(무한 루프 가드). 기본 5. */
    maxToolRounds?: number;
  }): Promise<{
    assistantText: string;
    pendingActions: AgentPendingAction[];
    updatedMessages: Message[];
  }> {
    const { messages, system, tools, maxTokens, mcpCallTool } = params;
    const maxRounds = params.maxToolRounds ?? 5;

    const toolConfig: { tools: Tool[] } | undefined =
      tools.length > 0
        ? {
            tools: tools.map(
              (t): Tool => ({
                toolSpec: {
                  name: t.name,
                  description: t.description,
                  // JsonSchema → Bedrock ToolInputSchema.json(DocumentType). 구조가
                  // 호환되므로 SDK의 재귀 DocumentType으로 캐스팅한다.
                  inputSchema: { json: t.inputSchema as unknown as Record<string, never> },
                },
              })
            ),
          }
        : undefined;

    let working: Message[] = [...messages];
    const assistantTexts: string[] = [];
    const pendingActions: AgentPendingAction[] = [];

    for (let round = 0; round < maxRounds; round++) {
      const command = new ConverseCommand({
        modelId: this.config.modelId,
        system: [{ text: system }],
        messages: working,
        toolConfig,
        inferenceConfig: {
          maxTokens: maxTokens ?? this.config.maxTokens,
          temperature: this.config.temperature,
        },
      });

      const response = await this.client.send(command);
      const assistantMessage = response.output?.message;
      const content = assistantMessage?.content ?? [];
      if (assistantMessage) working.push(assistantMessage);

      const text = content
        .map((b) => ('text' in b && typeof b.text === 'string' ? b.text : null))
        .filter((v): v is string => Boolean(v))
        .join('')
        .trim();
      if (text) assistantTexts.push(text);

      if (response.stopReason !== 'tool_use') break;

      // 이 응답의 모든 toolUse를 처리: 읽기는 즉시 실행, 쓰기는 pending으로.
      const toolResultBlocks: NonNullable<Message['content']> = [];
      let hasPending = false;
      for (const block of content) {
        if (!('toolUse' in block) || !block.toolUse) continue;
        const { toolUseId, name, input } = block.toolUse;
        if (!toolUseId || !name) continue;
        const args = (input ?? {}) as Record<string, unknown>;
        const policy = toolPolicy(name);

        if (policy === 'auto' && mcpCallTool) {
          // 읽기 도구 — 즉시 실행하고 결과를 모델에 돌려준다.
          try {
            const r = await mcpCallTool(name, args);
            toolResultBlocks.push({
              toolResult: {
                toolUseId,
                content: [{ text: stringifyToolContent(r.content) }],
                ...(r.isError ? { status: 'error' as const } : {}),
              },
            });
          } catch (err) {
            toolResultBlocks.push({
              toolResult: { toolUseId, content: [{ text: `도구 실행 오류: ${String(err)}` }], status: 'error' },
            });
          }
        } else {
          // 쓰기/회의록 수정(또는 읽기인데 실행기 없음) — 컨펌 대기.
          hasPending = true;
          pendingActions.push({
            id: uuidv4(),
            toolUseId,
            name,
            args,
            kind: policy === 'meeting_edit' ? 'meeting_edit' : 'sfdc_log',
          });
          toolResultBlocks.push({
            toolResult: {
              toolUseId,
              content: [{ text: '사용자 확인 대기 중입니다. 승인되면 실행됩니다.' }],
            },
          });
        }
      }
      if (toolResultBlocks.length > 0) {
        working.push({ role: 'user', content: toolResultBlocks });
      }

      // 컨펌 대기 액션이 생기면 턴을 멈춘다(자동으로 더 진행하지 않음).
      if (hasPending) break;
      // 읽기만 실행했으면 결과를 반영해 한 번 더 추론(다음 라운드).
    }

    return {
      assistantText: assistantTexts.join('\n').trim(),
      pendingActions,
      updatedMessages: working,
    };
  }

  private isAnthropicModel(modelId: string): boolean {
    return modelId.includes('anthropic.');
  }

  private buildAnthropicPayload(prompt: string, maxTokens: number): AnthropicPayload {
    return {
      anthropic_version: ANTHROPIC_API_VERSION,
      max_tokens: maxTokens,
      temperature: this.config.temperature,
      messages: [
        {
          role: 'user',
          content: [{ type: 'text', text: prompt }],
        },
      ],
    };
  }

  private async invokeAnthropic(payload: AnthropicPayload): Promise<string> {
    const command = new InvokeModelCommand({
      contentType: 'application/json',
      body: JSON.stringify(payload),
      modelId: this.config.modelId,
    });

    const response = await this.client.send(command);
    const responseBody = JSON.parse(new TextDecoder().decode(response.body));
    const resultText = responseBody.content?.[0]?.text ?? '';
    
    console.log('[BEDROCK-DEBUG] Anthropic Response:', {
      modelId: this.config.modelId,
      responseLength: resultText.length,
      first100: resultText.substring(0, 100),
      last100: resultText.substring(Math.max(0, resultText.length - 100)),
      rawText: resultText,
    });
    
    return resultText;
  }

  private async invokeConverse(prompt: string, maxTokens: number): Promise<string> {
    const command = new ConverseCommand({
      modelId: this.config.modelId,
      messages: [
        {
          role: 'user',
          content: [{ text: prompt }],
        },
      ],
      inferenceConfig: {
        maxTokens,
        temperature: this.config.temperature,
      },
    });

    const response = await this.client.send(command);
    const content = response.output?.message?.content ?? [];
    const textBlocks = content
      .map((block) => ('text' in block && typeof block.text === 'string' ? block.text : null))
      .filter((value): value is string => Boolean(value));
    const resultText = textBlocks.join('').trim();
    
    // 🔍 DEBUG: Bedrock raw response
    console.log('[BEDROCK-DEBUG] Converse Response:', {
      modelId: this.config.modelId,
      responseLength: resultText.length,
      rawText: resultText,
    });
    
    return resultText;
  }

  private async invokePrompt(prompt: string, maxTokens: number): Promise<string> {
    if (this.isAnthropicModel(this.config.modelId)) {
      const payload = this.buildAnthropicPayload(prompt, maxTokens);
      return this.invokeAnthropic(payload);
    }
    return this.invokeConverse(prompt, maxTokens);
  }

  async correctTranscription(
    text: string,
    language: TranscribeLanguage,
    context: string[] = []
  ): Promise<string> {
    const prompt = this.getCorrectionPrompt(text, language, context);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseCorrectionResponse(responseText, text);
  }

  async translateToKorean(
    text: string,
    context: string[] = []
  ): Promise<string> {
    const prompt = this.getTranslationToKoreanPrompt(text, context);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseTranslationResponse(responseText);
  }

  async translateToEnglish(
    text: string,
    context: string[] = []
  ): Promise<string> {
    const prompt = this.getTranslationToEnglishPrompt(text, context);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseTranslationResponse(responseText);
  }

  /**
   * Generic translation method that translates text from source to target language.
   * Returns the original text if source === target.
   */
  async translate(
    text: string,
    sourceLanguage: TranscribeLanguage,
    targetLanguage: TranscribeLanguage,
    context: string[] = []
  ): Promise<string> {
    if (sourceLanguage === targetLanguage) {
      return text;
    }
    const prompt = this.getGenericTranslationPrompt(text, sourceLanguage, targetLanguage, context);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseTranslationResponse(responseText);
  }

  /**
   * Correct and translate in a single API call.
   * If source === target, only correction is performed.
   */
  async correctAndTranslateGeneric(
    text: string,
    sourceLanguage: TranscribeLanguage,
    targetLanguage: TranscribeLanguage,
    context: string[] = []
  ): Promise<{ correctedText: string; translatedText: string }> {
    if (sourceLanguage === targetLanguage) {
      const corrected = await this.correctTranscription(text, sourceLanguage, context);
      return { correctedText: corrected, translatedText: corrected };
    }
    const prompt = this.getGenericCorrectionAndTranslationPrompt(text, sourceLanguage, targetLanguage, context);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseCorrectionTranslationResponse(responseText, text);
  }

  /**
   * Get human-readable language name for prompts.
   */
  private getLanguageName(language: TranscribeLanguage): string {
    const names: Record<TranscribeLanguage, string> = {
      'ko-KR': 'Korean',
      'en-US': 'English',
      'ja-JP': 'Japanese',
      'zh-CN': 'Chinese (Simplified)',
    };
    return names[language];
  }

  async generateEnglishSuggestions(
    context: string[] = [],
    count = 5
  ): Promise<TranslatedSuggestionResult> {
    const prompt = this.getEnglishSuggestionsPrompt(context, count);
    const responseText = await this.invokePrompt(prompt, Math.min(this.config.maxTokens, 600));
    return this.parseEnglishSuggestionsResponse(responseText);
  }

  async generateInterviewSuggestions(
    context: string[] = [],
    lpIds: LeadershipPrinciple[] = [],
    count = 5
  ): Promise<InterviewSuggestionResult> {
    const prompt = this.getInterviewSuggestionsPrompt(context, lpIds, count);
    const responseText = await this.invokePrompt(prompt, Math.min(this.config.maxTokens, 800));
    return this.parseInterviewSuggestionsResponse(responseText, lpIds);
  }

  async correctAndTranslate(
    text: string,
    context: string[] = []
  ): Promise<{ correctedText: string; translatedText: string }> {
    const prompt = this.getCorrectionAndTranslationPrompt(text, context);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseCorrectionTranslationResponse(responseText, text);
  }

  async generateSummary(
    transcript: string,
    _language: TranscribeLanguage
  ): Promise<{
    mainTopics: string[];
    topicDiscussions: TopicDiscussion[];
    keyTakeaways: string[];
    confirmedActions: ActionItem[];
    pendingActions: ActionItem[];
    followUps: string[];
    openIssues: string[];
  }> {
    const trimmedTranscript = transcript.trim();

    // 최소 길이 미만이면 기본 요약 반환 (API 호출 없이)
    if (trimmedTranscript.length < MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY) {
      return {
        mainTopics: [],
        topicDiscussions: [],
        keyTakeaways: [],
        confirmedActions: [],
        pendingActions: [],
        followUps: [],
        openIssues: [],
      };
    }

    // 트랜스크립트가 너무 길면 자르기 (최근 부분 우선)
    const truncatedTranscript = this.truncateTranscript(trimmedTranscript);
    const prompt = this.getSummaryPrompt(truncatedTranscript);
    // Use configured max tokens instead of hardcoded constant
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseSummaryJsonResponse(responseText);
  }

  /**
   * 대화 로그를 생성합니다.
   * 전사 내용을 주제별로 분절해 정리합니다.
   */
  async generateConversationLog(
    transcript: string,
    _language: TranscribeLanguage
  ): Promise<ConversationTopic[]> {
    const trimmedTranscript = transcript.trim();

    // 최소 길이 미만이면 빈 배열 반환 (API 호출 없이)
    if (trimmedTranscript.length < MIN_TRANSCRIPT_LENGTH_FOR_SUMMARY) {
      return [];
    }

    // 트랜스크립트가 너무 길면 자르기
    const truncatedTranscript = this.truncateTranscript(trimmedTranscript);
    const prompt = this.getConversationLogPrompt(truncatedTranscript);
    const responseText = await this.invokePrompt(prompt, this.config.maxTokens);
    return this.parseConversationLogResponse(responseText);
  }

  /**
   * 트랜스크립트를 최대 길이로 자릅니다.
   * 긴 트랜스크립트의 경우 최근 부분을 우선적으로 유지합니다.
   */
  private truncateTranscript(transcript: string): string {
    if (transcript.length <= MAX_TRANSCRIPT_LENGTH) {
      return transcript;
    }

    // 최근 부분을 우선적으로 유지 (끝에서부터 MAX_TRANSCRIPT_LENGTH만큼)
    const truncated = transcript.slice(-MAX_TRANSCRIPT_LENGTH);
    
    // 문장 경계를 찾아서 깔끔하게 자르기
    const firstNewline = truncated.indexOf('\n');
    if (firstNewline > 0 && firstNewline < 100) {
      return truncated.slice(firstNewline + 1);
    }

    return truncated;
  }

  private buildStandardPrompt(params: {
    task: string;
    outputSchema: string;
    context?: string[];
    text: string;
    extraRules?: string[];
  }): string {
    const contextText = params.context && params.context.length > 0
      ? `\n[Context (Previous conversation)]\n${params.context.join('\n')}\n`
      : '';

    const rules = [
      '- Output ONLY the JSON object that matches the schema.',
      '- Do not include any extra keys.',
      '- If a value is unavailable, use "".',
      '- Verify all braces { } and brackets [ ] are balanced.',
      '- Ensure proper comma placement (between elements, not after last).',
      ...(params.extraRules || []),
    ].join('\n');

    const schemaExample = this.generateSchemaExample(params.outputSchema);

    return `${JSON_SYSTEM_INSTRUCTIONS}` +
      `Task:\n${params.task}\n\n` +
      `Output schema (required):\n${params.outputSchema}\n\n` +
      (schemaExample ? `Example output:\n${schemaExample}\n\n` : '') +
      `Rules:\n${rules}\n` +
      `${contextText}\n` +
      `[Input Text]\n${params.text}\n`;
  }

  private generateSchemaExample(schema: string): string {
    if (schema.includes('"correctedText"') && schema.includes('"translatedText"')) {
      return '{"correctedText":"교정된 텍스트","translatedText":"번역된 텍스트"}';
    }
    if (schema.includes('"correctedText"')) {
      return '{"correctedText":"교정된 텍스트"}';
    }
    if (schema.includes('"translatedText"')) {
      return '{"translatedText":"번역된 텍스트"}';
    }
    return '';
  }

  private getCorrectionPrompt(text: string, language: TranscribeLanguage, context: string[] = []): string {
    const langName = this.getLanguageName(language);
    const task = `Correct the ${langName} text (fix typos/grammar, remove stutters/fillers) while preserving meaning.\nDo NOT paraphrase.`;

    return this.buildStandardPrompt({
      task,
      outputSchema: `{"correctedText":"string"}`,
      context,
      text
    });
  }

  private getTranslationToKoreanPrompt(text: string, context: string[] = []): string {
    return this.buildStandardPrompt({
      task: 'Translate the English text into natural Korean.\nWrap important keywords (names, numbers, technical terms) with <strong> tags.\nDo NOT add any other tags.',
      outputSchema: `{"translatedText":"string"}`,
      context,
      text
    });
  }

  private getTranslationToEnglishPrompt(text: string, context: string[] = []): string {
    return this.buildStandardPrompt({
      task: 'Translate the Korean text into clear, natural English.\nUse simple words and short sentences.\nIf the input is a question, keep it as a question.\nDo NOT add extra content.',
      outputSchema: `{"translatedText":"string"}`,
      context,
      text
    });
  }

  private getGenericTranslationPrompt(
    text: string,
    sourceLanguage: TranscribeLanguage,
    targetLanguage: TranscribeLanguage,
    context: string[] = []
  ): string {
    const sourceName = this.getLanguageName(sourceLanguage);
    const targetName = this.getLanguageName(targetLanguage);
    return this.buildStandardPrompt({
      task: `Translate the ${sourceName} text into natural ${targetName}.\nWrap important keywords (names, numbers, technical terms) with <strong> tags.\nDo NOT add any other tags.`,
      outputSchema: `{"translatedText":"string"}`,
      context,
      text
    });
  }

  private getGenericCorrectionAndTranslationPrompt(
    text: string,
    sourceLanguage: TranscribeLanguage,
    targetLanguage: TranscribeLanguage,
    context: string[] = []
  ): string {
    const sourceName = this.getLanguageName(sourceLanguage);
    const targetName = this.getLanguageName(targetLanguage);
    return this.buildStandardPrompt({
      task: `1) Correct the ${sourceName} text (fix typos/grammar, remove stutters/fillers) while preserving meaning.\n` +
            `2) Translate the corrected text into natural ${targetName}.\n` +
            '3) In translatedText ONLY, wrap important keywords (names, numbers, technical terms) with <strong> tags.\n' +
            '4) Do NOT add any other tags.',
      outputSchema: `{"correctedText":"string","translatedText":"string"}`,
      context,
      text
    });
  }

  private convertContextToSpkFormat(context: string[]): string {
    if (context.length === 0) {
      return '';
    }

    // [Speaker] text 형식을 spk_1: text 형식으로 변환
    const speakerMap = new Map<string, number>();
    let nextSpkId = 1;

    return context
      .map((line) => {
        // [Speaker] text 형식 파싱
        const match = line.match(/^\[([^\]]+)\]\s*(.+)$/);
        if (match) {
          const [, speakerLabel, text] = match;
          let spkId: number;

          if (speakerMap.has(speakerLabel)) {
            spkId = speakerMap.get(speakerLabel)!;
          } else {
            spkId = nextSpkId++;
            speakerMap.set(speakerLabel, spkId);
          }

          return `spk_${spkId}: ${text.trim()}`;
        }

        // 이미 spk_ 형식이면 그대로 반환
        if (line.startsWith('spk_')) {
          return line;
        }

        // 형식이 다르면 기본 처리
        const defaultSpkId = nextSpkId++;
        return `spk_${defaultSpkId}: ${line.trim()}`;
      })
      .join('\n');
  }

  private getEnglishSuggestionsPrompt(context: string[] = [], count: number): string {
    // 컨텍스트를 spk_1, spk_2 형식으로 변환
    const convertedContext = this.convertContextToSpkFormat(context);
    const contextText = convertedContext
      ? `Context:\n${convertedContext}\n`
      : 'Context:\nNo prior conversation available.\n';

    return `${JSON_SYSTEM_INSTRUCTIONS}` +
      `You are an assistant helping a non-native English speaker in a meeting. ` +
      `Based on the conversation context, suggest ${count} English sentences the user can say to ask questions, respond, or contribute to the discussion.\n\n` +
      `Rules:\n` +
      `- Focus on sentence structure and composition.\n` +
      `- Keep each sentence under 30 words.\n` +
      `- Technical terms and idioms are acceptable.\n` +
      `- Make them sound polite and natural.\n` +
      `- Each sentence must be on a single line without line breaks.\n\n` +
      `Return ONLY a JSON array of objects with keys "en" and "ko". Use double quotes for keys/values.\n` +
      `Do not include code fences, commentary, or extra text. Do not include any line breaks within the text values.\n\n` +
      `${contextText}`;
  }

  private getInterviewSuggestionsPrompt(context: string[] = [], lpIds: LeadershipPrinciple[], count: number): string {
    const convertedContext = this.convertContextToSpkFormat(context);
    const contextText = convertedContext
      ? `Interview Transcript:\n${convertedContext}\n`
      : 'Interview Transcript:\nNo prior conversation available.\n';

    const lpNames = lpIds.map((id) => {
      const lp = LEADERSHIP_PRINCIPLES.find((p) => p.id === id);
      return lp ? lp.name : id;
    }).join(', ');

    return `${JSON_SYSTEM_INSTRUCTIONS}` +
      `You are an expert Amazon interviewer conducting a behavioral interview. ` +
      `Based on the interview transcript, suggest ${count} follow-up questions to dig deeper into the candidate's responses.\n\n` +
      `Target Leadership Principles: ${lpNames}\n\n` +
      `Rules:\n` +
      `- Questions should probe for specific examples, data, and outcomes (STAR method).\n` +
      `- Focus on getting concrete details: numbers, timelines, specific actions taken.\n` +
      `- Ask about challenges faced, lessons learned, and what they would do differently.\n` +
      `- Questions should be direct and specific to what the candidate just said.\n` +
      `- Each question must be on a single line without line breaks.\n` +
      `- Assign each question to the most relevant LP from the target list.\n\n` +
      `Return ONLY a JSON array of objects with keys "text" (the question in English) and "lpId" (one of: ${lpIds.join(', ')}).\n` +
      `Do not include code fences, commentary, or extra text.\n\n` +
      `${contextText}`;
  }

  private getCorrectionAndTranslationPrompt(text: string, context: string[] = []): string {
    return this.buildStandardPrompt({
      task: '1) Correct the English text (fix typos/grammar, remove stutters/fillers) while preserving meaning.\n' +
            '2) Translate the corrected text into natural Korean.\n' +
            '3) In translatedText ONLY, wrap important keywords (names, numbers, technical terms) with <strong> tags.\n' +
            '4) Do NOT add any other tags.',
      outputSchema: `{"correctedText":"string","translatedText":"string"}`,
      context,
      text
    });
  }

  private getSummaryPrompt(transcript: string): string {
    return `${JSON_SYSTEM_INSTRUCTIONS}

## Your Role
You are an AWS Solutions Architect who takes comprehensive meeting notes. Your responsibility is to document meetings with the clarity and precision required for technical and business stakeholders. You focus on:
- Technical feasibility and architectural implications
- Business impact and customer value
- Implementation risks and mitigation strategies
- Clear ownership and accountability for all decisions
- Actionable guidance for follow-up work

## Your Task
Analyze this meeting transcript and produce a professional summary in Amazon narrative style. Follow a structured thinking process to ensure accuracy and completeness.

## Step 1: Initial Analysis (Think Through This)
Before extracting information, reason through:
1. What is the primary purpose/objective of this meeting?
2. What are the 2-3 main topics or themes discussed?
3. Who are the key participants and their technical/business roles?
4. What problems or challenges were identified? (technical, operational, business)
5. What solutions or decisions were made? (consider technical architecture, scalability, risk)
6. What are the architectural implications or dependencies?

## Step 2: Generate Narrative-Style Sections

### Meeting Summary (회의 개요)
Write a concise narrative (2-3 sentences) that:
- Opens with the meeting purpose, date, participants, and team
- Describes what was accomplished, discussed, or decided
- Highlights the most significant technical or business outcomes
- Note any critical blockers or risks that emerged
Example: "The APJ SA team conducted their monthly Panorama review on January 21st to assess customer insights and tool performance. The session reviewed 10 entries and identified critical issues with the Sift-based narrative tool including permission access problems and over-summarization reducing insight quality. The team prioritized voice agents technical gaps as a focus area for upcoming COP work."

### Key Discussion Topics (주요 논의 내용)
For each major topic, write narrative paragraphs that:
- Start with the topic title and technical/business context
- Describe the discussion flow, challenges, and key insights
- Include specific details: numbers, names, customer impact, technical constraints
- Address technical feasibility or architectural considerations if relevant
- End with any identified gaps, risks, or dependencies
Format: Full sentences that flow naturally, not bullet points
Example: "Tool Implementation Feedback revealed critical technical issues with permission settings on templates that must allow all AWS employees to enable proper insight rollup in narratives. The AI summarization feature was reducing insight quality by removing important context, next steps, and technical details from original entries, causing team members to bypass the narrative tool and read full insights instead. This over-summarization trade-off requires either adjusting the summarization parameters or implementing a dual-view approach for power users."

### Decisions Made (의사결정 사항)
For each decision, write a narrative statement that:
- Clearly states what was decided (technical choice, process change, priority shift)
- Explains the reasoning or business/technical context
- Names who will implement it
- Notes dependencies, risks, or implications
Format: "Decision: [What] because [Why]. [Owner] will [Implementation] by [Deadline]."
Example: "Decision: Templates must allow permissions to all AWS employees to enable proper insight rollup in narratives because current permission model blocks cross-team visibility and automation. Marc will update template permissions by end of week."

### Next Steps (다음 조치사항)
For each action item, write a narrative entry that:
- Describes the specific task with full technical/business context
- Names the owner and deadline
- Explains why this action is necessary (connects to decisions or blockers)
- Notes any dependencies or prerequisites
Format: Write as flowing sentences that explain the "what", "who", and "why" behind each action
Example: "Marc to update template permissions for all AWS employees access by January 24. This unblocks the narrative generation pipeline and enables the team to properly surface customer insights across regions. Glendon and James to create 2-3 sample narratives with different summarization levels for feedback by January 29 to inform Phase 2 model tuning."

## Output Schema (required)
{
  "summary": "string (2-3 narrative sentences describing meeting overview, participants, and outcomes)",
  "discussionTopics": [
    {
      "title": "string (topic name)",
      "context": ["string (narrative paragraph with technical/business context, details, examples, numbers, and insights - multiple sentences)"],
      "challenges": ["string (identified problems, gaps, risks, or dependencies in narrative form)"]
    }
  ],
  "decisions": [
    {
      "decision": "string (narrative statement: what was decided and why, with technical/business rationale)",
      "rationale": "string (detailed explanation of reasoning, business context, and implications)",
      "owner": "string (person responsible for implementation)"
    }
  ],
  "nextSteps": [
    {
      "task": "string (narrative description with full technical/business context, why it matters, and dependencies)",
      "owner": "string (person or team responsible)",
      "deadline": "string (specific date or '미정' if not specified)"
    }
  ],
  "mainTopics": ["string"],
  "keyTakeaways": ["string"],
  "openIssues": ["string"]
}

## AWS Solutions Architect Narrative Style Guidelines
- Write in flowing, professional business prose suitable for both technical and executive audiences
- Assume readers understand AWS architecture but may not have attended the meeting
- Balance technical depth with business clarity
- Include specific metrics, customer impact, and scalability considerations
- Highlight risks, dependencies, and mitigation strategies
- Use connecting phrases like "because", "as a result", "this requires", "this mitigates" to show relationships
- Name specific people to establish clear ownership and accountability
- Preserve exact numbers, dates, and technical specifications
- Consider long-term implications and architectural dependencies

## Rules
- Output ONLY the JSON object (no markdown, no explanation)
- All text must be in Korean
- Extract specific details: names, dates, metrics, customer impact, technical constraints
- Preserve exact numbers, timelines, and technical specifications mentioned
- If information is missing, use "미정" (not available / TBD)
- Do NOT summarize or paraphrase—capture what was actually said
- For decisions, always include the reasoning (rationale) if mentioned
- For next steps, extract the exact person name if mentioned
- Consider technical dependencies and architectural implications

## Quality Checklist
✓ Does narrative flow naturally and connect ideas with business/technical reasoning?
✓ Are all names, dates, and metrics accurate and specific?
✓ Are decisions justified with clear technical and business context?
✓ Are next steps specific, actionable, owned, and connected to decisions?
✓ Does each section include relevant details (numbers, names, timeframes, impact)?
✓ Are risks, dependencies, and implications clearly identified?
✓ Would an AWS SA unfamiliar with the meeting understand all sections?

[Transcript]
${transcript}
`;
  }

  private getConversationLogPrompt(transcript: string): string {
    return `${JSON_SYSTEM_INSTRUCTIONS}

You are an expert at converting meeting transcripts into structured conversation logs.

## Task
Transform the speech-recognized meeting transcript into a readable "conversation log" format, organized by topic with key points.

## EXACT Output Format (CRITICAL)
You MUST return EXACTLY this structure with NO deviations:

{
  "topics": [
    {
      "title": "string (max 15 chars)",
      "points": [
        "string (full sentence)",
        "string (full sentence)",
        ...
      ]
    }
  ]
}

## Structure Validation Checklist
Before submitting your response, verify:
✓ Root object has exactly ONE key: "topics"
✓ "topics" is an array [ ]
✓ Each topic object has EXACTLY TWO keys: "title" and "points"
✓ "points" is always an array [ ] containing 3-6 strings
✓ NO extra brackets or braces at the end
✓ Proper comma placement (comma BETWEEN elements, NOT after last element)

## Example of CORRECT structure:
{"topics":[{"title":"주제1","points":["내용1","내용2","내용3"]},{"title":"주제2","points":["내용A","내용B"]}]}

## Example of WRONG structures (DO NOT DO THIS):
❌ {"topics":[{"title":"주제","points":["내용"]}]} ]  ← Extra ] at end
❌ {"topics":[{"title":"주제","points":["내용",]}]}   ← Trailing comma
❌ {"topics":[{"title":"주제","points":["내용"]}}]}   ← Extra } before ]

## Transformation Rules

### 1. Topic Classification
- Divide topics naturally based on the flow of discussion
- Summarize the topic title within 15 characters
- Arrange topics in sequential order if they are connected

### 2. Content Organization
- Organize each topic into 3-6 key points
- Each point should be a full sentence that reads naturally on its own
- Aim for 40-120 characters per point to preserve context
- Remove filler words, repetitions, and colloquialisms
- Convert conversational endings like "~것 같아요", "~거든요" to formal "~다/한다" style

### 3. Information Preservation
- Always keep specific information: client names, product names, dates, person in charge
- Clearly mark decisions, action items, and deadlines
- Record conflicting opinions or unresolved matters

### 4. Exclusions
- Greetings, small talk, technical issues (mute, disconnection, etc.)
- Meaningless interjections or repetitions
- Unnecessary elaborations in context

## Final Validation
After generating JSON, verify the structure matches the EXACT format above.
Count your braces and brackets: { must equal }, [ must equal ]

[Transcript]
${transcript}
`;
  }

  private parseConversationLogResponse(text: string): ConversationTopic[] {
    const raw = text.trim();
    const parsed = this.extractJson(raw);

    if (!parsed) {
      return [];
    }

    const normalizeSentence = (value: string): string => {
      const trimmed = value.trim();
      if (!trimmed) return '';
      if (/[.!?]$/.test(trimmed)) return trimmed;
      return `${trimmed}.`;
    };

    const normalizeTopics = (value: unknown): ConversationTopic[] => {
      if (!Array.isArray(value)) return [];
      return value
        .filter((item): item is Record<string, unknown> =>
          typeof item === 'object' && item !== null)
        .map((item) => ({
          title: typeof item.title === 'string' ? item.title.trim() : '',
          points: Array.isArray(item.points)
            ? item.points
                .filter((p): p is string => typeof p === 'string')
                .map((p) => normalizeSentence(p))
                .filter(Boolean)
            : [],
        }))
        .filter((item) => item.title && item.points.length > 0);
    };

    return normalizeTopics(parsed.topics);
  }

  private parseSummaryJsonResponse(text: string): {
    mainTopics: string[];
    topicDiscussions: TopicDiscussion[];
    keyTakeaways: string[];
    confirmedActions: ActionItem[];
    pendingActions: ActionItem[];
    followUps: string[];
    openIssues: string[];
  } {
    const raw = text.trim();
    const parsed = this.extractJson(raw);

    const normalizeList = (value: unknown): string[] => {
      if (!Array.isArray(value)) return [];
      return value
        .filter((item) => typeof item === 'string')
        .map((item) => item.trim())
        .filter(Boolean);
    };

    const normalizeActions = (value: unknown): ActionItem[] => {
      if (!Array.isArray(value)) return [];
      return value
        .filter((item): item is Record<string, unknown> =>
          typeof item === 'object' && item !== null)
        .map((item) => ({
          task: typeof item.task === 'string' ? item.task : '',
          owner: typeof item.owner === 'string' ? item.owner : 'TBD',
          deadline: typeof item.deadline === 'string' ? item.deadline : 'TBD',
        }))
        .filter((item) => item.task);
    };

    const normalizeDiscussions = (value: unknown): TopicDiscussion[] => {
      if (!Array.isArray(value)) return [];
      return value
        .filter((item): item is Record<string, unknown> =>
          typeof item === 'object' && item !== null)
        .map((item) => {
          // 새로운 스키마: discussionTopics 배열의 형식
          let discussions: string[] = [];
          let decisions: string[] = [];

          // context 필드로부터 discussions 추출
          if (Array.isArray(item.context)) {
            discussions = item.context
              .filter((c): c is string => typeof c === 'string')
              .map((c) => c.trim())
              .filter(Boolean);
          }

          // challenges 필드는 무시하고 기존 decisions는 빈 배열로
          // (새 스키마에서는 따로 decisions 섹션이 있음)

          return {
            topic: typeof item.title === 'string' ? item.title.trim() : '',
            discussions,
            decisions,
          };
        })
        .filter((item) => item.topic);
    };

    // 새로운 스키마 또는 기존 스키마 모두 지원
    let result = {
      mainTopics: normalizeList(parsed?.mainTopics),
      topicDiscussions: normalizeDiscussions(parsed?.discussionTopics),
      keyTakeaways: normalizeList(parsed?.keyTakeaways),
      confirmedActions: normalizeActions(parsed?.nextSteps),
      pendingActions: [],
      followUps: [],
      openIssues: normalizeList(parsed?.openIssues),
    };

    // summary 필드가 있으면 mainTopics에 추가
    if (parsed?.summary && typeof parsed.summary === 'string') {
      const summaryText = parsed.summary.trim();
      if (summaryText && result.mainTopics.length === 0) {
        result.mainTopics = [summaryText];
      }
    }

    if (!parsed) {
      return {
        mainTopics: [],
        topicDiscussions: [],
        keyTakeaways: [],
        confirmedActions: [],
        pendingActions: [],
        followUps: [],
        openIssues: [],
      };
    }

    return result;
  }

  private parseCorrectionTranslationResponse(
    text: string,
    fallback: string
  ): { correctedText: string; translatedText: string } {
    const raw = text.trim();
    const parsed = this.extractJson(raw);
    if (parsed && typeof parsed.correctedText === 'string' && typeof parsed.translatedText === 'string') {
      return {
        correctedText: parsed.correctedText.trim(),
        translatedText: parsed.translatedText.trim(),
      };
    }

    return {
      correctedText: fallback,
      translatedText: '',
    };
  }

  private parseCorrectionResponse(text: string, fallback: string): string {
    const raw = text.trim();
    
    // 🔍 DEBUG: Parse attempt
    console.log('[BEDROCK-DEBUG] parseCorrectionResponse:', {
      inputLength: raw.length,
      startsWithBrace: raw.startsWith('{'),
      endsWithBrace: raw.endsWith('}'),
      preview: raw.substring(0, 200),
    });
    
    const parsed = this.extractJson(raw);
    
    console.log('[BEDROCK-DEBUG] extractJson result:', {
      success: !!parsed,
      hasCorrectedText: parsed && 'correctedText' in parsed,
      correctedTextType: parsed && typeof parsed.correctedText,
      keys: parsed ? Object.keys(parsed) : [],
    });
    
    if (parsed && typeof parsed.correctedText === 'string') {
      const value = parsed.correctedText.trim();
      console.log('[BEDROCK-DEBUG] Returning correctedText:', value.substring(0, 100));
      return value || '';
    }
    if (!raw) {
      console.log('[BEDROCK-DEBUG] Empty raw, returning fallback');
      return fallback;
    }
    if (raw.startsWith('{') && raw.endsWith('}')) {
      console.log('[BEDROCK-DEBUG] JSON-like but parse failed, returning fallback');
      return fallback;
    }
    console.log('[BEDROCK-DEBUG] Returning raw text as-is');
    return raw;
  }

  private parseTranslationResponse(text: string): string {
    const raw = text.trim();
    const parsed = this.extractJson(raw);
    if (parsed && typeof parsed.translatedText === 'string') {
      return parsed.translatedText.trim();
    }
    if (!raw) {
      return '';
    }
    if (raw.startsWith('{') && raw.endsWith('}')) {
      return '';
    }
    return raw;
  }

  private parseEnglishSuggestionsResponse(text: string): TranslatedSuggestionResult {
    const raw = text.trim();
    const parsed = this.extractJson(raw);
    if (!parsed) {
      return { suggestions: [] };
    }

    // 두 가지 형식 지원: {"en": "...", "ko": "..."} 또는 {"text": "...", "translatedText": "..."}
    let suggestionsArray: unknown[];

    if (Array.isArray(parsed)) {
      // 배열이 직접 반환된 경우
      suggestionsArray = parsed;
    } else if (Array.isArray(parsed.suggestions)) {
      suggestionsArray = parsed.suggestions;
    } else {
      return { suggestions: [] };
    }

    const normalizeItem = (value: unknown): TranslatedSuggestionItem | null => {
      if (!value || typeof value !== 'object') {
        return null;
      }

      const obj = value as Record<string, unknown>;

      // 새로운 형식: {"en": "...", "ko": "..."}
      let text = '';
      let translatedText = '';

      if (typeof obj.en === 'string' && typeof obj.ko === 'string') {
        text = obj.en.trim();
        translatedText = obj.ko.trim();
      } else if (typeof obj.text === 'string' && typeof obj.translatedText === 'string') {
        // 기존 형식: {"text": "...", "translatedText": "..."}
        text = obj.text.trim();
        translatedText = obj.translatedText.trim();
      } else {
        return null;
      }

      if (!text) {
        return null;
      }

      return { text, translatedText };
    };

    return {
      suggestions: suggestionsArray
        .map(normalizeItem)
        .filter((item): item is TranslatedSuggestionItem => Boolean(item)),
    };
  }

  private parseInterviewSuggestionsResponse(text: string, lpIds: LeadershipPrinciple[]): InterviewSuggestionResult {
    const raw = text.trim();
    const parsed = this.extractJson(raw);
    if (!parsed) {
      return { suggestions: [] };
    }

    let suggestionsArray: unknown[];
    if (Array.isArray(parsed)) {
      suggestionsArray = parsed;
    } else if (Array.isArray(parsed.suggestions)) {
      suggestionsArray = parsed.suggestions;
    } else {
      return { suggestions: [] };
    }

    const normalizeItem = (value: unknown): InterviewSuggestionItem | null => {
      if (!value || typeof value !== 'object') return null;
      const obj = value as Record<string, unknown>;
      const questionText = typeof obj.text === 'string' ? obj.text.trim() : '';
      if (!questionText) return null;

      let lpId = typeof obj.lpId === 'string' ? obj.lpId as LeadershipPrinciple : lpIds[0];
      if (!lpIds.includes(lpId)) lpId = lpIds[0];

      const lp = LEADERSHIP_PRINCIPLES.find((p) => p.id === lpId);
      return { text: questionText, lpId, lpName: lp?.shortName || lpId };
    };

    return {
      suggestions: suggestionsArray
        .map(normalizeItem)
        .filter((item): item is InterviewSuggestionItem => Boolean(item)),
    };
  }

  private extractJson(text: string): { [key: string]: unknown } | null {
    try {
      const result = JSON.parse(text);
      console.log('[BEDROCK-DEBUG] extractJson: Direct parse succeeded');
      return result;
    } catch (directError) {
      console.log('[BEDROCK-DEBUG] extractJson: Direct parse failed, trying balanced brace matching', {
        error: directError instanceof Error ? directError.message : String(directError),
      });
      
      return this.extractJsonWithBalancedBraces(text);
    }
  }

  private extractJsonWithBalancedBraces(text: string): { [key: string]: unknown } | null {
    let braceCount = 0;
    let startIdx = -1;
    let inString = false;
    let escapeNext = false;

    for (let i = 0; i < text.length; i++) {
      const char = text[i];

      if (escapeNext) {
        escapeNext = false;
        continue;
      }

      if (char === '\\') {
        escapeNext = true;
        continue;
      }

      if (char === '"') {
        inString = !inString;
        continue;
      }

      if (inString) {
        continue;
      }

      if (char === '{') {
        if (braceCount === 0) startIdx = i;
        braceCount++;
      } else if (char === '}') {
        braceCount--;
        if (braceCount === 0 && startIdx >= 0) {
          const candidate = text.slice(startIdx, i + 1);
          console.log('[BEDROCK-DEBUG] Balanced brace match found:', {
            startIdx,
            endIdx: i,
            length: candidate.length,
            preview: candidate.substring(0, 200),
          });
          
          try {
            const result = JSON.parse(candidate);
            console.log('[BEDROCK-DEBUG] Balanced brace parse succeeded');
            return result;
          } catch (parseError) {
            console.log('[BEDROCK-DEBUG] Balanced brace parse failed, trying auto-fix', {
              error: parseError instanceof Error ? parseError.message : String(parseError),
            });
            
            const fixed = this.tryFixCommonJsonErrors(candidate);
            if (fixed) {
              try {
                const result = JSON.parse(fixed);
                console.log('[BEDROCK-DEBUG] Auto-fix succeeded');
                return result;
              } catch {
                console.log('[BEDROCK-DEBUG] Auto-fix failed, continuing search');
              }
            }
            
            startIdx = -1;
          }
        }
      }
    }

    console.log('[BEDROCK-DEBUG] No valid JSON found with balanced brace matching');
    return null;
  }

  private tryFixCommonJsonErrors(json: string): string | null {
    console.log('[BEDROCK-DEBUG] tryFixCommonJsonErrors input length:', json.length);
    
    let fixed = json;
    let changed = false;

    const original = fixed;
    fixed = fixed.replace(/"\s*\]\s*\}\s*\}/g, '"]}}');
    if (fixed !== original) {
      changed = true;
      console.log('[BEDROCK-DEBUG] Fixed: Removed whitespace before ]}}');
    }

    const temp2 = fixed;
    fixed = fixed.replace(/,(\s*[}\]])/g, '$1');
    if (fixed !== temp2) {
      changed = true;
      console.log('[BEDROCK-DEBUG] Fixed: Removed trailing commas');
    }

    const temp3 = fixed;
    fixed = fixed.replace(/([}\]])(\s*)([{\[])/g, '$1,$2$3');
    if (fixed !== temp3) {
      changed = true;
      console.log('[BEDROCK-DEBUG] Fixed: Added missing commas between elements');
    }

    if (changed) {
      console.log('[BEDROCK-DEBUG] tryFixCommonJsonErrors result:', {
        originalLength: json.length,
        fixedLength: fixed.length,
        last100Original: json.substring(Math.max(0, json.length - 100)),
        last100Fixed: fixed.substring(Math.max(0, fixed.length - 100)),
      });
      return fixed;
    }

    return null;
  }

  async generateMeetingTitle(
    meetingType: MeetingType,
    correctedSentences: string[]
  ): Promise<string> {
    const prompt = this.getTitleGenerationPrompt(meetingType, correctedSentences);
    const responseText = await this.invokePrompt(prompt, 100);
    return this.parseTitleResponse(responseText, meetingType);
  }

  private getTitleGenerationPrompt(meetingType: MeetingType, sentences: string[]): string {
    const context = sentences.slice(0, 10).join('\n');
    const templateGuide = this.getTitleTemplateGuide(meetingType);

    return `${JSON_SYSTEM_INSTRUCTIONS}` +
      `Task:\n` +
      `Generate a concise meeting title based on the conversation context.\n\n` +
      `Meeting Type: ${meetingType}\n` +
      `Title Format: ${templateGuide}\n\n` +
      `Output schema (required):\n` +
      `{"title":"string"}\n\n` +
      `Rules:\n` +
      `- Output ONLY the JSON object that matches the schema.\n` +
      `- Title should be 30 characters or less.\n` +
      `- Use Korean for Korean meetings, English for English meetings.\n` +
      `- Extract key topic/subject from the conversation.\n\n` +
      `[Conversation Context]\n${context}\n`;
  }

  private getTitleTemplateGuide(meetingType: MeetingType): string {
    switch (meetingType) {
      case 'client':
        return '[고객사명] - 주제 (예: AWS Korea - 클라우드 마이그레이션)';
      case 'weekly':
        return '주제 싱크 (예: Q1 마케팅 캠페인 싱크)';
      case 'english':
        return 'Topic Discussion (예: Product Roadmap Discussion)';
      case 'interview':
        return '[후보자명] 인터뷰 (예: 홍길동 Backend Engineer 인터뷰)';
      default:
        return '주제 (예: 신규 서비스 브레인스토밍)';
    }
  }

  private parseTitleResponse(text: string, meetingType: MeetingType): string {
    const parsed = this.extractJson(text.trim());
    if (parsed && typeof parsed.title === 'string') {
      const title = parsed.title.trim();
      if (title) return title;
    }
    return this.getDefaultTitle(meetingType);
  }

  private getDefaultTitle(meetingType: MeetingType): string {
    const now = new Date();
    const dateStr = now.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' });
    switch (meetingType) {
      case 'client':
        return `고객 미팅 ${dateStr}`;
      case 'weekly':
        return `퀵 미팅 ${dateStr}`;
      case 'english':
        return `English Meeting ${dateStr}`;
      case 'interview':
        return `인터뷰 ${dateStr}`;
      default:
        return `미팅 ${dateStr}`;
    }
  }

}

/** 읽기 도구 결과(MCP content)를 모델에 돌려줄 텍스트로 직렬화(과도하게 길면 절단). */
function stringifyToolContent(content: unknown): string {
  let text: string;
  if (typeof content === 'string') {
    text = content;
  } else {
    try {
      text = JSON.stringify(content);
    } catch {
      text = String(content);
    }
  }
  const LIMIT = 8000;
  return text.length > LIMIT ? text.slice(0, LIMIT) + '…(생략)' : text;
}
