import { Logo } from "../../atoms/Logo/index.jsx";

export function AuthTemplate({ children }) {
  return (
    <div style={{ height: "100%", overflowY: "auto", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "48px 24px" }}>
      <div className="card-hero" style={{ width: "100%", maxWidth: 520, padding: 32 }}>
        <div className="auth-brand">
          <Logo className="auth-brand-logo" />
          <div>
            <div className="headline auth-brand-word">Agent Gateway</div>
            <div className="auth-brand-sub mono">&gt; Remote control for local agents</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}
