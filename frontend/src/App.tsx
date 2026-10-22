import { useState } from "react";
import { ActionsPage } from "./components/ActionsPage";
import { AskPage } from "./components/AskPage";
import { DocumentsPage } from "./components/DocumentsPage";
import { GlossaryPage } from "./components/GlossaryPage";
import { MeetingsPage } from "./components/MeetingsPage";
import { SignIn } from "./components/SignIn";
import { AuditPage, RequestsPage } from "./components/SupportPages";
import { PERSONAS } from "./personas";

type Tab = "ask" | "documents" | "meetings" | "glossary" | "requests" | "issues" | "audit";

function remembered(): string | null {
  try {
    return sessionStorage.getItem("lekha.user");
  } catch {
    return null;
  }
}

function remember(user: string | null): void {
  try {
    if (user) sessionStorage.setItem("lekha.user", user);
    else sessionStorage.removeItem("lekha.user");
  } catch {
    // Storage may be unavailable (private mode); the app works without it.
  }
}

export function App() {
  const [user, setUser] = useState<string | null>(remembered);
  const [tab, setTab] = useState<Tab>("ask");
  const persona = PERSONAS.find((p) => p.id === user);

  const signIn = (id: string | null) => {
    remember(id);
    setUser(id);
    setTab("ask");
  };

  if (!user || !persona) return <SignIn onPick={signIn} />;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">ಲೇ</span>
          <span className="brand-name">Lekha</span>
        </div>
        <nav className="tabs" aria-label="Sections">
          <button className={tab === "ask" ? "tab active" : "tab"} onClick={() => setTab("ask")}>
            Ask
          </button>
          <button className={tab === "documents" ? "tab active" : "tab"} onClick={() => setTab("documents")}>
            Documents
          </button>
          <button className={tab === "meetings" ? "tab active" : "tab"} onClick={() => setTab("meetings")}>
            Meetings
          </button>
          <button className={tab === "glossary" ? "tab active" : "tab"} onClick={() => setTab("glossary")}>
            Glossary
          </button>
          <button className={tab === "requests" ? "tab active" : "tab"} onClick={() => setTab("requests")}>
            Requests
          </button>
          <button className={tab === "issues" ? "tab active" : "tab"} onClick={() => setTab("issues")}>
            Issues
          </button>
          {user === "tara" && (
            <button className={tab === "audit" ? "tab active" : "tab"} onClick={() => setTab("audit")}>
              Audit
            </button>
          )}
        </nav>
        <div className="who">
          <span className="who-name">{persona.name}</span>
          <span className="who-role">{persona.role}</span>
          <button className="link" onClick={() => signIn(null)}>
            Switch user
          </button>
        </div>
      </header>
      <div className="banner">Local demo · synthetic documents only · development sign-in</div>
      <main className="content">
        {tab === "ask" && <AskPage key={user} user={user} />}
        {tab === "documents" && <DocumentsPage key={user} user={user} />}
        {tab === "meetings" && <MeetingsPage key={user} user={user} />}
        {tab === "glossary" && <GlossaryPage key={user} user={user} />}
        {tab === "requests" && <RequestsPage key={user} user={user} />}
        {tab === "issues" && <ActionsPage key={user} user={user} />}
        {tab === "audit" && <AuditPage key={user} user={user} />}
      </main>
    </div>
  );
}
