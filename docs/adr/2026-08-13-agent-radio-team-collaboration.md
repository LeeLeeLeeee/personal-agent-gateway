---
title: AgentRadio 기반 Team Run 협업의 책임 경계와 단계적 도입
type: adr
domain: personal-agent-gateway
feature: agent-radio-team-collaboration
status: active
decision_status: accepted
aliases:
  - AgentRadio 채택 결정
  - PAG 협업 허브와 LMG 실행 어댑터
  - radio-lite와 passive 모드 구분
  - Team Run passive awareness
tags:
  - team-run
  - multi-agent
  - collaboration
  - passive-awareness
  - pag
  - lmg
updated_at: 2026-08-13
---

# AgentRadio 기반 Team Run 협업의 책임 경계와 단계적 도입

## Context

[AgentRadio 논문](https://arxiv.org/pdf/2607.28430)은 장시간 실행되는 여러 에이전트가 각자 작업을 계속하면서 동료의 중요 메시지를 수동적으로 인지하는 협업 방식을 제안한다. primitive는 thread 생성, message 전송, mention 대기이고, background watcher가 받은 메시지는 현재 tool call이 끝나는 다음 model step에 노출된다. 공식 구현은 별도 message server와 얇은 shell wrapper를 사용한다.

PAG에는 Team Run, Leader/Worker, 영속 `team_messages`, upstream session 재개, Cycle 복구, 사용자 결정 흐름이 이미 있다. 그러나 현재 협업은 Leader가 Worker의 `needs_info`를 중재하는 순차적 yield-and-resume이고, 한 Team Run 내부에서는 dependency-ready 목록의 첫 항목만 실행한다(`team_runtime.py`의 `list_dependency_ready_tasks` 호출부). API는 effective `max_workers=1`, `execution_mode=sequential`을 반환한다. LMG Provider는 `Run(ctx, req, emit)` one-shot 계약이고 process runner는 초기 stdin을 쓰고 닫는다.

따라서 결정해야 할 질문은 "메시지 API를 추가할 수 있는가"가 아니다.

1. 기존 Team Run lifecycle을 깨지 않고 협업 phase와 mailbox를 어디에 둘 것인가.
2. 동시 쓰기 충돌 없이 언제부터 에이전트를 병렬로 실행할 것인가.
3. LMG를 협업 도메인으로 확장하지 않고 passive watcher를 실행할 수 있는가.
4. 논문의 QnA 결과가 PAG의 구현·자동화 작업에서도 재현되는지 어떻게 판단할 것인가.

설계 근거와 데이터 schema, protocol, 평가 지표의 전문은 [AgentRadio 기반 Team Run 협업 설계 계획](../todo/2026-08-12-agent-radio-team-collaboration-design-plan.md)에 있다. 이 ADR은 그 문서의 결정을 승인하고 경계를 고정한다.

## Assumptions and success criteria

- 협업 기능을 켜지 않은 Team Run의 lifecycle, 복구, 사용자 결정, contest 의미는 바뀌지 않는다.
- 메시지는 유실보다 중복이 낫다. 중복은 식별 가능해야 하고 유실은 허용하지 않는다.
- 논문의 절대 점수는 PAG의 승격 기준이 아니다. 논문은 장시간 코드베이스 QnA 중심이고 PAG는 구현·자동화를 포함한다.
- 효과를 수치로 증명하지 못한 단계는 기본 활성화하지 않는다.
- 어떤 단계에서 중단하더라도 그 시점의 산출물이 동작하는 상태여야 한다.

## Decision

### 1. 후보 B를 목표 구조로 채택하고 후보 A를 선행 단계로 구현한다

PAG가 협업 허브가 되어 thread, message, mention, cursor, phase, 권한을 소유한다. LMG는 provider가 background watcher를 안전하게 실행·정리할 수 있도록 wrapper 위치와 실행 metadata만 전달하는 얇은 어댑터로 남는다.

후보 A(PAG model-call boundary inbox)는 폐기하지 않는다. `radio_lite`라는 별도 이름으로 먼저 구현·평가하고, provider가 passive capability를 증명하지 못하면 영구적인 fallback 경로로 유지한다.

### 2. 협업 도메인의 source of truth는 PAG SQLite다

- LMG는 메시지를 저장하지 않고 누구에게 전달할지 결정하지 않는다.
- LMG는 thread, message, cursor, approval, Team phase를 보관하지 않는다.
- `EventBus`는 durable message가 commit된 **뒤** UI·관측용 event를 투영한다. 전달 보장의 source of truth로 사용하지 않는다.

`EventBus`는 메모리 history와 subscriber queue이므로(`events.py`) 재시작을 견디지 못한다. 전달 보장을 여기에 얹으면 crash 한 번에 mention이 사라진다.

### 3. blocking 질문과 passive mention을 분리한다

- 답이 없으면 현재 task가 진행할 수 없는 질문은 기존 `needs_info` yield-and-resume 경로를 계속 사용한다.
- passive mention은 수신자가 작업을 중단하지 않고 참고할 수 있는 cross-scope 정보에만 사용한다.

이 둘을 합치면 답을 기다려야 하는 질문이 조용히 무시되거나, 참고 정보가 불필요하게 Run을 멈춘다.

### 4. 모드를 사용자에게 정직하게 표시한다

| 모드 | 의미 |
| --- | --- |
| `legacy` | 현재 Leader mediation과 순차 Worker 실행 |
| `radio_lite` | 다음 PAG model-call boundary에 unread snapshot 삽입 |
| `passive` | background watcher가 mention을 받아 다음 CLI tool-step boundary에 노출 |

UI와 API는 `radio_lite`를 `passive`로 표시하지 않는다. provider capability가 없거나 watcher health가 나쁘면 모드를 명시적으로 downgrade하고 그 이유를 기록한다. passive 실패는 Run 실패가 아니라 radio-lite downgrade로 처리한다.

### 5. 병렬 실행은 read-only 탐색부터, 동시 쓰기는 금지한다

- Run 내부의 첫 병렬 실행은 explore phase의 read-only 탐색으로 제한한다.
- 같은 workspace의 동시 쓰기는 workspace isolation 설계가 별도로 승인되기 전까지 금지한다.
- Run 간 `TeamCycleDispatcher`의 concurrency(`config.py`의 `team_run_concurrency`, 기본 2)와 Run 내부 scout concurrency는 별도 설정으로 각각 상한을 지킨다.
- **`max_workers`를 2 이상으로 바꾸는 방식으로 병렬 execute를 켜지 않는다.** 격리된 병렬 execute는 task별 worktree 또는 write lease, dependency artifact 전달, merge, conflict 판정, 취소 시 정리까지 별도 설계와 승인을 받은 뒤 진행한다.

### 6. 단계마다 중단 가능한 gate를 둔다

| Stage | 내용 | 승격에 필요한 증거 |
| --- | --- | --- |
| 0 | 평가 fixture와 legacy baseline 고정 | versioned rubric·baseline, 평가가 실제 외부 mutation을 만들지 않음 |
| 1 | L2 협상 protocol (watcher 없음) | legacy 대비 성공률 또는 critical defect detection 개선, 비용·latency 한도 내, 기존 lifecycle/recovery/user-decision 회귀 없음 |
| 2 | radio-lite | crash/retry/restart와 ambiguous operation 복구에서 유실 0·같은 operation은 같은 snapshot, stale message로 인한 오수정 증가 없음, peer prompt injection이 정책을 우회하지 못함 |
| 3 | read-only 병렬 exploration | 두 scout 실행 구간이 실제로 겹침, write 시도는 provider 실행 전 또는 tool boundary에서 거부, 두 concurrency 상한 각각 준수 |
| 4 | provider passive watcher | Codex와 Claude를 독립 capability·승격 단위로 검증, cancel 20회 반복 뒤 watcher/process/goroutine 증가 0, terminal event·session.updated·partial content 계약 유지 |
| 5 | 격리된 병렬 execute | 이 ADR의 범위가 아니다. 별도 설계와 승인이 필요하다. |

Stage 0은 코드 작업이 아니다. 최소 20개 task 또는 유형별 5회 이상 반복이 없으면 기본 활성화 결정을 내리지 않는다. Stage 1~4는 구현된 모드를 고정 baseline과 직전 stage에 각각 비교한다.

### 7. 승격을 막는 조건을 미리 정한다

- critical defect detection이 감소하면 승격하지 않는다.
- message가 후속 plan/task 수정에 실제 사용된 비율이 40% 미만이면 전달 정책을 수정한다.
- message 때문에 생긴 잘못된 변경·재작업이 legacy보다 증가하면 기본 off로 유지한다.
- 비용이 legacy의 2배를 넘으면 opt-in으로만 유지한다.
- p95 latency가 운영 timeout을 넘으면 승격하지 않는다.
- cancel/restart 뒤 orphan process는 0이어야 한다.
- 격리 전 write 성공 건수는 0이어야 한다.

## 이 ADR이 결정하지 않은 것

- **Stage 5의 격리된 병렬 execute.** workspace 생성·artifact 전달·merge·conflict 판정·정리가 미설계다.
- **provider가 실제로 passive를 지원하는지.** LMG process runner가 초기 stdin을 닫으므로 CLI 자체의 background output 노출 여부를 PoC로 먼저 증명해야 한다. 증명 실패 시 Stage 4는 시작하지 않고 radio-lite가 최종 형태가 된다.
- **기본 활성화 시점.** Stage 0의 baseline 없이는 판단하지 않는다.
- 외부 message broker, 멀티 호스트 분산 실행, WebSocket 기반 범용 agent network는 도입하지 않는다.
- 기존 shell/tool approval과 사용자 결정 흐름을 협업 메시지로 대체하지 않는다.

## Alternatives

### 후보 A만 채택 (model-call boundary inbox로 끝내기)

현재 upstream session resume과 영속 메시지를 재사용하므로 background process도, 실행 중 입력 channel도 필요 없고 provider별 차이가 작다. 그러나 긴 CLI 실행 중에는 메시지를 받지 못하고, 순차 Worker만 있으면 실시간 협업 효과가 제한적이며, 논문과 같은 passive awareness라고 부를 수 없다. 그래서 최종 목표가 아니라 선행 단계 겸 fallback으로 둔다.

### 후보 C — LMG가 중앙 메시지 서버가 되고 양방향 Run API를 제공

실행 provider와 입력 channel을 한곳에서 제어할 수 있고, CLI background output을 쓸 수 없는 provider도 이론상 지원한다. 그러나 현재 one-shot Provider 인터페이스와 process lifecycle을 전면 변경해야 하고, Team/Agent 권한과 domain state가 PAG와 LMG에 중복된다. 아직 검증되지 않은 요구에 비해 변경 범위가 지나치게 크다. **기각한다.** wrapper PoC가 실패하고 passive 효과가 비용을 상회한다는 증거가 생길 때에만 별도 설계로 다시 검토한다.

### 기존 `max_workers`를 올려 Run 내부 병렬화

설정 한 줄로 병렬 실행을 켤 수 있지만, 현재 Team Run workspace는 Run 단위 working root이므로 여러 Worker가 같은 트리에 동시 쓰기를 하게 된다. merge도 conflict 판정도 취소 시 정리도 없다. **금지한다.**

### 논문 구현을 그대로 이식 (별도 message server + shell wrapper)

논문과의 충실도는 가장 높지만 PAG가 이미 소유한 영속 message, 권한, Run lifecycle과 중복되는 두 번째 도메인을 만든다. wrapper 아이디어만 차용하고 저장·권한은 PAG에 둔다.

## Consequences

- 협업 기능이 꺼진 경로는 그대로 남으므로 기존 Team Run 동작에 회귀가 없어야 하고, 그 사실이 각 stage의 gate에 포함된다.
- `TeamRuntime`은 Run/Cycle/Task 상태와 복구 의미를 계속 소유하고, 새 `TeamCollaborationCoordinator`는 phase와 전달 정책만 조율한다. coordinator가 두 번째 runtime이 되면 이 결정은 실패한 것이다.
- 첫 실질 작업이 코드가 아니라 평가 fixture이므로, 기능 착수가 늦어 보이는 대신 승격 판단에 근거가 생긴다.
- provider passive가 증명되지 않으면 최종 형태는 radio-lite다. 이 경우에도 Stage 1~3의 산출물은 그대로 쓸 수 있다.
- 모드 downgrade를 사용자에게 노출해야 하므로 API·UI에 모드와 그 이유를 표시하는 표면이 필요하다.

## Verification contract

이 ADR이 지켜졌는지 판단하는 기준이다. 구현 단계의 검증은 각 stage의 spec/plan이 소유한다.

- 협업 기능을 끈 Team Run에서 lifecycle, 복구, 사용자 결정, contest 동작이 이전과 동일하다.
- LMG의 어느 테이블·파일에도 thread, message, cursor, approval, Team phase가 저장되지 않는다.
- durable message commit 이전에는 `EventBus` event가 발행되지 않는다.
- blocking 질문은 `needs_info` 경로로, passive mention은 협업 경로로 각각 흐른다.
- API와 UI가 `radio_lite`를 `passive`로 표기하지 않고, downgrade에는 사유가 남는다.
- 어떤 stage에서도 격리 승인 없이 두 Worker가 같은 workspace에 write하지 않는다.
- Stage 0의 baseline이 versioned되기 전에는 어떤 모드도 기본 활성화되지 않는다.
- Stage 4는 provider별로 독립 승격되고, 한 provider의 실패가 다른 provider를 막지 않는다.

## Related

- [AgentRadio 기반 Team Run 협업 설계 계획](../todo/2026-08-12-agent-radio-team-collaboration-design-plan.md) — 데이터 schema, protocol, 평가 지표 전문
- [AgentRadio paper](https://arxiv.org/pdf/2607.28430) · [official implementation](https://github.com/Coral-Protocol/AgentRadio)
- [Team collaboration and reliability design](../superpowers/specs/2026-07-10-team-collaboration-and-reliability-design.md)
- [Team Run 배치형 사용자 결정](2026-07-16-team-run-batched-user-decisions.md) — blocking 사용자 결정 흐름
- [Parallel team run dispatcher design](../design/2026-08-08-parallel-team-run-dispatcher-design.md) — Run 간 병렬성
- [Runtime domain relationship map](../knowledge/2026-07-16-runtime-domain-relationship-map.md)
