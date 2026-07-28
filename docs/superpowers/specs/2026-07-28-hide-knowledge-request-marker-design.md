# Knowledge Request Chat Marker Hiding Design

## 목표

Chat 응답에 포함된 내부 `<knowledge_request>...</knowledge_request>` 블록을
사용자에게 노출하지 않고, 해당 위치를 `Library에 요청되었습니다`로 표시한다.
Knowledge Request 생성과 Library의 Requests 목록 저장 동작은 그대로 유지한다.

## 결정

백엔드와 프론트엔드 표시 경계를 함께 정규화한다.

- `ArchiveService.capture_response_requests()`는 유효한 마커를 기존처럼
  Knowledge Request로 저장한 뒤, 영문 제목 안내 대신
  `Library에 요청되었습니다`를 반환한다.
- Chat의 LMG SSE `message.completed`는 백엔드의 최종 정제보다 먼저 UI에
  도착하므로, 프론트엔드 타임라인 변환에서도 동일한 마커를 제거하고 같은
  안내 문구로 치환한다.
- 저장된 transcript와 실시간 SSE가 같은 결과를 표시하도록 두 경계의 문구를
  일치시킨다.

## 데이터 흐름

1. 모델이 일반 답변 뒤에 Knowledge Request 마커를 생성한다.
2. PAG는 마커의 JSON을 파싱하여 Knowledge Request를 저장한다.
3. transcript와 HTTP 응답에는 일반 답변과 `Library에 요청되었습니다`만 남긴다.
4. 프론트엔드는 정제 전 `message.completed` SSE를 받더라도 같은 형태로
   표시하여 원본 블록이 최종 말풍선에 남지 않게 한다.

## 경계와 예외

- 정확히 짝이 맞는 `<knowledge_request>...</knowledge_request>` 블록은
  화면에서 제거한다. 일반 텍스트는 변경하지 않는다.
- JSON이 유효하고 필수 필드가 있어 실제 요청을 저장한 경우에만 안내 문구를
  표시한다.
- 하나의 응답에 여러 유효한 요청이 있어도 안내 문구는 한 번만 표시한다.
- Knowledge Request의 제목, 사유, 목차, 참고 자료 저장 형식은 변경하지 않는다.
- Library의 `Send to team`, `Write in Library`, 상태 전이 동작은 변경하지 않는다.
- 이번 변경은 완료된 `message.completed` 표시를 대상으로 하며 LMG의 원본
  이벤트 계약은 변경하지 않는다.

## 검증

- 백엔드 테스트: 마커가 제거되고 정확한 한국어 안내가 반환되며 요청은 한 건
  저장된다.
- 프론트엔드 테스트: 원본 마커를 포함한 `message.completed`가 일반 답변과
  한국어 안내만 표시한다.
- 기존 Archive, Runtime, Timeline 관련 테스트를 실행해 요청 저장과 일반
  Chat 표시의 회귀가 없는지 확인한다.
