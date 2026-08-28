# 팀런이 design-studio에 디자인을 맡기고, 사장님이 PAG에서 승인한다

작성 2026-08-28 · 상태: 설계 확정, 구현 계획 전

---

## 1. 한 줄

팀런의 UX 기획자·Frontend 개발자가 **design-studio에 디자인을 요청해 초안을 받아 쓰고**,
그 산출물이 **PAG 안의 요청함 모달에 preview로 올라와** 사장님이 승인하거나 반려한다.
반려 이유는 다음 사이클의 워커에게 되돌아간다.

---

## 2. 확정된 결정

브레인스토밍에서 사장님이 고른 것들이다. 구현은 이 선택을 바꾸지 않는다.

| # | 결정 | 대안이었던 것 |
|---|---|---|
| 1 | **승인은 사장님이 PAG 화면에서** 한다 | 에이전트가 근거를 남기고 대신 승인 / 승인 없이 초안만 참고 |
| 2 | 워커는 **초안까지만 기다린다.** 사이클이 안 끊긴다 | 승인까지 기다려 사이클을 끊는다 / 아예 안 기다린다 |
| 3 | **프로젝트 생성과 시스템 선택은 에이전트가** 한다 | 사장님이 런 만들 때 지정 |
| 4 | 요청함은 **목록형 모달** — 왼쪽 목록, 오른쪽 상세 | 한 건씩 뜨는 모달 / 폭만 갈리는 모달 |
| 5 | 요청함은 **팀런 안에서** 연다 | 전역 사이드바 / 둘 다 |
| 6 | 사장님의 행위는 **승인 · 반려** 둘. 반려하면 에이전트가 다시 요청한다 | 보기만 / 핀을 꽂아 지적까지 |

### 그 결정에서 따라 나오는 것

- **결정 2 때문에** 반려는 그 워커에게 못 간다. 워커는 이미 끝났다. 반려는 **다음 사이클의 일감**이다.
- **결정 2 때문에** 워커는 design-studio HTTP를 **직접** 부른다. PAG는 작업 도중에 결과를 끼워 넣을 수 없다.
- **결정 6 때문에** 반려에는 **이유가 반드시 있어야 한다.** 이유가 없으면 다음 사이클이 같은 것을 다시 만든다.

---

## 3. 배경 — 두 도구가 이미 가진 것

### design-studio (`playground/design-studio`)

디자인 판단을 **계약으로 만드는** 1인용 도구. `docs/agent-guide.md`가 에이전트용 조작법을 담고 있다.

- `127.0.0.1:7777` HTTP. **LMG(`127.0.0.1:8788`)와 `LMG_LOCAL_TOKEN`이 있어야 뜬다.** 이 도구는 LMG를 켜지 않는다
- 산출물은 **자기완결 HTML 한 파일** — `out/<화면>.html`, 배치는 `out/<화면>.layout.html`. 외부 자원은 P0 규칙이 막는다
- 세 계층: 도구(저장소) · 사용자(`~/.design/`) · 프로젝트(`<프로젝트>/.design/`)
- **`drafted`와 `accepted`가 이미 분리돼 있다.** 승인된 배치는 `.design/layouts/<화면>.md`에 계약으로 적히고,
  그 뒤 디자인 실행이 블록을 더하거나 빼거나 순서를 바꾸면 `layout-contract`라는 **P0**로 되돌려 보내진다.
  판단이 아니라 대조이므로 결정적이다

### PAG가 이미 가진 것

- `artifacts` 테이블에 `source_team_run_id` · `source_team_task_id` · `source_cycle_id` · `file_path` · `mime_type`
- `ArtifactModal`이 `<iframe src={contentUrl}>`로 산출물을 렌더한다 (`frontend/src/components/organisms/ArtifactModal/index.jsx:90`)
- `team_decision_requests` + 인라인 패널(`INPUT NEEDED · N`)로 팀원 질문을 받는다
- 워커 프롬프트는 **블록 합성** 구조다:
  `_space_block + _rules_block + _archive_block + _team_note_block + WORKER_PROMPT.format(...)`
  (`team_runtime.py:4722` 이하)
- SPACE 블록이 워커에게 **Working root를 이미 알려준다** (`team_runtime.py:994`)

**그래서 preview는 거의 공짜다.** 디자인 산출물이 아티팩트 한 줄이 되면 기존 화면이 그대로 띄운다.

---

## 4. 범위

### 만든다

