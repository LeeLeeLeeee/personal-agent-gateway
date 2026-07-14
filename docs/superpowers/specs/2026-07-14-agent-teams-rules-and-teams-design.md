# Agent Teams: 팀 엔티티 · 규칙(Rules) · 목록/상세/규칙 화면 설계

작성일: 2026-07-14

## 배경

Claude Design의 `Agent Teams.dc.html`에는 다섯 화면(Teams Home, New Team Run,
Personas, Team Run Detail, Rules)이 있다. 이 중 **Team Runs 목록**, **Team Run 상세**,
**Rules** 세 화면을 반영한다. 그러나 디자인은 현재 백엔드에 없는 개념을 전제한다.

현재 구조:

- `team_runs`만 존재하고 재사용 가능한 "팀" 엔티티는 없다. 각 실행은 시작 시 리더 페르소나
  1명과 멤버 페르소나 N명을 직접 골라 스냅샷으로 고정한다.
- 페르소나는 `name/role/description/responsibilities/constraints/backend/model/avatar`를
  가지지만 공유 "규칙"이나 "성격(personality)" 개념은 없다.
- 런타임(`team_runtime.py`)의 프롬프트는 페르소나 스냅샷 JSON만 삽입한다.
- 목록 API(`GET /api/team-runs`)는 Run 수준 필드만 반환한다(리더·멤버·Task 카운트 없음).
- 상세 화면(`TeamRunDetail`)은 이미 에이전트 레인과 Task 카드에 아바타 이미지를 렌더한다.

디자인은 (1) 팀별 규칙 선택기, (2) 목록 카드의 리더/멤버/진행바, (3) 성격·규칙 편집을
요구한다. 따라서 화면 반영 전에 데이터 구조와 프로세스를 먼저 바꾼다.

## 결정 요약

사용자와 확정한 방향:

1. **팀(Team)을 새 엔티티로 도입**하고 실행은 팀에서 시작한다. 실행의 로스터는 **팀 로스터 고정**.
2. **규칙(Rules)** 은 세 스코프로 둔다: `Global`(모든 실행 상속), `팀별`(Global 위에 추가),
   `Persona Baseline`(모든 페르소나 상속). 각 규칙 세트는 **personality(성격·말투) + rules[]**.
3. 규칙 적용은 **프롬프트 주입(soft)** 이다. REQUIRED/GUIDELINE은 강조 차이일 뿐 하드 게이트
   없음.
4. 규칙은 **편집 가능(CRUD)**.
5. 상세 화면은 **디자인 D에 맞춰 레이아웃 정렬**하되 보드의 아바타 이미지는 유지한다.
6. 기존 팀 없는 실행은 **레거시(읽기전용)** 로 유지, 신규 실행은 팀 필수.
7. **팀 생성·로스터 편집은 신규 `Teams` 화면**에서 한다(디자인엔 없는 화면). Rules 화면은
   규칙만 다룬다.
8. 상세에 **DOCUMENTS 탭**을 추가해 팀 워크스페이스 파일을 브라우징하고 모달로 프리뷰한다.

용어: 디자인 라벨의 "Charter"는 쓰지 않는다. 코드·데이터·UI 모두 **Rules/규칙**으로 통일한다.
성격·말투 항목(personality)은 유지한다.

## 목표

- 팀 엔티티(로스터 + 규칙)를 만들고 실행이 팀에서 시작되게 한다.
- 실행 시작 시점에 규칙(Global + 팀 + Persona Baseline)을 합성해 **스냅샷으로 고정**하고
  런타임 프롬프트에 주입한다("실행 시작 시 동결" 반영).
- Team Runs 목록을 디자인 A에 맞춰 리치 카드 + 상태 필터로 만든다.
- Team Run 상세를 디자인 D에 맞춰 정렬하고 보드에 아바타 이미지를 유지한다.
- Rules 화면에서 세 스코프의 성격·규칙을 조회/편집한다.
- 신규 Teams 화면에서 팀 CRUD와 로스터 할당을 한다.
- 상세 DOCUMENTS 탭에서 워크스페이스 파일을 안전하게 나열·프리뷰한다.

## 비목표

