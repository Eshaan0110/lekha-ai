import { type FormEvent, useEffect, useState } from "react";
import { type MeetingDetail, type MeetingItem, type Me, type Minutes, ApiError, api } from "../api";
import { PERSONAS } from "../personas";

const PEOPLE = ["asha", "meera", "ravi", "chitra", "bina"];

export function MeetingsPage({ user }: { user: string }) {
  const [meetings, setMeetings] = useState<MeetingItem[] | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState<MeetingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api.meetings(user).then(setMeetings).catch(() => setError("Couldn't load meetings."));
    api.me(user).then(setMe).catch(() => undefined);
  };
  useEffect(() => {
    load(); // reload only when the user changes
  }, [user]);

  const show = (id: string) =>
    api
      .meeting(user, id)
      .then(setOpen)
      .catch(() => setError("That meeting isn't available to you."));

  const unit = me?.business_units.find((bu) => bu.roles.length > 0);

  return (
    <div className="docs">
      <section>
        <h1>Meetings you took part in</h1>
        <p className="muted">
          Only the people in a meeting can find it, read it, or get answers from it. Not even a
          curator can, unless they were there.
        </p>
        {error && <p className="error">{error}</p>}
        {meetings && meetings.length === 0 && <p className="muted">None yet.</p>}
        {meetings && meetings.length > 0 && (
          <ul className="doc-list">
            {meetings.map((m) => (
              <li key={m.id}>
                <button className="link doc-title left" onClick={() => void show(m.id)}>
                  {m.title}
                </button>
                <span className="muted small">{new Date(m.started_at).toLocaleString()}</span>
                <span className="version">v{m.version_number}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {open && <Transcript user={user} meeting={open} onClose={() => setOpen(null)} />}
      {unit && <ImportMeeting user={user} unitId={unit.business_unit_id} onDone={load} />}
    </div>
  );
}

function Transcript({ user, meeting, onClose }: { user: string; meeting: MeetingDetail; onClose: () => void }) {
  return (
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="panel" role="dialog" aria-label="Meeting transcript" onClick={(e) => e.stopPropagation()}>
        <button className="link close" onClick={onClose}>
          Close
        </button>
        <p className="muted small">Meeting · {new Date(meeting.started_at).toLocaleString()}</p>
        <h2>
          {meeting.title} <span className="version">v{meeting.version_number}</span>
        </h2>
        <p className="muted small">With {meeting.participants.join(", ")}</p>
        <MinutesSection user={user} meeting={meeting} />
        <h3 className="subhead">Transcript</h3>
        <ol className="transcript">
          {meeting.transcript.map((turn, i) => (
            <li key={i}>
              <span className="turn-time">{turn.at ?? ""}</span>
              <span>
                {turn.speaker && <strong>{turn.speaker}: </strong>}
                {turn.text}
              </span>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}

function MinutesSection({ user, meeting }: { user: string; meeting: MeetingDetail }) {
  const [all, setAll] = useState<Minutes[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = () => {
    api.minutes(user, meeting.id).then(setAll).catch(() => setAll([]));
  };
  useEffect(() => {
    load(); // reload when the meeting changes
  }, [meeting.id]);

  const draft = async () => {
    setBusy(true);
    setNote("Drafting minutes from the transcript… (about a minute on this laptop)");
    try {
      await api.draftMinutes(user, meeting.id);
      setNote(null);
      load();
    } catch (error) {
      setNote(error instanceof ApiError ? error.message : "Drafting failed.");
    } finally {
      setBusy(false);
    }
  };

  const decide = async (id: string, approve: boolean) => {
    setBusy(true);
    try {
      await api.decideMinutes(user, id, approve);
      setNote(approve ? "Approved. The minutes become searchable for the participants shortly." : "Rejected.");
      load();
    } catch (error) {
      setNote(error instanceof ApiError ? error.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const shown = (all ?? []).filter((m) => m.current && m.status !== "superseded");
  return (
    <section className="minutes">
      <div className="minutes-head">
        <h3 className="subhead">Minutes</h3>
        <button className="pill" disabled={busy} onClick={() => void draft()}>
          Draft minutes
        </button>
      </div>
      {note && <p className="muted small">{note}</p>}
      {all && shown.length === 0 && <p className="muted small">No minutes yet.</p>}
      {shown.map((m) => (
        <article key={m.id} className={`minutes-card ${m.status}`}>
          <p className="muted small">
            {m.status === "draft" ? "Draft, waiting for the organiser" : m.status === "approved" ? "Approved" : "Rejected"}
            {m.edited ? " · edited" : ""}
            {m.dropped_items > 0 ? ` · ${m.dropped_items} unsupported item(s) removed` : ""}
          </p>
          {m.summary && <p>{m.summary}</p>}
          {m.decisions.length > 0 && (
            <>
              <h4>Decisions</h4>
              <ul>
                {m.decisions.map((d, i) => (
                  <li key={i}>
                    {d.text}
                    {d.evidence && (
                      <span className="quote block">
                        {d.at ? `${d.at} · ` : ""}“{d.evidence}”
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {m.action_items.length > 0 && (
            <>
              <h4>Action items</h4>
              <ul>
                {m.action_items.map((a, i) => (
                  <li key={i}>
                    {a.task}
                    <span className="muted small">
                      {" "}
                      · {a.owner || "no owner named"}
                      {a.owner && a.owner_spoke === false ? " (didn't speak in the meeting)" : ""}
                      {a.due ? ` · due ${a.due}` : ""}
                    </span>
                    {a.evidence && (
                      <span className="quote block">
                        {a.at ? `${a.at} · ` : ""}“{a.evidence}”
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {m.status === "draft" && meeting.you_organised && (
            <div className="feedback">
              <button className="pill on" disabled={busy} onClick={() => void decide(m.id, true)}>
                Approve
              </button>
              <button className="pill" disabled={busy} onClick={() => void decide(m.id, false)}>
                Reject
              </button>
            </div>
          )}
        </article>
      ))}
    </section>
  );
}

function ImportMeeting({ user, unitId, onDone }: { user: string; unitId: string; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [startedAt, setStartedAt] = useState("");
  const [people, setPeople] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !title.trim() || !startedAt) return;
    setBusy(true);
    setStatus("Importing…");
    try {
      // Development only: look up the synthetic participants' IDs.
      const ids = await Promise.all(people.map((p) => api.me(p).then((m) => m.user_id)));
      const imported = await api.importMeeting(user, file, {
        title: title.trim(),
        business_unit_id: unitId,
        started_at: `${startedAt}:00+05:30`, // the form's local time, India Standard Time
        participant_user_ids: ids,
      });
      setStatus(`Imported ${imported.turns} turns (version ${imported.version_number}). Processing…`);
      for (let i = 0; i < 300; i++) {
        const job = await api.job(user, imported.ingestion_job_id);
        if (job.status === "succeeded") {
          setStatus("Ready: the meeting is searchable by its participants.");
          onDone();
          return;
        }
        if (job.status === "failed") {
          setStatus(`Processing failed (${job.error_code ?? "unknown"}).`);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      setStatus("Still processing. Is the worker running?");
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="upload">
      <h2>Import a meeting</h2>
      <p className="muted small">
        A transcript exported from Teams (.vtt), or an .srt or .txt file. You are always a
        participant; add the others who were there.
      </p>
      <form onSubmit={submit}>
        <label>
          Transcript
          <input type="file" accept=".vtt,.srt,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
        </label>
        <label>
          Started at (IST)
          <input type="datetime-local" value={startedAt} onChange={(e) => setStartedAt(e.target.value)} />
        </label>
        <fieldset>
          <legend>Who else was there</legend>
          {PEOPLE.filter((p) => p !== user).map((p) => (
            <label key={p} className="check">
              <input
                type="checkbox"
                checked={people.includes(p)}
                onChange={(e) => setPeople((all) => (e.target.checked ? [...all, p] : all.filter((x) => x !== p)))}
              />
              {PERSONAS.find((x) => x.id === p)?.name}
            </label>
          ))}
        </fieldset>
        <button className="primary" disabled={busy || !file || !title.trim() || !startedAt}>
          Import
        </button>
        {status && <p className="muted small">{status}</p>}
      </form>
    </section>
  );
}
