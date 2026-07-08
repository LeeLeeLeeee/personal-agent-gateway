export function AuthTemplate({ children }) {
  return (
    <div style={{ height: "100%", overflowY: "auto", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "48px 24px" }}>
      <div className="card-hero" style={{ width: "100%", maxWidth: 520, padding: 32 }}>
        {children}
      </div>
    </div>
  );
}
