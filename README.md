# Personal Agent Gateway

브라우저에서 내 로컬 Mac의 Codex 기반 에이전트를 호출하기 위한 개인용 웹 게이트웨이입니다.

기본 실행 방식은 OpenAI API 직접 호출이 아니라 로컬 `codex exec --json` 호출입니다. 따라서 기본 설정에서는 `OPENAI_API_KEY`를 앱에 넣지 않습니다. 이미 로컬 Mac에 로그인되어 있는 Codex CLI 설정을 그대로 사용합니다.

## 무엇을 하는가

- 로컬 FastAPI 서버를 `127.0.0.1` 또는 `localhost`에만 띄웁니다.
- 브라우저 UI와 API 전체를 `AGENT_WEB_TOKEN`으로 보호합니다.
- Cloudflare Quick Tunnel을 통해 외부 브라우저에서 임시 HTTPS URL로 접속할 수 있게 합니다.
- 받은 메시지를 로컬 `codex exec --json`으로 전달하고 최종 응답을 웹 UI에 표시합니다.
- 대화 기록은 로컬 디스크에 저장하고 재시작 후에도 active session을 이어갑니다.

## 하지 않는 것

이 프로젝트는 공개 멀티유저 서비스가 아닙니다. 인증 없는 에이전트 엔드포인트도 아니고, 범용 원격 쉘도 아닙니다.

다음 값들은 직접 보호해야 합니다.

- `AGENT_WEB_TOKEN`
- Cloudflare Quick Tunnel URL
- 로컬 Codex 로그인 상태
- 로컬 Mac 자체
- `AGENT_WORKSPACE_ROOT` 아래의 파일

## 아키텍처

```text
외부 브라우저
  -> Cloudflare Quick Tunnel 공개 HTTPS URL
  -> 로컬 loopback FastAPI gateway
  -> token-protected Web UI / API
  -> AgentRuntime
  -> CodexModelClient
  -> local `codex exec --json`
  -> 로컬 workspace 파일/명령 실행
```

현재 Version A에서는 Cloudflare Quick Tunnel이 유일한 외부 ingress입니다. 앱 서버는 loopback 주소에만 bind하고, tunnel이 `127.0.0.1:${AGENT_WEB_PORT}`로 요청을 전달합니다.

터널 URL을 알고 있어도 바로 사용할 수 없게 모든 페이지, static asset, API 요청에 `AGENT_WEB_TOKEN` 인증을 적용합니다.

## 주요 모듈

- `src/personal_agent_gateway/app.py`: FastAPI 앱, route, static HTML, provider 선택
- `src/personal_agent_gateway/auth.py`: query token, bearer token, HttpOnly cookie 인증
- `src/personal_agent_gateway/runtime.py`: 대화 runtime, transcript 저장/복원, provider 호출, 민감값 redaction
- `src/personal_agent_gateway/model_client.py`: provider client 구현. 기본값은 `CodexModelClient`
- `src/personal_agent_gateway/transcript.py`: JSONL transcript와 `active.json` session pointer 관리
- `src/personal_agent_gateway/tools.py`: OpenAI provider 경로에서 사용하는 파일/쉘 tool
- `src/personal_agent_gateway/approval.py`: OpenAI provider 경로의 브라우저 승인 flow
- `scripts/run_local.sh`: 로컬 서버 실행
- `scripts/run_tunnel.sh`: Cloudflare Quick Tunnel 실행

## 기술적 근거

- **로컬 우선 실행**: 브라우저는 Codex나 파일시스템에 직접 접근하지 않습니다. 브라우저는 gateway에만 요청하고, gateway가 로컬 agent engine을 호출합니다.
- **loopback bind**: `AGENT_WEB_HOST`는 `127.0.0.1` 또는 `localhost`로 제한합니다. 앱이 LAN에 직접 노출되는 것을 막기 위한 선택입니다.
- **임시 public ingress**: 도메인 구매 없이 Cloudflare Quick Tunnel의 `trycloudflare.com` URL을 사용합니다.
- **token gate**: 터널 URL은 secret으로 취급하지 않습니다. 실제 접근 제어는 `AGENT_WEB_TOKEN`으로 합니다.
- **Codex CLI 재사용**: 기본 provider는 로컬 `codex exec --json` subprocess를 실행합니다. 로컬 Codex 로그인/config를 재사용하므로 기본 경로에서는 OpenAI API key가 필요하지 않습니다.
- **workspace boundary**: `AGENT_WORKSPACE_ROOT`를 Codex 실행 위치로 넘기고, OpenAI provider의 파일/쉘 tool boundary로도 사용합니다.
- **재시작 유지**: transcript는 디스크에 저장하고 `active.json`이 현재 session을 가리킵니다.

## 구현된 기능

- token-protected web UI
- token-protected API endpoint
- `?token=...` 접속 시 HttpOnly `agent_web_token` cookie 설정
- `AGENT_SESSION_DIR` 기반 로컬 transcript 저장
- 프로세스 재시작 후 active session 복원
- reset 버튼으로 active session 초기화
- 기본 로컬 Codex provider: `codex exec --json`
- 선택적 OpenAI API provider: `AGENT_MODEL_PROVIDER=openai`
- OpenAI provider 경로의 shell tool 브라우저 승인 flow
- 도메인 없이 외부 접속 가능한 Cloudflare Quick Tunnel script
- token/API key류 민감값 redaction
- config/auth, app routing, transcript, runtime, tools, model client 테스트

## 현재 제약

