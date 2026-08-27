"""리드가 사이클 끝에 내는 다음 사이클 제안을 합성 응답에서 꺼내는 순수 함수.

`extract_team_note`, `extract_coverage_gaps` 와 같은 자리, 같은 규칙이다.
합성은 리드 단계라 여기서 예외가 나면 사이클 하나가 통째로 죽는다. 제안은
선택이므로, 선택인 것이 필수인 것을 죽일 수 있으면 안 된다 -- 이 함수는
아무것도 던지지 않고, 읽을 수 없는 것은 없는 것으로 본다.
"""

import json
import re

_BLOCK = re.compile(r"```next-cycle\s*\n(.*?)\n?```", re.DOTALL)
#: 다음 사이클의 지시 하나다. 이보다 길면 사이클 지시가 아니라 보고서다.
MAX_INSTRUCTION_CHARS = 2_000


def extract_next_cycle(text: str) -> tuple[str, str | None]:
    """요약에서 다음 사이클 제안을 떼어내고, 요약과 지시를 돌려준다.

    제안이 없으면 (요약, None). 리드가 더 할 일이 없다고 판단한 경우이고,
    그것이 시리즈를 끝내는 신호다.

    첫 블록만 읽는다. 뒤에 더 있으면 요약에 그대로 남는데, 그 편이 조용히
    지우는 것보다 낫다 -- 사람이 보면 리드가 형식을 잘못 지켰다는 것을 안다.
    """
    if not isinstance(text, str):
        return "", None
    match = _BLOCK.search(text or "")
    if match is None:
        return (text or "").strip(), None
    summary = (text[: match.start()] + text[match.end() :]).strip()
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return summary, None
    if not isinstance(payload, dict):
        return summary, None
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        return summary, None
    return summary, instruction.strip()[:MAX_INSTRUCTION_CHARS]