- 워커 프롬프트의 design-studio 사용법 블록 (대기 방법 포함)
- 워커가 승인을 청하는 표시 — ` ```design-review ` 블록
- `team_design_reviews` 표 하나
- 요청함 읽기 API — 질문과 디자인을 한 목록으로
- 요청함 모달 (목록 + 상세, 종류에 따라 본문 분기)
- design-studio 얇은 HTTP 클라이언트 (프로젝트 전환 · 배치 승인)
- 반려 이유를 다음 사이클 워커에게 되돌리는 블록

### 만들지 않는다

- **디자인 시스템 만들기.** 워커는 있는 것 중에서 고른다. 맞는 게 없으면 요청함에 질문으로 올린다.
  (`system/source`는 원본 자료가 필요하고 `system/accept`는 사람의 행위다)
- **핀 꽂기·고치기(`pin`/`fix`)를 PAG로 가져오기.** 지적하려면 design-studio를 연다
- **design-studio 자동 기동.** `LMG_LOCAL_TOKEN`이 필요하고 그건 사람 몫이다
- **전역 요청함.** 팀런 안에서만 연다
- **PAG 쪽 직렬화.** design-studio가 이미 자물쇠를 가지고 있다 (§6.4)
- **design-studio 코드 변경.** 이 설계는 저쪽을 하나도 고치지 않는다

---

## 5. 데이터

### 5.1 표를 합치지 않는다

`team_decision_requests`는 **런을 재우는 것과 얽혀 있다**:

```python
self._teams.defer_run_for_user_decision(run.id, decision, stage="planning", cycle_id=cycle_id)
return self._close_collaboration(await self._publish_user_decision_request(run, cycle_id))
```

디자인 승인은 런을 재우지 않는다(결정 2). 같은 표에 넣으면 그 의미가 섞이고,
`status`를 읽는 기존 코드가 디자인 항목에도 반응한다.

**추상화는 화면과 읽기 API에서 한다.** 사장님이 보는 것은 한 목록이고, 뒤에는 두 소스가 있다.

### 5.2 새 표

```
team_design_reviews
  id                text  pk
  team_run_id       text  not null
  cycle_id          text
  task_id           text
  artifact_id       text  not null    -- preview 는 이것으로. 기존 아티팩트 재사용
  screen            text  not null    -- design-studio 의 화면 이름
  project_root      text  not null    -- 승인할 때 어느 프로젝트인지
  stage             text  not null    -- 'layout' | 'design'
  status            text  not null    -- 'pending' | 'accepted' | 'rejected'
  reason            text              -- 반려 이유. rejected 이면 비어 있을 수 없다
  created_at        text  not null
  answered_at       text
