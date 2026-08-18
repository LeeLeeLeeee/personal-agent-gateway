# Stage 0 — 협업 모드를 비교할 수 있게 만드는 도구

승격 gate가 "legacy 대비 개선"을 요구하는데, 지금은 legacy가 얼마나 잘하는지 아무도 모른다. 이 단계는 그 숫자를 만들 **도구**를 만든다. 숫자 자체는 만들지 않는다.

부모 결정: [ADR 2026-08-13](../../adr/2026-08-13-agent-radio-team-collaboration.md) ·
설계 전문: [2026-08-12 설계 계획](../../todo/2026-08-12-agent-radio-team-collaboration-design-plan.md)

## 이번 범위

fixture 정의 형식, rubric, 실행 기록 스키마, 집계 스크립트, 그리고 "평가가 실제 mutation을 만들지 않는다"는 성질을 코드로 강제하는 것까지.

**모델 호출은 0이다.** 20개 태스크를 실제로 돌려 baseline을 만드는 일은 이 spec에 없다. 별도 승인 사항이다. 도구가 오동작하는지를 모델 비용 지출 **전에** 드러내는 것이 이 단계의 값이다.

## 왜 도구를 먼저 만드나

Stage 1~4는 각자 "고정 baseline과 직전 stage에 비교"해야 한다. 비교 대상이 나중에 형식이 바뀌면 이전 측정이 전부 무효가 된다. 그래서 형식을 먼저 고정하고, 그 형식이 실제로 기록 가능한지 도구로 확인한 뒤 측정에 돈을 쓴다.

## 배치와 그 이유

```
evaluation/agent_radio/
  tasks/<id>.json     fixture 정의 (버전관리)
  rubric.md           채점 규칙과 판정 방법 (버전관리)
  records/<...>.json  실행 기록 (버전관리 — 이게 증거다)
  fixture.py          정의·기록 로드와 검증
  aggregate.py        비교 표 생성
tests/test_agent_radio_evaluation.py
```

세 가지 결정과 근거:

- **`data/` 아래 두지 않는다.** `data/`는 `.gitignore`에 있다. 기록을 거기 두면 승격 근거가 버전관리되지 않고, ADR의 "baseline fixture와 rubric이 versioned돼 있다" gate를 만족할 수 없다.
- **`src/personal_agent_gateway/` 아래 두지 않는다.** 평가 도구는 제품 코드가 아니다. 배포 패키지에 들어가면 안 된다.
- **그래도 import 가능해야 한다.** 집계 로직을 subprocess로만 테스트하면 경계 조건을 못 짚는다. `pyproject.toml`의 `pythonpath = ["src"]`를 `["src", "evaluation"]`로 한 줄 늘린다. 대안(테스트에서 `sys.path` 조작, conftest hack)은 같은 효과에 더 숨은 방식이라 기각한다.

기록을 버전관리하는 이유는 크기가 작기 때문이 아니라, 나중에 "그때 legacy가 이랬다"를 다투게 될 때 git 이력이 유일한 심판이기 때문이다.

## fixture 태스크 정의

```json
{
  "schema": "gateway.eval-fixture/v1",
  "id": "understand-acceptance-gate",
  "type": "understanding",
  "title": "수용 게이트가 무엇을 실제로 검사하는지 설명한다",
  "goal": "Team Run의 수용 게이트가 required_verifications를 어떻게 판정하는지, 어떤 검사 종류가 실제로 실행되는지 설명하라.",
  "repo_ref": "d8e9cce",
  "execution_profile": "read_only",
  "rubric": [
    {
      "id": "R1",
      "criterion": "모든 검사 종류가 파일 읽기임을 말한다",
      "check": "답변이 file_nonempty/file_contains/file_matches/json_parses 중 둘 이상을 이름으로 들고, 컴파일·테스트 실행이 없다고 말한다"
    }
  ]
}
```

