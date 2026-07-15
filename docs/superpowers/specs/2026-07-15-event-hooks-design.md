# Event Hooks 설계 (이메일 첫 어댑터)

- 작성일: 2026-07-15
- 상태: 설계 확정, 구현 대기
- 관련 도메인: Automation(Jobs/Schedules) 인접, Chat 실행 재사용

## 1. 배경과 목적

Personal Agent Gateway에 **외부 이벤트를 주기적으로 감지해 Agent가 자동 처리하는 hook 기능**을 추가한다. 첫 사례는 이메일이다: 이메일 계정을 연동해두면 gateway가 주기적으로 폴링하고, 새 메시지가 오면 hook이 발동해 지정한 Agent가 자동으로 처리하며, 그 결과를 실행 기록으로 남기고 UI 알림으로 표시한다.

이 발상은 기존 구조와 거의 1:1로 맞는다. 이미 존재하는 `SchedulerLoop`(폴링 루프) + `JobWorker`(큐 소비) 쌍과, `agent.instruct` capability + `AgentRunner`(프롬프트 하나로 Agent를 headless 실행)를 그대로 본떠 만든다.

### 확정된 결정 (브레인스토밍 결과)

| 질문 | 결정 |
| --- | --- |
| hook 발동 시 동작 | Agent에게 자동으로 시킴 → 그 결과를 알림으로 받음 |
| 설계 범위 | 일반 프레임워크(소스 어댑터 경계), 이메일이 첫 구현체 |
| 데이터 수집 방식 | 주기적 polling만 (webhook 수신 없음) |
| 실행 형태 | 이벤트마다 독립 실행 + 전용 `hook_run` 기록 (기존 Job에 위임하지 않음) |
| 이메일 연결 | IMAP + 앱 비밀번호 (제공자 무관, OAuth 불필요) |
| 발동 조건 | 새 메일 + 간단한 필터(발신자/제목 포함어/폴더) |

### 범위 밖 (YAGNI)

- webhook(push) 수신, 공개 URL, 서명 검증
- Gmail API/OAuth, 라벨·검색 등 제공자 고유 기능
- 이메일 외 소스 구현체(Slack/RSS/파일 감시). 단, 어댑터 경계는 이를 나중에 꽂을 수 있도록 설계한다.
- hook 실행 결과의 artifact 파일 생성(초기에는 결과 텍스트만 기록)

## 2. 구성요소

기존 루프/워커 패턴을 복제한다. 신규 모듈:

```
hooks.py            HookService     — hooks CRUD, cursor 갱신, 실행할 새 항목 판별
hook_runs.py        HookRunService  — hook_run 생성/상태전이/조회 (jobs.py와 동형)
sources/base.py     SourceAdapter   — poll(connection, cursor) → (items, new_cursor)
sources/email.py    ImapEmailAdapter— IMAP 폴링, UID 기반 신규 판별, 필터
hook_secrets.py     HookSecretStore — 연결 비밀(앱 비번)을 auth_dir 스타일 파일로 저장
hook_loop.py        HookLoop        — SchedulerLoop 동형: N초마다 poll→hook_run(queued) 생성+enqueue
hook_runner.py      HookRunner      — JobWorker 동형: queued run을 순차 실행(runtime_factory 재사용)
api/hooks.py        hooks_router    — hook CRUD, 연결 테스트, run-now, hook_run 목록/재실행
```

각 단위의 책임과 경계:

- **HookService**: `hooks` 테이블의 source of truth. hook CRUD, enabled/poll 주기 판정, cursor·last_polled_at·last_error 갱신. 소스 종류에 따라 알맞은 `SourceAdapter`를 선택한다.
- **SourceAdapter**: 순수 인터페이스. `poll(connection, cursor) -> (items, new_cursor)`. 이메일 지식은 전부 `ImapEmailAdapter`에 격리한다. 새 소스는 이 인터페이스 구현만 추가한다.
- **HookRunService**: `hook_runs` 테이블의 source of truth. 상태 전이(`queued→running→succeeded|failed`)는 `JobService`와 동형으로 만든다.
- **HookLoop**: 감지 + enqueue만 담당. 주기가 도래한 enabled hook을 순회하며 adapter.poll → 필터 통과 항목마다 `hook_run(queued)` 생성 → `HookRunner`에 enqueue → cursor 갱신.
- **HookRunner**: queued hook_run을 순차 실행. `runtime_factory.create_default_runtime()`을 재사용하고, `AgentRunner`와 동일한 headless 안전장치(승인 필요 도구는 자동 승인 불가 → 실패 처리)를 적용한다.
- **HookSecretStore**: IMAP 앱 비밀번호를 `AuthStore`와 같은 파일 기반 방식으로 저장한다. hook 행에는 `connection_ref`만 남는다.

앱 수명주기: `app.py` lifespan에서 `scheduler_loop`/`job_worker`와 나란히 `HookLoop`+`HookRunner`를 `start()/stop()` 한다. startup 시 `running`으로 남은 hook_run은 `failed`로 정규화한다.

