import React, { useState, useEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Box,
  TextField,
  IconButton,
  Typography,
  Paper,
  Chip,
  CircularProgress,
  Alert,
  Avatar,
  Fade,
  Grow,
  Button,
  ButtonGroup,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  Send,
  SmartToy,
  Person,
  CloudOff,
  Cloud,
  Build,
  Check,
  Close,
  DeleteOutline,
} from '@mui/icons-material';
import { invokeAgent, validateAWSConfig, getAWSConfig } from '../services/awsAgentCore';
import { fetchAccessToken, fetchIdToken } from '../services/authService';
import { useAuth } from '../services/AuthContext';
import { startVoiceSession, stopVoiceSession, voiceConfigured } from '../services/voiceClient';

// Coerce any error-ish value (Error, RTVI error object, string, or nested
// { message }/{ error }/{ text }) into a readable string — so the error banner
// never shows "[object Object]" when a voice transport emits a non-Error payload.
const _errText = (e) => {
  if (e == null) return 'Voice error';
  if (typeof e === 'string') return e;
  const m = e.message || e.error || e.reason || e.text || e.data;
  if (typeof m === 'string' && m) return m;
  if (m && typeof m === 'object') return _errText(m);
  try { const s = JSON.stringify(e); if (s && s !== '{}') return s; } catch (_) {}
  return 'Voice error';
};

// Parse agent response for SQL approval requests
const parseSqlApproval = (content) => {
  if (!content) return null;
  const match = content.match(/<!--SQL_APPROVAL_REQUEST-->([\s\S]*?)<!--\/SQL_APPROVAL_REQUEST-->/);
  if (match) {
    try {
      const approval = JSON.parse(match[1].trim());
      const textBefore = content.substring(0, content.indexOf('<!--SQL_APPROVAL_REQUEST-->')).trim();
      return { type: 'sql_approval', ...approval, textBefore };
    } catch (e) {
      return null;
    }
  }
  return null;
};

// Parse a <chart ...> tag out of an agent response (text path). The agent presigns
// charts in its stream loop, so the tag carries a ready url:
//   <chart caption="Bookings by breed" url="https://..." />
// Returns { content, chart } where content has the tag stripped and chart is
// { src, caption } (or null). Mirrors the voice bot's _extract_chart_tags so both
// modalities render charts identically (message.chart in the render path below).
const CHART_TAG_RE = /<chart\b([^>]*?)\/?>(?:\s*<\/chart>)?/i;
const extractChartTag = (content) => {
  if (!content) return { content, chart: null };
  const m = content.match(CHART_TAG_RE);
  if (!m) return { content, chart: null };
  const attrs = m[1] || '';
  const urlM = attrs.match(/\burl\s*=\s*"([^"]+)"/i);
  const capM = attrs.match(/\bcaption\s*=\s*"([^"]*)"/i);
  const stripped = content.replace(new RegExp(CHART_TAG_RE.source, 'gi'), '').trim();
  if (!urlM) return { content: stripped, chart: null };  // no usable URL → just strip
  return { content: stripped, chart: { src: urlM[1].trim(), caption: capM ? capM[1].trim() : '' } };
};

// Clean text for the LIVE streaming preview: hide the SQL-approval block and any
// <chart ...> tag (incl. a half-streamed one) so the raw tag + long presigned URL
// never flashes in the dialog. On completion, extractChartTag() does the final
// split into message.content + message.chart; this only governs the in-flight view.
const cleanStreamingText = (raw) => {
  let t = raw || '';
  // SQL approval block (same handling as before).
  const s = t.indexOf('<!--SQL_APPROVAL_REQUEST-->');
  if (s !== -1) {
    const e = t.indexOf('<!--/SQL_APPROVAL_REQUEST-->');
    t = e !== -1 ? t.substring(0, s) + '\n\n' + t.substring(e + 28) : t.substring(0, s);
  }
  // Completed chart tags anywhere → drop them (the image renders separately on done).
  t = t.replace(/<chart\b[^>]*?\/?>(?:\s*<\/chart>)?/gi, '');
  // An OPEN, still-streaming chart tag has no '>' yet: hide from the last unclosed
  // '<' onward IF what's been streamed so far could be the start of "<chart..." —
  // catches both a full "<chart url=..." mid-tag AND a just-started "<ch" before
  // the tag name finishes. A bare '<' in prose ("Revenue < 100") is left alone
  // because "< 100" is not a prefix of "<chart".
  const lt = t.lastIndexOf('<');
  if (lt !== -1 && t.indexOf('>', lt) === -1) {
    const frag = t.substring(lt).toLowerCase();
    if ('<chart'.startsWith(frag) || frag.startsWith('<chart')) {
      t = t.substring(0, lt);
    }
  }
  return t;
};