- `type`은 `understanding` · `architecture_impact` · `bounded_implementation` 셋만 허용한다. 문서가 정한 대표 작업군이 이 셋이다. 새 유형은 이 spec을 고쳐야 추가된다.
- `repo_ref`는 그 태스크가 가정하는 저장소 상태다. **필수다.** 코드가 움직이면 같은 질문의 정답이 달라지므로, 이것 없이는 3개월 뒤 재측정이 비교가 아니라 다른 실험이 된다.
- `execution_profile`은 `read_only` 또는 `bounded_write` 둘뿐이다.
- `rubric` 항목의 `check`는 **판정 방법**을 쓴다. "정확한가" 같은 문장은 거부한다 — 채점자가 둘이면 결과가 둘이 되기 때문이다.

## rubric 규칙

- 항목은 이진 판정이다. 부분 점수 없음.
- 각 항목은 답변 또는 workspace 산출물만 보고 판정 가능해야 한다. **하니스의 실행 로그를 뒤져야 하는 항목은 만들지 않는다** — 재현 가능하지 않기 때문이다.
- **다만 채점자가 제출된 테스트와 린트를 직접 돌리는 것은 허용한다.** 이건 로그를 뒤지는 것과 다르다: 누구나 반복할 수 있고, `bounded_implementation` 유형에서 "읽기에 맞다"와 "실제로 맞다"를 가르는 유일한 방법이다. 이 구분을 흐리면 그 유형은 자기 존재 이유를 검사하지 못한다.
- 태스크당 3~6개. 그보다 적으면 통과율이 운에 흔들리고, 많으면 채점이 안 된다.
- `criterion`은 무엇을 요구하는지, `check`는 어떻게 확인하는지로 역할을 나눈다.

## 실행 기록

```json
{
  "schema": "gateway.eval-record/v1",
  "fixture_id": "understand-acceptance-gate",
  "fixture_sha256": "<tasks/<id>.json의 해시>",
  "mode": "legacy",
  "repeat": 1,
  "harness_version": "0.1.0",
  "started_at": "2026-08-14T01:00:00Z",
  "finished_at": "2026-08-14T01:06:20Z",
  "wall_ms": 380000,
  "cost": {"provider": "codex", "input_tokens": 41200, "output_tokens": 3100},
  "rubric_results": [{"id": "R1", "passed": true, "note": "…"}],
  "rework_count": 0,
  "conflict_count": 0,
  "critical_defects_found": 0,
  "mode_metrics": {}
}
```

- `mode`는 `single_agent` · `legacy` · `radio_lite` · `passive` 넷. ADR의 모드 어휘와 정확히 같아야 한다.
- `fixture_sha256`이 있어야 "정의를 조용히 고치고 재측정"을 탐지할 수 있다. 집계 시 현재 파일 해시와 다르면 그 기록은 stale로 표시한다.
- `mode_metrics`는 Stage 1부터 협업 지표(전달 수, 유용하게 쓰인 message 비율 등)가 들어갈 자리다. 지금은 빈 객체이며, **지금 필드를 발명하지 않는다.** Stage 1이 무엇을 셀지 정할 때 그 spec이 채운다.
- `critical_defects_found`는 ADR이 "감소하면 승격 금지"로 지정한 지표라 core에 둔다.

## 집계

`aggregate.py`는 기록을 읽어 ADR의 판정 기준과 같은 축으로 비교 표를 만든다: 태스크 성공률, critical defect detection, distraction(재작업), 비용, wall-clock p50/p95.

- 비교 기준선은 항상 `mode: "legacy"`다. `single_agent`는 별도 참고 열이다.
- 유형별로 나눠 출력한다. `understanding`에서 이겼는데 `bounded_implementation`에서 졌다면 총계 하나로 가리면 안 된다.
- 반복이 유형별 5회 미만이거나 총 20 태스크 미만이면 **표에 "기본 활성화 판단 불가"를 함께 출력한다.** ADR의 gate를 사람 기억에 맡기지 않는다.
- p95는 표본이 적으면 무의미하다. 표본 수를 항상 같이 출력하고, 5개 미만이면 p95 칸에 `n/a`를 쓴다.
- stale 기록(해시 불일치)은 집계에서 제외하고 몇 건 제외했는지 출력한다. 조용히 버리지 않는다.

## mutation 금지를 코드로 강제한다

