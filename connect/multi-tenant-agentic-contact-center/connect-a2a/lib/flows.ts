// Amazon Connect flow for the A2A demo (flow language, Version 2019-10-30).
//
//   log -> Connect assistant (opens the AI session) -> Get customer input (Lex)
//          with x-amz-lex:q-in-connect:ai-agent-arn = the A2A orchestrator version,
//          which hands the conversation to the external agent immediately
//     Tool = Escalate          -> "connecting you to an agent" -> support queue
//     Tool = Complete          -> thanks -> end
//     Lex error                -> route on the partner's l3_outcome contact attribute
//                                 (ESCALATE / COMPLETE as above)
//     no match / any error     -> support queue (never drop the customer: the
//                                 fallback path when the partner fails)

/* eslint-disable @typescript-eslint/no-explicit-any */
type Action = Record<string, any>;
const id = (n: number) => `a2a00000-0000-4000-8000-${String(n).padStart(12, "0")}`;

export function a2aChatFlow(p: { assistantArn: string; botAliasArn: string; aiAgentArn: string; queueArn: string }) {
  const [LOG, WIS, WATT, LEX, TOOL, ESC, SETQ, XFER, DONE, END, SORRY, OUT, LEXERR, PARTNER] = Array.from({ length: 14 }, (_, i) => id(i + 1));
  const msg = (identifier: string, text: string, next: string): Action => ({
    Identifier: identifier,
    Type: "MessageParticipant",
    Parameters: { Text: text },
    Transitions: { NextAction: next, Errors: [{ NextAction: next, ErrorType: "NoMatchingError" }], Conditions: [] },
  });
  const lexAttrs = {
    "x-amz-lex:q-in-connect:session-arn": "$.Wisdom.SessionArn",
    "x-amz-lex:q-in-connect:ai-agent-arn": p.aiAgentArn,
  };
  const actions: Action[] = [
    {
      Identifier: LOG,
      Type: "UpdateFlowLoggingBehavior",
      Parameters: { FlowLoggingBehavior: "Enabled" },
      Transitions: { NextAction: WIS },
    },
    {
      Identifier: WIS,
      Type: "CreateWisdomSession",
      Parameters: { WisdomAssistantArn: p.assistantArn },
      Transitions: { NextAction: WATT, Errors: [{ NextAction: SORRY, ErrorType: "NoMatchingError" }] },
    },
    {
      Identifier: WATT,
      Type: "UpdateContactAttributes",
      Parameters: { Attributes: { "x-amz-lex:q-in-connect:session-arn": "$.Wisdom.SessionArn", a2a_partner: "level3" } },
      Transitions: { NextAction: LEX, Errors: [{ NextAction: LEX, ErrorType: "NoMatchingError" }] },
    },
    {
      Identifier: LEX,
      Type: "ConnectParticipantWithLexBot",
      Parameters: {
        Text: "Hi, you're chatting with our partner assistant. How can I help today?",
        LexV2Bot: { AliasArn: p.botAliasArn },
        LexSessionAttributes: lexAttrs,
      },
      Transitions: {
        NextAction: OUT,
        Errors: [
          { NextAction: LEXERR, ErrorType: "NoMatchingError" },
          { NextAction: OUT, ErrorType: "NoMatchingCondition" },
        ],
        Conditions: [],
      },
    },
    // Keep the AI outcome on the contact (agent CCP, contact search, troubleshooting).
    {
      Identifier: OUT,
      Type: "UpdateContactAttributes",
      Parameters: { Attributes: { a2a_tool: "$.Lex.SessionAttributes.Tool" } },
      Transitions: { NextAction: TOOL, Errors: [{ NextAction: TOOL, ErrorType: "NoMatchingError" }] },
    },
    {
      Identifier: LEXERR,
      Type: "UpdateContactAttributes",
      Parameters: { Attributes: { a2a_tool: "lex-error" } },
      Transitions: { NextAction: PARTNER, Errors: [{ NextAction: PARTNER, ErrorType: "NoMatchingError" }] },
    },
    // When the partner finishes on a later chat turn, the Lex block returns an error
    // even though Connect processed the FINISH (observed on chat with message
    // streaming). The partner also writes its outcome to the contact, so route on it;
    // anything else goes to a human.
    {
      Identifier: PARTNER,
      Type: "Compare",
      Parameters: { ComparisonValue: "$.Attributes.l3_outcome" },
      Transitions: {
        NextAction: SORRY,
        Conditions: [
          { NextAction: ESC, Condition: { Operator: "Equals", Operands: ["ESCALATE"] } },
          { NextAction: DONE, Condition: { Operator: "Equals", Operands: ["COMPLETE"] } },
        ],
        Errors: [{ NextAction: SORRY, ErrorType: "NoMatchingCondition" }],
      },
    },
    {
      Identifier: TOOL,
      Type: "Compare",
      Parameters: { ComparisonValue: "$.Lex.SessionAttributes.Tool" },
      Transitions: {
        NextAction: SORRY,
        Conditions: [
          { NextAction: ESC, Condition: { Operator: "Equals", Operands: ["Escalate"] } },
          { NextAction: DONE, Condition: { Operator: "Equals", Operands: ["Complete"] } },
        ],
        Errors: [{ NextAction: SORRY, ErrorType: "NoMatchingCondition" }],
      },
    },
    msg(ESC, "Connecting you to a support agent now. They'll see a summary of your conversation.", SETQ),
    msg(SORRY, "Let me connect you to a support agent.", SETQ),
    {
      Identifier: SETQ,
      Type: "UpdateContactTargetQueue",
      Parameters: { QueueId: p.queueArn },
      Transitions: { NextAction: XFER, Errors: [{ NextAction: END, ErrorType: "NoMatchingError" }] },
    },
    {
      Identifier: XFER,
      Type: "TransferContactToQueue",
      Parameters: {},
      Transitions: {
        NextAction: END,
        Errors: [
          { NextAction: END, ErrorType: "QueueAtCapacity" },
          { NextAction: END, ErrorType: "NoMatchingError" },
        ],
      },
    },
    msg(DONE, "Thanks for chatting with us. Goodbye!", END),
    { Identifier: END, Type: "DisconnectParticipant", Parameters: {}, Transitions: {} },
  ];
  return {
    Version: "2019-10-30",
    StartAction: LOG,
    Metadata: {
      EntryPointPosition: { x: 20, y: 20 },
      ActionMetadata: Object.fromEntries(
        actions.map((a, i) => [a.Identifier, { Position: { x: 160 + (i % 6) * 220, y: 40 + Math.floor(i / 6) * 200 } }])
      ),
    },
    Actions: actions,
  };
}