```

`status='rejected'`이면서 `reason`이 비면 저장을 거부한다 — 결정 6에서 따라 나온다.

---

## 6. 흐름

### 6.1 워커가 디자인을 맡긴다

워커는 SPACE 블록에서 자기 **Working root**를 이미 안다.

```
GET  /api/systems                                   있는 시스템을 본다. 맞는 게 없으면 여기서 멈추고 질문으로 올린다
GET  /api/projects                                  등록된 root 를 글자 그대로 읽는다
POST /api/projects  {"root": <Working root>, "system": <고른 것>}     없을 때만
POST /api/project   {"root": <같은 값>}
POST /api/project/context  {"context": "- …"}       비우면 안 된다 (§6.2)
POST /api/run       {"brief": "…", "screen": "home", "stage": "layout"}
```

`root`는 레지스트리에 적힌 것과 **글자 그대로** 맞아야 하므로 반드시 `GET`으로 실제 값을 먼저 읽는다.

### 6.2 맥락은 거르지 않는다

안내서 3.3:

> "이것을 비워 두면 **에이전트가 이름·컬럼·상태값을 지어낸다.** 시스템 쪽은 프롬프트에 32KB가 가는데
> 이 칸이 비면 '무엇을 만드는지'는 한 줄도 가지 않는다."

워커는 팀런 목표와 사이클 지시에서 **이미 정해진 사실**(이름·값·상태)만 적는다. 취향은 적지 않는다.

### 6.3 워커가 기다리는 방법

`/api/run`은 SSE다. codex 워커는 **한 번의 명령이 약 30초에서 돌아온다**(2026-08-28 실측, 근거는 §9.1).
그러므로 SSE를 한 호출로 끝까지 받을 수 없다. design-studio가 짧게 확인할 길을 열어 두었다.

1. `POST /api/run`을 **떼어놓고** 던진다 — 응답은 파일로 받는다
2. `GET /api/state`의 `running`이 `null`이 될 때까지, **자기 대기 천장 아래로 끊어** 확인한다
3. `GET /api/runs?limit=1`로 `ok`를 확인하고 `out/<화면>.layout.html`을 읽는다

claude 워커는 천장이 600초라 한 번에 기다려도 된다. **워커 프롬프트가 제공자별로 갈린 문단을 주는 것이
전제다** — 이 사안은 별도 분석 문서에 있다: `docs/reports/2026-08-28-worker-wait-ceiling-analysis.md`.

> **의존:** 그 문서의 제안(제공자별 블록)이 채택되지 않으면, 이 절은 codex 워커에서 실패한다.
> 그 경우 이 설계의 §6.3만 다시 쓴다. 나머지 절은 영향받지 않는다.

### 6.4 겹치면 design-studio가 막는다

안내서 4장:

> "긴 실행은 동시에 하나뿐이다. 두 번째 요청은 **409**와 함께 무엇이 도는지 알려준다."
> "도는 실행은 시작할 때 `projectRoot`와 `screen`을 **값으로 받아 들고 있으므로** 뒤에 바꿔도 따라가지 않는다."

자물쇠는 `run` · `fix` · `foundation` · `components` 넷에만 걸리고 **보는 일은 막지 않는다.**
그러므로 PAG는 직렬화를 만들지 않는다. 워커는 409를 받으면 `running`을 보고 기다렸다 다시 보낸다.

### 6.5 워커가 승인을 청한다

산출물을 태스크 산출물로 내고, 결과에 블록을 하나 적는다. `team-note` · `next-cycle` 블록과 같은 방식이다.

````
```design-review
{"screen":"home","stage":"layout","file":"out/home.layout.html"}
```
````

**PAG가 산출물 종류로 알아서 판단하지 않는다.** 그러면 HTML 아티팩트를 전부 승인 대상으로 오해한다.
블록을 읽는 함수는 다른 두 블록과 같은 계약을 따른다 — **어떤 입력에도 예외를 던지지 않고**,
못 읽으면 항목을 만들지 않는다.

항목을 만들 때 **파일이 실제로 있는지 확인한다.** 없으면 만들지 않는다 — 빈 preview를 만들지 않기 위해서다.

### 6.6 사장님이 승인한다

```
POST /api/project        {"root": <project_root>}    실행 중에도 된다
POST /api/layout/accept  {"screen": <screen>}
```

**CSRF 헤더는 붙이지 않아도 된다.** 안내서 4장: *"셋 다 없는 요청은 통과한다 — 브라우저가 아닌
클라이언트를 막지 않기 위해서다."* `Origin`을 잘못 붙이면 오히려 403이다.

승인하면 골격이 `.design/layouts/<화면>.md`에 계약으로 적히고, 그 뒤 디자인 실행이 그것을 어기면
`layout-contract` P0로 되돌려 보내진다.

`stale`(승인 뒤 배치를 다시 잡은 상태)이면 **계약이 아니다.** 그 항목은 요청함에 다시 올린다.

### 6.7 반려가 돌아간다

반려는 이유를 저장하고 **다음 사이클의 워커 프롬프트로** 되돌린다. 팀 노트와 같은 자리, 같은 방식이다.

```python
prompt = _space_block(...) + _rules_block(...) + self._archive_block(...) \
       + self._team_note_block(run) + self._design_review_block(run) + WORKER_PROMPT.format(...)
```

`_design_review_block`은 **반려됐고 아직 다시 만들지 않은 것**만 넣는다.

**자동으로 사이클을 만들지 않는다.** 언제 다시 돌릴지는 사장님이 정하고, 돌 때 워커가 반려 이유를 들고 간다.

---

## 7. 화면

### 7.1 자리

지금 팀런 상세의 인라인 패널(`team-decision-panel`, `INPUT NEEDED · N`)이 있던 자리를
**`받은 요청 N` 버튼**으로 바꾼다. 누르면 모달이 열린다.

### 7.2 모달

```
┌ 받은 요청 3 ─────────────────────────────────┐
│ ┌──────────┬────────────────────────────┐ │
│ │ [디자인] │  home 배치                  │ │
│ │ home 배치│  ┌──────────────────────┐  │ │
│ │──────────│  │ iframe               │  │ │
│ │ [질문]⏸  │  │ out/home.layout.html │  │ │
│ │ DB 선택  │  └──────────────────────┘  │ │
│ │──────────│  [승인]  [반려]             │ │
│ │ [질문]⏸  │                            │ │
│ │ 가격 표기│                            │ │
│ └──────────┴────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

- 왼쪽은 목록, 오른쪽은 선택한 항목
- **⏸ 표시는 "이 런을 멈추고 있다"는 뜻이다.** 질문은 런을 멈추고 디자인은 멈추지 않으므로,
  표시가 없으면 급한 것을 뒤로 미루게 된다
- 본문이 종류에 따라 갈린다 — **질문이면 입력칸 + 답변**, **디자인이면 iframe + 승인·반려**
- 반려를 누르면 **이유 칸이 열리고, 비어 있으면 보낼 수 없다**
- 답한 항목은 목록에서 사라진다

### 7.3 읽기 API