## 3. 데이터 모델

기존 스키마는 변경하지 않고 신규 테이블 2개를 `db.py`의 `SCHEMA_SQL`에 추가한다.

```sql
create table if not exists hooks (
    id text primary key,
    name text not null,
    source_type text not null,          -- 'email' (첫 어댑터)
    connection_ref text not null,       -- HookSecretStore의 파일 키 (비밀은 DB에 없음)
    filter_json text not null default '{}',   -- {from_contains, subject_contains, folder}
    target_backend text not null,       -- 'codex' | 'claude'
    target_model text not null,
    target_options_json text not null default '{}',
    prompt_template text not null,      -- {{from}} {{subject}} {{body}} 치환
    poll_interval_seconds integer not null default 300,
    enabled integer not null,
    cursor_json text,                   -- {uidvalidity, last_uid}
    last_polled_at text,
    last_error text,
    created_at text not null,
    updated_at text not null
);

create table if not exists hook_runs (
    id text primary key,
    hook_id text not null,
    dedup_key text not null,            -- 'email:{uidvalidity}:{uid}' 중복실행 차단
    trigger_summary text not null,      -- "메일: (제목) — (발신자)"
    trigger_payload_json text not null, -- 정규화된 이벤트(from/subject/date/body_text 등)
    status text not null,               -- queued|running|succeeded|failed
    result_text text,
    error_message text,
    created_at text not null,
    started_at text,
    finished_at text,
    foreign key (hook_id) references hooks(id) on delete cascade,
    unique(hook_id, dedup_key)
);
```

- **비밀 분리**: IMAP 앱 비번은 DB가 아니라 `HookSecretStore`(파일)에 둔다. 기존 `AuthStore`가 비밀을 파일로 두는 보안 태세와 일치한다.
- **dedup**: `unique(hook_id, dedup_key)`로 재시작·중복 폴링에도 같은 메일을 두 번 실행하지 않는다.
- **cursor 소유**: hook 행의 `cursor_json`이 "어디까지 봤는가"의 source of truth다.

### 신규 설정 (config.py)

- `AGENT_HOOK_POLL_INTERVAL_SECONDS`: HookLoop 기본 틱 간격(기본 30초, hook별 `poll_interval_seconds`와 별개로 루프 자체 주기). hook은 자신의 `poll_interval_seconds`가 지난 경우에만 폴링된다.
- hooks 비밀 디렉토리: 기본 `data_root/hooks` (auth_dir와 동일한 파생 방식).

## 4. 실행 흐름

```mermaid
sequenceDiagram
    participant Loop as HookLoop (틱)
    participant Hook as HookService
    participant Adapter as ImapEmailAdapter
    participant Run as HookRunService
    participant Runner as HookRunner
    participant RT as AgentRuntime(headless)
    participant SSE as EventBus

    Loop->>Hook: enabled + poll 주기 도달 hook 조회
    Hook->>Adapter: poll(connection, cursor)
    Adapter-->>Hook: 새 항목들 + new_cursor
    loop 필터 통과 항목마다
        Hook->>Run: hook_run(queued) 생성 (dedup_key unique)
        Run-->>Loop: run.id
        Loop->>Runner: enqueue(run.id)
    end
    Hook->>Hook: cursor / last_polled_at 갱신
    Runner->>RT: prompt_template 치환 후 실행
    RT-->>Runner: 결과 텍스트 (또는 승인대기 = 실패)
    Runner->>Run: result / error 기록
    Runner->>SSE: hook.run.updated 발행
    SSE-->>UI: hook run 목록 · 알림 갱신
```

유지해야 할 계약:

- **cursor는 poll 성공 시에만 전진**한다. poll 실패 시 cursor를 그대로 두어 다음 주기에 재시도한다.
- hook_run 생성이 dedup(`unique` 제약)으로 실패하면 이미 처리한 항목이므로 조용히 건너뛴다.
- HookLoop는 **감지 + enqueue만** 하고, agent 실행은 HookRunner가 순차 처리한다. IMAP I/O와 agent 실행이 서로를 막지 않는다.
- 재시작 복구: startup에서 `running`으로 남은 hook_run은 `failed`("gateway restarted while hook run was running")로 정규화한다.

## 5. 이메일 어댑터

`ImapEmailAdapter`는 표준 라이브러리 `imaplib`만 사용한다.

- 폴더 선택(기본 `INBOX`) → `UID SEARCH` → cursor의 `last_uid` 초과 UID만 fetch.
- cursor = `{uidvalidity, last_uid}`.
- `uidvalidity`가 변하면 메일박스가 재구성된 것이므로 과거 메일 폭주를 막기 위해 현재 최신 UID로 cursor를 리셋하고 이번 회차는 실행하지 않는다.
- **필터**(`filter_json`): `from_contains`, `subject_contains`, `folder`. 소스 단계에서 미통과 항목은 hook_run 자체를 만들지 않는다.
- **정규화 이벤트**: `{from, subject, date, body_text}`. 본문은 `text/plain` 파트 우선, 길이 상한을 두고 잘라 프롬프트에 주입한다.
- **prompt_template 치환**: `{{from}}`, `{{subject}}`, `{{body}}`, `{{date}}` 자리표시자를 정규화 이벤트 값으로 치환해 최종 프롬프트를 만든다.

