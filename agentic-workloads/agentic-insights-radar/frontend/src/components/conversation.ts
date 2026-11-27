// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { type ChatFrame, newSessionId } from './chat';

export interface Turn {
  /** Stable within the conversation, for React keys. */
  id: number;
  role: 'user' | 'assistant';
  text: string;
  tools: string[];
  error?: string;
  /** The error code of the frame that set `error` (e.g. "auth" when the session expired). */
  code?: string;
}

export interface ConversationState {
  turns: Turn[];
  busy: boolean;
}

/** A request started by {@link ChatConversation.begin}. Only the newest request may change the conversation. */
export interface ChatRequest {
  signal: AbortSignal;
  update(frame: ChatFrame): void;
  done(): void;
}

/** Where a tab keeps its conversation, so a reload or Back/Forward does not lose it (CTR HU-031). */
export const CHAT_STORAGE_KEY = 'air.chat.v1';
export const INTERRUPTED_NOTE = 'Interrupted: the page was reloaded or left while this answer was arriving.';

type ChatStore = Pick<Storage, 'getItem' | 'setItem'>;

/** Removes the tab's kept conversation (on sign-out). Storage that refuses access kept nothing. */
export function forgetChat(): void {
  try {
    window.sessionStorage.removeItem(CHAT_STORAGE_KEY);
  } catch {
    // some private modes throw on access
  }
}

const nextFrame: (run: () => void) => unknown = (run) =>
  typeof requestAnimationFrame === 'function' ? requestAnimationFrame(run) : setTimeout(run, 16);

/**
 * One chat conversation: its turns, its session id and the running request. It lives outside the
 * panel, so closing the assistant drawer (which unmounts the panel) keeps the conversation and lets
 * a running answer finish. Given a store (the tab's sessionStorage), it is kept there: sessionStorage,
 * not localStorage, because a chat is one tab's working state and the runtime's memory behind the
 * session id is the durable copy. An answer that was still arriving when the page went away comes back
 * marked as interrupted.
 */
export class ChatConversation {
  private state: ConversationState = { turns: [], busy: false };
  private session = newSessionId();
  private inflight: AbortController | null = null;
  private ids = 0;
  private readonly listeners = new Set<() => void>();
  /** Streamed text not yet shown: applied once per frame, not once per token (CTR's React #185 fix). */
  private pending = '';
  private flushScheduled = false;
  /** Whose conversation it is (the user's subject), so another user signing in to this tab does not read it. */
  private owner = '';
  /** Turns kept from an earlier page load (sessionStorage), as opposed to ones started on this page. */
  private restored = false;

  constructor(private readonly store: ChatStore | null = null) {
    const saved = ChatConversation.load(store);
    if (!saved) return;
    this.session = saved.session;
    this.owner = saved.owner;
    const turns = saved.turns;
    const last = turns[turns.length - 1];
    if (saved.busy && last?.role === 'assistant' && !last.error) turns[turns.length - 1] = { ...last, error: INTERRUPTED_NOTE, code: 'interrupted' };
    this.state = { turns, busy: false };
    this.ids = Math.max(0, ...turns.map((t) => t.id));
    this.restored = turns.length > 0;
  }

  private static load(store: ChatStore | null): { session: string; turns: Turn[]; busy: boolean; owner: string } | null {
    try {
      const raw: unknown = JSON.parse(store?.getItem(CHAT_STORAGE_KEY) ?? 'null');
      if (!raw || typeof raw !== 'object') return null;
      const { session, turns, busy, owner } = raw as Record<string, unknown>;
      if (typeof session !== 'string' || !session.startsWith('chat-') || !Array.isArray(turns)) return null;
      const valid = turns.filter(
        (t): t is Turn =>
          Boolean(t) && typeof t === 'object' && typeof t.id === 'number' && (t.role === 'user' || t.role === 'assistant') &&
          typeof t.text === 'string' && Array.isArray(t.tools),
      );
      return { session, turns: valid, busy: busy === true, owner: typeof owner === 'string' ? owner : '' };
    } catch {
      return null; // a damaged entry starts a new conversation
    }
  }

