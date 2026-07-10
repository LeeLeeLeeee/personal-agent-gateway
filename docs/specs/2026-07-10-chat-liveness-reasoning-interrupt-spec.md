# 채팅 라이브니스·reasoning 보존·인터럽트 설계

- 날짜: 2026-07-10
- 상태: Draft (사용자 검토 대기)
- 범위: `frontend/` (React 정본) + `src/personal_agent_gateway/` (백엔드 API/런타임)
- 관련 이전 spec: `completed_2026-07-07-live-activity-viewer-chat-redesign-spec.md`, `2026-07-09-session-scoped-chat-and-sse-spec.md`

## 1. 배경 / 문제

대화창(Live Activity Viewer)에서 세 가지 증상이 관찰된다.

1. **정렬 불일치**: 라이브 스트리밍 중 대화·활동 순서가 어긋난다. 세션을 나갔다가 다시 들어오면 그제서야 올바르게 정렬된다.
2. **thinking 소실**: codex의 reasoning(사고 요약)이 재진입 시 사라지고 최종 응답만 남는다.
3. **로딩 느낌 부족**: 에이전트가 작업 중이라는 "살아있는" 표현이 약하다. 경과 시간이 흐르고 중단할 수 있는 인라인 인디케이터를 원한다 (`Working (3m 37s • esc to interrupt)` 스타일).

## 2. 현황 분석 (근거)

- 백엔드는 codex가 내보내는 **모든 이벤트**(reasoning / agent_message / command_execution)를 `publish_codex_event` → `SessionActivityPublisher.publish` 경로로 `session_activity_events` 테이블에 저장하고 동시에 SSE로 발행한다. 즉 **재진입 replay(`/api/sessions/{id}/activity`)에는 라이브와 동일한 데이터가 모두 존재한다.**
- 프론트 `entryFromSse`(`frontend/src/lib/timeline.js`)는 `command_execution`과 `agent_message`만 렌더하고 **`reasoning` 아이템 타입은 `null` 처리**한다. 프론트/레거시 어디에도 reasoning 처리가 없다. → 증상 2의 원인.
- 라이브 경로는 도착순 `nextLocalOrder`로 엔트리를 쌓고, 재진입 경로는 `timelineFromSession`이 `createdAtMs → timelineRank → serverOrder`로 정식 재정렬한다. 정렬 기준이 두 갈래여서 라이브에서 어긋난다. → 증상 1의 원인.
- 라이브 표시는 `LoaderCube("AGENT WORKING")` + `LiveStatusSummary` 바뿐이다. 경과 시간·중단 조작이 없다. → 증상 3.
- 턴 실행 경로: `POST /api/sessions/{id}/chat` → `chat_for_session` → `await handle_user_message()` 안에서 codex 서브프로세스를 spawn해 await한다. `SessionRunRegistry`는 메타데이터만 보유하고 **취소 가능한 핸들이 없다.** → 인터럽트 미지원.

## 3. 목표 / 비목표

**목표**
- ① 라이브·재진입 정렬을 단일 기준으로 통일한다.
- ② codex reasoning을 라이브·재진입 양쪽에서 렌더하고 보존한다. 기본 **접힘** 블록.
- ③ 스트리밍 중 경과 시간이 살아 움직이는 인라인 라이브 인디케이터를 표시한다.
- ④ 실제 동작하는 esc 인터럽트(프론트 키바인딩 + 백엔드 취소)를 추가한다.

**비목표**
- reasoning 토큰 단위 char-streaming(아이템 단위 렌더로 충분).
- 인터럽트 후 재개(resume) 기능.
- 백엔드 이벤트 스키마의 구조적 재설계.

## 4. 설계

### ① 정렬 통일 — 단일 comparator

- `timeline.js`에 `compareEntries(a, b)`를 추출한다. 우선순위: `createdAtMs → timelineRank → serverOrder → key/type`.
- `timelineFromSession`의 인라인 정렬을 `compareEntries`로 교체한다.
- `Timeline`의 `orderedEntries`를 `compareEntries` 사용으로 교체한다 (기존 `order` 필드 기반 정렬 대체).
- 로컬 생성 엔트리(내가 방금 보낸 user 메시지, fallback agent 등)에는 생성 시각을 `createdAtMs`로 스탬프해, `serverOrder`가 없어도 시간순으로 안정 정렬되게 한다.
- 결과: 라이브 화면이 처음부터 재진입 후와 동일한 순서로 표시된다.

