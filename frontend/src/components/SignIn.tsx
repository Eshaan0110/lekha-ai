import { PERSONAS } from "../personas";

export function SignIn({ onPick }: { onPick: (id: string) => void }) {
  return (
    <div className="signin">
      <div className="signin-card">
        <div className="brand large">
          <span className="brand-mark">ಲೇ</span>
          <span className="brand-name">Lekha</span>
        </div>
        <p className="lede">
          Company memory that answers from approved documents, cites its sources, and respects who
          may see what.
        </p>
        <h2 className="section-title">Sign in as</h2>
        <p className="muted small">
          Development sign-in with synthetic people. Real sign-in will use Group single sign-on.
        </p>
        <div className="persona-grid">
          {PERSONAS.map((p) => (
            <button key={p.id} className="persona" onClick={() => onPick(p.id)}>
              <span className="avatar" aria-hidden="true">
                {p.name.charAt(0)}
              </span>
              <span className="persona-text">
                <span className="persona-name">{p.name}</span>
                <span className="persona-role">{p.role}</span>
                <span className="persona-note">{p.note}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