- REQUIRED 규칙의 하드 런타임 게이트(검증 통과 강제) — soft 주입만 한다.
- 디자인 상세의 **VERIFICATION EVIDENCE** — 뒷받침 데이터 모델이 없어 이번 범위에서 제외한다.
  RESULTS는 에이전트 리포트 + 최종 요약만 표시한다.
- 이미지/바이너리 문서 렌더링 — 텍스트 계열(md/json/txt/코드) 프리뷰 우선. 그 외는
  "미리보기 불가"로 표시한다.
- 기존 레거시(팀 없는) 실행의 팀 소급 마이그레이션.
- Personas 화면·New Team Run 폼의 시각 리디자인(디자인 B/C). 단, New Team Run은 팀 선택
  방식으로 **동작을 교체**한다.

## 데이터 모델

`app.sqlite`에 두 테이블을 추가하고 `team_runs`에 두 컬럼을 추가한다.

### `teams`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | text pk | uuid hex |
| `name` | text | 팀 이름 |
| `description` | text | 설명(빈 문자열 허용) |
| `leader_persona_id` | text | 리더 페르소나 |
| `member_persona_ids_json` | text | 멤버 페르소나 id 배열(순서 유지) |
| `created_at` / `updated_at` | text | ISO |

멤버는 조인 테이블 대신 JSON 배열로 저장한다(기존 `persona_snapshot_json` 패턴과 일관).
페르소나 삭제 시 정합성은 실행 시작 시 스냅샷으로 확정되므로 팀 정의는 느슨하게 둔다. 팀 편집
저장 시 존재하지 않는 페르소나 id는 거부한다.

### `rule_sets`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | text pk | uuid hex |
| `scope` | text | `global` \| `persona_baseline` \| `team` |
| `team_id` | text null | `scope=team`일 때만 채움 |
| `personality` | text | 성격·말투 문단 |
| `rules_json` | text | `[{"level":"REQUIRED"\|"GUIDELINE","text":"..."}]` |
| `updated_at` | text | ISO |

`unique(scope, team_id)`로 스코프당 한 세트를 보장한다(`global`/`persona_baseline`은
`team_id`가 null인 단일 행). 팀 생성 시 해당 팀의 빈 `team` rule_set을 함께 만든다. 팀 삭제 시
해당 rule_set을 함께 삭제한다.

시드: 앱 초기화 시 `global`, `persona_baseline` 행이 없으면 디자인의 기본 문구로 시드한다.

### `team_runs` 추가 컬럼

- `team_id` text null — 실행이 시작된 팀. 레거시 실행은 null.
- `rules_snapshot_json` text null — 실행 생성 시 합성한 규칙 스냅샷.

`db.py`의 backfill 섹션에서 두 컬럼을 `alter table add column`으로 추가한다(기존 마이그레이션
패턴과 동일). 레거시 실행은 두 값 모두 null로 남는다.

### 규칙 스냅샷 형태

```json
{
  "global": {"personality": "...", "rules": [{"level": "REQUIRED", "text": "..."}]},
  "team": {"name": "...", "personality": "...", "rules": [...]},
  "persona_baseline": {"personality": "...", "rules": [...]}
}
```

레거시 실행(스냅샷 null)은 런타임에서 규칙 주입 없이 기존과 동일하게 동작한다.

## 런타임: 규칙 주입

`TeamRunService.create_team_run`을 팀 기반으로 바꾼다.

- 입력: `team_id`, `goal`, `run_mode`, `max_workers`.
- 팀 로스터에서 리더/멤버 페르소나를 읽어 기존과 동일하게 에이전트 스냅샷을 만든다.
- `RuleSetService`로 `global` + 해당 `team` + `persona_baseline`을 합성해
  `rules_snapshot_json`에 고정 저장한다.

`team_runtime.py`의 프롬프트 조립을 바꾼다.

- 공통 헬퍼 `_rules_block(snapshot, section)`가 스냅샷에서 텍스트 블록을 만든다.
  - personality는 "TEAM PERSONALITY & VOICE" 등의 헤더로 문단을 넣는다.
  - REQUIRED 규칙은 "MUST:" 접두, GUIDELINE은 "SHOULD:" 접두로 나열한다.
