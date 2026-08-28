# 워커의 명령 대기 상한 — 분석과 제안 (검토 요청)

작성 2026-08-28 · 작성자 Claude (Opus 5) · **검토자는 아래 "신뢰도 경고"를 먼저 읽을 것**

---

## 0. 검토자에게

이 문서를 쓴 에이전트는 **같은 사안에서 네 번 틀렸다.** 매번 "확인했다"고 말한 뒤 틀렸다.
그러므로 이 문서의 결론을 그대로 받지 말고, **§3의 재현 명령을 직접 돌려서** 다시 확인하기 바란다.

특히 다음 습관이 반복됐다:

- 한 환경에서 재고 다른 환경에 일반화했다.
- 로그에 어떤 문자열이 **없다**는 것을 그 기능이 **없다**는 뜻으로 읽었다.
- 큰 숫자를 보고 그 숫자가 무엇을 센 것인지 확인하기 전에 결론을 냈다.

요청하는 것은 두 가지다.

1. **§3의 "확인된 사실"을 재현해서 참인지 판정할 것.** 하나라도 재현이 안 되면 §6 제안은 폐기 대상이다.
2. **§6 제안이 §3의 사실로부터 실제로 따라 나오는지** 판정할 것. 사실이 참이어도 제안이 과하거나 빗나갈 수 있다.

---

## 1. 한 줄 요약

팀런 워커는 제공자(codex / claude)에 따라 **한 번의 명령이 기다려주는 시간 상한이 20배 다른데**,
워커 프롬프트는 상수 하나여서 그 차이를 담을 자리가 없다.
그래서 한쪽에 맞는 지시가 다른 쪽을 죽인다. **제안: 제공자별 문단을 프롬프트에 주입한다.**

---

## 2. 무슨 일이 있었나

영어 학습 앱을 만드는 팀런(`team_run_id` 접두 `ad28fa24`)이 **6문장 지문을 한 번에 처리하는 작업**에서
반복 실패했다. 워커가 붙인 실패 사유:

| 사이클 | `team_tasks.error_message` |
|---|---|
| 36 | `long_run_not_observable_in_worker_env` |
| 37 | `needs_step_runner_before_observation` |

워커의 최종 보고(원문):

> "이 환경의 Windows 명령 실행은 `yield_time_ms`를 600초로 명시해도 약 30초에 세션/exit code 없이
> 반환됩니다. 장기 동기 6문장 실행을 재개할 session ID가 제공되지 않아 (A)를 완료할 수 없고 …"

해당 앱의 6문장 처리는 **약 4분 30초(270초)** 걸린다(팀이 앞선 사이클에서 1문장 37.2초로 실측한 값의 외삽).

---

## 3. 확인된 사실 — 각각 재현 방법 포함

### 3.1 codex의 한 명령 대기 상한은 약 30초다 (Windows)

**근거 A — codex 바이너리에 박힌 문서 문자열**

```bash
B=~/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe
strings -n 20 "$B" | grep -i "Effective range on Windows"
```

기대 출력에 포함된 문장:

> `exec_command.yield_time_ms` — "Maximum time to wait before returning a session ID for a still-running
> command. … **Effective range on Windows is 10000-30000 ms.**"

**근거 B — 실측.** 워커와 같은 모델·같은 플래그로 `Start-Sleep -Seconds 120`을 시켰더니
`yield_time_ms`와 무관하게 **30.0초**에 반환됐다.

```bash
mkdir -p /tmp/probe && cd /tmp/probe && git init -q .
echo '120초 자는 명령을 한 번 실행하고 몇 초 만에 돌아왔는지 보고하라.' | \
codex exec --json -c approval_policy='"on-request"' -c model_reasoning_effort='"high"' \
  --sandbox workspace-write -C /tmp/probe --skip-git-repo-check --model gpt-5.6-terra -
```

관측된 반환값:

```json
{"chunk_id":"6d1863","wall_time_seconds":30.0008931,"session_id":12727,"original_token_count":0,"output":""}
```

`exit_code`가 없고 `session_id`가 있다 = **아직 안 끝났다**는 뜻.

### 3.2 claude의 같은 상한은 600초다

**근거 — 실측.** 같은 과제를 `claude -p`로 돌렸을 때 도구가 스스로 붙인 값:

```
PowerShell(timeout=700000)   ← 700초
PowerShell(timeout=600000)   ← 600초
Bash(timeout=600000)
```

