import { useCallback, useEffect, useRef, useState } from "react";
import { loadConnectConfig } from "./config";
import { startChat } from "./chatApi";

// Minimal shape of the bits of ChatJS we use. ChatJS attaches itself to
// `window.connect` when imported; we lazy-import it so it never loads on the
// admin pages (which use amazon-connect-streams on the same global).
interface ChatMessageData {
  Id: string;
  Content?: string;
  ContentType?: string;
  DisplayName?: string;
  ParticipantRole?: "AGENT" | "CUSTOMER" | "SYSTEM";
  Type?: string;
  AbsoluteTime?: string;
}
interface ChatSession {
  connect: () => Promise<unknown>;
  sendMessage: (a: { contentType: string; message: string }) => Promise<unknown>;
  disconnectParticipant: () => Promise<unknown>;
  getTranscript: (a: {
    scanDirection?: string;
    sortOrder?: string;
    maxResults?: number;
  }) => Promise<{ data?: { Transcript?: ChatMessageData[] } }>;
  onMessage: (cb: (e: { data: ChatMessageData }) => void) => void;
  onConnectionEstablished: (cb: (e: unknown) => void) => void;
  onConnectionLost: (cb: (e: unknown) => void) => void;
  onEnded: (cb: (e: unknown) => void) => void;
}
interface ConnectGlobal {
  ChatSession: {
    setGlobalConfig: (c: unknown) => void;
    create: (a: unknown) => ChatSession;
  };
}
interface ChatDetails {
  contactId: string;
  participantId: string;
  participantToken: string;
  region?: string;
}

export type ChatStatus = "idle" | "connecting" | "connected" | "ended" | "error";

export interface UiMessage {
  id: string;
  role: "agent" | "customer" | "system";
  text: string;
  name?: string;
  time?: string;
}

export interface UseConnectChatOptions {
  /** When set, binds the chat to a case (case chat flow + tenant-verified). */
  caseId?: string;
  /** Fired once when the chat ends, with the non-system messages, so the caller
   *  can persist a transcript. Best-effort: throwing allows a retry on next end. */
  onEnded?: (messages: UiMessage[]) => Promise<void> | void;
}

export interface ConnectChat {
  status: ChatStatus;
  messages: UiMessage[];
  input: string;
  setInput: (v: string) => void;
  error: string | null;
  active: boolean;
  begin: () => void;
  /** Send the typed input, or `text` (quick replies) when given. */
  send: (text?: string) => void;
  end: () => void;
  restart: () => void;
}

const sentMs = (t?: string) => {
  const n = t ? Date.parse(t) : NaN;
  return Number.isFinite(n) ? n : 0;
};

/**
 * Encapsulates a customer-side Amazon Connect chat over ChatJS: starts a contact
 * via the backend `StartChatContact` proxy (tenant stamped server-side), opens a
 * WebSocket, tracks the transcript, and rehydrates an in-flight chat across a
 * page refresh. Shared by the case-bound chat and the floating widget.
 */