**tiebreak 안정성**: `createdAtMs`가 동률일 때 `timelineRank`가 논리 순서를 보장한다. rank 테이블을 정수로 재배치한다: `{user:0, user_message.started:1, command/runtime_error:2, reasoning:3, agent:4, completed:5, interrupted:6}`.

### ② reasoning 표시·보존

- `entryFromSse`에 분기 추가:
  ```
  if (item.type === "reasoning") →
    { type: "reasoning", key: `reasoning:${session}:${item.id ?? seq}`,
      text: item.text ?? "", time, serverOrder: event.event_seq, createdAtMs }
  ```
- 재진입은 `timelineFromSession`이 activity를 `entryFromSse`로 매핑하므로 **동일 코드로 커버**된다(추가 작업 없음).
- `timelineFromHistory`는 변경하지 않는다(트랜스크립트에는 reasoning이 없고 activity에만 있으므로).
- **렌더링**: `Timeline`에 reasoning 클러스터를 추가한다. 연속된 reasoning 엔트리를 하나의 접이식 `REASONING` 블록으로 묶는다(command/event_row 클러스터와 유사한 flush 메커니즘). 기본 **접힘**, 헤더에 스텝 수 표시(`▸ REASONING · N steps`), 클릭 시 펼침. 톤은 어둑하게(터미널 thinking 느낌).
- 중복 제거: reasoning은 history-agent와 텍스트가 겹치지 않으므로 기존 dedup에 영향 없음.

### ③ 라이브 인디케이터

- `ChatView`의 `{busy && !turnStreamed ? <LoaderCube/> : null}`를 **스트리밍 중 항상 표시되는 인라인 인디케이터**로 교체한다: 깜빡이는 점 + `WORKING · {elapsed}` + `esc to interrupt` 힌트.
- 경과 시간은 기존 `useForceTick`(1초 틱) + `deriveLive({turnStart,turnEnd}).elapsed`를 그대로 사용한다.
- 트랜스크립트 하단에 배치. `LiveStatusSummary` 바는 유지(요약 지표)하되 "살아있는" 주 신호는 이 인디케이터가 담당한다.

### ④ esc 인터럽트 (프론트 + 백엔드)

**백엔드**
- `SessionRunRegistry`에 취소 핸들을 보관한다. `start`/`start_if_exists` 시점에 `asyncio.Task`를 등록할 수 있도록 `attach_task(session_id, request_id, task)`와 `interrupt(session_id) -> bool`(등록된 task.cancel 호출)를 추가한다. 스레드 락은 유지하되 `task.cancel()`은 이벤트 루프(엔드포인트) 위에서 호출된다.
- `chat_for_session`: `start_if_exists` 직후 `run_registry.attach_task(session_id, request_id, asyncio.current_task())`. `CancelledError`를 잡아 `runtime.interrupted` 발행 + interrupted 결과 반환. `finally`에서 `run_registry.finish`.
- `CodexModelClient._communicate_stream`(및 claude 대응부): 서브프로세스 상호작용을 try/finally로 감싸 `CancelledError` 시 `process.kill()`로 orphan 방지(기존 timeout 경로의 `process.kill()` 재사용).
- 새 엔드포인트 `POST /api/sessions/{id}/interrupt`: 실행 중이면 `run_registry.interrupt()` 호출 후 200, 아니면 409/404.
- `runtime.interrupted` SSE 이벤트(세션 스코프)를 발행 → `SessionActivityPublisher`가 activity에도 기록. **트랜스크립트에는 append하지 않는다**(모델 컨텍스트 오염 방지). 재진입 시 "중단됨" 표시는 `timelineFromSession`이 activity의 `runtime.interrupted`를 렌더하는 것으로 커버된다. 이미 스트리밍된 reasoning/command 엔트리도 activity에 남아 있으므로 재진입 시 그대로 보존된다(부분 응답을 합성 최종답변으로 만들지 않음).

