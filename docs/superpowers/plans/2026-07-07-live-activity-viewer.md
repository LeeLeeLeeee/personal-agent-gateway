# Live Activity Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat 화면의 대화·활동을 `/api/history` + `/api/events` 통합 단일 인라인 타임라인으로 개편한다.

**Architecture:** 기존 이원화(트랜스크립트 `state.messages` + 드로어 `state.activity`)를 단일 `state.timeline`으로 통합한다. history 이벤트와 SSE 이벤트를 공통 엔트리 타입으로 매핑·병합하고, 파생 라이브 상태(phase/running/elapsed/sse)를 계산해 LiveStatusSummary 바와 상태 바 인디케이터를 구동한다. 오른쪽 드로어를 제거하고 승인은 트랜스크립트 인라인 카드로 옮긴다.

**Tech Stack:** 순수 바닐라 JS(`app.js`, `el()` 팩토리), CSS(`styles.css`, `--c-*`/`--font-*`/`--bd*` 토큰), SSE(`EventSource`), FastAPI 백엔드(변경 없음).

## Global Constraints

- 프론트엔드 전용. `src/personal_agent_gateway/static/app.js`, `styles.css`만 수정. 백엔드/API 변경 금지.
- 기존 API 계약 유지: `/api/history`(events[].kind/payload), `/api/events`(SSE {type,item,...}), `/api/status`, `/api/chat`, `/api/artifacts`.
- 디자인 토큰만 사용: 색상 `--c-warn`(#FFA500)/`--c-ok`(#008000)/`--c-danger`(#FF0000)/`--c-grey`(#808080)/`--c-black`/`--c-white`, 폰트 `--font-mono`/`--font-headline`/`--font-body`.
- 테스트 프레임워크 없음 → 각 태스크는 앱 실행 + 브라우저 상태 확인으로 검증. 브라우징은 `/gstack-browse` 사용, `mcp__claude-in-chrome__*` 금지.
- 반응형 유지: 데스크톱 / <900px / 390px.

---

### Task 1: CSS — 신규 컴포넌트 클래스 추가

**Files:**
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Produces: 클래스 `.chat-header`, `.summary-bar`/`.summary-cell`/`.summary-k`/`.summary-v`, `.timeline`/`.tl-row`/`.tl-dot`/`.tl-time`/`.tl-label`/`.tl-detail`, `.cmd-block`/`.cmd-head`/`.cmd-out`/`.cmd-foot`/`.cmd-line`, `.badge`(+`.badge-running`/`-completed`/`-failed`/`-error`/`-idle`/`-working`), `.msg-user`/`.msg-agent`/`.agent-cursor`, `.artifact-card`, `.rt-error`, `.idle-empty`, `.sse-dot`/`.sse-label`. `@keyframes rb-blink`.

- [ ] **Step 1: 배지·타임라인·명령블록·요약바·헤더·메시지·아티팩트·오류·idle 클래스 추가**

`styles.css` 하단(`/* ---- responsive ---- */` 앞)에 추가. 목업 `Live Activity Viewer.dc.html` / `ActivityStream.dc.html`의 인라인 스타일을 토큰 기반 클래스로 이식. 핵심:
- `@keyframes rb-blink{0%,49%{opacity:1}50%,100%{opacity:0}}` (커서/점 깜빡임; 기존 loader용 rb-blink와 이름 충돌하므로 별도 키프레임 `rb-blink2` 사용하거나 점/커서는 인라인 애니메이션 유지 — 충돌 회피 위해 신규 이름 `blink-hard` 사용).
- `.badge{display:inline-flex;align-items:center;gap:6px;border:var(--bd-sm);font-family:var(--font-mono);font-size:10px;letter-spacing:1px;padding:2px 8px;white-space:nowrap}` + 상태 변형(색/테두리/채움), `.badge .dot{width:7px;height:7px;flex:none;animation:blink-hard 1s step-end infinite}`.
- `.timeline{border-left:3px solid var(--c-black);margin-left:8px;padding-left:22px;display:flex;flex-direction:column;gap:2px}` / `.tl-row{position:relative;...}` / `.tl-dot{position:absolute;left:-27px;top:9px;width:9px;height:9px}`.
- `.cmd-block{border:var(--bd)}` / `.cmd-head`(버튼, 좌측정렬, hover 없음) / `.cmd-out{background:var(--c-black);max-height:200px;overflow:auto;padding:11px 13px}` / `.cmd-line{font-family:var(--font-mono);font-size:11px;line-height:1.6;white-space:pre-wrap}` / `.cmd-foot`.
- `.summary-bar{display:flex;border-bottom:var(--bd)}` / `.summary-cell{flex:1;padding:9px 16px;border-right:var(--bd-in)}`.
- `.chat-header{border-bottom:var(--bd);padding:12px 20px;display:flex;align-items:center;gap:12px}`.
- `.msg-user`(우측정렬, `#F0F0F0` 배경) / `.msg-agent`(좌측) / `.agent-cursor{display:inline-block;width:8px;height:15px;background:var(--c-black);animation:blink-hard 1s step-end infinite}`.
- `.artifact-card{border:var(--bd)}`, `.rt-error{border:3px solid var(--c-danger)}`, `.idle-empty{border:var(--bd-in);padding:40px 24px;text-align:center}`.
- `.sse-dot{width:8px;height:8px}`, `.sse-label{font-family:var(--font-mono);font-size:11px}`, statusbar 우측 정렬용 `.sse-wrap{margin-left:auto;display:flex;align-items:center;gap:8px;padding:0 16px}`.

- [ ] **Step 2: 드로어 전용 스타일 정리(제거 예약)**

`.drawer`, `.activity-list`, `.activity-item`, `.activity-head`, `.activity-title`, `.activity-console` 는 Task 4에서 드로어 제거 후 삭제. 이 태스크에서는 남겨둔다(중간 상태에서 참조 존재).

- [ ] **Step 3: 검증** — 앱 로드해 CSS 파싱 오류 없음 확인(기존 화면 정상). `/gstack-browse`로 로그인→Chat 로드. 회귀 없음.

- [ ] **Step 4: Commit** — `git add styles.css && git commit -m "feat(ui): Live Activity Viewer 컴포넌트 CSS 클래스 추가"`

---

### Task 2: app.js — 통합 타임라인 데이터 모델

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js`

**Interfaces:**
- Produces:
  - `timelineFromHistory(events) -> Entry[]` — history 이벤트를 엔트리 배열로. 엔트리 shape: `{type, id?, role?, text?, time?, command?, status?, lines?, exit?, duration?, open?, label?, detail?, dotColor?, artifact?, message?}`.
  - `entryFromSseEvent(event) -> Entry | null` — SSE 이벤트 1건을 엔트리로.
  - `deriveLive(timeline, {busy, sseState}) -> {phase, phaseColor, running, lastKind, elapsedStart}` — 파생 상태.
  - `state.timeline: Entry[]`, `state.turnStart: number|null`, `state.sseState: 'idle'|'streaming'|'connected'|'error'`.
- Consumes: 기존 `state`, `api.history()`, `messagesFromEvents`(제거 예정), `activityItem`(제거 예정).

- [ ] **Step 1: state 확장 + 매핑 함수 작성**

`state`에 `timeline:[], turnStart:null, sseState:'idle'` 추가. `messagesFromEvents`를 `timelineFromHistory`로 대체:
- `user`→`{type:'user', text:p.content, time}`
- `assistant`→`{type:'agent', text:p.content}`
- `tool_result`→`{type:'command', command:p.command, status:(p.exit_code===0?'completed':'failed'), lines:[stdout/stderr를 줄 단위], exit:p.exit_code, open:(exit!==0)}`
- `tool_denial`→`{type:'event_row', label:'tool_denial', detail:p.command, dotColor:'#FF0000'}`
- `runtime_error`→`{type:'runtime_error', message:p.message}`
- 마지막 이벤트가 미해결 `tool_request(shell.run)`(=pending)면 `{type:'approval', ...}`는 렌더 단계에서 `state.pendingApproval`로 처리(별도).

`entryFromSseEvent(event)`:
- `codex.event`+`command_execution`→`{type:'command', command:item.command, status:item.status, lines:item.aggregated_output 분해, exit:item.exit_code, open:(item.status!=='completed'), live:true}`
- `codex.event`+`agent_message`→`{type:'agent', text:item.text, streaming:true}`
- `runtime.user_message.started`→`{type:'event_row', label:'runtime.user_message.started', detail:'message accepted', dotColor:'#000'}` + `state.turnStart` 세팅
- `runtime.completed`→`{type:'event_row', label:'runtime.completed', dotColor:'#008000'}` + 아티팩트 조회 트리거(Task 5/6)
- `runtime.error`→`{type:'runtime_error', message}`

- [ ] **Step 2: dedup/정합성** — `rebuildTimeline()`: `timelineFromHistory(await api.history())`로 영속 부분 생성 후 라이브 전용 임시 엔트리(`live:true`이고 동일 command가 history에 존재)는 제거. 턴 종료(`/api/chat` resolve) 후 호출.

- [ ] **Step 3: 검증** — 콘솔에서 `timelineFromHistory` 결과가 기대 엔트리 배열인지 로드된 세션으로 확인(임시 `console.log`).

- [ ] **Step 4: Commit** — `git commit -m "feat(ui): 통합 타임라인 데이터 모델(history+SSE 매핑)"`

---

### Task 3: app.js — 타임라인 렌더러

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js`

**Interfaces:**
- Produces: `statusBadge(kind)->Node`, `renderCommandBlock(entry)->Node`, `renderEventRow(entry)->Node`, `renderTimeline()->Node`, `renderArtifactCard(entry)`, `renderRuntimeError(entry)`, idle 빈 상태. 명령 블록 토글은 엔트리 인덱스 기반 `state`에 `openCmd:Set`.
- Consumes: Task 2 엔트리, Task 1 CSS 클래스.

- [ ] **Step 1: statusBadge + 하위 렌더러**

`statusBadge(kind)`: kind→{label,색,깜빡}. `renderCommandBlock`: 헤더(토글 버튼 `$ cmd` + 배지) / `open`이면 `.cmd-out`(AGGREGATED OUTPUT + 색상 줄) / 푸터(EXIT + DURATION + toggle 라벨). 토글은 `state.openCmd`에 인덱스 추가/제거 후 `renderShell()`. `renderEventRow`: 스파인 점(dotColor, running이면 blink) + time + label + detail.

- [ ] **Step 2: renderTimeline** — `state.timeline`를 순회하며 type별 디스패치. `event_row`/`command`는 `.timeline` 스파인 컨테이너로 묶고, `user`/`agent`/`artifact`/`runtime_error`는 스파인 밖. idle(빈 타임라인 & !busy)이면 `.idle-empty`. 스트리밍 중 agent는 `.agent-cursor` 부착.

- [ ] **Step 3: 검증** — Chat 로드 후 과거 명령이 접힘 블록으로, 메시지가 좌우로 렌더되는지 `/gstack-browse` 확인.

- [ ] **Step 4: Commit** — `git commit -m "feat(ui): 인라인 타임라인 렌더러(명령블록/이벤트행/배지)"`

---

### Task 4: app.js — 레이아웃 개편(헤더/요약바/드로어 제거/인라인 승인)

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js`
- Modify: `src/personal_agent_gateway/static/styles.css` (드로어 스타일 삭제)

**Interfaces:**
- Produces: `renderChatHeader()`, `renderLiveStatusSummary()`. `renderChat()`는 세션레일 + 채팅컬럼(헤더/요약바/트랜스크립트/컴포저)만.
- Consumes: `deriveLive`, Task 3 렌더러.

- [ ] **Step 1: renderChat 재구성** — `renderChatDrawer`/`renderActivity` 제거. `renderChat`: `[renderSessionRail(), el('div',{class:'chat-col'},[renderChatHeader(), renderLiveStatusSummary(), el('div',{class:'transcript'}, transcriptInner), renderComposer()])]`. transcriptInner = `renderTimeline()` + (busy면 loader) + (pendingApproval면 `renderProposal` 인라인).

- [ ] **Step 2: 헤더/요약바** — `renderChatHeader`: 세션 제목(active session title) + `SESSION · title · started HH:MM`. `renderLiveStatusSummary`: `deriveLive` 기반 4셀(PHASE/RUNNING/LAST/ELAPSED), LAST는 statusBadge.

- [ ] **Step 3: 드로어 CSS 삭제** — `.drawer`, `.activity-*` 규칙 제거. `@media` 내 `.drawer{display:none}`도 제거.

- [ ] **Step 4: 검증** — 드로어 사라지고 승인/활동이 인라인으로 이동. 승인 카드 approve/deny 동작. `/gstack-browse`로 확인.

- [ ] **Step 5: Commit** — `git commit -m "feat(ui): 채팅 드로어 제거 + 헤더/요약바 + 인라인 승인"`

---

### Task 5: app.js — 상태 바 인디케이터 + SSE 상태 + 경과 타이머

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js`

**Interfaces:**
- Produces: `renderStatusbar()` 확장(PHASE/EVENTS 셀 + SSE 점/라벨), `connectEvents()`에서 `onopen`/`onerror`로 `state.sseState` 갱신, 경과 타이머(`setInterval` 1초, phase 활성 중 요약바만 갱신).
- Consumes: `deriveLive`, `state.sseState`, `state.turnStart`.

- [ ] **Step 1: SSE 상태 배선** — `connectEvents`에 `source.onopen=()=>{state.sseState='connected';renderShell()}`, `source.onerror=()=>{state.sseState='error';renderShell()}`. 이벤트 수신 중(턴 활성)엔 `deriveLive`가 'streaming' 계산.

- [ ] **Step 2: 상태 바** — `renderStatusbar`에 PHASE·EVENTS 셀 추가, RUNNING을 `deriveLive().running`으로, 우측 `.sse-wrap`에 점(색: streaming=warn/connected=ok/error=danger)+라벨.

- [ ] **Step 3: 경과 타이머** — phase 활성(`working|command running`)이면 `setInterval`로 elapsed 표시 갱신. 비활성 시 clear. 중복 인터벌 방지(`state.elapsedTimer`).

- [ ] **Step 4: 검증** — 메시지 전송 시 SSE 점이 STREAMING(주황 깜빡)→CONNECTED, PHASE/RUNNING/EVENTS/ELAPSED가 실시간 갱신. `/gstack-browse`.

- [ ] **Step 5: Commit** — `git commit -m "feat(ui): 상태바 PHASE/EVENTS/SSE 인디케이터 + 경과 타이머"`

---

### Task 6: 아티팩트 카드(조건부) + 전체 상태 검증

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js`

**Interfaces:**
- Produces: `api.artifacts()`(GET `/api/artifacts`), `renderArtifactCard`, `runtime.completed` 시 세션 아티팩트 조회→타임라인 append.
- Consumes: Task 3 `renderArtifactCard`.

- [ ] **Step 1: 아티팩트 조회** — `api.artifacts()` 추가. `runtime.completed` 수신 시 `/api/artifacts` 조회, `source_session_id===state.status.session_id`이고 turnStart 이후 생성된 항목만 `{type:'artifact', artifact}` 엔트리 append. 없으면 미표시.

- [ ] **Step 2: 전체 상태 검증** — 실제 Codex 런타임으로 각 상태 구동(`/gstack-browse`): idle/working/cmd running/completed/failed/runtime error/pending approval/새로고침 영속성/세션 전환/모바일(390)/좁은화면(<900).

- [ ] **Step 3: Commit** — `git commit -m "feat(ui): runtime.completed 아티팩트 카드(조건부)"`

---

## Self-Review

- **Spec coverage:** §3 데이터모델→Task2, §3.4 파생상태→Task2/5, §4 레이아웃→Task4, §4.2 상태바→Task5, §5 컴포넌트→Task1/3, §6 아티팩트→Task6, §7 검증→각 Task Step + Task6, §8 범위→전 태스크가 Chat 한정. 갭 없음.
- **rb-blink 충돌:** 기존 `.loader-label`용 `@keyframes rb-blink`(0%,60% 형태)와 목업의 하드 blink(0%,49%)가 다름 → 신규 `blink-hard` 이름으로 분리(Task 1 명시).
- **네이밍 일관성:** `state.timeline`/`timelineFromHistory`/`entryFromSseEvent`/`deriveLive`/`rebuildTimeline`/`renderTimeline`/`statusBadge`/`renderCommandBlock` — 전 태스크 동일 사용.
- **드로어 제거 순서:** Task1에서 CSS 남김 → Task4에서 JS 제거 후 CSS 삭제(중간 상태 무결).