LMG는 claude 워커를 도구 제한 없이 띄운다 — `local-model-gateway/internal/provider/claude/command.go`
의 `buildCommand`에 `--allowedTools` 계열 인자가 없다.

### 3.3 팀런 워커는 양쪽으로 돈다

```sql
select stage, provider, count(*) from team_model_operations
where team_run_id like 'ad28fa24%' group by stage, provider;
```

관측: `worker_execution` → **codex 112건, claude 24건**.
로스터에도 섞여 있다 — QA 리드 = `opus`, 기능 QA = `gpt-5.6-terra`.

### 3.4 "codex가 5시간 돈다"와 30초 상한은 충돌하지 않는다

사장님 개인 세션(`~/.codex/sessions/2026/08/2[5-8]`, 명령 662건)의 분포:

```bash
cd ~/.codex/sessions/2026/08
grep -rhoE 'Wall time [0-9.]+ seconds' 2[5-8]/*.jsonl | sed 's/[^0-9.]//g' | sort -n | awk '{a[NR]=$1} END{
  print "총:",NR; print "중앙값:",a[int(NR/2)]; print "p90:",a[int(NR*0.9)];
  c=0;for(i=1;i<=NR;i++) if(a[i]>31) c++; print "31초 초과:",c}'
```

| 항목 | 값 |
|---|---|
| 총 명령 | 662 |
| 중앙값 | **1.0초** |
| p90 | **4.8초** |
| 31초 초과 | **4건 (0.6%)** |

그리고 31초를 넘은 4건은 **컴퓨터가 일한 시간이 아니라 사람 승인을 기다린 시간**이다.
가장 긴 436.9초 건의 실제 호출:

```javascript
tools.exec_command({
  cmd: "New-Item -ItemType Directory -Force -Path 'C:\\tmp\\allre-flow\\...'",  // 즉시 끝나는 명령
  sandbox_permissions: "require_escalated",
  justification: "…저장하도록 허용할까요?"
});
```

결론: **긴 세션 = 짧은 명령 수백 번**. 한 명령이 30초를 넘길 때만 상한과 부딪히고, 사장님 작업에선
그런 일이 662건 중 0건이다. 영어 학습 앱의 270초 파이프라인이 그 드문 경우다.

### 3.5 "떼어놓고 파일로 확인하기"는 codex에서 통한다 (실측)

§3.1과 같은 환경에서, 아래 규칙을 프롬프트로 주고 90초짜리 일을 시켰다.

> 1. 오래 걸리는 일은 도구가 기다리게 두지 마라. 떼어놓고 돌려서 진행 상황과 끝났다는 표시를
>    파일에 쓰게 하고, 명령은 즉시 빠져나와라.
> 2. 기다림은 네 도구의 천장 아래에서 끊어 가져가라. 천장은 한 번 재서 알아둬라.
> 3. 종료 코드 없이, 출력 없이 돌아온 것은 "끝났는데 빈손"이 아니라 "아직"이다. 판단은 반환값이
>    아니라 파일로 해라. 다시 띄우지 마라.

관측된 실행 (도구 호출 4번, 90초 작업 완료, `progress.log`에 `1..9 DONE` 기록됨):

| # | 한 일 | 반환 |
|---|---|---|
| 1 | 일을 떼어놓고 던짐 | 2.0초, `exit_code: 0` |
| 2 | 천장 측정 (`Start-Sleep 120`) | **30.0초, exit_code 없음, session_id 있음** |
| 3 | 아직 살아 있는지 확인 | 5.0초 |
| 4 | 25초 대기 후 파일 읽기 | `1..9 DONE`, `exit_code: 0` |

### 3.6 같은 규칙이 claude에서는 실패한다 (실측)

`claude -p --permission-mode acceptEdits --model opus`로 **같은 프롬프트**를 돌린 결과:

- 도구 호출 **15번**, **195초** 소요, **결과 없음**
- `progress.log`가 **5줄에서 멈춤**. 세션 종료 후에도 자라지 않음
  → `run_in_background`로 던진 일은 **`claude -p`가 끝나면 같이 죽는다**
- 최종 보고 원문: *"감시자를 걸어뒀습니다. … **알림을 기다립니다.**"*
  → 헤드리스 워커에는 **다음 턴이 없다.** 거기서 실행이 끝난다

