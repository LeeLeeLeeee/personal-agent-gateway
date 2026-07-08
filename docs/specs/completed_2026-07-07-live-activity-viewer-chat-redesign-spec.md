# Live Activity Viewer — Chat 화면 개편 Spec

- 작성일: 2026-07-07
- 대상: `src/personal_agent_gateway/static/app.js`, `styles.css`
- 디자인 출처: `Live Activity Viewer.dc.html`, `ActivityStream.dc.html` (claude.ai/design 프로젝트 "UI/UX 기획서 기반 디자인")
- 범위: 프론트엔드 전용. 백엔드/API 변경 없음.

## 1. 배경 / 문제

현재 Chat 화면은 대화 내용과 에이전트 활동을 **두 곳에 분리**해서 보여준다.

- `state.messages`: `/api/history`에서 로드 → 가운데 트랜스크립트에 렌더
- `state.activity`: `/api/events`(SSE)에서 수신 → **오른쪽 드로어**에 최신순·최대 40개 목록으로 렌더

이 구조는 "에이전트가 지금 무엇을 하고 있는가"를 좁은 드로어에 가두고, 명령 실행 결과를 `$ cmd / exit / output` 평문으로만 보여준다. 사용자가 실행 흐름(생각 → 명령 → 출력 → 완료/실패 → 산출물)을 시간순으로 스캔하기 어렵다.

Live Activity Viewer 디자인은 이를 **하나의 인라인 활동 트랜스크립트**로 통합한다: 메시지·이벤트·명령 블록·산출물·오류가 시간순으로 한 흐름에 섞이고, 최신이 아래에 쌓인다. 승인 흐름은 제거되지 않는다(Agent Gateway 목업에 승인 UI가 그대로 존재). Live Activity Viewer 슬라이스가 승인을 안 그릴 뿐이다.

## 2. 목표 / 성공 기준

- Chat 트랜스크립트가 `/api/history` + `/api/events`를 병합한 단일 시간순 타임라인으로 렌더된다.
- 명령 실행이 접힘/펼침 가능한 CommandExecutionBlock(배지·터미널 출력·EXIT·DURATION)으로 표시된다.
- 헤더 아래 LiveStatusSummary 바가 PHASE / RUNNING / LAST EVENT / ELAPSED를 표시한다.
- 상단 상태 바에 SSE 연결 상태(깜빡이는 점 + STREAMING/CONNECTED/IDLE), PHASE, EVENTS가 표시되고 RUNNING이 실제 개수와 연결된다.
- 오른쪽 활동 드로어가 제거되고, 대기 승인은 트랜스크립트 맨 아래 인라인 카드로 표시된다.
- 새로고침·세션 전환 후에도 `/api/history`로 타임라인(메시지 + 완료된 명령 블록 + 오류)이 재구성된다.
- idle 상태에서 "AGENT IDLE — 메시지를 보내면 활동이 여기 실시간으로 나타남" 빈 상태가 보인다.
- 데스크톱 / 좁은 화면(<900px) / 모바일(390px)에서 레이아웃이 깨지지 않는다.

## 3. 데이터 모델

### 3.1 통합 타임라인

`state.messages` + `state.activity`를 `state.timeline`(정렬된 배열)로 대체한다. 각 엔트리는 판별 가능한 타입을 가진다.

| 타입 | 렌더러 | 정렬 키 |
|---|---|---|
| `user` | 사용자 메시지(우측 정렬) | history 순서 / SSE 수신순 |
| `agent` | 에이전트 메시지(좌측, 스트리밍 커서) | 〃 |
| `event_row` | 활동 이벤트 행(스파인+점+`time · label · detail`) | 〃 |
| `command` | CommandExecutionBlock(접힘/펼침) | 〃 |
| `artifact` | ARTIFACT READY 카드 | 완료 시 append |
| `runtime_error` | 빨간 배너 | 〃 |
| `approval` | 인라인 Job Proposal 카드(항상 맨 아래) | 파생 |

### 3.2 소스 매핑 (백엔드 변경 없음)

**`/api/history` 이벤트 `.kind` →**
- `user` → `user`
- `assistant` → `agent`
- `tool_result` → `command` (완료/실패: `exit_code`와 `stdout`/`stderr`로 판정, `command` 표시)
- `tool_denial` → `event_row` (denied)
- `runtime_error` → `runtime_error`
- 대기 중 `tool_request(name=shell.run)` → `approval`

**`/api/events` SSE →**
- `codex.event` + `item.type=command_execution` → `command` (라이브, `item.status`→배지, `item.aggregated_output`→출력, `item.exit_code`)
- `codex.event` + `item.type=agent_message` → 스트리밍 `agent` / `event_row`
- `runtime.user_message.started` → `event_row` (턴 시작, 검정 점)
- `runtime.completed` → `event_row` (녹색) + 아티팩트 조회 트리거
- `runtime.error` → `runtime_error`

### 3.3 정합성(reconciliation)

