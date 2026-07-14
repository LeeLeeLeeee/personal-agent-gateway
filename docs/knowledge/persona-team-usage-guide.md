# Persona & Team Run 사용 가이드

Personal Agent Gateway의 **Persona**와 **Team Run** 기능을 어떻게 쓰는지, 그리고 직접 따라 해볼 수 있는 시나리오를 정리한다. 동작 설명은 실제 코드(`personas.py`, `teams.py`, `team_runtime.py`, `TeamRunForm`)에 근거한다.

---

## 1. 개념

### Persona (페르소나)
재사용 가능한 **에이전트 프로필**이다. 한 번 만들어 두면 여러 Team Run에서 리더/멤버로 반복 배치할 수 있다.

| 필드 | 의미 |
| --- | --- |
| `name` | 에이전트 이름 (필수) |
| `role` | 역할 한 줄 (예: "프론트엔드 개발자") |
| `description` | 이 페르소나가 어떤 관점/성격으로 일하는지 |
| `responsibilities` | 책임 목록. UI에서는 **한 줄에 하나씩** 입력 |
| `constraints` | 제약/금지 사항. 역시 **한 줄에 하나씩** |
| `default_backend` | 실행 백엔드. 사실상 `codex` |
| `default_model` | 모델. 기본 `default` |
| `avatar` | 아바타 이미지 키(선택) |

- 관리 위치: 좌측 사이드바 **TEAMS → Personas** 탭에서 생성/수정/삭제.
- **스냅샷 복사**: Team Run에 배치되는 순간 페르소나 내용이 `persona_snapshot`으로 복사된다. 이후 원본 페르소나를 수정해도 이미 만들어진 run에는 영향이 없다.
- 어떤 Team Run이 사용 중인 페르소나는 삭제가 거부될 수 있다(in use).

### Team Run (팀 실행)
하나의 **목표(goal)**를 여러 페르소나 에이전트로 처리하는 단위다.

구성:
- **Goal**: 팀이 달성할 목표 (자유 텍스트)
- **Leader persona**: 리더 1명 — 목표를 세부 task로 분해한다
- **Member personas**: 멤버 N명 — task를 실제로 수행한다
- **Run mode**: 실행 방식 (아래)
- **Max workers**: 동시 워커 상한(현재 라운드로빈 분배의 기준값)

**Run mode 3종 — 실제 런타임 동작 기준:**

| 모드 | UI 설명 | 실제 동작(`team_runtime.py`) |
| --- | --- | --- |
| `planning_only` | 리더가 목표를 task로 분해만, 실행 없음 | 리더 1회 호출 → task JSON 배열 생성 → **완료** |
| `plan_and_execute` | 리더가 계획 후 멤버가 실행·보고 | 리더가 task 생성 → 멤버들이 task를 **라운드로빈으로 실행** → 결과 저장 → summary로 완료 |
| `review_only` | 멤버가 기존 작업을 리뷰·보고 | ⚠️ **현재 구현상 planning_only와 동일 경로**로 처리된다(플래닝 후 종료). 멤버 실행 로직은 `plan_and_execute`에서만 돈다 |

> ⚠️ **정직한 한계**: 런타임은 `run_mode != "plan_and_execute"`이면 플래닝만 하고 즉시 완료한다. 따라서 `review_only`는 아직 "리뷰 실행"이 아니라 플래닝 1회로 끝난다. 실제로 멤버 에이전트를 돌려보려면 **`plan_and_execute`**를 써야 한다.
>
> `plan_and_execute`인데 **멤버가 0명**이면 run은 `failed`가 된다(워커가 없기 때문). 이 모드에서는 멤버를 최소 1명 넣어야 한다.

**실행 흐름 요약**
```text
Team Run 생성 → (자동 start)
  → 리더 에이전트: PLANNING_PROMPT로 goal을 [{title, description}, ...] task 배열로 분해
  → planning_only / review_only : 여기서 completed
  → plan_and_execute :
        각 task를 멤버에게 라운드로빈 배정
        멤버 에이전트: WORKER_PROMPT로 task 수행 → 결과 message 저장
        전부 끝나면 summarizing → completed(summary 포함)
```
- 각 에이전트 호출 = Codex CLI 1회 실행(`codex exec`, effort high). 리더 1회 + task 개수만큼 멤버 호출이 발생한다.
- Team Run마다 `workspace_root/{team_run_id}` 전용 작업 디렉터리가 잡힌다.

---

## 2. 결과를 어디서 보나 (TeamRunDetail)
Team Runs 탭에서 run을 선택하면:
- **Agents**: 리더/멤버 상태(pending → running → completed/failed)
- **Tasks**: 리더가 만든 task 목록과 각 상태(pending → in_progress → completed), 멤버가 채운 `result`
- **Messages**: 리더의 플래닝 노트, 멤버들의 `agent_output`
- **Summary**: plan_and_execute 완료 시 요약

