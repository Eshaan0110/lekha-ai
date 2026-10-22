import { useEffect, useState } from "react";
import { type Me, type Proposal, type TicketInputs, ApiError, api } from "../api";

const STATUS_LABEL: Record<Proposal["status"], string> = {
  pending_approval: "waiting for approval",
  approved: "approved, creating ticket",
  rejected: "rejected",
  executed: "ticket created",
  failed: "failed",
  expired: "expired",
};

const EMPTY: TicketInputs = { title: "", category: "facilities", priority: "P3", description: "" };

/** Log an issue as a ticket PROPOSAL; a named approver of the unit decides. Nothing is
 * created until then, and tickets go to the simulated ticketing system only. */
export function ActionsPage({ user }: { user: string }) {
  const [me, setMe] = useState<Me | null>(null);
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [draft, setDraft] = useState<TicketInputs>(EMPTY);
  // One key per draft: a double click or a retry returns the same proposal.
  const [draftKey, setDraftKey] = useState(() => crypto.randomUUID());
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const load = () => {
    api.proposals(user).then(setItems).catch(() => setError("Couldn't load issues."));
  };
  useEffect(() => {
    api.me(user).then(setMe).catch(() => setError("Couldn't load your profile."));
    load(); // reload only when the user changes
  }, [user]);

  // Refresh while a ticket is being created by the worker.
  useEffect(() => {
    if (!items?.some((p) => p.status === "approved")) return;
    const timer = setTimeout(load, 2000);
    return () => clearTimeout(timer);
  }, [items]);

  const unit = me?.business_units.find((bu) => bu.roles.length > 0);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!unit) return;
    setError(null);
    try {
      await api.proposeTicket(user, unit.business_unit_id, draft, draftKey);
      setDraft(EMPTY);
      setDraftKey(crypto.randomUUID());
      setSent(true);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };

  const decide = async (id: string, approve: boolean) => {
    setError(null);
    try {
      await api.decideProposal(user, id, approve, reasons[id] ?? "");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };

  return (
    <div className="docs">
      {unit && (
        <section className="upload">
          <h2>Log an issue</h2>
          <p className="muted">
            Lekha turns this into a ticket proposal. A named approver in your unit must approve it
            before a ticket is created, in the simulated ticketing system (no real system yet).
          </p>
          <form onSubmit={(e) => void submit(e)}>
            <label>
              Title
              <input
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                minLength={3}
                maxLength={120}
                required
              />
            </label>
            <label>
              Category
              <select
                value={draft.category}
                onChange={(e) => setDraft({ ...draft, category: e.target.value as TicketInputs["category"] })}
              >
                <option value="facilities">Facilities</option>
                <option value="it">IT</option>
                <option value="hr">HR</option>
                <option value="safety">Safety</option>
              </select>
            </label>
            <label>
              Priority
              <select
                value={draft.priority}
                onChange={(e) => setDraft({ ...draft, priority: e.target.value as TicketInputs["priority"] })}
              >
                <option value="P1">P1, urgent</option>
                <option value="P2">P2</option>
                <option value="P3">P3, normal</option>
                <option value="P4">P4, low</option>
              </select>
            </label>
            <label>
              What happened
              <textarea
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                maxLength={2000}
                rows={3}
                required
              />
            </label>
            <button className="primary" type="submit">
              Propose ticket
            </button>
            {sent && <span className="muted small">Sent for approval.</span>}
          </form>
        </section>
      )}
      <section>
        <h1>Issues</h1>
        <p className="muted">Your proposals, and the ones waiting for you if you are an approver.</p>
        {error && <p className="error">{error}</p>}
        {items && items.length === 0 && <p className="muted">No issues.</p>}
        {items && items.length > 0 && (
          <ul className="doc-list requests">
            {items.map((p) => (
              <li key={p.id}>
                <div className="request-body">
                  <strong>
                    {p.inputs.title} <span className="muted small">({p.inputs.category}, {p.inputs.priority})</span>
                  </strong>
                  <span className="muted small">{p.inputs.description}</span>
                  <span className="muted small">
                    {p.mine ? "You" : p.proposed_by} · {new Date(p.created_at).toLocaleString()}
                    {p.decided_by && ` · decided by ${p.decided_by}`}
                    {p.reason && `: ${p.reason}`}
                  </span>
                  {p.external_id && <span className="small">Ticket {p.external_id} (simulated)</span>}
                  {p.can_decide && (
                    <div className="feedback">
                      <input
                        className="inline-input"
                        placeholder="Reason (needed to reject)"
                        value={reasons[p.id] ?? ""}
                        onChange={(e) => setReasons((all) => ({ ...all, [p.id]: e.target.value }))}
                        maxLength={500}
                      />
                      <button className="pill on" onClick={() => void decide(p.id, true)}>
                        Approve
                      </button>
                      <button
                        className="pill"
                        disabled={!(reasons[p.id] ?? "").trim()}
                        onClick={() => void decide(p.id, false)}
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
                <span className={p.status === "pending_approval" ? "tag" : "muted small"}>
                  {STATUS_LABEL[p.status]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
