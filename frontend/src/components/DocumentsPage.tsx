import { type FormEvent, useEffect, useState } from "react";
import { type DocumentItem, type Me, ApiError, api } from "../api";
import { PERSONAS } from "../personas";

const READERS = ["asha", "meera", "ravi"];

export function DocumentsPage({ user }: { user: string }) {
  const [me, setMe] = useState<Me | null>(null);
  const [docs, setDocs] = useState<DocumentItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api.me(user).then(setMe).catch(() => setError("Couldn't load your profile."));
    api.documents(user).then(setDocs).catch(() => setError("Couldn't load documents."));
  };
  useEffect(() => {
    load(); // reload only when the user changes
  }, [user]);

  const curatorUnit = me?.business_units.find((bu) => bu.roles.includes("curator"));

  return (
    <div className="docs">
      <section>
        <h1>Documents you can read</h1>
        <p className="muted">Lekha only ever searches these. Others exist, but are invisible to you.</p>
        {error && <p className="error">{error}</p>}
        {docs && docs.length === 0 && <p className="muted">None yet.</p>}
        {docs && docs.length > 0 && (
          <ul className="doc-list">
            {docs.map((d) => (
              <li key={d.id}>
                <span className="doc-title">{d.title}</span>
                <span className="tag">{d.classification}</span>
                <span className="version">v{d.version_number}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {curatorUnit && <Upload user={user} unitId={curatorUnit.business_unit_id} onDone={load} />}
    </div>
  );
}

function Upload({ user, unitId, onDone }: { user: string; unitId: string; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [classification, setClassification] = useState("internal");
  const [readers, setReaders] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !title.trim() || readers.length === 0) return;
    setBusy(true);
    setStatus("Uploading…");
    try {
      // Development only: look up the synthetic readers' IDs to build the guest list.
      const ids = await Promise.all(readers.map((r) => api.me(r).then((m) => `user:${m.user_id}`)));
      const key = `synthetic:${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60)}`;
      const uploaded = await api.upload(user, file, {
        title: title.trim(),
        canonical_key: key,
        business_unit_id: unitId,
        classification,
        acl_subjects: ids,
      });
      setStatus(`Uploaded as version ${uploaded.version_number}. Processing…`);
      for (let i = 0; i < 300; i++) {
        const job = await api.job(user, uploaded.ingestion_job_id);
        if (job.status === "succeeded") {
          setStatus(`Ready: ${job.chunk_count ?? 0} sections searchable.`);
          onDone();
          return;
        }
        if (job.status === "failed") {
          setStatus(`Processing failed (${job.error_code ?? "unknown"}).`);
          return;
        }
        setStatus(`Processing: ${job.stage}…`);
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      setStatus("Still processing. Is the worker running?");
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="upload">
      <h2>Upload a document</h2>
      <p className="muted small">PDF or Word. Uploading a new file with the same title adds a new version.</p>
      <form onSubmit={submit}>
        <label>
          File
          <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        </label>
        <label>
          Sensitivity
          <select value={classification} onChange={(e) => setClassification(e.target.value)}>
            <option value="public">Public</option>
            <option value="internal">Internal</option>
          </select>
        </label>
        <fieldset>
          <legend>Who may read it</legend>
          {READERS.map((r) => (
            <label key={r} className="check">
              <input
                type="checkbox"
                checked={readers.includes(r)}
                onChange={(e) => setReaders((all) => (e.target.checked ? [...all, r] : all.filter((x) => x !== r)))}
              />
              {PERSONAS.find((p) => p.id === r)?.name}
            </label>
          ))}
        </fieldset>
        <button className="primary" disabled={busy || !file || !title.trim() || readers.length === 0}>
          Upload
        </button>
        {status && <p className="muted small">{status}</p>}
      </form>
    </section>
  );
}
