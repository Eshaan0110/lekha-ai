import { type FormEvent, useEffect, useRef, useState } from "react";
import { type AskResult, ApiError, api } from "../api";
import { explainAbstention, formatMs, splitAnswer } from "../answer";
import { SourcePanel } from "./SourcePanel";

const EXAMPLES: Record<string, string[]> = {
  asha: [
    "How many days of paid annual leave do I get?",
    "What is the hotel limit per night in metro cities?",
    "ಪ್ರತಿ ವರ್ಷ ಎಷ್ಟು ದಿನಗಳ ಸಾಂದರ್ಭಿಕ ರಜೆ ಸಿಗುತ್ತದೆ?",
    "What is the Grade 7 salary band?",
    "Is there a gym in the office?",
  ],
  meera: ["What is the Grade 7 salary band?", "What is the vendor arbitration case number?"],
  ravi: [
    "What shift allowance do night shift workers get?",
    "How many days of paid annual leave do I get?",
  ],
};

const STAGES: Record<string, string> = {
  received: "Question received",
  searching: "Searching documents you can read",
  sources: "Reading the best matches",
  writing: "Writing an answer from those sources",
  checking: "Checking every quote against its source",
};

interface Turn {
  id: number;
  question: string;
  stage: string;
  sources: number | null;
  started: number;
  result?: AskResult;
  error?: string;
}

export function AskPage({ user }: { user: string }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<{ chunkId: string; span: string } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // A block body on purpose: newer browsers return a Promise from scrollIntoView, and an
    // effect must return nothing (or a clean-up function).
    void endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const update = (id: number, change: Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...change } : t)));

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const id = Date.now();
    setTurns((all) => [...all, { id, question: trimmed, stage: "received", sources: null, started: Date.now() }]);
    setQuestion("");
    setBusy(true);
    try {
      const result = await api.ask(user, trimmed, (stage, data) =>
        update(id, { stage, ...(typeof data.count === "number" ? { sources: data.count } : {}) }),
      );
      update(id, { result });
    } catch (error) {
      const message =
        error instanceof ApiError && error.code === "rate_limited"
          ? "Too many questions this minute. Please wait a moment."
          : error instanceof ApiError && error.code === "upstream_unavailable"
            ? "The answer model is unavailable right now."
            : "Something went wrong. Please try again.";
      update(id, { error: message });
    } finally {
      setBusy(false);
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void ask(question);
  };

  return (
    <div className="ask-layout">
      <section className="conversation" aria-live="polite">
        {turns.length === 0 && (
          <div className="empty">
            <h1>Ask about company policies</h1>
            <p className="muted">
              Answers come only from documents you're allowed to read, with every quote checked.
              If the documents don't say, Lekha says so.
            </p>
            <div className="examples">
              {(EXAMPLES[user] ?? EXAMPLES.asha ?? []).map((example) => (
                <button key={example} className="chip" onClick={() => void ask(example)}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((turn) => (
          <article key={turn.id} className="turn">
            <p className="question">{turn.question}</p>
            {turn.result ? (
              <Answer result={turn.result} user={user} onOpen={(chunkId, span) => setOpen({ chunkId, span })} />
            ) : turn.error ? (
              <p className="error">{turn.error}</p>
            ) : (
              <Progress turn={turn} />
            )}
          </article>
        ))}
        <div ref={endRef} />
      </section>
      <form className="composer" onSubmit={submit}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask in English or Kannada…"
          maxLength={1000}
          aria-label="Your question"
        />
        <button className="primary" disabled={busy || !question.trim()}>
          {busy ? "Working…" : "Ask"}
        </button>
      </form>
      {open && <SourcePanel user={user} chunkId={open.chunkId} span={open.span} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Progress({ turn }: { turn: Turn }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.floor((Date.now() - turn.started) / 1000);
  const order = Object.keys(STAGES);
  const current = order.indexOf(turn.stage);
  return (
    <div className="progress">
      <ol>
        {order.map((stage, index) => (
          <li key={stage} className={index < current ? "done" : index === current ? "now" : "later"}>
            {STAGES[stage]}
            {stage === "sources" && turn.sources !== null && index <= current ? ` (${turn.sources})` : ""}
          </li>
        ))}
      </ol>
      <span className="muted small">{seconds}s · the local model takes about 20–30 seconds</span>
    </div>
  );
}

function Answer({
  result,
  user,
  onOpen,
}: {
  result: AskResult;
  user: string;
  onOpen: (chunkId: string, span: string) => void;
}) {
  const [rated, setRated] = useState<boolean | null>(null);
  const byNumber = new Map(result.citations.map((c) => [c.number, c]));

  const rate = async (helpful: boolean) => {
    setRated(helpful);
    try {
      await api.feedback(user, result.query_id, helpful);
    } catch {
      setRated(null);
    }
  };

  if (result.abstained) {
    return (
      <div className="answer abstained">
        <p>{result.answer}</p>
        <p className="muted small">{explainAbstention(result.abstention_reason)}</p>
        <LogRequest user={user} queryId={result.query_id} />
      </div>
    );
  }
  return (
    <div className="answer">
      <p className="answer-text">
        {splitAnswer(result.answer, new Set(byNumber.keys())).map((part, i) =>
          part.kind === "text" ? (
            <span key={i}>{part.text}</span>
          ) : (
            <button
              key={i}
              className="cite"
              title="Open source"
              onClick={() => {
                const c = byNumber.get(part.number);
                if (c) onOpen(c.chunk_id, c.span);
              }}
            >
              {part.number}
            </button>
          ),
        )}
      </p>
      <ul className="sources">
        {result.citations.map((c) => (
          <li key={c.number}>
            <button className="source" onClick={() => onOpen(c.chunk_id, c.span)}>
              <span className="cite static">{c.number}</span>
              <span className="source-text">
                <span className="source-title">
                  {c.kind === "meeting" && <span className="tag">Meeting</span>} {c.title}{" "}
                  <span className="version">v{c.version_number}</span>
                  {c.kind === "meeting" && c.start_ms !== null && (
                    <span className="muted small"> · at {formatMs(c.start_ms)}</span>
                  )}
                </span>
                <span className="quote">“{c.span}”</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {result.also_searched.length > 0 && (
        <p className="muted small also">Also searched for: {result.also_searched.join(", ")}</p>
      )}
      <div className="feedback">
        <span className="muted small">Was this helpful?</span>
        <button className={rated === true ? "pill on" : "pill"} onClick={() => void rate(true)}>
          Yes
        </button>
        <button className={rated === false ? "pill on" : "pill"} onClick={() => void rate(false)}>
          No
        </button>
        {result.model && <span className="muted small model">{result.model}</span>}
      </div>
    </div>
  );
}

/** "Would you like me to log it as a request?" (plan): only if the person chooses to. */
function LogRequest({ user, queryId }: { user: string; queryId: string }) {
  const [state, setState] = useState<"idle" | "sent" | "failed">("idle");
  const send = async () => {
    try {
      const me = await api.me(user);
      const unit = me.business_units.find((bu) => bu.roles.length > 0);
      if (!unit) throw new Error("no unit");
      await api.logRequest(user, queryId, unit.business_unit_id, "");
      setState("sent");
    } catch {
      setState("failed");
    }
  };
  if (state === "sent") return <p className="muted small">Sent to your curators. You'll see the outcome under Requests.</p>;
  return (
    <div className="feedback">
      <button className="pill" onClick={() => void send()}>
        Send this question to the curators
      </button>
      {state === "failed" && <span className="error small">Couldn't send it.</span>}
    </div>
  );
}