원인: **규칙 1("떼어놓아라")이 claude에는 불필요하고 해롭다.** claude의 천장(600초)은 이 작업(90초)보다
크므로 한 호출로 기다리면 끝난다. 실제로 claude는 중간에 `timeout=600000`으로 한 번에 기다리는
옳은 방법을 썼다가, 규칙을 따르느라 백그라운드로 옮겨가서 죽었다.

---

## 4. 확인되지 **않은** 것 (가설이거나 미확인)

검토자는 이 절의 항목을 사실로 취급하지 말 것.

| # | 내용 | 상태 |
|---|---|---|
| 4.1 | 실패한 워커 세션에 `write_stdin`이 **없었다** | **미확인.** 세션 기록에는 도구 *정의*가 남지 않고 *호출*만 남는다. 확실한 것은 "워커가 부르지 않았고, session_id를 못 받았다고 보고했다"뿐이다 |
| 4.2 | 이어받은 옛 세션(8/24)은 옛 도구 구성을 물려받는다 | **가설.** 실패 세션은 8/24 재개본이었고 새 세션 실험에서는 `write_stdin`이 있었다. 인과는 확인 안 됨 |
| 4.3 | `code_mode.default_exec_yield_time_ms` 설정이 무엇의 기본값인지 | **미확인.** 바이너리 문자열로만 봤다. LMG가 `-c`로 넘길 통로는 있다(`internal/provider/codex/command.go:14` `baseConfigArgs`) |
| 4.4 | codex의 exec **셀**(JS)이 30초 넘게 *계산*을 지속할 수 있는지 | **미확인.** 30초 넘은 관측 사례는 전부 승인 대기였다 |
| 4.5 | 270초라는 6문장 소요 시간 | **외삽.** 1문장 37.2초 실측에서 추정. 6문장 성공 사례 없음 |
| 4.6 | claude 워커의 600초 상한이 LMG 경유 시에도 같은지 | **부분 확인.** `claude -p`를 직접 돌려 관측했다. LMG가 추가 제약을 걸지 않는 것은 코드로 확인했으나 LMG 경유 실측은 안 했다 |

---

## 5. 이 에이전트가 틀렸던 이력 (신뢰도 판단용)

| # | 주장 | 실제 | 왜 틀렸나 |
|---|---|---|---|
| 1 | "앱의 60초 소켓 타임아웃이 원인" | 팀이 이미 고친 코드였음 | 낡은 파일을 읽고 결론 |
| 2 | "`yield_time_ms`를 400000으로 주면 된다" | Windows 상한 30초라 무효 | 한 번 실패한 값(60000)을 키우면 될 거라 가정 |
| 3 | "`write_stdin`으로 이어받으면 된다" | 워커는 session_id를 못 받았고 그 도구를 안 씀 | **다른 환경**(맨 codex exec)에서 재고 워커 환경에 적용 |
| 4 | "436초 사례가 있으니 30초는 상한이 아니다" | 그 436초는 **사람 승인 대기** | 숫자가 무엇을 센 것인지 확인 전에 결론 |

네 건 모두 **"측정한 환경 ≠ 적용할 환경"** 또는 **"측정값의 의미 미확인"** 이다.

---

## 6. 제안 (검토 대상)

### 6.1 문제의 구조적 원인

`src/personal_agent_gateway/team_runtime.py:166`의 `WORKER_PROMPT`는 **모듈 상수 하나**이고
제공자를 모른다. 따라서 제공자마다 다른 사실(대기 상한, 백그라운드 프로세스의 수명, 도구 이름)은
**둘 곳이 없다.** 그 결과 그런 사실은 (a) 빠지거나 (b) 한쪽 기준으로 잘못 일반화된다.
§3.6이 (b)의 실제 사례다.

### 6.2 제안하는 변경

`_worker_prompt`(`team_runtime.py:4722`)는 이미 `worker`를 받고, `agent.backend`로 제공자를 알 수 있다
(`team_runtime.py:6216`에 `provider=agent.backend` 용례). 그리고 프롬프트는 이미 블록 합성 구조다:

```python
prompt = _space_block(...) + _rules_block(...) + self._archive_block(...) \
       + self._team_note_block(run) + WORKER_PROMPT.format(...)
```

여기에 `self._environment_block(worker)` 하나를 더한다. 내용은 제공자별로 다음 한 문단씩:

- **codex**: 한 번의 기다림은 약 30초에서 돌아온다(2026-08-28 측정). 그보다 오래 걸리는 일은
  떼어놓고 진행 상황과 완료 표시를 파일에 쓰게 한 뒤, 25초쯤씩 끊어 파일을 확인해라.
- **claude**: 한 번의 기다림은 600초까지 간다(2026-08-28 측정). 그 안에 드는 일은 **한 호출로**
  기다려라. 백그라운드로 던지지 마라 — 네 실행이 끝나면 같이 죽는다. 알림을 기다리며 끝내지 마라.

양쪽 공통으로: **"측정값이 다르다고 느끼면 잠깐 자는 명령 하나로 다시 재고, 잰 값을 써라."**
(숫자가 낡아도 워커가 스스로 고치게 하는 장치.)

그리고 `WORKER_PROMPT`에는 제공자 중립 태도만 남긴다:
**"종료 코드 없이 돌아온 것은 아직이다. 판단은 반환값이 아니라 결과물로. 다시 띄우지 마라."**

### 6.3 검토하지 않고 채택한 대안 (기각 사유 포함)

| 안 | 내용 | 기각 사유 |
|---|---|---|
| 재서 넣기 | 사이클마다 탐침을 돌려 상한 측정 | 상한은 자주 안 바뀐다. 사이클마다 비용·실패 지점 추가는 과함 |
| 플랫폼이 대신 | 올바른 방법을 담은 도우미 스크립트를 워크스페이스에 스테이징 | **더 나은 끝그림이나 순서가 나중.** 도우미가 뭘 해야 맞는지는 6.2의 문장이 정한다. 먼저 굳히면 미검증 방법을 코드로 박게 됨 |
| 팀 노트에 적기 | 리드가 사이클 말미에 기록 | 팀별이라 새 팀이 매번 재학습. 환경 사실은 전 팀 공통이므로 프롬프트가 맞는 자리 |

### 6.4 경계

| 어디에 | 무엇이 |
|---|---|
| 제공자 블록 (신규) | 전 팀 공통 **환경 사실** — 대기 상한, 백그라운드 수명 |
| 팀 노트 (기존) | 이 팀만 아는 것 — "6문장에 4분 30초" |
| 워커 프롬프트 (기존) | 제공자 중립 태도 |

### 6.5 채택 시 검증 조건

제안을 구현하면 **§3.5·§3.6과 같은 방식으로 codex·claude 양쪽에서 다시 실측**하여,
codex는 4회 내외 호출로 완료하고 **claude는 백그라운드를 쓰지 않고 1~2회로 완료**하는 것을
확인한 뒤에만 커밋한다.

---

## 7. 현재 코드 상태 (검토자 확인용)

커밋 `9712536` (main, 미푸시)에 다음이 이미 들어가 있다 — **§3.6에 의해 claude에 유해함이
드러난 상태이며, 되돌리거나 6.2로 대체해야 한다.**

- `src/personal_agent_gateway/team_runtime.py:220-227` — "종료 코드 없이 돌아왔으면 그 id로
  이어받아라" 규칙. **문제:** 이어받을 id가 없는 환경이 있고(§4.1), claude에는 해당 개념이 없다
- `tests/test_team_runtime.py:10561` — 위 규칙이 프롬프트에 닿는지 확인하는 테스트

관련 파일:

- 워커 프롬프트: `src/personal_agent_gateway/team_runtime.py:166`, 합성 지점 `:4722`
- LMG codex 명령 구성: `local-model-gateway/internal/provider/codex/command.go`
- LMG claude 명령 구성: `local-model-gateway/internal/provider/claude/command.go`
- 실패 워커 세션: `~/.codex/sessions/2026/08/24/rollout-2026-08-24T10-46-12-01a03172-*.jsonl`
- DB: `data/app.sqlite` — `team_run_cycles`, `team_tasks`, `team_model_operations`, `team_agents`

---

## 8. 검토자에게 묻는 것

1. **§3의 사실 중 재현되지 않는 것이 있는가?**
2. **§4의 미확인 항목 중, §6 제안이 실제로는 의존하고 있는 것이 있는가?** (있다면 제안은 재작성 대상)
3. **§6.2가 §3.6의 실패를 실제로 막는가?** 못 막는 경로가 있다면 무엇인가.
4. **§6.3에서 기각한 "플랫폼이 대신" 안이 사실은 지금 해야 할 일인가?**
5. **§7의 기존 커밋을 되돌려야 하는가, 6.2로 대체하면 되는가?**
