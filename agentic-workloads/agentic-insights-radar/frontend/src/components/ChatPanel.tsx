// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard';
import Modal from '@cloudscape-design/components/modal';
import PromptInput, { type PromptInputProps } from '@cloudscape-design/components/prompt-input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';

import { useSession } from '@/auth/session';
import { joinWords, KIND_LABELS } from '@/lib/format';
import { listVisible, sectionVisible } from '@/lib/profile';

import { ask, ChatIdleTimeout } from './chat';
import { ChatConversation } from './conversation';
import Markdown from './Markdown';

const TOOL_LABELS: Record<string, string> = {
  search_graph: 'Searching the knowledge graph',
  get_node: 'Reading an entry',
  node_neighbors: 'Following links',
  list_insights: 'Listing insights',
  insight_detail: 'Reading evidence',
  list_radars: 'Listing radars',
  get_radar: 'Reading a radar',
  list_products: 'Listing products',
  ask_product: 'Searching product knowledge',
  list_modules: 'Listing modules',
};

/** How long a chat request may go without sending anything before it is given up. */
export const CHAT_IDLE_MS = 60_000;

/** The longest question the runtime accepts (chat_runtime.py): checked here, not after a round trip. */
export const CHAT_MAX_CHARS = 8000;
/** The length from which the counter is shown, so it is there before the limit is reached. */
export const CHAT_COUNT_FROM = 6000;

/**
 * Keeps a render error in the chat (a Markdown edge case in a streamed answer) out of the app's
 * top-level boundary, which would otherwise replace every page, navigation included. Trying again
 * re-rendered the same conversation, which crashed again: the way out is the conversation as plain
 * text, or a new one (r3 F3-032), without reloading the page and its drafts.
 */
class ChatBoundary extends React.Component<{ conversation: ChatConversation; children: (plain: boolean) => React.ReactNode }, { failed: boolean; plain: boolean }> {
  override state = { failed: false, plain: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override render() {
    if (this.state.failed) {
      return (
        <Box padding="m">
          <SpaceBetween size="s">
            <Box color="text-status-error">The assistant stopped working because of a problem showing its answer.</Box>
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => this.setState({ failed: false, plain: true })}>Show the answers as plain text</Button>
              <Button
                onClick={() => {
                  this.props.conversation.restart();
                  this.setState({ failed: false, plain: false });
                }}
              >
                Start a new conversation
              </Button>
            </SpaceBetween>
          </SpaceBetween>
        </Box>
      );
    }
    return this.props.children(this.state.plain);
  }
}

export default function ChatPanel(props: Omit<React.ComponentProps<typeof Conversation>, 'plain'>) {
  const [own] = React.useState(() => new ChatConversation());
  const chat = props.conversation ?? own;
  // A conversation the panel owns dies with it, so its running request is aborted on unmount.
  React.useEffect(() => () => {
    if (!props.conversation) own.abort();
  }, [props.conversation, own]);
  return <ChatBoundary conversation={chat}>{(plain) => <Conversation {...props} conversation={chat} plain={plain} />}</ChatBoundary>;
}

/** Relative in-app links made absolute, so a copied answer still leads somewhere when pasted (r3 F3-104). */
export function absoluteLinks(markdown: string, origin: string): string {
  return markdown.replace(/\]\((\/[^)\s]*)\)/g, `](${origin}$1)`).replace(/href="(\/[^"]*)"/g, `href="${origin}$1"`);
}

/** Today's date in UTC, the day the chat budget is counted in. */
const utcDay = () => new Date().toISOString().slice(0, 10);