export function useConnectChat({ caseId, onEnded }: UseConnectChatOptions): ConnectChat {
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<ChatSession | null>(null);
  const seen = useRef<Set<string>>(new Set());
  const messagesRef = useRef<UiMessage[]>([]);
  const postedRef = useRef(false);
  // Keep the latest onEnded without re-wiring the session callbacks each render.
  const onEndedRef = useRef(onEnded);
  useEffect(() => {
    onEndedRef.current = onEnded;
  }, [onEnded]);

  // Per-context session key so a case chat and the floating chat (and different
  // cases) don't clobber each other's rehydration state.
  const sessionKey = caseId ? `anycompany-pay.chat.details:${caseId}` : "anycompany-pay.chat.details:floating";

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Dedupe by Id and keep the transcript sorted by send time — WebSocket
  // messages are NOT guaranteed to arrive in order (per ChatJS docs).
  const pushMessage = useCallback((m: ChatMessageData) => {
    if (!m?.Id || seen.current.has(m.Id)) return;
    if (m.ContentType !== "text/plain" && m.ContentType !== "text/markdown") return;
    seen.current.add(m.Id);
    const role: UiMessage["role"] =
      m.ParticipantRole === "AGENT" ? "agent" : m.ParticipantRole === "SYSTEM" ? "system" : "customer";
    setMessages((prev) =>
      [...prev, { id: m.Id, role, text: m.Content ?? "", name: m.DisplayName, time: m.AbsoluteTime }].sort(
        (a, b) => sentMs(a.time) - sentMs(b.time)
      )
    );
  }, []);

  const loadTranscript = useCallback(
    async (session: ChatSession) => {
      try {
        const resp = await session.getTranscript({
          scanDirection: "BACKWARD",
          sortOrder: "ASCENDING",
          maxResults: 50,
        });
        for (const it of resp?.data?.Transcript ?? []) pushMessage(it);
      } catch {
        // best-effort
      }
    },
    [pushMessage]
  );

  // Fire onEnded once with the real (non-system, non-empty) messages.
  const finalize = useCallback(async () => {
    if (postedRef.current) return;
    const msgs = messagesRef.current.filter((m) => m.role !== "system" && m.text.trim());
    if (msgs.length === 0) return; // nothing to persist; allow a retry
    postedRef.current = true;
    try {
      await onEndedRef.current?.(msgs);
    } catch {
      postedRef.current = false; // allow a retry on next end
    }
  }, []);

  const openSession = useCallback(
    async (details: ChatDetails) => {
      const cfg = await loadConnectConfig();
      // Region comes from the chat details or the SPA runtime config (SSM, seeded
      // with the deploy region). No hardcoded region — fail clearly if absent.
      const region = details.region || cfg?.region;
      if (!region) {
        throw new Error(
          "No AWS region available for the chat session (missing from chat details and runtime config)."
        );
      }
      // Lazy-load ChatJS (registers window.connect). ChatJS ships a global-only
      // type declaration (not a module), so this dynamic import is side-effect only.
      // @ts-expect-error side-effect import; ChatJS attaches to window.connect
      await import("amazon-connect-chatjs");
      const connect = (window as unknown as { connect: ConnectGlobal }).connect;
      connect.ChatSession.setGlobalConfig({ region });
      const session = connect.ChatSession.create({
        chatDetails: {
          contactId: details.contactId,
          participantId: details.participantId,
          participantToken: details.participantToken,
        },
        options: { region },
        type: "CUSTOMER",
        disableCSM: true,
      });
      sessionRef.current = session;
      session.onMessage((e) => pushMessage(e.data));
      session.onConnectionEstablished(() => {
        setStatus("connected");
        void loadTranscript(session);
      });
      session.onConnectionLost(() => setStatus("connecting"));
      session.onEnded(() => {
        setStatus("ended");
        sessionStorage.removeItem(sessionKey);
        void finalize();
      });
      await session.connect();
      setStatus("connected");
    },
    [pushMessage, loadTranscript, finalize, sessionKey]
  );

  // Rehydrate an in-flight chat after a browser refresh.
  useEffect(() => {
    const raw = sessionStorage.getItem(sessionKey);
    if (!raw) return;
    let details: ChatDetails | null = null;
    try {
      details = JSON.parse(raw);
    } catch {
      sessionStorage.removeItem(sessionKey);
      return;
    }
    if (!details?.participantToken) {
      sessionStorage.removeItem(sessionKey);
      return;
    }
    setStatus("connecting");
    openSession(details).catch(() => {
      sessionStorage.removeItem(sessionKey);
      setStatus("idle");
    });
  }, [openSession, sessionKey]);

  const begin = useCallback(() => {
    setStatus("connecting");
    setError(null);
    postedRef.current = false;
    void (async () => {
      try {
        const res = await startChat(caseId ? { caseId } : {});
        const details: ChatDetails = {
          contactId: res.contactId,
          participantId: res.participantId,
          participantToken: res.participantToken,
          region: res.region,
        };
        sessionStorage.setItem(sessionKey, JSON.stringify(details));
        await openSession(details);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to start chat");
        setStatus("error");
      }
    })();
  }, [caseId, openSession, sessionKey]);

  // Sends the typed input, or `override` (e.g. a quick-reply button) without
  // touching what the merchant has typed.
  const send = useCallback((override?: string) => {
    const text = (override ?? input).trim();
    if (!text || !sessionRef.current) return;
    if (override === undefined) setInput("");
    void sessionRef.current
      .sendMessage({ contentType: "text/plain", message: text })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to send"));
  }, [input]);

  const end = useCallback(() => {
    void (async () => {
      try {
        await sessionRef.current?.disconnectParticipant();
      } catch {
        // ignore
      }
      sessionStorage.removeItem(sessionKey);
      await finalize();
      setStatus("ended");
    })();
  }, [sessionKey, finalize]);

  const restart = useCallback(() => {
    seen.current.clear();
    postedRef.current = false;
    setMessages([]);
    begin();
  }, [begin]);

  return {
    status,
    messages,
    input,
    setInput,
    error,
    active: status === "connected",
    begin,
    send,
    end,
    restart,
  };
}