// Strip account_id references and SQL approval markers from displayed text
const stripSensitiveContent = (text) => {
  if (!text) return text;
  return text
    // Remove account_id UUIDs and surrounding context
    .replace(/\b(account_id|account id)[:\s]*['"]?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['"]?/gi, '')
    // Remove "for account <uuid>" or "WHERE account_id = '<uuid>'"
    .replace(/\b(for account|WHERE\s+\w*account_id\s*=)\s*['"]?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['"]?/gi, '')
    // Remove standalone account_id mentions with UUIDs in parentheses
    .replace(/\(\s*account_id[^)]*\)/gi, '')
    // Remove SQL approval markers that might leak into display
    .replace(/<!--\/?SQL_APPROVAL_REQUEST-->/g, '')
    // Remove raw JSON sql_approval blocks that leak when history is replayed
    .replace(/\{"type":\s*"sql_approval"[\s\S]*?"explanation"[\s\S]*?"\}/g, '')
    // Clean up leftover double spaces/newlines
    .replace(/  +/g, ' ')
    .replace(/\n{3,}/g, '\n\n');
};

// Markdown renderer component with MUI-compatible styling
const MarkdownContent = ({ children }) => (
  <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    components={{
      // Tables: size columns to their content (width:auto + min-width) inside a
      // horizontal scroller, so a wide table scrolls cleanly instead of being
      // squeezed into the 80%-width bubble (which clipped the right-hand columns
      // and made them look border-less). Every th/td carries its own border so
      // the grid is complete across ALL columns.
      table: ({ node, ...props }) => (
        <Box sx={{ overflowX: 'auto', my: 1, width: '100%' }}>
          <table style={{ borderCollapse: 'collapse', width: 'auto', minWidth: '100%', fontSize: '0.85rem' }} {...props} />
        </Box>
      ),
      thead: ({ node, ...props }) => <thead {...props} />,
      th: ({ node, ...props }) => (
        <th style={{ border: '1px solid #555', padding: '6px 10px', backgroundColor: '#243447', color: '#9cdcfe', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' }} {...props} />
      ),
      td: ({ node, ...props }) => (
        <td style={{ border: '1px solid #3a3a3a', padding: '6px 10px', verticalAlign: 'top' }} {...props} />
      ),
      p: ({ node, ...props }) => <Typography variant="body2" sx={{ mb: 1 }} {...props} />,
      h1: ({ node, ...props }) => <Typography variant="h6" sx={{ mt: 1, mb: 0.5 }} {...props} />,
      h2: ({ node, ...props }) => <Typography variant="subtitle1" sx={{ mt: 1, mb: 0.5, fontWeight: 600 }} {...props} />,
      h3: ({ node, ...props }) => <Typography variant="subtitle2" sx={{ mt: 1, mb: 0.5, fontWeight: 600 }} {...props} />,
      li: ({ node, ordered, ...props }) => <li style={{ marginBottom: 2, fontSize: '0.875rem' }} {...props} />,
      code: ({ node, inline, ...props }) => inline
        ? <code style={{ backgroundColor: '#1e1e1e', padding: '2px 4px', borderRadius: 3, fontSize: '0.8rem' }} {...props} />
        : <pre style={{ backgroundColor: '#1e1e1e', padding: 8, borderRadius: 4, overflowX: 'auto', fontSize: '0.8rem' }}><code {...props} /></pre>,
    }}
  >
    {children}
  </ReactMarkdown>
);

// SQL Approval Component — shows tree-format query plan, hides SQL
const SqlApprovalCard = ({ sql, query_plan, query_steps, explanation, textBefore, onApprove, onCancel, disabled }) => {
  const planText = query_plan || (query_steps && Array.isArray(query_steps) ? query_steps.join('\n') : null);
  return (
    <Box>
      {textBefore && <MarkdownContent>{stripSensitiveContent(textBefore)}</MarkdownContent>}
      <Paper sx={{ p: 2, backgroundColor: '#1a2332', borderRadius: 1, mb: 2, borderLeft: '3px solid #4caf50' }}>
        <Typography variant="caption" sx={{ color: '#81c784', mb: 1, display: 'block', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Proposed Query Plan:
        </Typography>
        <Box component="pre" sx={{ m: 0, p: 1.5, backgroundColor: '#0d1520', borderRadius: 1, fontFamily: 'monospace', fontSize: '0.8rem', lineHeight: 1.6, color: '#e0e0e0', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
          {planText || 'Analyzing your data...'}
        </Box>
      </Paper>
      {explanation && <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{explanation}</Typography>}
      <Box sx={{ display: 'flex', gap: 1 }}>
        <Button variant="contained" color="success" size="small" startIcon={<Check />} onClick={() => onApprove(sql)} disabled={disabled}>
          Approve & Run
        </Button>
        <Button variant="outlined" color="error" size="small" startIcon={<Close />} onClick={onCancel} disabled={disabled}>
          Cancel
        </Button>
      </Box>
    </Box>
  );
};

const ChatPanel = ({ onPanelUpdate, staffInfo }) => {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState(`session_${Date.now()}`);
  const { user: currentUser, authenticated } = useAuth();
  const messagesContainerRef = useRef(null);
  const messagesEndRef = useRef(null);
  const [currentTool, setCurrentTool] = useState('');
  const [awsConfigValid, setAwsConfigValid] = useState(false);
  const [connectionError, setConnectionError] = useState('');

  // ── Voice Mode (presenter) ─────────────────────────────────────────────
  // 'off' | 'connecting' | 'on'. Additive: text chat is unaffected when off.
  const [voiceState, setVoiceState] = useState('off');
  const voiceClientRef = useRef(null);
  const [thinkingWord, setThinkingWord] = useState('');  // '' = not thinking; set once per turn
  const thinkingStartRef = useRef(0);  // ms timestamp when current thinking started

  // Whimsical-but-real words shown while the agent works (Claude-Code style).
  const THINKING_WORDS = [
    'Pondering', 'Cogitating', 'Ruminating', 'Calculating', 'Crunching',
    'Analyzing', 'Synthesizing', 'Deliberating', 'Computing', 'Tabulating',
    'Investigating', 'Surveying', 'Distilling', 'Correlating', 'Aggregating',
    'Mulling', 'Percolating', 'Noodling', 'Conjuring', 'Divining',
    'Untangling', 'Sleuthing', 'Wrangling', 'Marshalling', 'Foraging',
    'Spelunking', 'Excavating', 'Assembling', 'Brewing', 'Simmering',
    'Reckoning', 'Surmising', 'Inferring', 'Parsing', 'Querying',
    'Sifting', 'Scrutinizing', 'Contemplating', 'Formulating', 'Galvanizing',
  ];
  const startThinking = () => {
    // Pick ONE word per turn and hold it for the whole turn (no mid-turn churn).
    // It only changes on the next turn because stopThinking clears it first.
    setThinkingWord(prev => {
      if (prev) return prev;
      thinkingStartRef.current = Date.now();
      return THINKING_WORDS[Math.floor(Math.random() * THINKING_WORDS.length)];
    });
  };
  const stopThinking = () => { thinkingStartRef.current = 0; setThinkingWord(''); };

  const addAssistantMessage = (content) => {
    if (!content) return;
    setMessages(prev => [...prev, { role: 'assistant', content, timestamp: new Date().toISOString() }]);
  };
  const addUserMessage = (content) => {
    if (!content) return;
    setMessages(prev => [...prev, { role: 'user', content, timestamp: new Date().toISOString() }]);
  };

  const stopVoice = async () => {
    const c = voiceClientRef.current;
    voiceClientRef.current = null;
    setVoiceState('off');
    await stopVoiceSession(c);
  };

  const startVoice = async () => {
    if (!authenticated) { setConnectionError('Please login to use voice.'); return; }
    setConnectionError('');
    setVoiceState('connecting');
    // Coalesce consecutive user finals (Deepgram may split one utterance into
    // several finals, or self-correct) into a SINGLE user bubble, until the bot
    // responds. `voiceUserOpenRef` marks whether the last message is an open
    // user-voice bubble we should append to rather than create anew.
    const voiceUserOpenRef = { current: false };
    const appendUserVoice = (text) => {
      // Defensive: store a string, never an object (which would render as
      // "[object Object]"). voiceClient already coerces, but guard here too.
      if (text && typeof text === 'object') text = text.text || JSON.stringify(text);
      if (typeof text !== 'string') text = String(text ?? '');
      setMessages(prev => {
        if (voiceUserOpenRef.current && prev.length && prev[prev.length - 1].role === 'user') {
          const merged = [...prev];
          const last = merged[merged.length - 1];
          merged[merged.length - 1] = { ...last, content: `${last.content} ${text}`.trim() };
          return merged;
        }
        return [...prev, { role: 'user', content: text, timestamp: new Date().toISOString() }];
      });
      voiceUserOpenRef.current = true;
    };

    try {
      const client = await startVoiceSession({
        onUserTranscript: (text) => appendUserVoice(text),
        // The spoken (voice) version is heard, NOT shown as text. The displayed
        // track (formal answer + tables, digit form) arrives via onDisplay below.
        // A bot reply closes the open user bubble so the next turn starts fresh.
        // "Thinking" is primarily cleared by onDisplay (the real answer). The 2.5s
        // grace here is a backstop for the optional spoken filler (off by default):
        // if enabled, the filler's early bot-speech shouldn't hide the indicator.
        onBotSpoken: () => {
          if (thinkingStartRef.current && Date.now() - thinkingStartRef.current > 2500) stopThinking();
        },
        onThinking: (on) => { if (on) startThinking(); else stopThinking(); },
        onDisplay: (markdown) => { stopThinking(); voiceUserOpenRef.current = false; detectPanelContext(markdown); addAssistantMessage(markdown); },
        onServerMessage: (data) => {
          if (data && data.type === 'chart' && (data.url || data.b64)) {
            stopThinking();
            voiceUserOpenRef.current = false;
            // Prefer a presigned S3 URL (current contract); fall back to inline
            // base64 for backward compatibility with older agent builds.
            const src = data.url || `data:${data.mime || 'image/png'};base64,${data.b64}`;
            setMessages(prev => [...prev, {
              role: 'assistant', timestamp: new Date().toISOString(),
              chart: { src, caption: data.caption || '' },
            }]);
          }
        },
        onReady: () => setVoiceState('on'),
        onError: (e) => { stopThinking(); setConnectionError(_errText(e)); stopVoice(); },
        onDisconnected: () => { stopThinking(); voiceClientRef.current = null; setVoiceState('off'); },
        // Share the SAME app session id as the text chat so voice + text turns
        // land in one AgentCore Memory thread (context carries across both).
        sessionId,
      });
      voiceClientRef.current = client;
    } catch (e) {
      setConnectionError((e && (e.message || String(e))) || 'Could not start voice. Check your mic and retry.');
      setVoiceState('off');
    }
  };

  const toggleVoice = () => { if (voiceState === 'off') startVoice(); else stopVoice(); };

  // Tear down the voice session if the component unmounts.
  useEffect(() => () => { stopVoiceSession(voiceClientRef.current); }, []);

  const clearChat = () => {
    setMessages([]);
    setSessionId(`session_${Date.now()}`);
    setCurrentTool('');
    setConnectionError('');
  };
  
  // SQL Editor Dialog state
  const [sqlEditorOpen, setSqlEditorOpen] = useState(false);
  const [editingSql, setEditingSql] = useState('');
  const [pendingApprovalIndex, setPendingApprovalIndex] = useState(null);
  
  const [streamingState, setStreamingState] = useState({ isStreaming: false, message: '', counter: 0 });
  const streamingStateRef = useRef(streamingState);
  streamingStateRef.current = streamingState;
  
  const updateStreamingState = (newState) => {
    flushSync(() => {
      setStreamingState(newState);
      streamingStateRef.current = newState;
    });
  };

  const suggestions = [
    "Show top 5 customers by revenue",
    "What are the monthly revenue trends?",
    "Which unicorn breeds generate most revenue?",
    "Show customers at risk of churning",
    "What unicorns need maintenance?",
    "Show customer segmentation breakdown",
  ];

  const panelUpdatedRef = useRef(false);

  const scrollToBottom = () => {
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight;
    }
  };

  useEffect(() => {
    const isValid = validateAWSConfig();
    setAwsConfigValid(isValid);
    if (!isValid) {
      setConnectionError('AWS AgentCore configuration is missing. Please check environment variables.');
      console.error('Missing AWS configuration:', getAWSConfig());
    }
  }, []);

  const detectPanelContext = (content) => {
    const lowerContent = content.toLowerCase();
    if (lowerContent.includes('revenue') || lowerContent.includes('transaction') || lowerContent.includes('payment')) {
      onPanelUpdate('revenue');
    } else if (lowerContent.includes('customer') || lowerContent.includes('segment')) {
      onPanelUpdate('customers');
    } else if (lowerContent.includes('unicorn') || lowerContent.includes('breed') || lowerContent.includes('availability')) {
      onPanelUpdate('unicorns');
    } else if (lowerContent.includes('booking') || lowerContent.includes('rental')) {
      onPanelUpdate('bookings');
    }
  };

  useEffect(() => { scrollToBottom(); }, [messages, streamingState.message]);

  const sendMessage = async () => {
    if (!input.trim() || isLoading || streamingState.isStreaming || !awsConfigValid) return;
    if (!authenticated) {
      setConnectionError('Please login to start chatting.');
      return;
    }

    const userMessage = { role: 'user', content: input.trim(), timestamp: new Date().toISOString() };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);
    updateStreamingState({ isStreaming: false, message: '', counter: 0 });
    panelUpdatedRef.current = false;
    const currentMessageTools = [];

    try {
      // Fetch gateway token from Cognito
      const gatewayToken = await fetchAccessToken();
      const idToken = fetchIdToken();
      
      await invokeAgent({
        message: userMessage.content,
        sessionId,
        gatewayToken,
        idToken,
        enableStreaming: true,
        onStreamChunk: (chunk) => {
          setIsLoading(false);
          const currentState = streamingStateRef.current;
          const newMessage = currentState.message + chunk;
          updateStreamingState({ isStreaming: true, message: newMessage, counter: currentState.counter + 1 });
          if (!panelUpdatedRef.current && newMessage.length > 50) {
            detectPanelContext(newMessage);
            panelUpdatedRef.current = true;
          }
        },
        onStreamComplete: (fullResponse) => {
          const { content, chart } = extractChartTag(fullResponse);
          setMessages(prev => [...prev, {
            role: 'assistant',
            content,
            ...(chart && { chart }),
            timestamp: new Date().toISOString(),
            tools: currentMessageTools.length > 0 ? [...currentMessageTools] : undefined
          }]);
          updateStreamingState({ isStreaming: false, message: '', counter: 0 });
          setIsLoading(false);
          setTimeout(() => setCurrentTool(''), 2000);
        },
        onStreamError: (error) => {
          setConnectionError(error.message || 'Failed to get response from AWS AgentCore');
          setIsLoading(false);
          updateStreamingState({ isStreaming: false, message: '', counter: 0 });
        },
        onToolUse: (toolName) => {
          const displayName = toolName.replace(/AgenticAnalyticsLambdaTarget___/g, '').replace(/_tool$/g, '').replace(/_/g, ' ');
          setCurrentTool(`Running ${displayName}`);
          if (!currentMessageTools.includes(toolName)) currentMessageTools.push(toolName);
        }
      });
    } catch (error) {
      setConnectionError(error.message || 'Failed to send message.');
      setIsLoading(false);
    }
  };

  const handleSuggestionClick = (suggestion) => {
    if (!isLoading && !streamingState.isStreaming && awsConfigValid) setInput(suggestion);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  // SQL Approval handlers
  const handleSqlApprove = async (sql, messageIndex) => {
    setPendingApprovalIndex(messageIndex);
    setMessages(prev => prev.map((msg, idx) => 
      idx === messageIndex ? { ...msg, sqlApprovalHandled: true } : msg
    ));
    // Send approval action — agent will generate SQL and call execute_sql_tool
    const approvalPayload = JSON.stringify({ action: 'approve_sql', sql });
    setInput('');
    setIsLoading(true);
    
    const userMessage = { role: 'user', content: `Approved SQL execution`, timestamp: new Date().toISOString(), isSystemAction: true };
    setMessages(prev => [...prev, userMessage]);
    
    try {
      const gatewayToken = await fetchAccessToken();
      await invokeAgent({
        message: approvalPayload,
        sessionId,
        gatewayToken,
        enableStreaming: true,
        onStreamChunk: (chunk) => {
          setIsLoading(false);
          const currentState = streamingStateRef.current;
          updateStreamingState({ isStreaming: true, message: currentState.message + chunk, counter: currentState.counter + 1 });
        },
        onStreamComplete: (fullResponse) => {
          const { content, chart } = extractChartTag(fullResponse);
          setMessages(prev => [...prev, { role: 'assistant', content, ...(chart && { chart }), timestamp: new Date().toISOString() }]);
          updateStreamingState({ isStreaming: false, message: '', counter: 0 });
          setIsLoading(false);
          setPendingApprovalIndex(null);
          setTimeout(() => setCurrentTool(''), 2000);
        },
        onStreamError: (error) => {
          setConnectionError(error.message);
          setIsLoading(false);
          setPendingApprovalIndex(null);
          setCurrentTool('');
        },
        onToolUse: (toolName) => setCurrentTool(`Running ${toolName.replace(/_tool$/g, '').replace(/_/g, ' ')}`)
      });
    } catch (error) {
      setConnectionError(error.message);
      setIsLoading(false);
      setPendingApprovalIndex(null);
    }
  };

  const handleSqlEdit = (sql) => {
    setEditingSql(sql);
    setSqlEditorOpen(true);
  };

  const handleSqlEditorSubmit = async () => {
    setSqlEditorOpen(false);
    const sql = editingSql;
    setEditingSql('');
    
    // Mark any pending approval as handled
    if (pendingApprovalIndex !== null) {
      setMessages(prev => prev.map((msg, idx) => 
        idx === pendingApprovalIndex ? { ...msg, sqlApprovalHandled: true } : msg
      ));
    }
    
    const declinePayload = JSON.stringify({ action: 'decline_sql', sql });
    setIsLoading(true);
    
    const userMessage = { role: 'user', content: `Running custom SQL`, timestamp: new Date().toISOString(), isSystemAction: true };
    setMessages(prev => [...prev, userMessage]);
    
    try {
      const gatewayToken = await fetchAccessToken();
      await invokeAgent({
        message: declinePayload,
        sessionId,
        gatewayToken,
        enableStreaming: true,
        onStreamChunk: (chunk) => {
          setIsLoading(false);
          const currentState = streamingStateRef.current;
          updateStreamingState({ isStreaming: true, message: currentState.message + chunk, counter: currentState.counter + 1 });
        },
        onStreamComplete: (fullResponse) => {
          const { content, chart } = extractChartTag(fullResponse);
          setMessages(prev => [...prev, { role: 'assistant', content, ...(chart && { chart }), timestamp: new Date().toISOString() }]);
          updateStreamingState({ isStreaming: false, message: '', counter: 0 });
          setIsLoading(false);
          setPendingApprovalIndex(null);
          setTimeout(() => setCurrentTool(''), 2000);
        },
        onStreamError: (error) => {
          setConnectionError(error.message);
          setIsLoading(false);
          setCurrentTool('');
        },
        onToolUse: (toolName) => setCurrentTool(`Running ${toolName.replace(/_tool$/g, '').replace(/_/g, ' ')}`)
      });
    } catch (error) {
      setConnectionError(error.message);
      setIsLoading(false);
    }
  };

  const handleSqlCancel = async (messageIndex) => {
    setMessages(prev => prev.map((msg, idx) => 
      idx === messageIndex ? { ...msg, sqlApprovalHandled: true } : msg
    ));
    
    const cancelPayload = JSON.stringify({ action: 'cancel_sql' });
    const userMessage = { role: 'user', content: `Cancelled SQL execution`, timestamp: new Date().toISOString(), isSystemAction: true };
    setMessages(prev => [...prev, userMessage]);
    
    try {
      const gatewayToken = await fetchAccessToken();
      await invokeAgent({
        message: cancelPayload,
        sessionId,
        gatewayToken,
        enableStreaming: true,
        onStreamChunk: () => {},
        onStreamComplete: (fullResponse) => {
          const { content, chart } = extractChartTag(fullResponse);
          setMessages(prev => [...prev, { role: 'assistant', content, ...(chart && { chart }), timestamp: new Date().toISOString() }]);
        },
        onStreamError: () => {},
        onToolUse: () => {}
      });
    } catch (error) {
      console.error('Cancel error:', error);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      {connectionError && (
        <Alert severity="error" variant="outlined" sx={{ mx: 2, mt: 1 }} onClose={() => setConnectionError('')}>{connectionError}</Alert>
      )}

      {voiceState === 'connecting' && (
        <Alert severity="info" icon={<CircularProgress size={16} />} variant="outlined" sx={{ mx: 2, mt: 1 }}>
          Enabling voice mode… this can take a few seconds.
        </Alert>
      )}

      <Box ref={messagesContainerRef} sx={{
        flexGrow: 1, 
        overflow: 'auto', 
        px: 2, 
        py: 1, 
        minHeight: 0,
        maxHeight: 'calc(100vh - 200px)',
        '&::-webkit-scrollbar': { width: '6px' },
        '&::-webkit-scrollbar-thumb': { backgroundColor: 'rgba(0,0,0,0.2)', borderRadius: '3px' }
      }}>
        {messages.length === 0 && (
          <Box sx={{ textAlign: 'center', py: 4 }}>
            <SmartToy sx={{ fontSize: 48, color: 'primary.main', mb: 2 }} />
            <Typography variant="h6" sx={{ mb: 1 }}>Timely-Unicorn Analytics</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              {authenticated 
                ? 'Ask me about revenue, customers, unicorns, bookings, and business insights.'
                : 'Login to start chatting with the analytics assistant.'}
            </Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, justifyContent: 'center', mb: 2 }}>
              {suggestions.map((suggestion, index) => (
                <Grow in timeout={300 + index * 100} key={suggestion}>
                  <Chip label={suggestion} onClick={() => handleSuggestionClick(suggestion)} clickable variant="filled" size="small"
                    sx={{ transition: 'all 0.2s', backgroundColor: 'action.hover', '&:hover': { backgroundColor: 'primary.main', color: 'white', transform: 'scale(1.02)' } }} />
                </Grow>
              ))}
            </Box>
          </Box>
        )}

        {messages.map((message, index) => {
          const sqlApproval = message.role === 'assistant' ? parseSqlApproval(message.content) : null;
          
          return (
          <Fade in timeout={300} key={`${message.timestamp}-${index}`}>
            <Box sx={{ mb: 2, display: 'flex', alignItems: 'flex-start', gap: 1 }}>
              <Avatar sx={{ width: 32, height: 32, bgcolor: message.role === 'user' ? 'primary.main' : 'secondary.main', fontSize: '0.8rem' }}>
                {message.role === 'user' ? <Person fontSize="small" /> : <SmartToy fontSize="small" />}
              </Avatar>
              <Paper elevation={1} sx={{ p: 2, maxWidth: '80%', backgroundColor: message.role === 'user' ? 'primary.light' : 'background.paper', color: message.role === 'user' ? 'white' : 'text.primary' }}>
                {message.role === 'assistant' && message.tools?.length > 0 && (
                  <Box sx={{ display: 'flex', gap: 0.5, mb: 1, flexWrap: 'wrap' }}>
                    {message.tools.map((tool, idx) => (
                      <Chip key={idx} icon={<Build sx={{ fontSize: 14 }} />} label={tool.replace(/AgenticAnalyticsLambdaTarget___/g, '').replace(/_/g, ' ')} size="small"
                        sx={{ fontSize: '0.7rem', height: 20, backgroundColor: 'action.selected', color: 'primary.main' }} />
                    ))}
                  </Box>
                )}
                {sqlApproval ? (
                  <SqlApprovalCard
                    sql={sqlApproval.sql}
                    query_plan={sqlApproval.query_plan}
                    query_steps={sqlApproval.query_steps}
                    explanation={sqlApproval.explanation}
                    textBefore={sqlApproval.textBefore}
                    onApprove={(sql) => handleSqlApprove(sql, index)}
                    onCancel={() => handleSqlCancel(index)}
                    disabled={isLoading || streamingState.isStreaming}
                  />
                ) : (
                  <>
                    {/* The text answer (e.g. the table) and the chart are NOT
                        mutually exclusive — a "show me X and a chart" turn has
                        both. Render the markdown answer first, then the chart. */}
                    {message.content && message.content.trim() && (
                      <MarkdownContent>{stripSensitiveContent(message.content)}</MarkdownContent>
                    )}
                    {message.chart && (
                      <Box sx={{ mt: message.content && message.content.trim() ? 1 : 0 }}>
                        <Box component="img" src={message.chart.src} alt={message.chart.caption || 'chart'}
                          sx={{ maxWidth: '100%', borderRadius: 1, display: 'block' }} />
                        {message.chart.caption && (
                          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                            {message.chart.caption}
                          </Typography>
                        )}
                      </Box>
                    )}
                  </>
                )}
              </Paper>
            </Box>
          </Fade>
        )})}

        {(streamingState.message || streamingState.isStreaming) && (
          <Box sx={{ mb: 2, display: 'flex', alignItems: 'flex-start', gap: 1 }}>
            <Avatar sx={{ width: 32, height: 32, bgcolor: 'secondary.main', fontSize: '0.8rem' }}><SmartToy fontSize="small" /></Avatar>
            <Paper elevation={1} sx={{ p: 2, maxWidth: '80%', backgroundColor: 'background.paper', border: 1, borderColor: 'primary.light' }}>
              <MarkdownContent>{stripSensitiveContent(cleanStreamingText(streamingState.message)) || "..."}</MarkdownContent>
              <Box component="span" sx={{ display: 'inline-block', width: 2, height: 16, backgroundColor: 'primary.main', ml: 0.5, animation: 'blink 1s infinite', '@keyframes blink': { '0%, 50%': { opacity: 1 }, '51%, 100%': { opacity: 0 } } }} />
            </Paper>
          </Box>
        )}

        {currentTool && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, justifyContent: 'center' }}>
            <Build sx={{ fontSize: 16, color: 'primary.main' }} /><Typography variant="caption" color="primary">{currentTool}</Typography><CircularProgress size={12} />
          </Box>
        )}

        {thinkingWord && (
          <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
            <Avatar sx={{ width: 32, height: 32, bgcolor: 'secondary.main', fontSize: '0.8rem',
              animation: 'tpulse 1.2s ease-in-out infinite',
              '@keyframes tpulse': { '0%,100%': { opacity: 0.35 }, '50%': { opacity: 1 } } }}>
              <SmartToy fontSize="small" />
            </Avatar>
            <Typography variant="body2" sx={{ fontStyle: 'italic', color: 'text.secondary',
              animation: 'tfade 1.2s ease-in-out infinite', '@keyframes tfade': { '0%,100%': { opacity: 0.5 }, '50%': { opacity: 1 } } }}>
              {thinkingWord}…
            </Typography>
          </Box>
        )}
        <div ref={messagesEndRef} />
      </Box>

      <Box sx={{ p: 2, flexShrink: 0, borderTop: 1, borderColor: 'divider', backgroundColor: 'background.paper' }}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-end' }}>
          <TextField fullWidth multiline maxRows={3} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown}
            placeholder={awsConfigValid ? "Ask about revenue, customers, unicorns..." : "Not configured..."} disabled={!awsConfigValid || isLoading || streamingState.isStreaming}
            variant="outlined" size="small" sx={{ '& .MuiOutlinedInput-root': { borderRadius: 2 } }} />
          {/* One compact control cluster. Distinct colors prevent mis-clicks:
              Send = primary (blue), Voice = green when on / neutral when off,
              New chat = subtle grey (red only on hover). */}
          <ButtonGroup variant="contained" size="small" sx={{ flexShrink: 0, boxShadow: 'none' }}>
            <Button onClick={sendMessage}
              disabled={!input.trim() || !awsConfigValid || isLoading || streamingState.isStreaming}
              color="primary" title="Send" sx={{ minWidth: 0, px: 1.25 }}>
              {isLoading ? <CircularProgress size={18} color="inherit" /> : <Send fontSize="small" />}
            </Button>
            {voiceConfigured() && authenticated && (
              <Button onClick={toggleVoice} disabled={voiceState === 'connecting'}
                color={voiceState === 'on' ? 'success' : 'inherit'}
                variant={voiceState === 'on' ? 'contained' : 'outlined'}
                title={voiceState === 'on' ? 'Voice mode ON (listening + speaking) — click to turn off'
                  : voiceState === 'connecting' ? 'Enabling voice…' : 'Turn on voice mode (talk + hear replies)'}
                sx={{ minWidth: 0, px: 1.25, whiteSpace: 'nowrap',
                  ...(voiceState === 'on' && { animation: 'vpulse 1.4s infinite', '@keyframes vpulse': { '0%,100%': { boxShadow: '0 0 0 0 rgba(76,175,80,0.5)' }, '50%': { boxShadow: '0 0 0 5px rgba(76,175,80,0)' } } }),
                }}>
                {voiceState === 'connecting' ? <CircularProgress size={14} color="inherit" /> : 'Voice'}
              </Button>
            )}
            {messages.length > 0 && !isLoading && (
              <Button onClick={clearChat} title="New chat" color="inherit" variant="outlined"
                sx={{ minWidth: 0, px: 1, color: 'text.secondary', '&:hover': { color: 'error.main' } }}>
                <DeleteOutline fontSize="small" />
              </Button>
            )}
          </ButtonGroup>
        </Box>
      </Box>

      {/* SQL Editor Dialog */}
      <Dialog open={sqlEditorOpen} onClose={() => setSqlEditorOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit SQL Query</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            multiline
            rows={10}
            value={editingSql}
            onChange={(e) => setEditingSql(e.target.value)}
            variant="outlined"
            sx={{ mt: 1, fontFamily: 'monospace', '& .MuiInputBase-input': { fontFamily: 'monospace', fontSize: '0.9rem' } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSqlEditorOpen(false)}>Cancel</Button>
          <Button onClick={handleSqlEditorSubmit} variant="contained" color="primary">Run Query</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ChatPanel;
