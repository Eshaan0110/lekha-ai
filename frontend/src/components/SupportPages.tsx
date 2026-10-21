import { useEffect, useState } from "react";
import { type AuditEvent, type QuestionRequest, ApiError, api } from "../api";

/** Curators see their units' requests; everyone sees the ones they logged. */
export function RequestsPage({ user }: { user: string }) {
  const [items, setItems] = useState<QuestionRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const load = () => {
    api.requests(user).then(setItems).catch(() => setError("Couldn't load requests."));
  };
  useEffect(() => {
    load(); // reload only when the user changes
  }, [user]);

  const close = async (id: string, status: "resolved" | "dismissed") => {
    try {
      await api.resolveRequest(user, id, status, notes[id] ?? "");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't work.");
    }
  };

  return (
    <div className="docs">
      <section>
        <h1>Requests</h1>
        <p className="muted">
          Questions Lekha couldn't answer, which people chose to send to their curators. Fix the
          gap (for example, upload the missing policy), then mark the request resolved.
        </p>
        {error && <p className="error">{error}</p>}
        {items && items.length === 0 && <p className="muted">No requests.</p>}
        {items && items.length > 0 && (
          <ul className="doc-list requests">
            {items.map((r) => (
              <li key={r.id}>
                <div className="request-body">
                  <strong>“{r.question}”</strong>
                  <span className="muted small">
                    {r.mine ? "You" : r.asked_by} · {new Date(r.created_at).toLocaleString()}
                    {r.note && ` · ${r.note}`}
                  </span>
                  {r.status !== "open" && (
                    <span className="muted small">
                      {r.status === "resolved" ? "Resolved" : "Dismissed"}
                      {r.resolution && `: ${r.resolution}`}
                    </span>
                  )}
                  {r.status === "open" && !r.mine && (
                    <div className="feedback">
                      <input
                        className="inline-input"
                        placeholder="What was done (optional)"
                        value={notes[r.id] ?? ""}
                        onChange={(e) => setNotes((all) => ({ ...all, [r.id]: e.target.value }))}
                        maxLength={500}
                      />
                      <button className="pill on" onClick={() => void close(r.id, "resolved")}>
                        Resolved
                      </button>
                      <button className="pill" onClick={() => void close(r.id, "dismissed")}>
                        Dismiss
                      </button>
                    </div>
                  )}
                </div>
                <span className={r.status === "open" ? "tag" : "muted small"}>{r.status}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** The audit trail, for people with the audit_reader role. */
export function AuditPage({ user }: { user: string }) {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = (after: string | null) =>
    api
      .audit(user, after)
      .then((page) => {
        setItems((all) => (after ? [...all, ...page.items] : page.items));
        setCursor(page.next_cursor);
      })
      .catch((e) =>
        setError(e instanceof ApiError && e.status === 403 ? "You need the audit reader role." : "Couldn't load."),
      );
  useEffect(() => {
    void load(null);
  }, [user]);

  return (
    <div className="docs">
      <section>
        <h1>Audit log</h1>
        <p className="muted">
          Every request by people in your business unit, newest first. Entries can't be changed
          or deleted by anyone. Question and document text is never shown here.
        </p>
        {error && <p className="error">{error}</p>}
        {items.length > 0 && (
          <table className="audit">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>What</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={`${e.request_id}-${e.occurred_at}`}>
                  <td>{new Date(e.occurred_at).toLocaleString()}</td>
                  <td>{e.actor ?? "-"}</td>
                  <td>
                    <code>
                      {e.method} {e.path}
                    </code>
                  </td>
                  <td className={e.outcome === "success" ? "" : "error"}>{e.status_code}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {cursor && (
          <button className="pill" onClick={() => void load(cursor)}>
            Load more
          </button>
        )}
      </section>
    </div>
  );
}
