import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";

export function AuthCard({ stage, setup, recoveryCodes, authError, onLogin, onSetupStart, onSetupVerify, onContinue }) {
  const [otp, setOtp] = useState("");
  const error = authError ? (
    <div className="mono" style={{ border: "3px solid var(--c-danger)", color: "var(--c-danger)", padding: "12px 14px", marginTop: 16, fontSize: 12 }}>
      {authError}
    </div>
  ) : null;

  if (stage === "setup") {
    return (
      <>
        <div className="headline" style={{ fontSize: 22, marginBottom: 6 }}>Set up authenticator</div>
        <div style={{ fontSize: 13, color: "var(--c-dark)", marginBottom: 16 }}>Scan the QR in Google Authenticator, then enter the 6-digit code.</div>
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
          <div className="qr" dangerouslySetInnerHTML={{ __html: setup?.qr_svg || "" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="mono" style={{ fontSize: 10, letterSpacing: 1, color: "var(--c-grey)", marginBottom: 4 }}>MANUAL SETUP KEY</div>
            <div className="mono" style={{ fontSize: 13, wordBreak: "break-all", border: "2px solid var(--c-black)", padding: "8px 10px" }}>{setup?.secret || ""}</div>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <InputField type="text" inputMode="numeric" maxLength="6" placeholder="000000" value={otp} onChange={(event) => setOtp(event.target.value)} />
        </div>
        {error}
        <div style={{ marginTop: 24 }}>
          <Button variant="primary" size="btn-lg" style={{ width: "100%" }} onClick={() => onSetupVerify(otp.trim())}>Verify & enable</Button>
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="mono" style={{ background: "none", border: "none", padding: 0, color: "var(--c-link)", cursor: "pointer", textDecoration: "underline", fontSize: 12 }} onClick={onSetupStart}>
            Back to sign in
          </button>
        </div>
      </>
    );
  }

  if (stage === "recovery") {
    return (
      <>
        <div className="headline" style={{ fontSize: 22, marginBottom: 6 }}>Recovery codes</div>
        <div style={{ fontSize: 13, color: "var(--c-dark)", marginBottom: 16 }}>Store these now. They are shown only once and let you sign in if you lose your device.</div>
        <div className="mono" style={{ border: "3px solid var(--c-black)", padding: 14, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 13 }}>
          {(recoveryCodes || []).map((code) => <span key={code}>{code}</span>)}
        </div>
        <div className="mono" style={{ marginTop: 16, border: "3px solid var(--c-warn)", padding: "10px 12px", fontSize: 12 }}>These codes will not be shown again.</div>
        <div style={{ marginTop: 24 }}>
          <Button variant="primary" size="btn-lg" style={{ width: "100%" }} onClick={onContinue}>I have saved these - continue</Button>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="headline" style={{ fontSize: 22, marginBottom: 6 }}>Sign in</div>
      <div style={{ fontSize: 13, color: "var(--c-dark)", marginBottom: 24 }}>Enter the 6-digit code from your authenticator app.</div>
      <InputField type="text" inputMode="numeric" maxLength="6" placeholder="000000" value={otp} onChange={(event) => setOtp(event.target.value)} />
      {error}
      <div style={{ marginTop: 24 }}>
        <Button variant="primary" size="btn-lg" style={{ width: "100%" }} onClick={() => onLogin(otp.trim())}>Continue</Button>
      </div>
      <div style={{ marginTop: 20, borderTop: "1px solid #CCC", paddingTop: 16 }}>
        <button className="mono" style={{ background: "none", border: "none", padding: 0, color: "var(--c-link)", cursor: "pointer", textDecoration: "underline", fontSize: 12 }} onClick={onSetupStart}>
          First time on this device? Set up authenticator
        </button>
      </div>
    </>
  );
}