## 6. 보안

- 이메일 본문·제목·발신자는 **신뢰 불가 입력**이며 프롬프트 인젝션 위험이 있다. 실제 행동 격리는 hook의 `target_options`가 지정하는 codex `sandbox`/`approval_policy` 또는 claude `permission_mode`에 달려 있다. 이메일 hook은 보수적 설정(예: codex `sandbox=read-only`)을 권장한다.
- `AgentRunner`와 동일하게, agent가 mid-turn 도구 승인을 요구해 실행이 멈추면 headless에서 응답할 수 없으므로 hook_run을 `failed`로 처리한다. 이는 **backstop**이지 "shell을 절대 실행하지 않는다"는 보장이 아니다(`approval_policy=never`면 승인 없이 실행된다). 이 위험 수준은 기존 scheduled agent job과 동일하다.
- 앱 비밀번호는 `HookSecretStore` 파일에만 저장하고 DB·로그·오류·SSE 어디에도 노출하지 않는다. 오류 메시지에는 기존 `_redact_text` 방식(비밀 치환)을 재사용한다.
- hook API는 기존 `require_session` 의존성으로 보호한다.
- **결정 기록(2026-07-15, 프론트엔드 구현 후 재검토)**: 이메일 hook의 Agent 기본 posture는 permissive(codex `sandbox=workspace-write`/`approval_policy=never`)를 **그대로 유지**하기로 확정. 기존 scheduled agent job과 동일 위험 수준으로 간주하며, 필요 시 hook 생성 시 AgentPicker로 hook별 `read-only` 설정이 가능하다.

## 7. 오류 처리

- **poll 실패**(인증·네트워크): `hooks.last_error`에 기록, cursor 미전진, 다음 주기에 재시도. 루프 자체는 죽지 않는다(`SchedulerLoop`의 예외 격리 방식과 동일).
- **실행 실패**: hook_run을 `failed`로 두고 `error_message`를 기록한다.
- **승인 대기**: agent가 mid-turn 도구 승인을 요구하면 headless에서 응답할 수 없으므로 `failed`로 처리한다.

## 8. API 표면 (api/hooks.py)

기존 `api/schedules.py`·`api/jobs.py` 라우터 패턴을 따른다.

- `POST /api/hooks` — hook 생성(연결 비밀은 별도 필드로 받아 HookSecretStore에 저장, 응답에는 비밀 미포함)
- `GET /api/hooks` — hook 목록
- `GET /api/hooks/{id}` — hook 상세
- `PATCH /api/hooks/{id}` — 설정 수정 / enable·disable
- `DELETE /api/hooks/{id}` — hook 삭제(연결 비밀 파일도 함께 제거)
- `POST /api/hooks/{id}/test-connection` — IMAP 연결·인증만 검증(메일 처리 없음)
- `POST /api/hooks/{id}/run-now` — 즉시 1회 폴링(수동 트리거)
- `GET /api/hooks/{id}/runs` — hook_run 목록
- `POST /api/hooks/{id}/runs/{run_id}/rerun` — 저장된 trigger_payload로 재실행

## 9. 테스트 전략

실제 IMAP 서버 없이 pytest로 검증한다.

- **HookLoop / HookRunner**: `FakeSourceAdapter`로 단위 테스트 — 신규 항목 감지 → run 생성, dedup 재폴링 시 중복 없음, cursor 전진, 필터 배제 항목은 run 미생성.
- **HookRunService**: 상태 전이(`jobs.py` 테스트 패턴 복제), 승인 대기 → failed.
- **api/hooks.py**: 기존 `test_api_*` 패턴 — CRUD, run-now, rerun, 응답에 비밀 미노출, 미인증 접근 차단.
- **ImapEmailAdapter**: `imaplib` 응답을 스텁으로 두고 UID 파싱·필터·정규화·uidvalidity 리셋 로직 단위 테스트.

## 10. 근거 파일

- `src/personal_agent_gateway/scheduler_loop.py` — HookLoop의 본
- `src/personal_agent_gateway/job_worker.py` — HookRunner의 본
- `src/personal_agent_gateway/schedules.py`, `jobs.py` — 서비스·상태전이 패턴
- `src/personal_agent_gateway/runners/agent.py` — headless 실행 + 승인불가 안전장치
- `src/personal_agent_gateway/auth_store.py` — 파일 기반 비밀 저장 태세
- `src/personal_agent_gateway/app.py` — lifespan 배선, `_attach_local_services`
- `src/personal_agent_gateway/db.py` — 스키마·additive 마이그레이션 방식
- `src/personal_agent_gateway/config.py` — 설정·데이터 경로 파생
</content>
</invoke>
