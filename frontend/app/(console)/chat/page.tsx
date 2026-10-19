"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatResults } from "@/components/chat-results";
import { Button, Icon, Notice, PageTitle, formatDateTime, useAction } from "@/components/ui";
import { api, type Chat, type ChatLine, type ChatReply, type ChatResult, type ChatSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useData } from "@/lib/use-data";

const starters = ["What orders came in today?", "Which products are low on stock?", "Summarise this week's sales"];
const nothing: ChatResult = { reply: "", status: "answered", blocks: [], steps: [], pending: [] };
/** Models emphasise with markdown asterisks however they are asked not to; the chat shows plain text. */
const plain = (text: string) => text.replaceAll("**", "");

export default function ChatPage() {
  const { user } = useAuth();
  const { busy, error, run } = useAction();
  // The open chat. One at a time: it stays open across reloads until it is ended, here or by signing out.
  const [messages, setMessages] = useState<ChatLine[]>([]);
  const [result, setResult] = useState<ChatResult>(nothing);
  // An earlier chat being read. It cannot be continued, so it has no input box.
  const [past, setPast] = useState<Chat>();
  const [draft, setDraft] = useState("");
  const { data: history, reload: reloadHistory } = useData<ChatSummary[]>("/chat/history");
  const end = useRef<HTMLLIElement>(null);
  const chatId = useRef<string | null>(null);

  const load = useCallback(async () => {
    const open = await api<Chat>("/chat");
    chatId.current = open.conversation_id;
    setMessages(open.messages);
    setResult(open);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- state is set only after the fetch resolves
    void load().catch(() => undefined); // a failed load leaves an empty chat; sending a message reports the real error
  }, [load]);

  const shown = past ?? { ...result, messages };
  const waiting = !past && result.status === "needs_confirmation";

  // Keep the newest message in view. The braces matter: newer browsers return
  // a promise from scrollIntoView, and React would call it as a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [shown.messages, busy]);

  /** Sends to the agent (a message, or the answer to a confirmation) and shows what comes back. */
  const ask = (path: string, body: unknown) =>
    run(async () => {
      let done: ChatReply;
      try {
        done = await api<ChatReply>(path, { body });
      } catch (error) {
        // The page may be behind the server: another tab answered the confirmation, or a reply got lost on
        // the way. Show the chat as the server has it, then the error.
        await load().catch(() => undefined);
        throw error;
      }
      // The chat on screen ran out (idle too long) and this message opened a new one: start the page over from it.
      const renewed = chatId.current !== null && chatId.current !== done.conversation_id;
      chatId.current = done.conversation_id;
      setResult(done);
      setMessages((all) => [...(renewed ? all.slice(-1) : all), { from: "agent", text: done.reply }]);
      if (renewed) void reloadHistory();
    });

  const send = (text: string) => {
    if (!text || busy || waiting) return;
    setMessages((all) => [...all, { from: "you", text }]);
    setDraft("");
    void ask("/chat/message", { message: text });
  };

  const endChat = () => {
    if (!window.confirm("End this chat? You can still read it afterwards, but not continue it.")) return;
    void run(async () => {
      await api("/chat/end", { method: "POST" });
      chatId.current = null;
      setMessages([]);
      setResult(nothing);
      await reloadHistory();
    });
  };

  return (
    <>
      <PageTitle title="Ask your stores.">
        {user?.role === "admin"
          ? "Ask the agent about any store."
          : "Ask the agent about your stores. It can only see the stores you work in."}
      </PageTitle>

      {/* Results on the left, the chat on the right. On a narrow screen the chat comes first. */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section aria-label="Results" className="frame cut order-2 flex min-h-80 flex-col lg:order-1 lg:h-[calc(100dvh-21rem)]">
          {/* Scrolls inside the frame, so the frame's cut corner stays put. */}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-5">
            <ChatResults blocks={shown.blocks} steps={shown.steps} />
          </div>
        </section>

        <section
          aria-label="Conversation"
          className="frame cut order-1 flex h-[calc(100dvh-27rem)] min-h-80 flex-col lg:order-2 lg:h-[calc(100dvh-21rem)]"
        >
          <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
            {past ? (
              <>
                <p className="text-sm text-ink-soft">Earlier chat, read only</p>
                <Button variant="quiet" className="px-3.5 py-1.5" onClick={() => setPast(undefined)}>
                  Back
                </Button>
              </>
            ) : (
              <>
                <label className="min-w-0 text-sm">
                  <span className="sr-only">Earlier chats</span>
                  <select
                    value=""
                    disabled={busy || !history?.length}
                    onChange={(event) => {
                      const id = event.target.value;
                      void run(async () => setPast(await api<Chat>(`/chat/history/${id}`)));
                    }}
                    className="max-w-full truncate bg-transparent py-1.5 text-ink-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    <option value="">{history?.length ? "Earlier chats" : "No earlier chats"}</option>
                    {history?.map((chat) => (
                      <option key={chat.conversation_id} value={chat.conversation_id}>
                        {formatDateTime(chat.last_used_at)}: {chat.title}
                      </option>
                    ))}
                  </select>
                </label>
                <Button variant="quiet" className="shrink-0 px-3.5 py-1.5" disabled={busy || messages.length === 0} onClick={endChat}>
                  End chat
                </Button>
              </>
            )}
          </header>

          <ol aria-live="polite" className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
            {shown.messages.length === 0 && (
              <li className="m-auto max-w-sm text-center">
                <p className="font-display text-2xl font-light">What do you want to know?</p>
                <p className="mt-1 text-ink-soft">Ask about orders, products or stock, or start with one of these.</p>
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  {starters.map((starter) => (
                    <button
                      key={starter}
                      type="button"
                      onClick={() => send(starter)}
                      className="border border-edge px-3.5 py-1.5 text-sm transition-colors hover:bg-ink hover:text-canvas focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </li>
            )}
            {shown.messages.map((message, index) => (
              <li
                key={index}
                className={`arrive max-w-[85%] px-4 py-2.5 whitespace-pre-wrap ${message.from === "you" ? "self-end bg-ink text-canvas" : "self-start bg-accent-wash"}`}
              >
                <span className="sr-only">{message.from === "you" ? "You: " : "Agent: "}</span>
                {message.from === "agent" ? plain(message.text) : message.text}
              </li>
            ))}
            {busy && (
              <li className="typing self-start bg-accent-wash px-4 py-3">
                <span />
                <span />
                <span />
                <p className="sr-only">The agent is working</p>
              </li>
            )}
            {waiting && !busy && (
              <li className="arrive self-stretch border-l-2 border-brass bg-brass-wash p-4">
                <p className="text-sm font-medium">The agent wants to make {result.pending.length === 1 ? "this change" : "these changes"}:</p>
                <ul className="mt-2 list-inside list-disc space-y-1 text-sm">
                  {result.pending.map((change) => (
                    <li key={change.call_id}>
                      {change.summary} <span className="text-ink-soft">({change.server})</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 flex gap-2">
                  <Button onClick={() => void ask("/chat/confirm", { approve: true })}>Approve</Button>
                  <Button variant="quiet" onClick={() => void ask("/chat/confirm", { approve: false })}>
                    Decline
                  </Button>
                </div>
              </li>
            )}
            <li ref={end} aria-hidden="true" />
          </ol>

          {!past && (
            <form
              className="flex items-end gap-2 border-t border-line p-3"
              onSubmit={(event) => {
                event.preventDefault();
                send(draft.trim());
              }}
            >
              <label htmlFor="message" className="sr-only">
                Message
              </label>
              <textarea
                id="message"
                rows={1}
                disabled={waiting}
                placeholder={waiting ? "Approve or decline first." : "Ask about your stores"}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    send(draft.trim());
                  }
                }}
                className="block max-h-40 min-h-11 flex-1 resize-none border-b border-ink-soft/50 bg-transparent px-1 py-2.5 placeholder:text-ink-soft/70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60"
              />
              <button
                type="submit"
                aria-label="Send"
                disabled={busy || waiting || !draft.trim()}
                className="cut inline-flex size-11 shrink-0 items-center justify-center bg-ink text-canvas transition [--cut:9px] hover:bg-accent-deep active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-canvas"
              >
                <Icon name="send" />
              </button>
            </form>
          )}
        </section>
      </div>
      {error && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
    </>
  );
}