- 로드 / `activate` / `reset` 시: `/api/history`로 영속 타임라인을 재구성한다(과거 명령은 완료/실패 상태의 CommandExecutionBlock).
- 라이브 턴 중: SSE가 임시 엔트리(라이브 명령 블록, 스트리밍 행)를 append.
- `/api/chat`(또는 approve/deny)가 resolve되면: history로 영속 부분을 다시 만들고, 이제 영속화된 임시 엔트리를 제거한다. **dedup 키 = 명령 문자열 + 현재 턴**. 라이브 command 블록은 history의 `tool_result`와 매칭되면 대체된다.

### 3.4 파생 라이브 상태

렌더 시 타임라인에서 계산:
- `phase`: `idle | working | command running | done | failed | error`
- `running`: 상태가 running인 command 블록 수
- `lastEvent`: 마지막 이벤트의 StatusBadge kind
- `elapsed`: `runtime.user_message.started` 이후 경과(활성 중 1초 간격 tick)
- `sse`: `EventSource.readyState` 기반 → `STREAMING`(턴 활성) / `CONNECTED`(idle) / `IDLE` / error

## 4. 레이아웃

### 4.1 Chat (드로어 제거)

`chat` = 세션 레일(그대로) + 전체 폭 채팅 컬럼. 채팅 컬럼(위→아래):

1. **Chat 헤더**: 세션 제목 + `SESSION · <title> · started HH:MM`
2. **LiveStatusSummary 바**: 4셀(PHASE / RUNNING / LAST EVENT 배지 / ELAPSED)
3. **통합 트랜스크립트**: `max-width:760px` 중앙, 시간순, 최신 아래. 하단 근처면 새 이벤트 시 자동 스크롤.
4. **컴포저**: 기존 유지. 대기 승인은 트랜스크립트 마지막 엔트리(`approval`)로 인라인 표시.

`renderChatDrawer` / `.drawer` / `.activity-*` 관련 코드·스타일 제거.

### 4.2 상단 상태 바

기존 셀(WORKSPACE/MODEL/SESSION/PENDING) 유지 + 변경:
- `RUNNING`: `state` 파생 running 개수와 연결(기존 "PLANNED" 제거)
- `PHASE` 셀 추가, `EVENTS` 셀 추가(타임라인 이벤트 수)
- 우측에 **SSE 점**(깜빡임) + 라벨(STREAMING/CONNECTED/IDLE)
- `TUNNEL`은 PLANNED 유지(범위 밖)

## 5. 컴포넌트 (styles.css + app.js)

목업 인라인 스타일을 기존 CSS 토큰(`--c-*`, `--font-*`, `--bd*`) 기반 재사용 클래스로 이식.

- `statusBadge(kind)` → RUNNING(주황, 깜빡 점)/COMPLETED(녹색)/FAILED(빨강)/ERROR(빨강 채움)/IDLE(회색)/WORKING(주황 깜빡) 배지 span 반환
- `renderChatHeader()`
- `renderLiveStatusSummary()`
- `renderTimeline()` → 엔트리 타입별 디스패치
- `renderCommandBlock(entry)` → 접힘 규칙: running=펼침, completed=접힘("SHOW OUTPUT · N LINES"), failed=펼침. 헤더($ cmd + 배지) / 검은 출력(max-height 스크롤, 색상 줄) / 푸터(EXIT + DURATION + 토글)
- `renderEventRow(entry)` → 스파인 점 + `time` + `label` + `detail`
- `renderArtifactCard(entry)`
- `renderRuntimeError(entry)`
- idle 빈 상태

## 6. 아티팩트 카드 (조건부)

`runtime.completed` 수신 시 `/api/artifacts` 조회 → `source_session_id`가 현재 활성 세션이고 이번 턴에 생성된 항목만 카드로 표시. 없으면 렌더하지 않음(투기적 작업 없음). 런타임이 아직 아티팩트를 생성하지 않으면 카드는 나타나지 않으며 무해하다.

## 7. 검증

프론트엔드 테스트 프레임워크 없음(바닐라 JS). 앱을 실제 Codex 런타임으로 구동하고 `/gstack-browse`로 상태별 확인:

- idle 빈 상태
- working(스트리밍 커서 + WORKING 배지)
- command running(라이브 출력 + RUNNING 배지 + running 카운트 + 경과 타이머)
- command completed(접힘 블록 + EXIT 0 + 녹색 완료 행)
- command failed(펼침 블록 + EXIT 1 + FAILED 배지)
- runtime error(빨간 배너)
- 대기 승인(인라인 카드 → approve/deny 동작)
- **새로고침 영속성**: 완료 명령 블록 + 메시지 재구성
- 세션 전환
- 모바일(390px) / 좁은 화면(<900px)

## 8. 범위 (YAGNI)

**포함**: Chat 활동 개편 전체(통합 타임라인, LiveStatusSummary, SSE 인디케이터, StatusBadge, CommandExecutionBlock, 인라인 승인, idle 상태, 조건부 아티팩트 카드, 반응형).

**제외**: Jobs/Schedules/Capabilities/Artifacts/Settings 화면(PLANNED 유지), TUNNEL 상태, 백엔드/API 변경, 완전한 영속 이벤트 로그.
