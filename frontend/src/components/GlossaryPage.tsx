import { type FormEvent, useEffect, useState } from "react";
import { type GlossaryEntry, type Me, ApiError, api } from "../api";

export function GlossaryPage({ user }: { user: string }) {
  const [entries, setEntries] = useState<GlossaryEntry[] | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api.glossary(user).then(setEntries).catch(() => setError("Couldn't load the glossary."));
    api.me(user).then(setMe).catch(() => undefined);
  };
  useEffect(() => {
    load(); // reload only when the user changes
  }, [user]);

  const remove = async (id: string) => {
    try {
      await api.deleteGlossary(user, id);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete.");
    }
  };

  const curatorUnit = me?.business_units.find((bu) => bu.roles.includes("curator"));

  return (
    <div className="docs">
      <section>
        <h1>Glossary</h1>
        <p className="muted">
          Names that mean the same thing. When a question uses one of them, Lekha also searches
          for the others, across English and Kannada.
        </p>
        {error && <p className="error">{error}</p>}
        {entries && entries.length === 0 && <p className="muted">No entries you can see.</p>}
        {entries && entries.length > 0 && (
          <ul className="doc-list glossary">
            {entries.map((e) => (
              <li key={e.id}>
                <span className="doc-title">
                  <strong>{e.canonical}</strong>
                  {e.description && <span className="muted small"> · {e.description}</span>}
                  <span className="aliases">
                    {e.aliases.map((a) => (
                      <span key={a} className="tag">
                        {a}
                      </span>
                    ))}
                  </span>
                </span>
                {e.shared && <span className="muted small">shared</span>}
                {e.can_edit && (
                  <button className="link" onClick={() => void remove(e.id)}>
                    Delete
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      {curatorUnit && <AddEntry user={user} unitId={curatorUnit.business_unit_id} onDone={load} />}
    </div>
  );
}

function AddEntry({ user, unitId, onDone }: { user: string; unitId: string; onDone: () => void }) {
  const [canonical, setCanonical] = useState("");
  const [aliases, setAliases] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(true);
  const [status, setStatus] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.addGlossary(user, {
        business_unit_id: unitId,
        canonical: canonical.trim(),
        aliases: aliases
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean),
        description: description.trim(),
        shared,
      });
      setCanonical("");
      setAliases("");
      setDescription("");
      setStatus("Added.");
      onDone();
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : "Couldn't add.");
    }
  }

  return (
    <section className="upload">
      <h2>Add a term</h2>
      <p className="muted small">As a curator you manage your unit's terms.</p>
      <form onSubmit={submit}>
        <label>
          Name
          <input value={canonical} onChange={(e) => setCanonical(e.target.value)} maxLength={120} />
        </label>
        <label>
          Other names (comma-separated, any language)
          <input value={aliases} onChange={(e) => setAliases(e.target.value)} />
        </label>
        <label>
          Description (optional)
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
        </label>
        <label className="check">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          Share with the whole organisation
        </label>
        <button className="primary" disabled={!canonical.trim()}>
          Add
        </button>
        {status && <p className="muted small">{status}</p>}
      </form>
    </section>
  );
}
