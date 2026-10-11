"use client";

import { useEffect, useRef, useState } from "react";

import { Icon, Notice, PageTitle, useAction } from "@/components/ui";
import { api, type ChatReply } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Message = { from: "you" | "agent"; text: string };

const starters = ["What orders came in today?", "Which products are low on stock?", "Summarise this week's sales"];

export default function ChatPage() {
  const { user } = useAuth();
  const { busy, error, run } = useAction();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLLIElement>(null);

  // Keep the newest message in view. The braces matter: newer browsers return
  // a promise from scrollIntoView, and React would call it as a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [messages, busy]);

  const send = (text: string) => {
    if (!text || busy) return;
    setMessages((all) => [...all, { from: "you", text }]);
    setDraft("");
    void run(async () => {
      const { reply } = await api<ChatReply>("/chat/message", { body: { message: text } });
      setMessages((all) => [...all, { from: "agent", text: reply }]);
    });
  };

  return (
    <>
      <PageTitle title="Chat">
        {user?.role === "admin"
          ? "Ask the agent about any store."
          : "Ask the agent about your stores. It can only see the stores you work in."}
      </PageTitle>

      <div className="flex h-[calc(100dvh-21rem)] min-h-80 max-w-3xl flex-col rounded-2xl border border-line bg-white lg:h-[calc(100dvh-15rem)]">
        <ol aria-live="polite" className="flex flex-1 flex-col gap-3 overflow-y-auto p-5">
          {messages.length === 0 && (
            <li className="m-auto max-w-sm text-center">
              <p className="font-display text-xl font-semibold">What do you want to know?</p>
              <p className="mt-1 text-ink-soft">Ask about orders, products or stock, or start with one of these.</p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {starters.map((starter) => (
                  <button
                    key={starter}
                    type="button"
                    onClick={() => send(starter)}
                    className="rounded-full border border-line px-3.5 py-1.5 text-sm transition-colors hover:border-accent hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </li>
          )}
          {messages.map((message, index) => (
            <li
              key={index}
              className={`arrive max-w-[85%] rounded-2xl px-4 py-2.5 ${message.from === "you" ? "self-end rounded-br-md bg-accent text-white" : "self-start rounded-bl-md bg-canvas"}`}
            >
              <span className="sr-only">{message.from === "you" ? "You: " : "Agent: "}</span>
              {message.text}
            </li>
          ))}
          {busy && (
            <li className="typing self-start rounded-2xl rounded-bl-md bg-canvas px-4 py-3">
              <span />
              <span />
              <span />
              <p className="sr-only">The agent is writing</p>
            </li>
          )}
          <li ref={end} aria-hidden="true" />
        </ol>

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
            placeholder="Ask about your stores. Enter sends, Shift+Enter adds a line."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send(draft.trim());
              }
            }}
            className="block max-h-40 min-h-11 flex-1 resize-none rounded-xl bg-canvas px-3.5 py-2.5 placeholder:text-ink-soft/70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          />
          <button
            type="submit"
            aria-label="Send"
            disabled={busy || !draft.trim()}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-accent text-white transition hover:bg-accent-deep active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <Icon name="send" />
          </button>
        </form>
      </div>
      {error && (
        <div className="mt-4 max-w-3xl">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
    </>
  );
}
