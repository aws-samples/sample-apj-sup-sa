import { useEffect, useState } from "react";
import { ChatBubbleIcon, XIcon } from "@/components/ui/icons";
import { ChatConversation } from "@/components/chat/ChatConversation";
import { loadConnectConfig } from "@/connect/config";
import { useConnectChat } from "@/connect/useConnectChat";
import { cn } from "@/lib/cn";

/**
 * Floating live-chat widget for merchant pages: a bottom-right bubble that opens
 * a chat panel and connects to a support agent via the same tenant-isolated
 * `StartChatContact` backend (no case binding). Mounted once in the merchant
 * layout, so the session survives navigation between merchant pages and stays
 * alive while minimized. Only renders when the Chat API is configured.
 */
export function FloatingChat() {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [seenCount, setSeenCount] = useState(0);
  const chat = useConnectChat({}); // standalone live chat — no case binding

  useEffect(() => {
    void loadConnectConfig().then((cfg) => setEnabled(Boolean(cfg?.chatApiUrl)));
  }, []);

  // Track unread agent/system messages that arrive while the panel is minimized.
  useEffect(() => {
    if (open) setSeenCount(chat.messages.length);
  }, [open, chat.messages.length]);
  const unread = open ? 0 : Math.max(0, chat.messages.length - seenCount);

  if (!enabled) return null;

  const subtitle =
    chat.status === "connecting"
      ? "Connecting you to an agent…"
      : chat.active
      ? "Connected to support"
      : chat.status === "ended"
      ? "Chat ended"
      : "We're here to help";

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end gap-3 print:hidden">
      {open && (
        <div className="flex h-[30rem] max-h-[80vh] w-[22rem] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-ink-200 bg-white shadow-2xl">
          <div className="flex items-start justify-between gap-2 bg-brand-600 px-4 py-3 text-white">
            <div>
              <div className="text-sm font-semibold">Live chat</div>
              <div className="text-xs text-white/80">{subtitle}</div>
            </div>
            <div className="flex items-center gap-1">
              {chat.active && (
                <button
                  onClick={() => chat.end()}
                  className="rounded-md px-2 py-1 text-xs font-medium text-white/90 hover:bg-white/15"
                >
                  End
                </button>
              )}
              <button
                onClick={() => setOpen(false)}
                aria-label="Minimize chat"
                className="rounded-md p-1 text-white/90 hover:bg-white/15"
              >
                <XIcon className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-col p-3">
            <ChatConversation
              chat={chat}
              idlePrompt="Need help? Start a live chat and we'll connect you to a support agent."
              endedText="Chat ended. Thanks for reaching out."
            />
          </div>
        </div>
      )}

      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Minimize chat" : "Open live chat"}
        className={cn(
          "relative flex h-14 w-14 items-center justify-center rounded-full text-white shadow-lg transition-colors",
          "bg-brand-600 hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-400 focus:ring-offset-2"
        )}
      >
        {open ? <XIcon className="h-6 w-6" /> : <ChatBubbleIcon className="h-6 w-6" />}
        {/* Connected indicator */}
        {chat.active && !open && (
          <span className="absolute -right-0.5 -top-0.5 h-3.5 w-3.5 rounded-full border-2 border-white bg-emerald-500" />
        )}
        {/* Unread badge */}
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-5 min-w-[1.25rem] items-center justify-center rounded-full border-2 border-white bg-red-500 px-1 text-[11px] font-semibold">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
    </div>
  );
}