ADR의 Stage 0 gate는 "평가 실행이 user data나 실제 외부 mutation을 만들지 않는다"다. 문서상 약속으로는 부족하다.

`fixture.py`의 검증이 다음을 거부한다.

- `execution_profile`이 두 값 중 하나가 아닌 정의.
- `goal`에 절대 경로, `..`, 또는 `git push` · `npm publish` · `gh pr` 같은 외부 mutation 명령이 들어간 정의.
- `repo_ref`가 없거나 현재 저장소에 없는 커밋인 정의.

세 번째는 실행기가 아직 없어도 지금 검증한다. 잘못된 `repo_ref`는 측정 시점이 아니라 정의 시점에 잡아야 싸다.

**실행기가 이 spec에 없으므로, 실행 시점의 격리는 여기서 보장할 수 없다.** 이 spec은 "정의가 위험한 요구를 담지 못하게" 막을 뿐이다. 실제 실행 격리(workspace 정책, write 차단)는 실행기를 만드는 후속 spec의 책임이고, 그 spec 없이 측정을 시작하면 안 된다.

## 실행기에 대해 지금 아는 것

실행기는 이번 범위가 아니지만, 기록 스키마가 그 형태에 묶이므로 확인한 사실을 남긴다.

- **HTTP API로는 못 돈다.** `/api/team-runs/{id}/detail`이 `401 OTP login required`를 반환한다. 하니스가 OTP 로그인을 자동화하는 것은 인증을 우회하는 방향이라 택하지 않는다.
- **in-process 구동이 유력하다.** 기존 테스트가 `TeamRunService`/`TeamRuntime`을 직접 만들어 돌린다. 같은 경로를 쓰면 API 인증을 건드리지 않는다.
- ~~**위험:** `runtime_factory`의 헤드리스 경로 테스트 16건이 현재 실패 중이다.~~ **해소됨(2026-08-13, `7802300`).** 낡은 테스트였고 프로덕션 코드는 손대지 않았다. 하니스가 의지할 경로에 이제 커버리지가 있다.
- **협상은 이미 있다(2026-08-14).** Stage 1의 plan 협상이 구현돼 `plan_negotiation_enabled` 플래그로 켜진다. 기록의 `mode` 어휘에는 아직 이에 해당하는 값이 없다 — ADR의 넷(`single_agent`·`legacy`·`radio_lite`·`passive`)은 watcher 축이고 협상은 그와 직교하기 때문이다. 실행기 spec이 협상 켠 런을 어떤 mode로 기록할지 정해야 한다. 지금 발명하지 않는다.

## 이번 범위가 아닌 것

- 태스크 실제 실행, 모델 호출, baseline 수치 생성.
- 실행기와 실행 시점 격리.
- 협업 지표 필드 정의(Stage 1 소유).
- 채점 자동화. 지금은 사람이 rubric으로 채점하고 그 결과를 기록에 넣는다. 자동 채점기는 채점 규칙이 실제 답변들에 부딪혀 본 뒤에 검토한다.
- CI 통합.

## 검증

- 유형 3개를 대표하는 fixture 정의가 있고, 모두 검증을 통과한다.
- `execution_profile`이 잘못된 정의, `goal`에 `git push`가 든 정의, `repo_ref`가 없는 정의, 존재하지 않는 커밋을 가리키는 정의가 각각 거부된다.
- rubric 항목이 3개 미만이거나 7개 이상인 정의가 거부된다.
- 손으로 쓴 기록 몇 건으로 집계 표가 생성되고, 기준선이 `legacy`이며 유형별로 나뉘어 나온다.
- 표본이 5개 미만인 축의 p95가 `n/a`로 나오고 표본 수가 함께 출력된다.
- fixture 정의를 한 글자 바꾸면 기존 기록이 stale로 분류되고, 제외 건수가 출력된다.
- 반복이 부족하면 "기본 활성화 판단 불가"가 표에 출력된다.
- 이 도구를 실행하는 어떤 경로도 모델을 호출하지 않는다 — 테스트가 네트워크·provider client를 건드리지 않고 통과한다.
- `pythonpath` 변경 후에도 기존 백엔드 스위트의 실패 분포가 baseline과 같다.
