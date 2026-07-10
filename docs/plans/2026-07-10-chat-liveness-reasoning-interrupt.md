# 채팅 라이브니스·reasoning 보존·인터럽트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대화창의 정렬 불일치·reasoning 소실·로딩 표현 부족을 고치고, 실제 동작하는 esc 인터럽트를 추가한다.

**Architecture:** 프론트는 라이브·재진입 정렬을 단일 comparator로 통일하고, codex `reasoning` 아이템을 접이식 블록으로 렌더하며, 스트리밍 중 경과시간 인디케이터를 띄운다. 인터럽트는 실행 중 턴의 asyncio Task를 run_registry에 등록해 두고 `/interrupt` 엔드포인트가 취소, 서브프로세스를 정리한 뒤 `runtime.interrupted`를 발행한다.

**Tech Stack:** React 19 (.jsx), Vitest + @testing-library/react, FastAPI, pytest + pytest-asyncio, 단일 전역 스타일시트 `src/personal_agent_gateway/static/styles.css`.

## Global Constraints

- 프론트 컴포넌트는 `.jsx`, 테스트는 `vitest run`(`npm test`), 파일당 하나의 책임.
- 스타일은 전역 `src/personal_agent_gateway/static/styles.css`에만 추가(프론트 로컬 CSS 없음). 기존 CSS 변수(`--c-warn`, `--c-grey`, `--c-ok`, `--c-danger`, `--font-mono`) 사용.
- 백엔드 테스트는 `pytest`(`testpaths=["tests"]`), 비동기는 `pytest-asyncio`.
- 커밋은 한국어 Conventional Commits(예: `feat: ...`, `fix: ...`).
- `asyncio.CancelledError`는 `BaseException` 계열 → 기존 `except Exception`에 안 잡힘(설계가 이에 의존).
- reasoning/interrupted는 **트랜스크립트 미기록**, activity replay로만 재진입 표시.
- 프론트 명령/작업 실행 디렉터리는 `frontend/`, 백엔드는 리포지토리 루트.

---

## 파일 구조

- `frontend/src/lib/timeline.js` — 정렬 comparator, reasoning/interrupted 매핑, rank 테이블.
- `frontend/src/lib/timeline.test.js` — 위 단위 테스트.
- `frontend/src/components/organisms/Timeline/index.jsx` — reasoning 블록·interrupted 렌더, `orderedEntries` 교체.
- `frontend/src/components/organisms/Timeline/Timeline.test.jsx` — 렌더 테스트.
- `frontend/src/components/organisms/ChatView/index.jsx` — 라이브 인디케이터, Esc 리스너.
- `frontend/src/components/organisms/ChatView/ChatView.test.jsx` — 인디케이터·Esc 테스트.
- `frontend/src/components/containers/GatewayApp/index.jsx` — 로컬 `createdAtMs` 스탬프, `runtime.interrupted` 처리, `onInterrupt` 전달.
- `frontend/src/api/client.js` + `client.test.js` — `interruptSession`.
- `src/personal_agent_gateway/run_state.py` + `tests/test_run_state.py` — task 등록·취소.
- `src/personal_agent_gateway/model_client.py` + `tests/test_model_client.py` — CancelledError 시 subprocess 정리.
- `src/personal_agent_gateway/app.py` + `tests/test_app.py` — `/interrupt` 엔드포인트, `chat_for_session` 취소·발행.
- `src/personal_agent_gateway/static/styles.css` — reasoning 블록·인디케이터 스타일.

---

## Task 1: 단일 정렬 comparator + rank 테이블

**Files:**
- Modify: `frontend/src/lib/timeline.js` (`timelineRank` 확장, `compareEntries` 신설·export, `timelineFromSession` 정렬 교체)
- Test: `frontend/src/lib/timeline.test.js`

**Interfaces:**
- Produces: `export function compareEntries(left, right): number` — `createdAtMs → timelineRank → serverOrder/historyOrder/activityOrder/order → key/type` 순.

- [ ] **Step 1: 실패하는 테스트 작성** — `frontend/src/lib/timeline.test.js` 하단 `describe` 블록에 추가하고 import에 `compareEntries` 추가.

```js
import { compareEntries, deriveLive, entryFromSse, timelineFromHistory, timelineFromSession } from "./timeline.js";

describe("compareEntries", () => {
  it("orders by createdAtMs first", () => {
    const a = { type: "agent", createdAtMs: 200, serverOrder: 1 };
    const b = { type: "user", createdAtMs: 100, serverOrder: 9 };
    expect([a, b].sort(compareEntries).map((e) => e.type)).toEqual(["user", "agent"]);
  });

  it("breaks createdAtMs ties by logical rank (reasoning before agent)", () => {
    const reasoning = { type: "reasoning", createdAtMs: 100, serverOrder: 5 };
    const agent = { type: "agent", createdAtMs: 100, serverOrder: 2 };
    expect([agent, reasoning].sort(compareEntries).map((e) => e.type)).toEqual(["reasoning", "agent"]);
  });

  it("breaks rank ties by serverOrder", () => {
    const first = { type: "command", createdAtMs: 100, serverOrder: 1 };
    const second = { type: "command", createdAtMs: 100, serverOrder: 2 };
    expect([second, first].sort(compareEntries).map((e) => e.serverOrder)).toEqual([1, 2]);
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/lib/timeline.test.js -t compareEntries`
Expected: FAIL — `compareEntries is not a function`.

