import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import type { ConnectChat } from "@/connect/useConnectChat";

// Yes / No questions the merchant answers with buttons:
//  - the AI assistant, when it can't complete a request (e.g. initiating a refund);
//  - the flow, after hours, before logging the request as a support case.
const QUICK_REPLIES: { question: RegExp; yes: string }[] = [
  { question: /escalate (this|you|the conversation)? ?to a human agent\?/i, yes: "Yes, connect me to an agent" },
  { question: /log this as a support case/i, yes: "Yes, log a case" },
];

interface ChatConversationProps {
  chat: ConnectChat;
  /** Prompt shown before a chat is started. */
  idlePrompt: string;
  /** Message shown once the chat has ended. */
  endedText: string;
  /** Label for the start button in the idle state. */
  startLabel?: string;
}

/**
 * Presentational chat body shared by the case-bound chat card and the floating
 * widget. All session state/behaviour lives in the `useConnectChat` hook; this
 * only renders the idle prompt, the transcript, the composer, and the ended
 * state from that hook's result.
 */
export function ChatConversation({
  chat,
  idlePrompt,
  endedText,
  startLabel = "Start chat",
}: ChatConversationProps) {
  const { status, messages, input, setInput, error, active, begin, send, restart } = chat;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Offer Yes / No only while the latest message is one of those questions.
  const last = messages[messages.length - 1];
  const quickReply =
    active && last && last.role !== "customer" ? QUICK_REPLIES.find((q) => q.question.test(last.text)) : undefined;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {status === "idle" && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
          <p className="text-sm text-ink-500">{idlePrompt}</p>
          <Button variant="primary" onClick={() => begin()}>
            {startLabel}
          </Button>
        </div>
      )}

      {(status === "connecting" || active || status === "ended" || status === "error") && (
        <>
          <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto rounded-lg bg-ink-50 p-3">
            {status === "connecting" && (
              <p className="text-center text-xs text-ink-400">Connecting you to support…</p>
            )}
            {messages.map((m) => (
              <div
                key={m.id}
                className={cn(
                  "max-w-[80%] rounded-lg px-3 py-2 text-sm",
                  m.role === "customer"
                    ? "ml-auto bg-brand-600 text-white"
                    : m.role === "system"
                    ? "mx-auto bg-ink-100 text-ink-500 text-xs"
                    : "mr-auto bg-white text-ink-800 shadow-sm"
                )}
              >
                {m.role === "agent" && m.name && (
                  <div className="mb-0.5 text-[11px] font-medium text-ink-400">{m.name}</div>
                )}
                {m.text}
              </div>
            ))}
            {quickReply && (
              <div className="flex gap-2" data-testid="escalation-options">
                <Button size="sm" variant="primary" onClick={() => send("Yes")}>
                  {quickReply.yes}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => send("No")}>
                  No, thanks
                </Button>
              </div>
            )}
            {status === "ended" && <p className="text-center text-xs text-ink-400">{endedText}</p>}
          </div>

          {active && (
            <div className="mt-3 flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") send();
                }}
                placeholder="Type a message…"
                className="input flex-1"
              />
              <Button variant="primary" onClick={() => send()} disabled={!input.trim()}>
                Send
              </Button>
            </div>
          )}
          {(status === "ended" || status === "error") && (
            <div className="mt-3 flex justify-center">
              <Button variant="secondary" size="sm" onClick={restart}>
                Start a new chat
              </Button>
            </div>
          )}
        </>
      )}

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </div>
  );
}
