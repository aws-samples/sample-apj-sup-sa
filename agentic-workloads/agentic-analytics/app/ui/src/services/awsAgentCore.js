// JWT-native invoke: the AgentCore Runtime now uses a CustomJWTAuthorizer, so the
// browser calls InvokeAgentRuntime over plain HTTPS with the user's Cognito access
// token as `Authorization: Bearer` — NOT the AWS SDK (which signs SigV4) and NOT a
// Cognito Identity Pool. AWS docs: "If you plan on integrating your agent with OAuth,
// you can't use the AWS SDK to call InvokeAgentRuntime. Instead, make an HTTPS request."
import { fetchAccessToken } from './authService';

// AWS Configuration
// Runtime config (window.__APP_CONFIG__) overrides build-time env vars in demo mode.
const RC = (typeof window !== 'undefined' && window.__APP_CONFIG__) || {};

// InvokeAgentRuntime expects the runtime ARN + a qualifier (the endpoint name).
// Demo mode hands us the full endpoint ARN, which embeds both:
//   arn:...:runtime/<runtime-id>/runtime-endpoint/<qualifier>
// Split it so the SDK gets the right shape. Local dev keeps the legacy
// REACT_APP_AGENT_RUNTIME_ARN + REACT_APP_AGENT_QUALIFIER pair.
const splitEndpointArn = (arn) => {
  const match = /^(arn:[^:]+:[^:]+:[^:]+:[^:]+:runtime\/[^/]+)\/runtime-endpoint\/(.+)$/.exec(arn || '');
  return match ? { runtimeArn: match[1], qualifier: match[2] } : null;
};
const endpointSplit = splitEndpointArn(RC.AGENTCORE_RUNTIME_ENDPOINT);

const AWS_REGION = RC.AWS_REGION || process.env.REACT_APP_AWS_REGION || 'us-east-1';
const AGENT_RUNTIME_ARN = (endpointSplit && endpointSplit.runtimeArn) || RC.AGENT_RUNTIME_ARN || process.env.REACT_APP_AGENT_RUNTIME_ARN || '';
const AGENT_QUALIFIER = (endpointSplit && endpointSplit.qualifier) || RC.AGENT_QUALIFIER || process.env.REACT_APP_AGENT_QUALIFIER || 'DEFAULT';

// Build the InvokeAgentRuntime HTTPS endpoint for the OAuth/JWT path:
//   POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-ARN>/invocations?qualifier=<q>
// The runtime's CustomJWTAuthorizer validates the Bearer token; no SigV4, no Identity Pool.
const buildInvokeUrl = () => {
  const enc = encodeURIComponent(AGENT_RUNTIME_ARN);
  const q = encodeURIComponent(AGENT_QUALIFIER || 'DEFAULT');
  return `https://bedrock-agentcore.${AWS_REGION}.amazonaws.com/runtimes/${enc}/invocations?qualifier=${q}`;
};

// Store runtime sessions to reuse them
const runtimeSessions = new Map();

// Generate or get a runtime session ID that's at least 33 characters
const getOrCreateRuntimeSessionId = (sessionId) => {
  // Check if we already have a runtime session for this sessionId
  if (runtimeSessions.has(sessionId)) {
    return runtimeSessions.get(sessionId);
  }
  
  // Create a new runtime session ID (must be 33+ characters)
  const timestamp = Date.now().toString();
  const random = Math.random().toString(36).substring(2, 15);
  const runtimeSessionId = `${sessionId}_${timestamp}_${random}`.padEnd(33, '0').substring(0, 100);
  
  // Store it for reuse
  runtimeSessions.set(sessionId, runtimeSessionId);
  console.log('Created new runtime session:', runtimeSessionId, 'for session:', sessionId);

  return runtimeSessionId;
};

// Exported so the voice path can use the SAME runtime session id as text — this
// is what makes voice + text turns share one AgentCore Memory thread (context
// carries across both within a single app session).
export const getRuntimeSessionId = (sessionId) => getOrCreateRuntimeSessionId(sessionId);