- 리더 프롬프트(PLANNING/SYNTHESIS/MEDIATION/ADD_WORK): global + team 규칙 블록을 선두에 둔다.
- 워커 프롬프트(WORKER): global + team 규칙 + persona_baseline 규칙 블록을 페르소나 스냅샷
  앞에 둔다.
- 스냅샷이 null이면(레거시) 블록을 넣지 않는다.

하드 게이트는 없다. 규칙은 지시문일 뿐이며 실행 흐름/상태 전이를 바꾸지 않는다.

## API

### Teams

- `GET /api/teams` — 팀 목록. 각 팀에 리더/멤버 페르소나 요약(name, avatar, role) 포함.
- `POST /api/teams` — `{name, description, leader_persona_id, member_persona_ids}`.
- `GET /api/teams/{id}` — 팀 상세(로스터 페르소나 요약 포함).
- `PUT /api/teams/{id}` — 이름/설명/로스터 수정.
- `DELETE /api/teams/{id}` — 팀과 팀 rule_set 삭제. 진행 중 실행이 참조해도 실행 스냅샷은
  이미 고정이므로 삭제를 허용한다(실행은 스냅샷으로 계속 동작).

존재하지 않는 페르소나 id 포함 시 `400`. 리더가 멤버 목록에 중복되어도 허용(리더 역할 우선).

### Rules

- `GET /api/rules` — `{global, persona_baseline, teams: [{team_id, name, ...}]}` 형태로 모든
  규칙 세트 반환.
- `GET /api/rules/global`, `GET /api/rules/persona-baseline`,
  `GET /api/teams/{id}/rules` — 개별 조회.
- `PUT` 대응 엔드포인트 — `{personality, rules:[{level,text}]}` 전체 교체.

규칙 항목은 순서 있는 배열 전체 교체(PUT)로 다룬다. 추가/삭제/순서변경/레벨토글은 프런트에서
배열을 조작해 PUT 한 번으로 저장한다. `level`은 `REQUIRED`/`GUIDELINE`만 허용.

### Team runs

- `POST /api/team-runs` — 요청을 `{team_id, goal, run_mode, max_workers}`로 교체한다.
  기존 `leader_persona_id`/`member_persona_ids` 필드는 제거한다.
- `GET /api/team-runs` — 응답을 enrich한다. Run별로:
  - `leader_name`
  - `members`: `[{name, avatar, initials}]` (에이전트 스냅샷 기준)
  - `task_counts`: 상태별 카운트 `{pending, in_progress, blocked, completed, failed, ...}`
  - `task_done` / `task_total`
  - `elapsed_seconds`: `started_at`~`finished_at`(없으면 now) 차이. 상태에 따라 UI가
    ELAPSED/TOOK 라벨 선택.
  - `team_id`

  enrich는 서비스에 목록 전용 메서드를 추가해 Run + 에이전트 + Task를 한 번에 모아 만든다.
- 기타 엔드포인트(start/resume/retry/add-work/cancel/delete/agents/tasks/messages)는 유지.

### Documents

- `GET /api/team-runs/{id}/documents` — 실행 워크스페이스(`workspace_root`) 아래 파일을
  재귀 나열. 각 항목: `path`(워크스페이스 상대), `size`, `modified_at`, `kind`(md/json/text/
  code/binary), `previewable`(bool). 숨김/대용량(예: 1MB 초과) 파일은 `previewable=false`.
- `GET /api/team-runs/{id}/documents/content?path=...` — 파일 내용을 텍스트로 반환.
  `{path, kind, content, truncated}`.
- **경로 안전**: `delete_team_run`이 쓰는 것과 동일한 워크스페이스 경계 검증을 재사용한다.
  요청 `path`를 워크스페이스 루트에 resolve해 루트 밖이면 `400`. 심볼릭 링크 이탈도 차단한다.
- 미리보기 불가(바이너리/대용량): `content` 없이 `previewable=false`와 사유를 반환.

## 화면

### 1) Team Runs 목록 (디자인 A)

