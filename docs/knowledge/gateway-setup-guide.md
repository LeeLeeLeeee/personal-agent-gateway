---
title: Personal Agent Gateway 설치·운영 가이드
type: knowledge
domain: personal-agent-gateway
feature: setup-and-operations
status: active
aliases:
  - PAG 설치 방법
  - Gateway 실행 방법
  - Cloudflare Tunnel 설정
tags:
  - setup
  - security
  - cloudflare
  - troubleshooting
updated_at: 2026-07-22
---

# Personal Agent Gateway 설치·운영 가이드

## 준비물

- Python 3.11 이상
- Node.js 20 이상 권장
- npm
- Codex CLI 또는 Claude Code 중 사용할 로컬 CLI의 로그인 상태
- 외부 접속을 쓸 경우 `cloudflared`

로컬 CLI가 제공하는 모델과 실행 옵션은 다음 명령으로 확인한다.

```bash
node scripts/detect_local_agent_capabilities.mjs --pretty
```

필요하면 `--codex-bin`, `--claude-bin`, `--cwd`를 지정한다. CLI 모델이나 설정을 바꾼 뒤에는 Gateway를 재시작해야 catalog가 갱신된다.

## Backend 설치

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

### macOS

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

## Frontend 설치

```bash
cd frontend
npm install
cd ..
```

## 인증 설정

OTP session은 필수다. 처음 접속하면 브라우저에서 TOTP setup을 진행하고 이후에는 인증 앱의 6자리 OTP로 로그인한다.

TOTP 최초 setup 화면도 token으로 보호하려면 임의 값을 만든다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

생성한 값은 `.env`의 `AGENT_AUTH_SETUP_TOKEN`에 저장한다. 하위 호환을 위해 `AGENT_WEB_TOKEN`도 fallback으로 사용할 수 있지만 새 설정은 전용 setup token을 권장한다.

## `.env` 설정

최소 설정:

```bash
AGENT_WEB_HOST=127.0.0.1
AGENT_WEB_PORT=8787
AGENT_WORKSPACE_ROOT=/absolute/path/to/workspace
AGENT_MODEL_PROVIDER=codex
AGENT_MODEL=default
```

전체 항목은 [`.env.example`](../../.env.example)을 기준으로 작성한다.

| 이름 | 설명 |
| --- | --- |
| `AGENT_WEB_HOST` | Gateway bind host. 기본 `127.0.0.1` |
| `AGENT_WEB_PORT` | Gateway port. 기본 `8787` |
| `AGENT_WORKSPACE_ROOT` | 로컬 agent가 기본으로 작업할 workspace |
| `AGENT_MODEL_PROVIDER` | 기본 agent provider. `codex` 또는 `claude` |
| `AGENT_SESSION_DIR` | transcript 저장 위치 |
| `AGENT_APP_DB_PATH` | SQLite DB 경로 |
| `AGENT_ARTIFACT_ROOT` | artifact 저장 위치 |
| `AGENT_AUTH_DIR` | TOTP 인증 정보 저장 위치 |
| `AGENT_AUTH_SETUP_TOKEN` | 선택값. TOTP 최초 setup 보호 token |
| `AGENT_COOKIE_SECURE` | HTTPS Tunnel에서 secure cookie 사용 여부 |
| `PAG_DEV_ALLOWED_HOST` | Vite dev server를 named tunnel로 열 때 허용할 hostname |

## Frontend build

FastAPI가 React UI를 서빙하려면 먼저 build한다.

```bash
cd frontend
npm run build
cd ..
```

결과는 `src/personal_agent_gateway/frontend_dist/`에 생성되며 Git에는 올리지 않는다.

## Local gateway 실행

### Windows PowerShell

```powershell
.\scripts\run_local.ps1
```

### macOS

```bash
scripts/run_local.sh
```

브라우저에서 `http://127.0.0.1:8787`에 접속한다.

## 외부 접속

### 임시 Quick Tunnel

다른 터미널에서 실행한다.

Windows:

```powershell
.\scripts\run_tunnel.ps1
```

macOS:

```bash
scripts/run_tunnel.sh
```

출력된 `https://<random>.trycloudflare.com` 주소로 접속한다. Quick Tunnel URL은 재시작할 때마다 바뀐다.

### 고정 hostname의 named tunnel

Cloudflare named tunnel을 만들고 사용자 홈의 `.cloudflared/config.yml`에서 ingress를 로컬 Gateway로 연결한다.

```yaml
tunnel: <tunnel-id>
credentials-file: <local-credentials-file>

ingress:
  - hostname: <private-subdomain.example.com>
    service: http://127.0.0.1:8787
  - service: http_status:404
```

실제 hostname은 repository가 아니라 Cloudflare 설정과 로컬 `.env`에만 둔다. Vite dev server를 직접 tunnel에 연결할 때만 `PAG_DEV_ALLOWED_HOST`에도 같은 hostname을 지정한다.

hostname 자체는 인증 수단이 아니다. Gateway OTP를 유지하고 필요하면 Cloudflare Access를 앞단에 추가한다.

## 개발 모드

Backend와 frontend를 별도 터미널에서 실행한다.

터미널 1:

```powershell
.\scripts\run_local.ps1
```

터미널 2:

```bash
cd frontend
npm run dev
```

Vite dev server는 `/api/*`, `/static/vendor/*`, `/static/avatars/*`를 `127.0.0.1:8787`로 proxy한다.

## 테스트

Backend:

```bash
pytest
ruff check .
```

Frontend:

```bash
cd frontend
npm test -- --run
npm run build
```

Local capability 탐지:

```bash
node scripts/detect_local_agent_capabilities.test.mjs
```

## 보안 기준

- Gateway는 기본적으로 `127.0.0.1`에 bind한다.
- 모든 데이터 API는 OTP session cookie를 요구한다.
- 외부 접속은 HTTPS Tunnel을 사용하고 `AGENT_COOKIE_SECURE=true`를 적용한다.
- Tunnel hostname과 별개로 OTP 또는 Cloudflare Access를 유지한다.
- `AGENT_WORKSPACE_ROOT`와 Spaces 정책은 agent에게 맡겨도 되는 경로로 제한한다.
- shell capability는 승인 기반으로 실행한다.
- 위험한 접근 정책 변경과 운영 동작은 audit에 남긴다.

공개하거나 commit하면 안 되는 값:

- `AGENT_AUTH_SETUP_TOKEN`, `AGENT_WEB_TOKEN`
- 로컬 Codex·Claude credential과 로그인 상태
- `AGENT_AUTH_DIR`의 TOTP 인증 데이터
- workspace의 민감한 파일
- 비공개로 운영하는 활성 Tunnel hostname

## Troubleshooting

| 증상 | 확인할 것 |
| --- | --- |
| `401 Unauthorized` | OTP login 또는 TOTP setup token이 필요한 요청인지 확인 |
| `OTP login required` | OTP login panel에서 6자리 TOTP 입력 |
| cookie가 저장되지 않음 | HTTPS Tunnel 전용이면 `AGENT_COOKIE_SECURE=true` 확인 |
| port 충돌 | `AGENT_WEB_PORT=8788`처럼 다른 port 사용 |
| agent가 파일을 못 봄 | Global·Persona·Team Spaces의 유효 read/write 범위 확인 |
| Codex 실행 실패 | `codex exec --json "hello"`와 로그인 상태 확인 |
| Claude 실행 실패 | `claude --help`와 로그인 상태 확인 |
| frontend 변경이 안 보임 | `cd frontend && npm run build` 후 Gateway 재시작 |
| Vite API 실패 | FastAPI가 `127.0.0.1:8787`에서 실행 중인지 확인 |
| 자동화가 멈춤 | Operations에서 Worker, Scheduler, Hook, Cycle health 확인 |

운영 상태, emergency stop과 backup 복구는 [Operations 진단 가이드](2026-07-15-operations-diagnostics-guide.md)를 참고한다.