  private save(): void {
    try {
      this.store?.setItem(
        CHAT_STORAGE_KEY,
        JSON.stringify({ session: this.session, turns: this.state.turns, busy: this.state.busy, owner: this.owner }),
      );
    } catch {
      // Storage full or refused (private mode): the conversation still works, it just does not survive a reload.
    }
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly snapshot = (): ConversationState => this.state;

  get sessionId(): string {
    return this.session;
  }

  /** Adds the user's turn and an empty assistant turn that the request's frames fill in. */
  begin(text: string): ChatRequest {
    const ctrl = new AbortController();
    this.inflight = ctrl;
    const live = () => this.inflight === ctrl;
    const user: Turn = { id: ++this.ids, role: 'user', text, tools: [] };
    this.set({ busy: true, turns: [...this.state.turns, user, { id: ++this.ids, role: 'assistant', text: '', tools: [] }] });
    return {
      signal: ctrl.signal,
      update: (f) => {
        if (!live()) return;
        if (f.data && !f.tool && !f.error) {
          this.pending += f.data;
          if (!this.flushScheduled) {
            this.flushScheduled = true;
            nextFrame(() => {
              this.flushScheduled = false;
              if (live()) this.flush();
            });
          }
          return;
        }
        this.flush();
        this.applyLast(f);
      },
      done: () => {
        if (!live()) return;
        this.flush();
        this.inflight = null;
        this.set({ busy: false });
      },
    };
  }

  /**
   * The signed-in user. A conversation kept in the tab by someone else (or by nobody known) is not theirs:
   * it is replaced by an empty one before they can read it.
   */
  claim(owner: string): void {
    if (!owner || owner === this.owner) return;
    // Someone else's: another owner, or turns kept from before with no owner. Turns started on this page before
    // /api/me answered are the signed-in user's own.
    const someoneElse = this.owner !== '' || this.restored;
    this.owner = owner;
    if (someoneElse) this.restart();
    else this.save();
  }

  /** Stops the running request, if any, and starts an empty conversation with a new session. */
  restart(): void {
    this.abort();
    this.pending = '';
    this.session = newSessionId();
    this.set({ turns: [], busy: false });
  }

  /**
   * The user stopped the running answer: it keeps what arrived so far, marked as stopped. Only the
   * browser's stream is aborted - the runtime is not told, so it may finish the turn and keep the
   * whole answer in the session's memory. The note says so, because the next question is answered
   * with that memory in view.
   */
  stop(): void {
    if (!this.inflight) return;
    this.flush();
    const message = 'Stopped. The assistant may still have finished this answer, and it remembers it for the next question.';
    this.applyLast({ error: 'stopped', message });
    this.abort();
  }

  /** Stops the running request without clearing the conversation. */
  abort(): void {
    this.inflight?.abort();
    this.inflight = null;
    if (this.state.busy) this.set({ busy: false });
  }

  /** Shows the streamed text that is waiting for the next frame. */
  private flush(): void {
    if (!this.pending) return;
    const data = this.pending;
    this.pending = '';
    this.applyLast({ data });
  }

  private applyLast(f: ChatFrame): void {
    const turns = this.state.turns;
    this.set({ turns: turns.map((turn, i) => (i === turns.length - 1 ? ChatConversation.apply(turn, f) : turn)) });
  }

  private static apply(turn: Turn, f: ChatFrame): Turn {
    const next = { ...turn };
    if (f.data) next.text += f.data;
    if (f.tool) next.tools = [...next.tools, f.tool];
    if (f.error) {
      next.error = f.message ?? f.error;
      next.code = f.error;
    }
    return next;
  }

  private set(next: Partial<ConversationState>): void {
    this.state = { ...this.state, ...next };
    this.save();
    for (const listener of this.listeners) listener();
  }
}