function Conversation({
  fetcher,
  idleMs = CHAT_IDLE_MS,
  conversation,
  plain = false,
}: {
  fetcher?: typeof fetch;
  idleMs?: number;
  /** Held by the caller so the conversation outlives the panel. Without one the panel keeps its own. */
  conversation?: ChatConversation;
  /** Answers as plain text, after one of them crashed the Markdown renderer. */
  plain?: boolean;
}) {
  const { config, idToken, expire, refreshConfig } = useSession();
  const chat = conversation as ChatConversation; // ChatPanel always passes one
  const { turns, busy } = React.useSyncExternalStore(chat.subscribe, chat.snapshot);
  const [prompt, setPrompt] = React.useState('');
  const [confirmRestart, setConfirmRestart] = React.useState(false);
  /** The UTC day the runtime said the chat budget was used up: every question was refused after a round trip (r3 F3-083). */
  const [spentOn, setSpentOn] = React.useState<string | null>(null);
  const end = React.useRef<HTMLDivElement>(null);
  const input = React.useRef<PromptInputProps.Ref>(null);
  const paused = !config.genai_enabled;
  const spent = spentOn === utcDay();
  const tooLong = prompt.trim().length > CHAT_MAX_CHARS;

  // Opening the drawer puts the keyboard in the prompt: it used to stay on Ask, many tabs away (r3 F3-070).
  React.useEffect(() => {
    input.current?.focus();
  }, []);

  React.useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect's return
    // value is taken as its cleanup function.
    end.current?.scrollIntoView?.({ block: 'end' });
  }, [turns]);

  const send = async () => {
    const text = prompt.trim();
    // The length is checked here: a pasted log used to be sent, refused by the runtime, and lost.
    if (!text || busy || tooLong) return;
    const token = idToken();
    if (!token) {
      expire(); // the prompt stays, so nothing typed is lost while the user signs in again
      return;
    }
    setPrompt('');
    const request = chat.begin(text);
    // A turn that ends in an error without a word of answer gives the question back to the input.
    let answered = false;
    let failed = false;
    const update = (frame: Parameters<typeof request.update>[0]) => {
      if (frame.data) answered = true;
      if (frame.error) failed = true;
      if (frame.error === 'budget_exhausted') setSpentOn(utcDay());
      request.update(frame);
    };
    try {
      await ask(config, token, text, chat.sessionId, update, fetcher, request.signal, idleMs);
    } catch (err) {
      failed = true;
      update(
        err instanceof ChatIdleTimeout
          ? { error: 'timeout', message: 'The answer stopped arriving, so it was cancelled. Try again, or start a new conversation.' }
          : { error: 'network', message: 'Chat could not be reached. Check your connection and try again.' },
      );
    } finally {
      request.done();
      // Put the question back only into an empty input: the input stays usable while an answer
      // streams, and the next question may already be half typed. Not after Stop or New
      // conversation: the user ended it, and the old question appeared in the new conversation (r3 F3-085).
      if (failed && !answered && !request.signal.aborted) setPrompt((typed) => typed || text);
    }
  };

  // Focus can still be elsewhere when an answer ends (the drawer was closed and reopened, say):
  // bring it back to the input rather than leaving a keyboard user on the page body.
  const wasBusy = React.useRef(busy);
  React.useEffect(() => {
    if (wasBusy.current && !busy && document.activeElement === document.body) input.current?.focus();
    wasBusy.current = busy;
  }, [busy]);

  // Screen readers hear the finished answer once, not every streamed chunk.
  const last = turns[turns.length - 1];
  // The runtime knows the kill switch was flipped before this tab does: re-read /api/config so the
  // banner and every AI button in the app agree with the answer the user just got.
  const runtimePaused = last?.code === 'genai_disabled';
  React.useEffect(() => {
    if (runtimePaused) refreshConfig();
  }, [runtimePaused, refreshConfig]);
  // "Answer ready" only: reading 3,000 characters of raw Markdown out loud - asterisks, pipes, link
  // syntax - is worse than silence. The rendered answer is there to be read.
  const announcement = busy ? 'Answering' : last?.role === 'assistant' ? (last.error ? 'The answer ended early. The reason is shown in the conversation.' : 'Answer ready.') : '';

  return (
    <div className="air-chat">
      <div className="air-sr-only" aria-live="polite">{announcement}</div>
      <div className="air-chat-log">
        {turns.length === 0 && (
          <Box color="text-body-secondary" padding="s">
            {`Ask about ${joinWords([...['signals', 'trends', 'gaps'].filter((k) => listVisible(k)).map((k) => (KIND_LABELS[k] as string).toLowerCase()), ...(sectionVisible('radars') ? ['radars'] : []), 'the knowledge graph'])}. Answers cite the entries they use.`}
          </Box>
        )}
        {turns.map((t, i) => (
          <div key={t.id} className={`air-turn air-${t.role}`}>
            {t.role === 'user' ? (
              <>
                <Box>{t.text}</Box>
                <CopyToClipboard
                  variant="icon"
                  textToCopy={t.text}
                  copyButtonAriaLabel="Copy the question"
                  copySuccessText="Question copied"
                  copyErrorText="The question could not be copied"
                />
              </>
            ) : (
              <>
                {t.tools.length > 0 && (
                  <Box fontSize="body-s" color="text-body-secondary">
                    {[...new Set(t.tools)].map((x) => TOOL_LABELS[x] ?? x).join(' \u00b7 ')}
                  </Box>
                )}
                {t.text ? (
                  plain ? <div className="air-pre">{t.text}</div> : <Markdown markdown={t.text} />
                ) : !t.error && busy && i === turns.length - 1 ? (
                  <Box color="text-body-secondary">{"Thinking\u2026"}</Box>
                ) : null}
                {t.error && <Box color={t.code === 'stopped' ? 'text-body-secondary' : 'text-status-error'}>{t.error}</Box>}
                {/* The client's own code for a 401/403, and the runtime's own frame when it finds no
                    subject in the token: both mean the same thing to the reader. */}
                {/* A 403 is the runtime refusing a session the rest of the app accepts: signing in again
                    does not help, so it is not offered there (r3 F3-082). */}
                {(t.code === 'auth' || t.code === 'unauthorized') && <Button onClick={expire}>Sign in again</Button>}
                {t.text && !(busy && i === turns.length - 1) && (
                  <CopyToClipboard
                    variant="icon"
                    textToCopy={absoluteLinks(t.text, window.location.origin)}
                    copyButtonAriaLabel="Copy the answer"
                    copySuccessText="Answer copied"
                    copyErrorText="The answer could not be copied"
                  />
                )}
              </>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
      <SpaceBetween size="xs">
        {paused && <Box color="text-status-inactive">{`The assistant is paused by an administrator. ${sectionVisible('radars') ? 'Radars, search' : 'Search'} and the knowledge graph still work.`}</Box>}
        {spent && !paused && <Box color="text-status-inactive">Today&apos;s chat budget is used up. The assistant takes questions again after midnight UTC.</Box>}
        {/* Enabled while an answer streams: disabling it threw the keyboard focus out of the drawer,
            and it never came back. Submitting is what is blocked (send() returns early). */}
        <PromptInput
          ref={input}
          value={prompt}
          onChange={({ detail }) => setPrompt(detail.value)}
          onAction={() => void send()}
          actionButtonAriaLabel="Send"
          actionButtonIconName="send"
          placeholder={paused ? 'The assistant is paused' : spent ? 'The chat budget is used up for today' : 'Ask a question'}
          ariaLabel="Ask a question"
          disabled={paused || spent}
          // Enter during an answer did nothing and said nothing (r3 F3-084): the button shows it is waiting.
          disableActionButton={busy}
          invalid={tooLong}
          maxRows={6}
        />
        {busy && prompt.trim() && <Box fontSize="body-s" color="text-body-secondary">Wait for the answer, or Stop it, to send this question.</Box>}
        {(tooLong || prompt.length >= CHAT_COUNT_FROM) && (
          <Box fontSize="body-s" color={tooLong ? 'text-status-error' : 'text-body-secondary'}>
            {tooLong
              ? `${prompt.trim().length} characters: shorten the question to ${CHAT_MAX_CHARS} before sending it.`
              : `${prompt.trim().length} of ${CHAT_MAX_CHARS} characters.`}
          </Box>
        )}
        <SpaceBetween direction="horizontal" size="s">
          {busy && <Button iconName="close" onClick={() => chat.stop()}>Stop</Button>}
          <Button variant="inline-link" disabled={turns.length === 0} onClick={() => setConfirmRestart(true)}>
            New conversation
          </Button>
        </SpaceBetween>
      </SpaceBetween>
      <Modal
        visible={confirmRestart}
        onDismiss={() => setConfirmRestart(false)}
        header="Start a new conversation?"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setConfirmRestart(false)}>Keep this one</Button>
              <Button
                variant="primary"
                onClick={() => {
                  setConfirmRestart(false);
                  chat.restart();
                }}
              >
                Start new
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {busy ? 'The answer that is still arriving is stopped, and this conversation is cleared.' : 'This conversation is cleared and cannot be brought back.'}
      </Modal>
    </div>
  );
}