- [ ] **Step 3: 구현** — `timeline.js`의 `timelineRank`를 아래로 교체하고, 바로 아래에 `compareEntries`를 추가한다.

```js
function timelineRank(entry) {
  if (entry.type === "user") return 0;
  if (entry.type === "event_row" && entry.label === "runtime.user_message.started") return 1;
  if (entry.type === "command" || entry.type === "runtime_error") return 2;
  if (entry.type === "reasoning") return 3;
  if (entry.type === "agent") return 4;
  if (entry.type === "event_row" && entry.label === "runtime.completed") return 5;
  if (entry.type === "event_row" && entry.label === "runtime.interrupted") return 6;
  return 7;
}

export function compareEntries(left, right) {
  const byTime = (left.createdAtMs ?? 0) - (right.createdAtMs ?? 0);
  if (byTime) return byTime;
  const byRank = timelineRank(left) - timelineRank(right);
  if (byRank) return byRank;
  const leftSeq = left.serverOrder ?? left.historyOrder ?? left.activityOrder ?? left.order ?? 0;
  const rightSeq = right.serverOrder ?? right.historyOrder ?? right.activityOrder ?? right.order ?? 0;
  if (leftSeq !== rightSeq) return leftSeq - rightSeq;
  return String(left.key || left.type).localeCompare(String(right.key || right.type));
}
```

  그리고 `timelineFromSession`의 `.sort((left, right) => ( ... ))` 블록 전체를 `.sort(compareEntries)`로 교체한다(정렬 결과 동일, 로직 중복 제거).

- [ ] **Step 4: 전체 timeline 테스트 통과 확인**

