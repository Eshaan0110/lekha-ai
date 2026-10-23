import { useEffect, useRef, useState } from "react";
import { type CallView, type EscalationItem, type OutboxMessage, ApiError, api } from "../api";

interface Line {
  speaker: "caller" | "lekha";
  text: string;
}

const STATE_LABEL: Record<CallView["state"], string> = {
  awaiting_id: "waiting for employee number",
  awaiting_code: "waiting for one-time code",
  ready: "verified",
  describing_issue: "describing an issue",
  confirming_ticket: "confirming a ticket",
  escalated: "handed to a person",
  ended: "ended",
};

const KIND_LABEL: Record<OutboxMessage["kind"], string> = {
  verification_code: "Code",
  answer: "Answer",
  ticket: "Ticket",
};

// Synthetic examples (apps/scripts/seed_dev_data.py: Asha is MG-10001).
const EXAMPLES = [
  "MG-10001",
  "ನನ್ನ ಉದ್ಯೋಗಿ ಸಂಖ್ಯೆ ೧೦೦೦೧",
  "How many days of paid annual leave do I get?",
  "ಪ್ರತಿ ವರ್ಷ ಎಷ್ಟು ದಿನಗಳ ಸಾಂದರ್ಭಿಕ ರಜೆ ಸಿಗುತ್ತದೆ?",
  "Log a complaint: the canteen fan is broken",
  "yes",
  "ticket status",
  "I want to talk to a person",
];

/** The SIMULATED phone line: typed words travel as fake audio through the speech
 * interfaces, exactly where a real call's audio would. Nothing is ever sent. */
export function PhonePage({ user }: { user: string }) {
  const [callerNumber, setCallerNumber] = useState("+919999900000");
  const [call, setCall] = useState<CallView | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outbox, setOutbox] = useState<OutboxMessage[]>([]);
  const [queue, setQueue] = useState<EscalationItem[]>([]);
  const end = useRef<HTMLDivElement>(null);

  const refreshSide = () => {
    api.outbox(user).then(setOutbox).catch(() => undefined);
    api.escalations(user).then(setQueue).catch(() => undefined);
  };
  useEffect(() => {
    refreshSide(); // reload only when the user changes
  }, [user]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [lines]);

  const open = call !== null && call.state !== "escalated" && call.state !== "ended";

  const run = async (action: () => Promise<CallView>, said?: string) => {
    setBusy(true);
    setError(null);
    if (said) setLines((all) => [...all, { speaker: "caller", text: said }]);
    try {
      const next = await action();
      setCall(next);
      setLines((all) => [...all, ...next.replies.map((text) => ({ speaker: "lekha" as const, text }))]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The call failed.");
    } finally {
      setBusy(false);
      refreshSide();
    }
  };

  const dial = () => {
    setLines([]);
    void run(() => api.startCall(user, callerNumber));
  };

  const send = (text: string) => {
    if (!call || !text.trim()) return;
    setWords("");
    void run(() => api.speak(user, call.id, text), text);
  };

  return (
    <div className="phone-layout">
      <section className="phone">
        <h1>Phone line</h1>
        <p className="muted small">
          Simulation: typed words stand in for speech, and SMS goes to the outbox on the right.
          Callers prove who they are with their HR number and the one-time code sent to the phone
          registered with HR. The calling number is never trusted.
        </p>
        <div className="phone-dial">
          <label className="small">
            Calling from (untrusted)
            <input
              className="inline-input"
              value={callerNumber}
              onChange={(e) => setCallerNumber(e.target.value)}
              disabled={open}
            />
          </label>
          {open && call ? (
            <button className="pill" disabled={busy} onClick={() => void run(() => api.hangUp(user, call.id))}>
              Hang up
            </button>
          ) : (
            <button className="primary" disabled={busy} onClick={dial}>
              Call Lekha
            </button>
          )}
        </div>
        {call && (
          <p className="small">
            <span className={open ? "tag" : "muted"}>{STATE_LABEL[call.state]}</span>
            {call.language && <span className="muted"> · language {call.language}</span>}
          </p>
        )}
        <div className="phone-lines" aria-live="polite">
          {lines.map((line, i) => (
            <div key={i} className={line.speaker === "lekha" ? "bubble lekha" : "bubble caller"}>
              {line.text}
            </div>
          ))}
          {busy && <div className="bubble lekha muted">…</div>}
          <div ref={end} />
        </div>
        {error && <p className="error">{error}</p>}
        {open && (
          <>
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                send(words);
              }}
            >
              <input
                className="inline-input"
                placeholder="Say something…"
                value={words}
                onChange={(e) => setWords(e.target.value)}
                maxLength={1000}
                disabled={busy}
              />
              <button className="primary" type="submit" disabled={busy || !words.trim()}>
                Say
              </button>
            </form>
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button key={example} className="chip" disabled={busy} onClick={() => send(example)}>
                  {example}
                </button>
              ))}
            </div>
          </>
        )}
      </section>
      <aside className="phone-side">
        <section>
          <h2>SMS outbox (never sent)</h2>
          {outbox.length === 0 && <p className="muted small">Empty.</p>}
          <ul className="doc-list">
            {outbox.slice(0, 12).map((m, i) => (
              <li key={`${m.at}-${i}`}>
                <div className="request-body">
                  <span className="small">
                    <span className="tag">{KIND_LABEL[m.kind]}</span> to {m.phone}
                  </span>
                  <span className="small sms">{m.body}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h2>Hand-over queue</h2>
          {queue.length === 0 && <p className="muted small">No calls waiting for a person.</p>}
          <ul className="doc-list">
            {queue.slice(0, 12).map((e) => (
              <li key={e.call_id}>
                <span className="small">{e.reason.replaceAll("_", " ")}</span>
                <span className="muted small">{new Date(e.at).toLocaleTimeString()}</span>
              </li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  );
}