SSE로 `team.*` 이벤트가 오면 상세 화면이 실시간 갱신된다.

---

## 3. 따라 하기 시나리오

전제: 게이트웨이 실행 + OTP 로그인 완료, Codex CLI 로그인 상태.

```bash
# 프로젝트 루트에서
make dev          # scripts/run_local.sh → 127.0.0.1:8787
```
브라우저에서 `http://127.0.0.1:8787` 접속 → OTP 로그인.

### STEP 1 — 페르소나 2개 만들기
**TEAMS → Personas → (New)**. 아래 두 개를 그대로 입력한다.

**① 리더용 — "기획 리드"**
- NAME: `기획 리드`
- ROLE: `프로덕트 기획 리더`
- DESCRIPTION: `목표를 실행 가능한 작업 단위로 쪼개고 우선순위를 정하는 리더. 각 작업은 담당자가 바로 착수할 수 있을 만큼 구체적으로 기술한다.`
- RESPONSIBILITIES (한 줄에 하나):
  ```
  목표를 3~6개의 독립적 task로 분해
  각 task에 명확한 완료 기준 포함
  task 간 의존성 최소화
  ```
- CONSTRAINTS (한 줄에 하나):
  ```
  코드를 직접 작성하지 않는다
  한 task는 한 사람이 반나절 안에 끝낼 크기로
  ```

**② 멤버용 — "프론트 개발자"**
- NAME: `프론트 개발자`
- ROLE: `React 프론트엔드 개발자`
- DESCRIPTION: `배정된 task를 구현하고, 변경 파일과 검증 방법을 함께 보고하는 개발자.`
- RESPONSIBILITIES:
  ```
  배정된 task를 구현
  변경한 파일 목록 보고
  간단한 검증 방법 제시
  ```
- CONSTRAINTS:
  ```
  범위 밖 리팩터링 금지
  요청되지 않은 기능 추가 금지
  ```

각각 저장하면 Personas 목록에 두 개가 보인다.

### STEP 2 — 계획만 뽑아보기 (planning_only)
**TEAMS → Team Runs → (New)**
- **Goal**: `방문자 로그인 페이지에 소셜 로그인(구글) 버튼을 추가한다`
- **Leader persona**: `기획 리드`
- **Members**: 비워둠 (planning_only는 멤버 불필요)
- **Run mode**: `PLANNING ONLY`
- **Max workers**: 기본값

생성하면 자동 실행된다. 잠시 후 상세 화면에서:
- Tasks에 리더가 쪼갠 task 3~6개가 채워짐
- Run 상태가 `completed`
- Messages에 "Planning completed with N tasks." 노트

> 여기까지는 **계획서만** 나온다. 실제 구현은 하지 않는다.

### STEP 3 — 실제로 굴려보기 (plan_and_execute)
다시 **New**로 새 run 생성:
- **Goal**: STEP 2와 동일하게 두거나 원하는 목표로
- **Leader persona**: `기획 리드`
- **Members**: `프론트 개발자` **체크**(최소 1명 필수)
- **Run mode**: `PLAN + EXECUTE`
- **Max workers**: `2`

생성 → 자동 실행. 이번엔:
- 리더가 task 분해 → 각 task가 `프론트 개발자`에게 라운드로빈 배정
- Tasks가 하나씩 `in_progress → completed`로 바뀌고 각 task의 `result`에 구현 보고가 채워짐
- Messages에 멤버의 `agent_output`들이 쌓임
- 마지막에 Run이 `completed`, Summary에 "Completed N tasks."

### STEP 4 — 실패 케이스 관찰(선택)
`PLAN + EXECUTE`인데 **멤버를 아무도 선택하지 않고** 실행하면 run이 `failed`가 되고 error에 "no worker agents" 메시지가 남는다. 멤버가 없으면 실행 모드가 성립하지 않음을 확인하는 용도.

---

## 4. 자주 막히는 지점
- **멤버 없이 PLAN+EXECUTE → failed**: 멤버를 최소 1명 넣는다.
- **review_only인데 아무것도 실행 안 됨**: 정상이다. 현재 구현상 플래닝만 한다. 실행이 필요하면 plan_and_execute를 쓴다.
- **페르소나 삭제 안 됨**: 그 페르소나를 쓰는 Team Run이 있으면 삭제가 거부된다. 해당 run을 지운 뒤 삭제한다.
- **결과가 JSON 파싱 에러로 실패**: 리더가 task 배열(JSON)을 반환하지 못한 경우다. Goal을 더 명확히 하거나 다시 실행한다.