```
GET /api/team-runs/{id}/inbox
→ {"items": [
     {"kind":"question","blocking":true, ...},
     {"kind":"design","blocking":false,"artifact_id":"…","screen":"home", ...}
   ]}
```

두 소스를 합치는 자리는 여기 하나뿐이다.

---

## 8. 오류

없는 상황을 위한 처리는 만들지 않는다. 아래는 실제로 일어나는 것들이다.

| 무엇 | 어떻게 |
|---|---|
| design-studio가 안 떠 있음 | 워커가 실패를 그대로 보고한다. 항목은 안 생긴다. **자동 기동하지 않는다** |
| `409` 이미 실행 중 | `running`을 보고 기다렸다 다시 보낸다 |
| `ok=false` / P0가 남음 | 산출물이 확정되지 않은 것 — **항목을 만들지 않고** 실패로 보고한다 |
| 승인이 `stale` | 계약이 아니다 — 요청함에 다시 올린다 |
| 블록은 있는데 파일이 없음 | 항목을 만들지 않는다 |
| 블록이 깨진 JSON | 항목을 만들지 않는다. 예외를 던지지 않는다 |
| 맞는 시스템이 없음 | 만들지 않는다. 요청함에 질문으로 올린다 |

---

## 9. 근거 — 재현 방법

이 설계가 기대는 사실들이다. 하나라도 재현되지 않으면 해당 절을 다시 쓴다.

### 9.1 codex 워커의 한 명령 대기 상한은 약 30초다 (§6.3이 여기 기댄다)

codex 바이너리에 박힌 문서 문자열:

```bash
B=~/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe
strings -n 20 "$B" | grep -i "Effective range on Windows"
```

> `exec_command.yield_time_ms` — "… **Effective range on Windows is 10000-30000 ms.**"

실측으로도 확인했다 — `Start-Sleep -Seconds 120`이 `yield_time_ms`와 무관하게 30.0초에 반환됐다.
상세와 claude 쪽 수치는 `docs/reports/2026-08-28-worker-wait-ceiling-analysis.md`.

### 9.2 design-studio의 자물쇠와 CSRF (§6.4 · §6.6이 여기 기댄다)

`playground/design-studio/docs/agent-guide.md` 4장. 위 §6.4 · §6.6에 원문을 인용했다.

### 9.3 PAG의 아티팩트 preview (§5.2 · §7.2가 여기 기댄다)

```bash
grep -n "iframe" frontend/src/components/organisms/ArtifactModal/index.jsx
python -c "import sqlite3;print([r[1] for r in sqlite3.connect('data/app.sqlite').execute('pragma table_info(artifacts)')])"
```

`artifacts`에 `source_team_run_id` · `source_team_task_id` · `source_cycle_id` · `file_path`가 있다.

---

## 10. 시험

design-studio를 띄우지 않고 돈다. HTTP 클라이언트만 가짜 서버로 세우고 나머지는 PAG 안에서 검사한다.

1. 워커가 `design-review` 블록을 내면 항목이 생긴다. **파일이 없으면 안 생긴다.** 깨진 JSON이면 예외 없이 안 생긴다
2. 요청함이 질문과 디자인을 **한 목록**으로 주고, 런을 멈춘 항목에 표시가 붙는다
3. 승인이 design-studio를 부른다 — 가짜 서버가 `POST /api/project` → `POST /api/layout/accept`를 **그 순서로** 받았는지
4. 반려 이유가 다음 워커 프롬프트에 닿는다 — **렌더된 프롬프트**로 확인한다.
   모듈 상수를 보면 `.format()`이 안 된 것도 통과한다
5. 모달이 종류에 따라 갈린다 — 질문이면 입력칸, 디자인이면 iframe
6. **이유 없이 반려가 안 된다** — API와 화면 양쪽에서

---

## 11. 열린 것 / 위험

| # | 무엇 | 지금 판단 |
|---|---|---|
| 1 | `system` 하나(`airbnb`)뿐이다. 팀이 만드는 앱에 맞는 시스템이 대개 없다 | 첫 사이클은 "시스템이 없다"는 질문으로 끝날 가능성이 높다. 그게 맞는 동작이다 — 사장님이 만들어 주시면 다음 사이클이 쓴다 |
| 2 | §6.3이 `worker-wait-ceiling` 분석의 채택에 의존한다 | 채택 안 되면 §6.3만 다시 쓴다 |
| 3 | 워커가 `design-review` 블록을 안 내면 PAG는 모른다 | 지금은 감수한다. 실제 문제가 되면 `<프로젝트>/.design/runs.jsonl`을 PAG가 읽는 쪽으로 옮긴다 — 그때 옮기는 비용도 크지 않다 |
| 4 | design-studio 서버 주소·포트가 고정 가정(`127.0.0.1:7777`) | 설정값 하나로 뺀다. 기본값은 7777 |