- 헤더 "Team Runs" + 부제 + **New team run** 버튼.
- STATUS 필터 칩: All / Running / Completed / Failed. Running은 `running`+`planning` 포함.
- Run 카드(가로):
  - 좌: id, 상태 배지, mode, goal(headline), `LEADER` 이름 박스, `MEMBERS` 아바타 묶음.
  - 우(고정폭): `TASKS x / y DONE`, 상태별 색 세그먼트 진행바, `ELAPSED|TOOK · 시간`, `OPEN →`.
- 멤버 아바타는 이미지 우선, 없으면 이니셜 박스 폴백.
- 레거시(team_id null) 실행도 표시하되 카드에 `LEGACY` 표식.

### 2) New team run 흐름 (기존 폼 동작 교체)

- 팀 선택(목록/드롭다운) → 선택 팀의 로스터(리더+멤버)를 **읽기전용**으로 표시(고정).
- goal 입력, run mode, max workers 설정 → 시작.
- 팀이 하나도 없으면 "먼저 팀을 만드세요" 안내와 Teams 화면 링크.
- 기존 페르소나 직접 선택 UI(`TeamRunForm`의 리더/멤버 선택)는 제거한다.

### 3) Team Run 상세 (디자인 D)

- 헤더: id, 상태 배지, goal.
- 메타 스트립(5칸): LEADER / MODE / ELAPSED / WORKERS / STARTED.
- **AGENT SESSIONS** 레인 그리드: 아바타 이미지(폴백 이니셜), 상태 dot+라벨, 현재 Task,
  `SNAPSHOT · backend/model`. 리더 강조 테두리.
- **TASK BOARD** 5열(pending/in_progress/blocked/completed/failed): 카드에 제목 + owner
  아바타 이미지 + note. 컬럼 헤더 색상은 디자인 규칙(진행=주황, 실패=빨강, 완료=검정 등).
- **탭바**: `RESULTS` / `LIVE ACTIVITY` / `SHARED·HANDOFFS` / `DOCUMENTS` + 우측 `+ ADD WORK`.
  - RESULTS(기본): AGENT REPORTS(`agent_output` 메시지) + FINAL SUMMARY(`run.summary`).
    (VERIFICATION EVIDENCE 제외.)
  - LIVE ACTIVITY: 메시지 타임라인.
  - SHARED·HANDOFFS: query/answer 페어(기존 로직 재사용).
  - DOCUMENTS: 워크스페이스 파일 목록 → 항목 클릭 시 **모달 프리뷰**.
- interrupted 배너와 Resume 버튼(기존) 유지.

디자인 목업의 탭 정의에는 RESULTS 버튼이 빠져 있으나(정적 예시 오류), 세 패널이 모두
존재하므로 RESULTS를 기본 탭으로 포함한다.

### 4) Rules 화면 (디자인 E)

- scope 탭: `TEAM RULES` / `PERSONA BASELINE`.
- TEAM RULES: 팀 선택기(`GLOBAL` + 각 팀). `GLOBAL`은 기본 규칙, 개별 팀은 상속 배너
  ("Global 규칙이 그대로 적용됩니다. 아래는 이 팀에만 추가되는 규칙입니다") + 추가 규칙.
- 좌측: **PERSONALITY & VOICE** 편집(텍스트), 규칙 목록(번호 + REQUIRED/GUIDELINE 배지 +
  텍스트), `+ ADD RULE`, 항목 편집/삭제/레벨 토글.
- 우측: 스코프 메타(예: 적용 대상, 마지막 편집) + ENFORCEMENT 범례(REQUIRED=차단성 표기,
  GUIDELINE=권고). 실제 게이트는 없으므로 문구는 "가이드"로 표현한다.
- 저장은 스코프별 PUT. 저장 성공/실패 토스트.

### 5) Teams 화면 (신규, 디자인 없음)

- 사이드바 TEAMS 섹션에 `Teams` 추가: `Team Runs` / `Teams` / `Personas` / `Rules`.
- 팀 목록 + `New team`.
- 팀 편집 폼: name, description, 리더 페르소나(단일 선택), 멤버 페르소나(다중 선택,
  기존 아바타/이니셜 UI 재사용).