- Cloudflare Quick Tunnel URL은 임시 URL입니다. tunnel을 재시작하면 URL이 바뀝니다.
- Quick Tunnel은 개인 테스트/개인 사용에는 편하지만 uptime이 보장되는 production endpoint가 아닙니다.
- Codex provider는 요청마다 `codex exec` subprocess를 새로 실행합니다. 웹 transcript를 context로 넘기지만 아직 `codex exec resume`으로 Codex thread ID를 이어가지는 않습니다.
- Codex provider는 현재 최종 assistant message만 반환합니다. JSONL 중간 이벤트를 브라우저로 streaming하지 않습니다.
- Codex provider에는 브라우저 승인 flow가 직접 연결되어 있지 않습니다. 명령 실행 제어는 `AGENT_CODEX_SANDBOX`, `AGENT_CODEX_APPROVAL_POLICY`가 담당합니다.
- 인증은 단일 shared token 방식입니다. 사용자 계정, role, revoke list, audit dashboard는 없습니다.
- named Cloudflare Tunnel, custom domain, Cloudflare Zero Trust login은 아직 구성하지 않았습니다.

## 보안 모델

- `AGENT_WEB_HOST`는 `127.0.0.1` 또는 `localhost`만 허용합니다.
- 모든 page, static asset, API에 `AGENT_WEB_TOKEN`이 필요합니다.
- 인증 방식은 `?token=...`, bearer token, `agent_web_token` cookie입니다.
- `?token=...`으로 첫 인증에 성공하면 HttpOnly `agent_web_token` cookie를 설정합니다.
- 파일 tool은 `AGENT_WORKSPACE_ROOT` 아래로 제한됩니다.
- `AGENT_MODEL_PROVIDER=openai`의 shell command는 브라우저 승인 후 `AGENT_WORKSPACE_ROOT`에서 실행됩니다.
- transcript는 `AGENT_SESSION_DIR` 아래에 저장됩니다.
- token/API key 값은 runtime 기록에 남기지 않도록 redaction합니다.
- `AGENT_MODEL_PROVIDER=codex`에서는 로컬 `codex exec`가 실행되고, shell approval은 Codex CLI sandbox/approval policy에 위임됩니다.

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

강한 web token을 생성합니다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`.env`를 열고 필요한 값을 설정합니다.

```bash
AGENT_WEB_HOST=127.0.0.1
AGENT_WEB_PORT=8787
AGENT_WEB_TOKEN=replace-with-strong-random-token
AGENT_WORKSPACE_ROOT=/absolute/path/to/workspace
AGENT_MODEL_PROVIDER=codex
AGENT_MODEL=default
AGENT_SESSION_DIR=./data/sessions
AGENT_CODEX_BIN=codex
AGENT_CODEX_SANDBOX=workspace-write
AGENT_CODEX_APPROVAL_POLICY=never
AGENT_CODEX_TIMEOUT_SECONDS=300
```

앱은 내부에서 `.env`를 읽습니다. 다만 shell script가 host/port override를 보려면 inline env 또는 export가 필요합니다.

```bash
AGENT_WEB_PORT=8788 scripts/run_local.sh
AGENT_WEB_PORT=8788 scripts/run_tunnel.sh
set -a; source .env; set +a
```

## 로컬 실행

```bash
scripts/run_local.sh
```

기본 주소:

```text
http://127.0.0.1:8787/?token=<AGENT_WEB_TOKEN>
```

## Cloudflare Quick Tunnel 실행

```bash
scripts/run_tunnel.sh
```

script는 `http://127.0.0.1:${AGENT_WEB_PORT}`를 Cloudflare Quick Tunnel에 연결합니다.

Cloudflare는 다음 형태의 임시 URL을 발급합니다.

```text
https://<random>.trycloudflare.com
```

접속할 때는 token을 붙입니다.

```text
https://<random>.trycloudflare.com/?token=<AGENT_WEB_TOKEN>
```

도메인 구매는 필요 없습니다. 단, tunnel을 재시작하면 URL이 바뀝니다.

## 재시작 유지 방식

대화 transcript는 `AGENT_SESSION_DIR` 아래에 JSONL로 저장됩니다.

현재 session pointer는 다음 파일입니다.

```text
<AGENT_SESSION_DIR>/active.json
```

gateway를 재시작하면 `active.json`을 읽어 마지막 active session을 복원합니다. UI의 reset은 active session을 초기화합니다.

## Shell approval 동작

`AGENT_MODEL_PROVIDER=openai`:

- shell tool call은 브라우저 승인 flow를 사용합니다.
- 승인된 command는 `AGENT_WORKSPACE_ROOT`에서 실행됩니다.

`AGENT_MODEL_PROVIDER=codex`:

- command 실행은 로컬 `codex exec`가 처리합니다.
- 제어값은 `AGENT_CODEX_SANDBOX`, `AGENT_CODEX_APPROVAL_POLICY`입니다.

## 테스트

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

## Troubleshooting

- 서버가 뜨지 않으면 `AGENT_WEB_HOST`가 `127.0.0.1` 또는 `localhost`인지 확인합니다.
- 인증 오류가 나면 `?token=<AGENT_WEB_TOKEN>`으로 접속하거나 bearer token을 사용합니다.
- cookie가 꼬였으면 `agent_web_token` cookie를 지우고 다시 접속합니다.
- `8787` port가 사용 중이면 `AGENT_WEB_PORT`를 inline env로 넘깁니다.
- tunnel URL이 동작하지 않으면 `scripts/run_tunnel.sh`를 재시작하고 새 URL을 사용합니다.
- agent가 파일을 보지 못하면 해당 파일이 `AGENT_WORKSPACE_ROOT` 아래에 있는지 확인합니다.
- session 상태가 이상하면 reset으로 active session pointer를 초기화합니다.
