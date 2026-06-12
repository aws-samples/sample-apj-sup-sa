export const IPC_CHANNELS = {
  SETTINGS_SAVE: 'settings:save',
  SETTINGS_LOAD: 'settings:load',
  SETTINGS_CLEAR: 'settings:clear',
  SETTINGS_GET_AWS_CREDENTIALS: 'settings:getAWSCredentials',

  MEETING_CREATE: 'meeting:create',
  MEETING_START: 'meeting:start',
  MEETING_STOP: 'meeting:stop',
  MEETING_PAUSE: 'meeting:pause',
  MEETING_RESUME: 'meeting:resume',
  MEETING_GET_STATUS: 'meeting:getStatus',
  MEETING_GET: 'meeting:get',
  MEETING_LIST: 'meeting:list',
  MEETING_DELETE: 'meeting:delete',
  MEETING_DELETE_ALL: 'meeting:deleteAll',
  MEETING_EXPORT: 'meeting:export',
  MEETING_TITLE_UPDATED: 'meeting:titleUpdated',
  MEETING_UPDATE_PREP_DATA: 'meeting:updatePrepData',
  MEETING_UPDATE_METADATA: 'meeting:updateMetadata',
  MEETING_GET_METADATA: 'meeting:getMetadata',

  AUDIO_CHUNK: 'audio:chunk',
  AUDIO_STREAM_START: 'audio:streamStart',
  AUDIO_STREAM_END: 'audio:streamEnd',

  TRANSCRIPTION_PARTIAL: 'transcription:partial',
  TRANSCRIPTION_FINAL: 'transcription:final',
  TRANSCRIPTION_CORRECTED: 'transcription:corrected',
  TRANSCRIPTION_ERROR: 'transcription:error',

  // 음성 어시스턴트(wake word → LLM → TTS) 이벤트 (main → renderer)
  ASSISTANT_EVENT: 'assistant:event',

  SUMMARY_GENERATE: 'summary:generate',
  SUMMARY_COMPLETE: 'summary:complete',

  CONVERSATION_LOG_GENERATE: 'conversation-log:generate',
  CONVERSATION_LOG_COMPLETE: 'conversation-log:complete',

  // Post-Meeting Agent (회의록 대화 수정 + SFDC 로깅)
  AGENT_CHAT_SEND: 'agent:chat-send',
  AGENT_RESOLVE_ACTION: 'agent:resolve-action',
  AGENT_RESET: 'agent:reset',

  ENGLISH_SUGGESTIONS: 'english:suggestions',
  ENGLISH_TRANSLATE: 'english:translate',

  INTERVIEW_SUGGESTIONS: 'interview:suggestions',
  
  MCP_CONNECT: 'mcp:connect',
  MCP_DISCONNECT: 'mcp:disconnect',
  MCP_GET_STATUS: 'mcp:getStatus',
  MCP_LIST_TOOLS: 'mcp:listTools',
  MCP_CALL_TOOL: 'mcp:callTool',

  // Vocabulary (용어집)
  VOCABULARY_LIST: 'vocabulary:list',
  VOCABULARY_GET: 'vocabulary:get',
  VOCABULARY_CREATE: 'vocabulary:create',
  VOCABULARY_UPDATE: 'vocabulary:update',
  VOCABULARY_DELETE: 'vocabulary:delete',
  VOCABULARY_ENTRY_LIST: 'vocabulary:entryList',
  VOCABULARY_ENTRY_ADD: 'vocabulary:entryAdd',
  VOCABULARY_ENTRY_UPDATE: 'vocabulary:entryUpdate',
  VOCABULARY_ENTRY_REMOVE: 'vocabulary:entryRemove',
  VOCABULARY_SET_DEFAULT: 'vocabulary:setDefault',
  VOCABULARY_GET_DEFAULT: 'vocabulary:getDefault',
  VOCABULARY_CLEAR_DEFAULT: 'vocabulary:clearDefault',
  VOCABULARY_SYNC_TO_AWS: 'vocabulary:syncToAws',
  VOCABULARY_CHECK_STATUS: 'vocabulary:checkStatus',
  VOCABULARY_GENERATE_FILE: 'vocabulary:generateFile',

  /** @deprecated Use MEETING_START instead */
  MEETING_START_RECORDING: 'meeting:start-recording',
  /** @deprecated Use MEETING_STOP instead */
  MEETING_STOP_RECORDING: 'meeting:stop-recording',
  /** @deprecated */
  MEETING_GET_TRANSCRIPTION: 'meeting:get-transcription',
} as const;

export type IPCChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
