# 채팅 응답 구조화 포맷 렌더링 Spec

- 작성일: 2026-07-08
- 대상: `src/personal_agent_gateway/static/app.js`, `styles.css`, `index.html`, `app.py`(정적 마운트 1줄), `static/vendor/**`
- 브랜치: feat/live-activity-viewer
- 범위: 프론트 중심 + 최소 백엔드(정적 서빙 경로). **2차 AI 변환 없음.**

## 1. 배경 / 문제

codex 에이전트 응답은 이미 마크다운/코드펜스/mermaid/표를 포함해 스트리밍된다(SSE `agent_message`). 현재 `renderMarkdown`(app.js)은 문단·제목·목록·굵게·인라인코드·링크·코드펜스(검은 `<pre>`)까지만 처리한다. 확인된 공백:

- 코드펜스: 구문 강조·복사 없음(밋밋한 블록).
- ` ```mermaid `: 다이어그램이 아니라 코드 텍스트로 보임(렌더러 없음).
- 마크다운 표: 미지원(파이프가 그대로 문단).

목표는 **이미 응답에 들어온 구조화 포맷을 그 포맷 전용으로 렌더링**하는 것. AI를 다시 호출하지 않는다.

## 2. 목표 / 성공 기준

- 코드펜스(` ```lang `)가 **구문 강조 + 복사 버튼**으로 렌더된다.
- ` ```mermaid ` 블록은 기본 코드로 보이고, **"그래프 보기"** 클릭 시 **mermaid를 lazy 로드**해 SVG 다이어그램으로 렌더, **⤢ 확대** 모달 제공, 코드↔그래프 토글.
- 마크다운 파이프 표가 HTML `<table>`로 렌더되고, 넓으면 스크롤 + ⤢ 확대.
- mermaid 라이브러리는 **초기 로드에 포함되지 않고** 첫 "그래프 보기" 시에만 받아진다.
- `renderShell` 전체 리렌더(경과 타이머/SSE) 시 다이어그램/강조가 **깜빡이지 않고** 유지된다(캐시 + 토글 상태 보존).
- 스트리밍 중 닫히지 않은 펜스는 깨지지 않는다(완료 시 승격).
- 오프라인(외부 요청 없음)에서 동작한다.

## 3. 정적 서빙 (백엔드 최소 변경)

현재 `/static`은 `app.js`/`styles.css` 개별 라우트뿐이고 디렉터리 마운트가 없다. 라이브러리를 두려면 서빙 경로가 필요하다.

- `app.py`: `app.mount("/static/vendor", StaticFiles(directory=static_dir / "vendor"), name="vendor")` **1줄** 추가. 기존 두 라우트는 건드리지 않음(충돌·테스트 영향 최소).
- `static/vendor/`에 라이브러리 벤더링(커밋): `highlight.min.js`(+ 최소 언어), mermaid ESM 번들(`mermaid.esm.min.mjs` 및 서브 청크).
- 근거: 이 앱은 "조용한 로컬/프라이빗 도구"라 CDN 외부 요청·오프라인 실패를 피한다(브리프 기조와 일치).
- 주의: Python/API는 평소 Codex 담당 — 사용자 승인 하에 프론트가 마운트 추가. 커밋/메모리 표기. 관련: [[pag-frontend-ownership]].

## 4. 렌더링 (app.js `renderMarkdown` 확장)

펜스 파싱 시 언어 태그로 분기:

| 입력 | 처리 |
|---|---|
| ` ```mermaid ` | mermaid 블록 컴포넌트(기본 코드 + "그래프 보기") |
| ` ```lang ` (lang 있음) | highlight.js 강조 + 복사 버튼 |
| ` ``` ` (lang 없음) | 현재 검은 코드 블록 + 복사 버튼 |
| `\| … \|` GFM 표 | HTML `<table>` (헤더 구분선 `---` 인식) |

### 4.1 코드 블록
- highlight.js는 일반 로드(가벼움). 강조 결과는 **내용 해시로 캐시**해 리렌더 시 재계산 안 함.
- 우측 상단 **복사** 버튼(클립보드 API). 언어 라벨 표시.

### 4.2 Mermaid (lazy)
- 기본: 소스 코드 표시 + **`▸ 그래프 보기`** 버튼.
- 클릭:
  1. 최초 1회 `import("/static/vendor/mermaid.esm.min.mjs")` 동적 로드 → `mermaid.initialize({ startOnLoad:false, securityLevel:"strict", theme:"neutral" })`.
  2. `mermaid.render(id, source)` → SVG. **내용 해시→SVG 캐시**에 저장.
  3. 인라인 삽입, 버튼 `▾ 코드 보기`로 토글, **⤢ 확대**(모달) 추가.
- 상태: `state.mermaidShown`(해시 Set) + `state.mermaidSvg`(해시→SVG). 렌더 시 해시가 shown이고 캐시 있으면 SVG 즉시 삽입(비동기 재렌더 안 함) → 무깜빡임.
- 렌더 실패(문법 오류) 시: 에러 메시지 + 코드로 폴백.

### 4.3 표
- GFM 파이프 표 파서. `<table>`(neo-brutal 스타일), 넓으면 래퍼 `overflow-x:auto` + ⤢ 확대.

### 4.4 확대 모달
- 공용 오버레이(neo-brutal): mermaid SVG / 넓은 표를 크게, 스크롤/줌(간단히 SVG 자연 크기 + 스크롤). 배경 클릭·Esc로 닫기. 코드는 복사만(확대 없음).

## 5. 기술적 주의 (codebase 구조)

- **전체 리렌더 캐싱:** `renderShell`이 매 렌더마다 DOM 전체 교체 → mermaid 재렌더는 비싸고 비동기. 반드시 해시 캐시 + shown 상태로 즉시 삽입. highlight 결과도 캐시.
- **스트리밍 안전:** 닫힌 펜스만 강조/그래프 대상. 미완성 펜스는 평문 코드; 완료 후 렌더에서 승격.
- **보안:** mermaid `securityLevel:"strict"`. highlight/표/마크다운은 노드 기반이라 XSS 안전 유지. 입력은 준신뢰(AI) 취급.

## 6. 검증

프론트 테스트 프레임워크 없음. 확인:
- 벤더 파일 서빙(`/static/vendor/...` 200), 초기 로드에 mermaid **미포함**(네트워크 탭에서 "그래프 보기" 후에만 요청).
- 코드 강조 + 복사 동작, 언어 라벨.
- mermaid "그래프 보기" → 다이어그램, 토글, ⤢ 확대, 문법오류 폴백.
- 표 렌더 + 넓은 표 스크롤/확대.
- 스트리밍 중 미완성 펜스 안 깨짐 → 완료 시 승격.
- 리렌더(메시지 전송/SSE/타이머) 시 무깜빡임(캐시).
- 백엔드: `StaticFiles` 마운트 추가 후 `pytest` 그린 유지.
- 실제 codex 응답으로 코드/mermaid/표 유도해 확인(`/static/vendor` 마운트 + 실 서버).

## 7. 범위 밖 (YAGNI)

2차 AI 변환, 수식(LaTeX)/이미지/인용구, 문서 artifact화·별도 문서 라우트, mermaid 외 lazy(코드/표는 일반). 기존 `app.js`/`styles.css` 정적 라우트 구조 변경(마운트는 `/static/vendor` 하위로 격리).