const TOOL_NAME_PATTERNS = [
  /['"]?tool_name['"]?\s*:\s*['"]([^'"\s]+)['"]/,
  /['"]?function_name['"]?\s*:\s*['"]([^'"\s]+)['"]/,
  /calling\s+(\w+)/,
  /function\s+(\w+)/,
  /(get_\w+|search_\w+|create_\w+|update_\w+|delete_\w+)/
];

const createSSEState = ({ onChunk, onToolUse }) => ({
  fullText: '',
  finalText: '',
  hasStreamed: false,
  detectedTools: new Set(),
  onChunk,
  onToolUse
});

const recordToolUse = (toolName, state, debugSource) => {
  if (!toolName || !state.onToolUse || state.detectedTools.has(toolName)) {
    return;
  }
  console.log('AgentCore: Detected tool', toolName, 'via', debugSource);
  state.detectedTools.add(toolName);
  state.onToolUse(toolName);
};

const detectToolUsageFromText = (text, state) => {
  if (!state.onToolUse) {
    return;
  }

  if (!(text.includes('tool') || text.includes('function') || text.includes('get_') || text.includes('search_'))) {
    return;
  }

  console.log('AgentCore: Checking text for tools:', text.substring(0, 150));
  for (const pattern of TOOL_NAME_PATTERNS) {
    const match = text.match(pattern);
    if (match && match[1]) {
      recordToolUse(match[1], state, 'text pattern');
      break;
    }
  }
};

const detectToolUsageFromParsed = (parsed, state, rawContent) => {
  if (!state.onToolUse) {
    return;
  }

  const foundNames = new Set();

  const registerName = (name, source) => {
    if (name && !foundNames.has(name)) {
      foundNames.add(name);
      recordToolUse(name, state, source);
    }
  };

  const isToolUseObject = (value) => {
    return value && typeof value === 'object' && typeof value.name === 'string' && (
      'toolUseId' in value || 'tool_use_id' in value || 'type' in value || 'input' in value
    );
  };

  const walk = (node, sourcePath = '') => {
    if (!node || typeof node !== 'object') {
      return;
    }

    if (Array.isArray(node)) {
      node.forEach((item, index) => walk(item, `${sourcePath}[${index}]`));
      return;
    }

    if (isToolUseObject(node)) {
      registerName(node.name, sourcePath || 'toolUseObject');
    }

    for (const [key, value] of Object.entries(node)) {
      if (key === 'tool' && typeof value === 'string') {
        registerName(value, `${sourcePath}.${key}`);
      }

      if (key === 'name' && typeof value === 'string' && isToolUseObject(node)) {
        registerName(value, sourcePath || 'toolUseObject');
      }

      if (key === 'function_name' || key === 'tool_name') {
        if (typeof value === 'string') {
          registerName(value, `${sourcePath}.${key}`);
        }
      }

      walk(value, sourcePath ? `${sourcePath}.${key}` : key);
    }
  };

  walk(parsed.event || parsed, 'event');

  const toolName = parsed.function_name || parsed.tool_name;
  if (toolName) {
    registerName(toolName, 'root.function/tool name');
  }

  if (parsed.event?.metadata?.tool) {
    registerName(parsed.event.metadata.tool, 'event.metadata.tool');
  }
};

const handleSSEDataContent = (dataContent, state) => {
  console.log('Processing SSE data:', dataContent.substring(0, 100));

  try {
    const parsed = JSON.parse(dataContent);

    if (parsed.event?.contentBlockDelta?.delta?.text) {
      const chunk = parsed.event.contentBlockDelta.delta.text;
      state.fullText += chunk;
      state.hasStreamed = true;
      console.log('Streaming chunk:', chunk);
      if (state.onChunk) {
        state.onChunk(chunk);
      }
    } else if (parsed.message?.content?.[0]?.text) {
      console.log('Final complete message received');
      const finalMessage = parsed.message.content[0].text;
      state.finalText = finalMessage;
      if (!state.hasStreamed) {
        state.fullText = finalMessage;
      }
    }

    detectToolUsageFromParsed(parsed, state, dataContent);
  } catch (error) {
    detectToolUsageFromText(dataContent, state);
  }
};

const findEventDelimiter = (text) => {
  const lfIndex = text.indexOf('\n\n');
  const crlfIndex = text.indexOf('\r\n\r\n');

  if (lfIndex === -1 && crlfIndex === -1) {
    return { index: -1, length: 0 };
  }

  if (lfIndex === -1) {
    return { index: crlfIndex, length: 4 };
  }

  if (crlfIndex === -1) {
    return { index: lfIndex, length: 2 };
  }

  return lfIndex < crlfIndex
    ? { index: lfIndex, length: 2 }
    : { index: crlfIndex, length: 4 };
};

const processSSEEventBlock = (eventBlock, state) => {
  const lines = eventBlock.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith('data: ')) {
      handleSSEDataContent(line.slice(6), state);
    }
  }
};

// Parse SSE (Server-Sent Events) response format
const parseSSEResponse = (sseText, onChunk, onToolUse) => {
  const state = createSSEState({ onChunk, onToolUse });
  const eventBlocks = sseText.split(/\r?\n\r?\n/);

  console.log('Parsing SSE response, total blocks:', eventBlocks.length);

  for (const eventBlock of eventBlocks) {
    if (eventBlock.trim().length === 0) {
      continue;
    }
    processSSEEventBlock(eventBlock, state);
  }

  console.log(
    'Parsing complete. HasStreamed:',
    state.hasStreamed,
    'FullText length:',
    state.fullText.length,
    'FinalText length:',
    state.finalText.length
  );

  return state.finalText || state.fullText;
};

// Main function to invoke the agent
export const invokeAgent = async ({
  message,
  sessionId,
  gatewayToken = null,  // OAuth access token for MCP gateway RBAC
  idToken = null,        // Cognito ID token for Identity Pool auth
  onStreamChunk = null,
  onStreamComplete = null,
  onStreamError = null,
  onToolUse = null,
  enableStreaming = true
}) => {
  console.log('[AgentCore] invokeAgent JWT-native (bearer HTTPS) rev 4');

  // JWT-native: the user's Cognito ACCESS token is the Bearer credential the runtime
  // authorizer validates. Prefer the passed gatewayToken; otherwise fetch a fresh one.
  const bearer = gatewayToken || (await fetchAccessToken());
  if (!bearer) {
    const e = new Error('Not authenticated — please log in again.');
    if (onStreamError) onStreamError(e);
    throw e;
  }

  // Get or create a runtime session ID for this session (33+ chars; reused across the convo).
  const runtimeSessionId = getOrCreateRuntimeSessionId(sessionId);

  // The agent expects "prompt"; mode selects text vs voice formatting. The token is NO
  // longer in the payload — it's the Authorization header (validated at the runtime edge).
  const payloadData = { prompt: message, mode: 'text' };
  console.log('Sending payload:', payloadData, '(auth via Bearer header)');

  console.log('Invoking agent with session:', runtimeSessionId);

  try {
    // Plain HTTPS POST to InvokeAgentRuntime with the Bearer token. fetch() streams the
    // SSE body via response.body (a ReadableStream) — same parsing loop as before.
    const response = await fetch(buildInvokeUrl(), {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${bearer}`,
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        // AgentCore session header (33+ chars). The SDK set this from runtimeSessionId;
        // over raw HTTPS we send it explicitly.
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': runtimeSessionId,
      },
      body: JSON.stringify(payloadData),
    });

    if (!response.ok) {
      // 401/403 → token invalid/expired (authorizer rejected). Surface clearly.
      const detail = await response.text().catch(() => '');
      throw new Error(`Agent invoke failed: HTTP ${response.status}${detail ? ' — ' + detail.slice(0, 200) : ''}`);
    }

    const streamReader = (enableStreaming && response.body && typeof response.body.getReader === 'function')
      ? response.body.getReader()
      : null;

    if (streamReader) {
      console.log('Streaming agent response via fetch ReadableStream reader');

      const state = createSSEState({ onChunk: onStreamChunk, onToolUse });
      const decoder = new TextDecoder();
      let buffer = '';

      try {
        while (true) {
          const { value, done } = await streamReader.read();
          if (done) {
            break;
          }

          const decoded = decoder.decode(value, { stream: true });
          buffer += decoded;

          let delimiter = findEventDelimiter(buffer);
          while (delimiter.index !== -1) {
            const eventBlock = buffer.slice(0, delimiter.index);
            buffer = buffer.slice(delimiter.index + delimiter.length);
            if (eventBlock.trim().length > 0) {
              processSSEEventBlock(eventBlock, state);
            }
            delimiter = findEventDelimiter(buffer);
          }
        }

        const remaining = decoder.decode();
        if (remaining) {
          buffer += remaining;
        }
      } finally {
        streamReader.releaseLock();
      }

      if (buffer.trim().length > 0) {
        processSSEEventBlock(buffer, state);
      }

      const finalText = state.finalText || state.fullText || '';

      if (enableStreaming && onStreamComplete) {
        onStreamComplete(finalText);
      }

      return finalText;
    }

    // Non-streaming fallback (enableStreaming=false or no readable body): read the
    // whole body as text and parse it.
    const rawResponse = await response.text();
    console.log('Raw response received, length:', rawResponse.length);

    const textResponse = parseSSEResponse(rawResponse, onStreamChunk, onToolUse);

    if (textResponse) {
      if (enableStreaming && onStreamComplete) {
        onStreamComplete(textResponse);
      }
      return textResponse;
    }

    console.warn('Could not parse SSE response, returning raw text');
    if (onStreamComplete) {
      onStreamComplete(rawResponse);
    }
    return rawResponse;
  } catch (error) {
    console.error('Error invoking agent:', error);
    if (onStreamError) {
      onStreamError(error);
    }
    throw error;
  }
};

// Function to clear a session (useful for starting a new conversation)
export const clearSession = (sessionId) => {
  if (runtimeSessions.has(sessionId)) {
    console.log('Clearing runtime session for:', sessionId);
    runtimeSessions.delete(sessionId);
  }
};

// Function to validate AWS configuration
export const validateAWSConfig = () => {
  const missingConfigs = [];
  
  if (!AGENT_RUNTIME_ARN) missingConfigs.push('REACT_APP_AGENT_RUNTIME_ARN');
  if (!AWS_REGION) missingConfigs.push('REACT_APP_AWS_REGION');
  
  if (missingConfigs.length > 0) {
    console.warn('Missing AWS configuration:', missingConfigs.join(', '));
    console.warn('Please set these environment variables in your .env file');
    return false;
  }
  
  return true;
};

// Export configuration for debugging
export const getAWSConfig = () => ({
  region: AWS_REGION,
  agentRuntimeArn: AGENT_RUNTIME_ARN,
  qualifier: AGENT_QUALIFIER,
  authMode: 'jwt-bearer',
  configured: validateAWSConfig()
});