**프론트**
- `ChatView`에서 `busy`일 때 `keydown` Esc 리스너 → `api.interruptSession(activeSessionId)`.
- `api/client.js`에 `interruptSession(id)` 추가.
- `runtime.interrupted` 수신 시(`entryFromSse` + GatewayApp SSE 핸들러): `busy=false`, 타임라인에 `INTERRUPTED` 마커 렌더(event_row 계열).
- `/chat` 프라미스가 interrupted 결과로 resolve되면 기존 `finally`가 busy를 내린다(이중 안전).

## 5. 이벤트/데이터 스키마 변경

- SSE 신규 타입: `runtime.interrupted` `{ session_id }`.
- 프론트 엔트리 신규 타입: `reasoning`, `interrupted`(event_row 렌더).
- 백엔드 신규 엔드포인트: `POST /api/sessions/{id}/interrupt`.
- DB 스키마 변경 없음(activity는 기존 테이블 재사용).

## 6. 테스트 전략

**백엔드**
- `test_app.py`: `/interrupt`가 실행 중 세션을 취소하고 `runtime.interrupted`를 발행하는지; 미실행 세션에 409/404; 취소 후 재시작 가능 여부.
- `test_run_state.py`(신규 또는 기존): `attach_task`/`interrupt` 등록·취소 동작.
- `test_model_client.py`: `CancelledError` 시 subprocess가 kill되는지(모의 프로세스).

**프론트**
- `timeline.test`(신규/기존): `entryFromSse`의 reasoning 매핑; `compareEntries`가 라이브·재진입에서 동일 순서를 내는지; interrupted 매핑.
- `ChatView`/`GatewayApp` 테스트: Esc → interrupt 호출; reasoning 접힘/펼침; 라이브 인디케이터 경과 표시.

## 7. 파일별 변경 요약

- `frontend/src/lib/timeline.js`: `compareEntries` 추출, reasoning/interrupted 매핑, rank 테이블 조정.
- `frontend/src/components/organisms/Timeline/index.jsx`: reasoning 접이식 블록, interrupted 렌더, `orderedEntries` 교체.
- `frontend/src/components/organisms/ChatView/index.jsx`: 라이브 인디케이터, Esc 리스너.
- `frontend/src/components/containers/GatewayApp/index.jsx`: `runtime.interrupted` 처리, 로컬 엔트리 `createdAtMs` 스탬프.
- `frontend/src/api/client.js`: `interruptSession`.
- `frontend/src/components/.../styles`(vanilla 또는 styles.css): reasoning 블록·인디케이터 스타일.
- `src/personal_agent_gateway/run_state.py`: task 등록·interrupt.
- `src/personal_agent_gateway/app.py`: `/interrupt` 엔드포인트, `chat_for_session` 취소 처리.
- `src/personal_agent_gateway/model_client.py`: CancelledError 시 subprocess 정리.
- `src/personal_agent_gateway/runtime.py`: `runtime.interrupted` 발행(및 마커 append).

## 8. 리스크 / 주의

- **취소 경쟁**: interrupt와 정상 완료가 거의 동시에 나면 `finish`/`attach_task`의 request_id 매칭으로 오취소 방지.
- **orphan 프로세스**: CancelledError 경로에서 반드시 `process.kill()` + `await process.wait()`.
- **정렬 스탬프**: 로컬 엔트리 `createdAtMs`는 클라이언트 시계 기준이라 서버 이벤트와 미세하게 어긋날 수 있으나, tiebreak rank가 논리 순서를 보정한다.
- **배포 빌드 차이**: "라이브에선 reasoning이 보였다"는 관찰은 현재 코드와 불일치. 구현 시 실제 실행 화면으로 대조 검증한다.

## 9. 결정된 사항 (구현 중 검증)

1. interrupted는 트랜스크립트 미기록, activity replay로만 표시(§4-④).
2. 인터럽트 시 부분 합성 최종답변 없음. 스트리밍된 reasoning/command만 activity로 보존(§4-④).
