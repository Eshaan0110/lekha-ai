import { useEffect, useState } from "react";
import { type Passage, api } from "../api";
import { formatMs } from "../answer";

/** The full passage behind a citation, with the quoted words highlighted. The server applies
 *  the same access rule as search, so a passage the user may not read is never fetched. */
export function SourcePanel({
  user,
  chunkId,
  span,
  onClose,
}: {
  user: string;
  chunkId: string;
  span: string;
  onClose: () => void;
}) {
  const [passage, setPassage] = useState<Passage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .passage(user, chunkId)
      .then((p) => live && setPassage(p))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [user, chunkId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="panel" role="dialog" aria-label="Source passage" onClick={(e) => e.stopPropagation()}>
        <button className="link close" onClick={onClose}>
          Close
        </button>
        {failed && <p className="error">This source isn't available to you any more.</p>}
        {!passage && !failed && <p className="muted">Loading source…</p>}
        {passage && (
          <>
            <p className="muted small">
              {passage.kind === "meeting" ? "Meeting transcript" : "Source"}
              {passage.kind === "meeting" && passage.start_ms !== null && ` · from ${formatMs(passage.start_ms)}`}
            </p>
            <h2>
              {passage.title} <span className="version">v{passage.version_number}</span>
            </h2>
            {passage.heading_path.length > 0 && <p className="muted small">{passage.heading_path.join(" › ")}</p>}
            <p className="passage">{highlight(passage.text, span)}</p>
          </>
        )}
      </aside>
    </div>
  );
}

function highlight(text: string, span: string) {
  const at = text.toLowerCase().indexOf(span.trim().toLowerCase());
  if (!span.trim() || at < 0) return text;
  const end = at + span.trim().length;
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, end)}</mark>
      {text.slice(end)}
    </>
  );
}
