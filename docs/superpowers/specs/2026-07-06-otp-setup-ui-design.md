# OTP Setup UI Design

## Goal

Let the owner configure Google Authenticator-compatible TOTP from the existing browser UI instead of running a local Python snippet.

## Scope

- Add token-protected setup endpoints under `/api/auth/setup/*`.
- Show an OTP setup panel in the existing static UI when `/api/auth/status` reports `totp_configured=false`.
- Let the user start setup, scan a QR code, enter a 6-digit code, and see recovery codes after verification.
- Keep existing token-based chat/session UI behavior unchanged.

## API

- `POST /api/auth/setup/start`
  - Requires the existing `agent_web_token` cookie when `AGENT_WEB_TOKEN` is configured.
  - Calls `AuthStore.start_totp_setup("local-owner")`.
  - Returns `otpauth_uri`, `secret`, and generated QR SVG.

- `POST /api/auth/setup/verify`
  - Requires the existing `agent_web_token` cookie when `AGENT_WEB_TOKEN` is configured.
  - Accepts `{ "otp": "123456" }`.
  - Calls `AuthStore.verify_totp_setup`.
  - Returns recovery codes on success.
  - Returns `401` on invalid code.

## UI Flow

On bootstrap, after token-authenticated history/status loading, the UI calls `/api/auth/status`.

If TOTP is not configured:

1. Show an `OTP setup` panel above the workspace.
2. `Start setup` calls `/api/auth/setup/start`.
3. The panel displays the QR SVG and manual secret.
4. User enters the code from Google Authenticator.
5. `Verify` calls `/api/auth/setup/verify`.
6. On success, display recovery codes and hide the setup form state.

## Security

Setup endpoints do not use the `agent_session` cookie because the session does not exist before TOTP is configured. They require the existing token-authenticated browser cookie instead.

## Tests

- API test: setup start requires token cookie.
- API test: setup start returns an otpauth URI and QR SVG.
- API test: setup verify enables TOTP and returns recovery codes.
- Existing auth login tests must continue to pass.