Run: `cd frontend && npx vitest run src/lib/timeline.test.js`
Expected: PASS (기존 정렬 테스트 포함 전부 통과).

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/lib/timeline.js frontend/src/lib/timeline.test.js
git commit -m "refactor: 타임라인 정렬을 단일 compareEntries로 통일하고 reasoning/interrupted rank 추가"
```

---

## Task 2: Timeline 렌더가 compareEntries 사용

**Files:**
- Modify: `frontend/src/components/organisms/Timeline/index.jsx` (`orderedEntries`)
- Test: `frontend/src/components/organisms/Timeline/Timeline.test.jsx`

**Interfaces:**
- Consumes: `compareEntries` (Task 1).

- [ ] **Step 1: 실패하는 테스트 작성** — `Timeline.test.jsx`에 추가.

```jsx
it("renders live entries in canonical createdAtMs order regardless of array order", () => {
  const entries = [
    { type: "agent", text: "answer", createdAtMs: 300, serverOrder: 3, order: 0 },
    { type: "user", text: "question", createdAtMs: 100, serverOrder: 1, order: 1 }
  ];
  render(<Timeline entries={entries} busy={false} />);
  const blocks = document.querySelectorAll(".msg-user, .msg-agent");
  expect(blocks[0].className).toContain("msg-user");
  expect(blocks[1].className).toContain("msg-agent");
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/components/organisms/Timeline/Timeline.test.jsx -t "canonical"`
Expected: FAIL — 현재는 `order` 기준이라 배열 순서(agent 먼저)로 렌더됨.

- [ ] **Step 3: 구현** — `Timeline/index.jsx` 상단 import에 `compareEntries` 추가하고 `orderedEntries`를 교체.

```jsx
import { compareEntries } from "../../../lib/timeline.js";
```

```jsx
function orderedEntries(entries) {
  return [...entries].sort(compareEntries);
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run src/components/organisms/Timeline/Timeline.test.jsx`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/organisms/Timeline/index.jsx frontend/src/components/organisms/Timeline/Timeline.test.jsx
git commit -m "fix: 라이브 타임라인도 compareEntries로 정렬해 재진입 없이 순서 일치"
```

---

## Task 3: 로컬 엔트리 createdAtMs 스탬프

**Files:**
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` (`stampSessionEntry`)
- Test: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

**설명:** SSE 엔트리는 `createdAtMs`를 갖지만, 로컬 생성 엔트리(내 user 메시지, fallback agent, artifact)는 없어서 `compareEntries`에서 맨 앞(0)으로 정렬된다. 로컬 엔트리에 전송 시각을 스탬프해 시간순 안정 정렬을 보장한다.

**Interfaces:**
- Consumes: 없음.
- Produces: `stampSessionEntry`가 반환하는 entry에 `createdAtMs`(기존 값 우선, 없으면 `Date.now()`).

- [ ] **Step 1: 실패하는 테스트 작성** — `GatewayApp.test.jsx`에 추가. 파일 상단 헬퍼(`installFetch`, `response`, `status`, `sessions`)와 import(`userEvent`, `waitFor`, `screen`)를 재사용한다. jsdom엔 `EventSource`가 없어 SSE 이펙트는 조기 종료되므로(코드의 `typeof EventSource === "undefined"` 가드) 별도 mock이 불필요하다. 초기 히스토리에 오래된 assistant 메시지를 넣고, 새 메시지가 그 아래(뒤)에 정렬되는지 검증한다.

```jsx
it("keeps a newly sent message below older history (createdAtMs stamped)", async () => {
  const oldEvents = [{ kind: "assistant", created_at: "2020-01-01T00:00:00Z", payload: { content: "old answer" } }];
  installFetch({
    "GET /api/auth/status": { authenticated: true, totp_configured: true },
    "GET /api/status": status,
    "GET /api/sessions": { sessions },
    "GET /api/history": { events: oldEvents },
    "GET /api/agents": { agents: [] },
    "GET /api/sessions/active/config": { config: null },
    "GET /api/artifacts": { artifacts: [] },
    "GET /api/sessions/session-1/history": { events: oldEvents },
    "GET /api/sessions/session-1/activity": { events: [] },
    "GET /api/sessions/session-1/status": { session_id: "session-1", status: "idle", pending_approval: false },
    "POST /api/sessions/session-1/chat": () => response({ messages: [], pending_approval: false, session_id: "session-1", request_id: "r1" })
  });

  render(<GatewayApp />);
  await screen.findByLabelText("Agent Gateway");

  const composer = screen.getByPlaceholderText("Message the agent, or describe a local action...");
  await userEvent.type(composer, "hello");
  await userEvent.keyboard("{Enter}");

  await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument());
  const texts = Array.from(document.querySelectorAll(".msg-user .bubble, .msg-agent .bubble")).map((node) => node.textContent);
  expect(texts.indexOf("old answer")).toBeLessThan(texts.indexOf("hello"));
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/components/containers/GatewayApp/GatewayApp.test.jsx -t "createdAtMs"`
Expected: FAIL — 스탬프 전에는 로컬 user 엔트리 `createdAtMs`가 undefined(→ `compareEntries`에서 0)라 "old answer"(실제 timestamp)보다 앞서 정렬됨.

- [ ] **Step 3: 구현** — `stampSessionEntry`를 교체.

```jsx
  function stampSessionEntry(sessionId, state, entry) {
    const withTime = entry.createdAtMs != null ? entry : { ...entry, createdAtMs: Date.now() };
    if (withTime.order != null) return { entry: withTime, nextLocalOrder: state.nextLocalOrder };
    const order = state.nextLocalOrder;
    return {
      entry: { ...withTime, order },
      nextLocalOrder: order + 1
    };
  }
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run src/components/containers/GatewayApp/GatewayApp.test.jsx`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "fix: 로컬 타임라인 엔트리에 createdAtMs 스탬프해 정렬 안정화"
```

---

## Task 4: reasoning 아이템 매핑 (entryFromSse)

**Files:**
- Modify: `frontend/src/lib/timeline.js` (`entryFromSse`)
- Test: `frontend/src/lib/timeline.test.js`

**Interfaces:**
- Produces: `entryFromSse`가 `item.type === "reasoning"`에 대해 `{ type: "reasoning", key, text, time, serverOrder, createdAtMs }` 반환.

- [ ] **Step 1: 실패하는 테스트 작성**

```js
describe("reasoning mapping", () => {
  it("maps codex reasoning items to reasoning entries", () => {
    const entry = entryFromSse({
      type: "item.completed",
      session_id: "s1",
      event_seq: 7,
      created_at: "2026-07-10T00:00:01Z",
      item: { id: "r1", type: "reasoning", text: "thinking about it" }
    });
    expect(entry).toMatchObject({ type: "reasoning", text: "thinking about it", serverOrder: 7 });
    expect(entry.key).toContain("reasoning:");
  });

  it("ignores reasoning items with no text as empty string", () => {
    const entry = entryFromSse({
      type: "item.completed", session_id: "s1", event_seq: 8,
      item: { id: "r2", type: "reasoning" }
    });
    expect(entry).toMatchObject({ type: "reasoning", text: "" });
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/lib/timeline.test.js -t "reasoning mapping"`
Expected: FAIL — 현재 reasoning은 `null` 반환.

- [ ] **Step 3: 구현** — `entryFromSse`에서 `item.type === "agent_message"` 분기 바로 다음, `return null` 이전에 추가.

```js
    if (item.type === "reasoning") {
      return {
        type: "reasoning",
        key: `reasoning:${event.session_id || "legacy"}:${item.id || event.event_seq || ""}`,
        text: item.text || "",
        time: fmtTime(event.created_at, true) || nowHMS(),
        serverOrder: event.event_seq,
        createdAtMs
      };
    }
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run src/lib/timeline.test.js`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/lib/timeline.js frontend/src/lib/timeline.test.js
git commit -m "feat: codex reasoning 아이템을 타임라인 엔트리로 매핑"
```

---

## Task 5: reasoning 접이식 블록 렌더 + 스타일

**Files:**
- Modify: `frontend/src/components/organisms/Timeline/index.jsx` (ReasoningBlock, 클러스터 로직)
- Modify: `src/personal_agent_gateway/static/styles.css` (append)
- Test: `frontend/src/components/organisms/Timeline/Timeline.test.jsx`

**Interfaces:**
- Consumes: reasoning 엔트리(Task 4).

- [ ] **Step 1: 실패하는 테스트 작성**

```jsx
it("groups consecutive reasoning entries into one collapsed block that expands on click", () => {
  const entries = [
    { type: "reasoning", text: "step one", createdAtMs: 100, serverOrder: 1 },
    { type: "reasoning", text: "step two", createdAtMs: 101, serverOrder: 2 },
    { type: "agent", text: "final", createdAtMs: 200, serverOrder: 3 }
  ];
  render(<Timeline entries={entries} busy={false} />);
  const head = document.querySelector(".reasoning-head");
  expect(head).toBeTruthy();
  expect(head.textContent).toContain("2 steps");
  expect(document.querySelector(".reasoning-body")).toBeNull(); // 기본 접힘
  fireEvent.click(head);
  expect(document.querySelector(".reasoning-body").textContent).toContain("step one");
  expect(document.querySelector(".reasoning-body").textContent).toContain("step two");
});
```

  (파일 상단 import에 `fireEvent`가 없으면 `@testing-library/react`에서 추가.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/components/organisms/Timeline/Timeline.test.jsx -t "reasoning"`
Expected: FAIL — `.reasoning-head` 없음.

- [ ] **Step 3: 구현 (컴포넌트)** — `Timeline/index.jsx`에 `useState`가 이미 import됨. `CommandBlock` 아래에 추가:

```jsx
function ReasoningBlock({ steps }) {
  const [open, setOpen] = useState(false);
  const text = steps.map((step) => step.text).filter(Boolean).join("\n\n");
  const label = `${steps.length} ${steps.length === 1 ? "step" : "steps"}`;
  return (
    <div className="tl-reasoning">
      <button type="button" className="reasoning-head" onClick={() => setOpen((value) => !value)}>
        <span className="reasoning-dot" />
        <span className="reasoning-title">REASONING · {label}</span>
        <span className="reasoning-toggle">{open ? "▾" : "▸"}</span>
      </button>
      {open ? <div className="reasoning-body">{text}</div> : null}
    </div>
  );
}
```

  그리고 `Timeline` 본체의 클러스터 루프를 아래로 교체(활동 클러스터 + reasoning 클러스터 분리, 타입 전환 시 상호 flush로 순서 보존):

```jsx
export function Timeline({ entries, busy, sessionId = null, registeredByPath = null, onRegistered = null }) {
  if (!entries.length && !busy) return <div className="stream"><IdleEmpty /></div>;

  const nodes = [];
  let cluster = [];
  let reasoning = [];
  const flushCluster = () => {
    if (!cluster.length) return;
    nodes.push(
      <div className="tl-wrap" key={`cluster-${nodes.length}`}>
        <div className="tl-label-head">AGENT ACTIVITY</div>
        <div className="timeline">
          {cluster.map((entry, index) => entry.type === "command"
            ? <CommandBlock key={entry.key || index} entry={entry} />
            : <EventRow key={`${entry.label}-${index}`} entry={entry} />)}
        </div>
      </div>
    );
    cluster = [];
  };
  const flushReasoning = () => {
    if (!reasoning.length) return;
    nodes.push(<ReasoningBlock key={`reasoning-${nodes.length}`} steps={reasoning} />);
    reasoning = [];
  };

  for (const entry of orderedEntries(entries)) {
    if (entry.type === "reasoning") {
      flushCluster();
      reasoning.push(entry);
      continue;
    }
    if (entry.type === "event_row" || entry.type === "command") {
      flushReasoning();
      cluster.push(entry);
      continue;
    }
    flushReasoning();
    flushCluster();
    if (entry.type === "user") nodes.push(<UserMessage key={`u-${nodes.length}`} entry={entry} />);
    if (entry.type === "agent") nodes.push(<AgentMessage key={`a-${nodes.length}`} entry={entry} sessionId={sessionId} registeredByPath={registeredByPath} onRegistered={onRegistered} />);
    if (entry.type === "artifact") nodes.push(<ArtifactCard key={`ar-${nodes.length}`} entry={entry} />);
    if (entry.type === "runtime_error") nodes.push(<RuntimeError key={`e-${nodes.length}`} entry={entry} />);
  }
  flushReasoning();
  flushCluster();
  return <div className="stream">{nodes}</div>;
}
```

- [ ] **Step 4: 구현 (스타일)** — `src/personal_agent_gateway/static/styles.css` 끝에 추가.

```css
.tl-reasoning { margin: 6px 0; }
.reasoning-head {
  display: flex; align-items: center; gap: 8px; width: 100%;
  background: transparent; border: 1px dashed var(--c-grey); border-radius: 4px;
  padding: 6px 10px; cursor: pointer; color: var(--c-grey);
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; text-align: left;
}
.reasoning-head:hover { color: #b8b8b8; }
.reasoning-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--c-grey); flex: none; }
.reasoning-title { flex: 1; }
.reasoning-toggle { flex: none; }
.reasoning-body {
  margin-top: 4px; padding: 8px 12px; white-space: pre-wrap;
  font-family: var(--font-mono); font-size: 12px; color: #9a9a9a;
  border-left: 2px solid var(--c-grey);
}
```

- [ ] **Step 5: 통과 확인**

Run: `cd frontend && npx vitest run src/components/organisms/Timeline/Timeline.test.jsx`
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/organisms/Timeline/index.jsx frontend/src/components/organisms/Timeline/Timeline.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: reasoning을 접이식 REASONING 블록으로 렌더(기본 접힘)"
```

---

## Task 6: 라이브 working 인디케이터 + 스타일

**Files:**
- Modify: `frontend/src/components/organisms/ChatView/index.jsx` (WorkingIndicator, LoaderCube 제거, `onInterrupt` prop 선반영)
- Modify: `src/personal_agent_gateway/static/styles.css` (append)
- Test: `frontend/src/components/organisms/ChatView/ChatView.test.jsx`

**Interfaces:**
- Consumes: `busy`, `turnStart`, `fmtElapsed`.
- Produces: `ChatView`가 `onInterrupt` prop을 받는다(Task 11에서 배선). 이 태스크에서는 인디케이터 힌트 표시까지.

- [ ] **Step 1: 실패하는 테스트 작성**

파일 상단의 기존 `props(entries)` 헬퍼(모든 필수 prop을 채우고 `busy:false`, `turnStart:null` 반환)를 재사용해 override한다.

```jsx
it("shows a live working indicator with elapsed time while busy", () => {
  render(<ChatView {...props([])} busy turnStart={Date.now() - 5000} turnStreamed />);
  const indicator = document.querySelector(".working-indicator");
  expect(indicator).toBeTruthy();
  expect(indicator.textContent).toContain("WORKING");
  expect(indicator.textContent).toContain("esc to interrupt");
});

it("hides the working indicator when idle", () => {
  render(<ChatView {...props([])} busy={false} />);
  expect(document.querySelector(".working-indicator")).toBeNull();
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/components/organisms/ChatView/ChatView.test.jsx -t "working indicator"`
Expected: FAIL — `.working-indicator` 없음.

- [ ] **Step 3: 구현 (컴포넌트)** — import 정리: `LoaderCube` import 줄 삭제, `fmtTime` import에 `fmtElapsed` 추가.

```jsx
import { fmtElapsed, fmtTime } from "../../../lib/time.js";
```

  (`LoaderCube` import 줄 `import { LoaderCube } from "../../molecules/LoaderCube/index.jsx";` 삭제.)

  `Proposal` 함수 아래에 추가:

```jsx
function WorkingIndicator({ turnStart }) {
  const elapsed = turnStart ? fmtElapsed((Date.now() - turnStart) / 1000) : "0s";
  return (
    <div className="working-indicator" role="status" aria-live="polite">
      <span className="working-dot" />
      <span className="working-label mono">WORKING · {elapsed}</span>
      <span className="working-hint mono">esc to interrupt</span>
    </div>
  );
}
```

  `ChatView` 시그니처에 `onInterrupt` prop 추가(구조분해에 `onInterrupt` 삽입). 트랜스크립트 내부의 LoaderCube 라인을 교체:

```jsx
        <div className="transcript" ref={transcriptRef} onScroll={handleTranscriptScroll}>
          <Timeline entries={entries} busy={busy} sessionId={activeSessionId} registeredByPath={registeredByPath} onRegistered={onArtifactChange} />
          {busy ? <WorkingIndicator turnStart={turnStart} /> : null}
          <Proposal approval={pendingApproval} onResolve={onResolveApproval} />
        </div>
```

  (경과시간 1초 갱신은 GatewayApp의 기존 `useForceTick(screen === "chat" && busy)`가 재렌더를 유발하므로 별도 타이머 불필요.)

- [ ] **Step 4: 구현 (스타일)** — `styles.css` 끝에 추가.

```css
.working-indicator {
  display: flex; align-items: center; gap: 10px;
  margin: 10px 0 4px; padding: 8px 12px;
  border: 1px solid var(--c-warn); border-radius: 4px;
  background: rgba(255, 165, 0, 0.06);
}
.working-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--c-warn); flex: none;
  animation: blink-hard 1s step-end infinite;
}
.working-label { color: var(--c-warn); font-size: 12px; letter-spacing: 1px; }
.working-hint { color: var(--c-grey); font-size: 10px; letter-spacing: 1px; margin-left: auto; }
```

- [ ] **Step 5: 통과 확인**

Run: `cd frontend && npx vitest run src/components/organisms/ChatView/ChatView.test.jsx`
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/organisms/ChatView/index.jsx frontend/src/components/organisms/ChatView/ChatView.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: 스트리밍 중 경과시간 라이브 working 인디케이터 표시"
```

---

## Task 7: run_state에 취소 가능한 task 등록

**Files:**
- Modify: `src/personal_agent_gateway/run_state.py`
- Test: `tests/test_run_state.py`

**Interfaces:**
- Produces:
  - `SessionRunRegistry.attach_task(session_id: str, request_id: str, task: asyncio.Task) -> None` — 현재 실행 중이고 request_id가 일치할 때만 등록.
  - `SessionRunRegistry.interrupt(session_id: str) -> bool` — 등록된 task가 있으면 `.cancel()` 호출 후 True, 없으면 False.
  - `finish`가 task도 함께 제거.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_run_state.py`에 추가.

```python
import asyncio


def test_interrupt_cancels_attached_task() -> None:
    registry = SessionRunRegistry()

    async def scenario() -> bool:
        registry.start("session-1", "request-1")

        async def worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.ensure_future(worker())
        registry.attach_task("session-1", "request-1", task)
        assert registry.interrupt("session-1") is True
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(scenario()) is True


def test_interrupt_returns_false_without_task() -> None:
    registry = SessionRunRegistry()
    registry.start("session-1", "request-1")
    assert registry.interrupt("session-1") is False


def test_attach_task_ignores_mismatched_request_id() -> None:
    registry = SessionRunRegistry()

    async def scenario() -> bool:
        registry.start("session-1", "request-1")

        async def worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.ensure_future(worker())
        registry.attach_task("session-1", "other-request", task)
        result = registry.interrupt("session-1")
        task.cancel()
        return result

    assert asyncio.run(scenario()) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_run_state.py -k "interrupt or attach_task" -v`
Expected: FAIL — `attach_task`/`interrupt` 없음.

- [ ] **Step 3: 구현** — `run_state.py` 상단에 `import asyncio` 추가하고, `SessionRunRegistry`를 수정.

```python
import asyncio
```

```python
    def __init__(self) -> None:
        self._running: dict[str, SessionRunState] = {}
        self._tasks: dict[str, tuple[str, asyncio.Task]] = {}
        self._lock = Lock()
```

  `finish`에 task 제거 추가:

```python
    def finish(self, session_id: str, request_id: str | None = None) -> None:
        with self._lock:
            current = self._running.get(session_id)
            if current is None:
                return
            if request_id is not None and current.request_id != request_id:
                return
            self._running.pop(session_id, None)
            self._tasks.pop(session_id, None)
```

  클래스에 메서드 추가:

```python
    def attach_task(self, session_id: str, request_id: str, task: asyncio.Task) -> None:
        with self._lock:
            current = self._running.get(session_id)
            if current is not None and current.request_id == request_id:
                self._tasks[session_id] = (request_id, task)

    def interrupt(self, session_id: str) -> bool:
        with self._lock:
            entry = self._tasks.get(session_id)
        if entry is None:
            return False
        entry[1].cancel()
        return True
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_run_state.py -v`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add src/personal_agent_gateway/run_state.py tests/test_run_state.py
git commit -m "feat: 세션 실행 task 등록·취소 지원(run_registry)"
```

---

## Task 8: model_client 취소 시 서브프로세스 정리

**Files:**
- Modify: `src/personal_agent_gateway/model_client.py` (`CodexModelClient.complete`, `ClaudeModelClient.complete`)
- Test: `tests/test_model_client.py`

**Interfaces:**
- Consumes: 없음.
- Produces: `complete()`가 `asyncio.CancelledError` 시 `process.kill()` 후 CancelledError를 재-raise.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_model_client.py`에 추가. 프로세스를 흉내내는 fake로 kill 호출을 검증.

```python
import asyncio
import pytest
from personal_agent_gateway.model_client import CodexModelClient


class _HangingStdout:
    async def readline(self) -> bytes:
        await asyncio.sleep(60)
        return b""


class _FakeStdin:
    def write(self, _data: bytes) -> None: ...
    async def drain(self) -> None: ...
    def close(self) -> None: ...


class _FakeProcess:
    def __init__(self) -> None:
        self.killed = False
        self.stdin = _FakeStdin()
        self.stdout = _HangingStdout()
        self.stderr = _HangingStdout()
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


@pytest.mark.asyncio
async def test_codex_complete_kills_process_on_cancel(monkeypatch) -> None:
    fake = _FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = CodexModelClient(binary="codex", model="m", workspace_root=".", sandbox="s", approval_policy="never")
    task = asyncio.ensure_future(client.complete([{"role": "user", "content": "hi"}]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.killed is True
```

  (생성자 인자는 파일 내 기존 테스트가 `CodexModelClient(...)`를 만드는 방식을 그대로 참고해 맞춘다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_model_client.py -k "kills_process_on_cancel" -v`
Expected: FAIL — 현재 CancelledError 시 kill 없음.

- [ ] **Step 3: 구현 (codex)** — `CodexModelClient.complete`의 `try/except TimeoutError` 블록에 CancelledError 분기 추가.

```python
        try:
            stdout_text, stderr_text = await asyncio.wait_for(
                self._communicate_stream(process, _codex_prompt(messages).encode()),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError("Codex execution timed out") from exc
        except asyncio.CancelledError:
            process.kill()
            try:
                await process.wait()
            except ProcessLookupError:
                pass
            raise
```

- [ ] **Step 4: 구현 (claude)** — `ClaudeModelClient.complete`의 동일 위치에 추가.

```python
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError("Claude execution timed out") from exc
        except asyncio.CancelledError:
            process.kill()
            try:
                await process.wait()
            except ProcessLookupError:
                pass
            raise
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_model_client.py -v`
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add src/personal_agent_gateway/model_client.py tests/test_model_client.py
git commit -m "feat: 모델 실행 취소 시 서브프로세스 kill로 orphan 방지"
```

---

## Task 9: /interrupt 엔드포인트 + chat_for_session 취소·발행

**Files:**
- Modify: `src/personal_agent_gateway/app.py` (`chat_for_session`, 신규 엔드포인트)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `run_registry.attach_task`/`interrupt` (Task 7), `app.state.session_activity_publisher`.
- Produces: `POST /api/sessions/{id}/interrupt` → 실행 중이면 200 `{session_id, interrupting: True}`, 아니면 409. 취소 시 `runtime.interrupted` `{session_id}` 발행. `/chat` 응답에 `interrupted: True`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_app.py`에 추가. 동기 TestClient로 실제 동시 실행 턴을 재현하기 어려우므로 검증을 두 갈래로 나눈다: (A) idle→409, (B) 화이트박스 — `run_registry`에 `.cancel()` 가능한 더미를 attach해 엔드포인트가 이를 취소하고 200을 주는지. (실행 턴 취소의 end-to-end는 Task 7·8 단위테스트 + Task 12 수동 검증으로 커버.) `<client>`는 이 파일의 기존 인증 TestClient fixture/헬퍼 이름에 맞춘다.

```python
def test_interrupt_idle_session_returns_409(<client>) -> None:
    session_id = <client>.post("/api/reset").json()["session_id"]
    resp = <client>.post(f"/api/sessions/{session_id}/interrupt")
    assert resp.status_code == 409


def test_interrupt_cancels_registered_task(<client>) -> None:
    session_id = <client>.post("/api/reset").json()["session_id"]

    class _DummyTask:
        def __init__(self) -> None:
            self.canceled = False

        def cancel(self) -> None:
            self.canceled = True

    registry = <client>.app.state.run_registry
    registry.start(session_id, "req-x")
    dummy = _DummyTask()
    registry.attach_task(session_id, "req-x", dummy)  # type: ignore[arg-type]

    resp = <client>.post(f"/api/sessions/{session_id}/interrupt")

    assert resp.status_code == 200
    assert resp.json()["interrupting"] is True
    assert dummy.canceled is True
    registry.finish(session_id, "req-x")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_app.py -k interrupt -v`
Expected: FAIL — 엔드포인트 404(미존재).

- [ ] **Step 3: 구현 (chat_for_session)** — `app.py`의 `chat_for_session`을 아래로 교체(취소 처리·발행 추가). 상단에 `import asyncio`가 없으면 추가.

```python
    async def chat_for_session(session_id: str, message: str) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            started = run_registry.start_if_exists(session_id, request_id, lambda: transcript.exists(session_id))
        except SessionAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail="Session is already running") from exc
        if not started:
            raise HTTPException(status_code=404, detail="Session not found")
        run_registry.attach_task(session_id, request_id, asyncio.current_task())
        try:
            result = await runtime_for_session(session_id).handle_user_message(message)
            return {
                **_runtime_response(result),
                "session_id": session_id,
                "request_id": request_id,
                "last_event_id": _last_session_event_id(event_bus.recent(), session_id),
            }
        except asyncio.CancelledError:
            await app.state.session_activity_publisher.publish(
                {"type": "runtime.interrupted", "session_id": session_id}
            )
            return {
                "messages": [],
                "pending_approval": False,
                "session_id": session_id,
                "request_id": request_id,
                "last_event_id": _last_session_event_id(event_bus.recent(), session_id),
                "interrupted": True,
            }
        finally:
            run_registry.finish(session_id, request_id)
```

- [ ] **Step 4: 구현 (엔드포인트)** — `/api/sessions/{session_id}/chat` POST 라우트 정의 근처에 추가(async def — 이벤트 루프에서 task.cancel 호출).

```python
    @app.post("/api/sessions/{session_id}/interrupt")
    async def interrupt_session(
        session_id: str,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        if not run_registry.interrupt(session_id):
            raise HTTPException(status_code=409, detail="Session is not running")
        return {"session_id": session_id, "interrupting": True}
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_app.py -k interrupt -v && pytest tests/test_app.py -q`
Expected: PASS (기존 app 테스트 회귀 없음).

- [ ] **Step 6: 커밋**

```bash
git add src/personal_agent_gateway/app.py tests/test_app.py
git commit -m "feat: 세션 인터럽트 엔드포인트와 취소 시 runtime.interrupted 발행"
```

---

## Task 10: interrupted 매핑 + 렌더 (프론트)

**Files:**
- Modify: `frontend/src/lib/timeline.js` (`entryFromSse`에 `runtime.interrupted`)
- Test: `frontend/src/lib/timeline.test.js`, `frontend/src/components/organisms/Timeline/Timeline.test.jsx`

**Interfaces:**
- Produces: `entryFromSse`가 `type === "runtime.interrupted"`에 대해 `{ type: "event_row", label: "runtime.interrupted", detail: "interrupted by user", dotColor, time, serverOrder, createdAtMs }` 반환(기존 EventRow가 렌더).

- [ ] **Step 1: 실패하는 테스트 작성** — `timeline.test.js`.

```js
it("maps runtime.interrupted to an event row", () => {
  const entry = entryFromSse({
    type: "runtime.interrupted", session_id: "s1", event_seq: 9,
    created_at: "2026-07-10T00:00:02Z"
  });
  expect(entry).toMatchObject({ type: "event_row", label: "runtime.interrupted" });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/lib/timeline.test.js -t "runtime.interrupted"`
Expected: FAIL — 현재 `null`.

- [ ] **Step 3: 구현** — `entryFromSse`에서 `event.type === "runtime.completed"` 분기 다음에 추가.

```js
  if (event.type === "runtime.interrupted") {
    return {
      type: "event_row",
      key: `event:${event.event_seq || event.id || event.type}`,
      label: "runtime.interrupted",
      detail: "interrupted by user",
      dotColor: "#FFA500",
      time: fmtTime(event.created_at, true) || nowHMS(),
      serverOrder: event.event_seq,
      createdAtMs
    };
  }
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run src/lib/timeline.test.js`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/lib/timeline.js frontend/src/lib/timeline.test.js
git commit -m "feat: runtime.interrupted를 타임라인 이벤트 행으로 매핑"
```

---

## Task 11: 인터럽트 배선 (api + Esc + GatewayApp)

**Files:**
- Modify: `frontend/src/api/client.js` + `frontend/src/api/client.test.js`
- Modify: `frontend/src/components/organisms/ChatView/index.jsx` (Esc 리스너)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` (`handleInterrupt`, `runtime.interrupted` busy 처리, `onInterrupt` 전달)
- Test: `frontend/src/components/organisms/ChatView/ChatView.test.jsx`

**Interfaces:**
- Consumes: `POST /api/sessions/{id}/interrupt` (Task 9), `entryFromSse` interrupted (Task 10), `WorkingIndicator`/`onInterrupt` prop (Task 6).
- Produces: `api.interruptSession(id)`; ChatView가 busy 중 Esc → `onInterrupt()`; GatewayApp가 `runtime.interrupted` 수신 시 `busy=false`.

- [ ] **Step 1: 실패하는 테스트 작성 (api)** — `client.test.js`.

```js
it("interruptSession posts to the interrupt endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ interrupting: true }) });
  global.fetch = fetchMock;
  const result = await api.interruptSession("sess 1");
  expect(fetchMock).toHaveBeenCalledWith("/api/sessions/sess%201/interrupt", { method: "POST" });
  expect(result).toEqual({ interrupting: true });
});
```

- [ ] **Step 2: 실패하는 테스트 작성 (ChatView Esc)** — `ChatView.test.jsx`.

```jsx
it("calls onInterrupt when Escape is pressed while busy", () => {
  const onInterrupt = vi.fn();
  render(<ChatView {...props([])} busy turnStart={Date.now()} onInterrupt={onInterrupt} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onInterrupt).toHaveBeenCalledTimes(1);
});

it("does not call onInterrupt on Escape when idle", () => {
  const onInterrupt = vi.fn();
  render(<ChatView {...props([])} busy={false} onInterrupt={onInterrupt} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onInterrupt).not.toHaveBeenCalled();
});
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/api/client.test.js -t interruptSession && npx vitest run src/components/organisms/ChatView/ChatView.test.jsx -t "onInterrupt"`
Expected: FAIL.

- [ ] **Step 4: 구현 (api)** — `client.js`의 `sendSessionChat` 다음에 추가.

```js
  async interruptSession(id) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/interrupt`, { method: "POST" }));
  },
```

- [ ] **Step 5: 구현 (ChatView Esc)** — `ChatView`에 useEffect 추가(`useEffect`는 이미 import됨). `handleTranscriptScroll` 함수 위에 삽입.

```jsx
  useEffect(() => {
    if (!busy || typeof onInterrupt !== "function") return undefined;
    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onInterrupt();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onInterrupt]);
```

- [ ] **Step 6: 구현 (GatewayApp)** — (a) `handleInterrupt` 추가, (b) SSE 핸들러에서 `runtime.interrupted` 시 busy 해제, (c) `ChatView`에 `onInterrupt` 전달.

  (a) `handleResolveApproval` 위에 추가:

```jsx
  async function handleInterrupt() {
    const sessionId = activeSessionIdRef.current;
    if (!sessionId || !busyRef.current) return;
    await api.interruptSession(sessionId);
  }
```

  (b) SSE `onmessage`의 busyRef 분기(‘runtime.completed || runtime.error’)와 setSessionStateById의 `busyNext`/`ended` 계산에 `runtime.interrupted`를 포함하도록 조건을 확장. 구체적으로 두 곳:

```jsx
          } else if (parsed.type === "runtime.completed" || parsed.type === "runtime.error" || parsed.type === "runtime.interrupted") {
            busyRef.current = false;
          }
```

```jsx
          const ended = parsed.type === "runtime.completed" || parsed.type === "runtime.error" || parsed.type === "runtime.interrupted" ? Date.now() : (parsed.type === "runtime.user_message.started" ? null : state.turnEnd);
          const busyNext = parsed.type === "runtime.user_message.started"
            ? true
            : (parsed.type === "runtime.completed" || parsed.type === "runtime.error" || parsed.type === "runtime.interrupted")
              ? false
              : state.busy;
```

  (c) `<ChatView ... />`에 prop 추가:

```jsx
          onResolveApproval={handleResolveApproval}
          onInterrupt={handleInterrupt}
```

- [ ] **Step 7: 통과 확인**

Run: `cd frontend && npx vitest run`
Expected: PASS (전체 프론트 스위트).

- [ ] **Step 8: 커밋**

```bash
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/components/organisms/ChatView/index.jsx frontend/src/components/organisms/ChatView/ChatView.test.jsx frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat: esc 인터럽트 배선(api·ChatView·GatewayApp)과 interrupted 상태 처리"
```

---

## Task 12: 전체 검증

- [ ] **Step 1: 프론트 전체 테스트**

Run: `cd frontend && npm test`
Expected: 전체 PASS.

- [ ] **Step 2: 백엔드 전체 테스트**

Run: `pytest -q`
Expected: 전체 PASS.

- [ ] **Step 3: 실제 앱 대조 검증(수동)** — 서버 기동(메모리 `pag-run-server` 절차: 루트에서 `PYTHONPATH=src`, `.env`의 상대 `./data`) 후:
  - 메시지 전송 → reasoning이 접힌 REASONING 블록으로 뜨고, 순서가 처음부터 올바른지.
  - 세션 나갔다 재진입 → reasoning·순서 유지, thinking 소실 없음.
  - 스트리밍 중 WORKING · {경과} 인디케이터가 1초마다 갱신.
  - Esc → 턴 중단, INTERRUPTED 이벤트 행 표시, busy 해제. (spec §2 "배포 빌드 차이" 대조 항목 확인)

- [ ] **Step 4: 확인 후 정리 커밋(불필요 시 생략)**

```bash
git status
```