- 저장/삭제. 기존 브루탈리즘 톤(3px 테두리, mono 라벨)에 맞춰 신규 컴포넌트를 작성한다.
- 팀의 규칙 편집은 Rules 화면으로 연결(중복 편집 UI를 두지 않음).

## 프런트엔드 구조

`component-pattern`(atomic design) 규약을 따른다. React 앱(`frontend/src`)이 대상이며,
빌드 산출물은 `src/personal_agent_gateway/frontend_dist`로 반영된다(app.py가 이를 서빙).

- 신규 organisms: `TeamsView`, `RulesView`, `DocumentPreview`(모달), 상세용 문서 패널.
- 신규/수정 molecules: `TeamRunCard`(목록 카드), `RuleRow`, `DocumentListItem`,
  `TeamRosterPicker`.
- `GatewayApp`에 `teams`(관리)·`rules` 스크린과 상태/로더 추가. 기존 `teams` 스크린 키는
  실행 목록/상세를 유지하되 관리 화면과 구분되는 새 키(예: `team-admin`)를 도입한다.
- `Sidebar`의 `TEAM_NAV`에 항목 추가.
- API 클라이언트(`api/client.js`)에 teams/rules/documents 호출 추가.

## API 클라이언트/이벤트

- 기존 SSE 처리에서 `team.*` 이벤트로 상세를 재조회하는 로직 유지. 문서 목록은 상세 재조회
  또는 DOCUMENTS 탭 진입 시 갱신한다.

## 테스트 전략

TDD로 진행한다.

### 백엔드

- `TeamService`: 팀 CRUD, 로스터 검증(없는 페르소나 거부), 삭제 시 rule_set 동반 삭제.
- `RuleSetService`: 스코프별 조회/업서트, 시드, `unique(scope, team_id)` 보장, 팀 삭제 연동.
- `create_team_run`(팀 기반): 로스터로 에이전트 생성, 규칙 스냅샷 합성/고정. 레거시 경로 부재
  확인.
- 런타임 주입: 스냅샷 있으면 프롬프트에 규칙 블록 포함, 없으면 미포함(레거시). REQUIRED/
  GUIDELINE 접두 표기.
- 목록 enrich: 리더명/멤버/Task 카운트/elapsed 계산.
- Documents: 목록·내용 반환, 워크스페이스 밖 경로/심링크 이탈 `400`, 대용량/바이너리
  `previewable=false`.

### 프런트엔드

- 목록: 상태 필터, 카드 렌더(리더/멤버/진행바/elapsed), 레거시 표식.
- 상세: 탭 전환, 보드 아바타 렌더, DOCUMENTS 목록→모달 프리뷰(md/json/text), 미리보기 불가
  처리.
- Rules: 스코프/팀 전환, personality 편집, 규칙 추가/삭제/레벨 토글/저장.
- Teams: 팀 생성/편집/삭제, 로스터 선택.

### 통합 검증

1. 팀 생성 → 로스터 할당 → Rules에서 팀 규칙 편집.
2. New team run으로 팀에서 실행 시작 → 규칙 스냅샷 고정 확인.
3. 목록 카드/필터, 상세 보드 아바타, DOCUMENTS 프리뷰 확인.
4. 레거시 실행이 목록/상세에서 읽기전용으로 보이는지 확인.
5. 전체 백엔드·프런트 테스트와 프런트 빌드 실행.

## 마이그레이션/레거시

- `team_runs.team_id`, `rules_snapshot_json`을 backfill로 추가(기존 행 null 유지).
- 레거시 실행은 목록에 `LEGACY` 표식, 상세는 규칙 주입 없이 기존 데이터로 조회 가능.
- 앱 초기화 시 `global`/`persona_baseline` rule_set 시드.

## 재검토 조건

- REQUIRED 규칙의 실제 강제(게이트)가 요구되면 검증/게이팅 메커니즘을 별도 설계한다.
- 문서가 이미지/바이너리 중심이 되면 프리뷰 렌더러를 확장한다.
- 팀 수가 많아져 규칙/로스터 관리가 무거워지면 Teams·Rules 화면을 분리·검색 가능하게 한다.
