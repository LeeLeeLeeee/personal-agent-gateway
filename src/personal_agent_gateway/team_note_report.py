"""리드가 사이클 끝에 남기는 팀 노트를 합성 응답에서 꺼내는 순수 함수.

`extract_coverage_gaps` 와 같은 자리, 같은 규칙이다. 합성은 리드 단계라
여기서 예외가 나면 사이클 하나가 통째로 죽는다. 노트는 선택이므로, 선택인
것이 필수인 것을 죽일 수 있으면 안 된다 -- 그래서 이 함수는 아무것도 던지지
않고, 읽을 수 없는 것은 없는 것으로 본다.
"""

import json
import re
from dataclasses import dataclass, field

_BLOCK = re.compile(r"```team-note\s*\n(.*?)\n?```", re.DOTALL)
_MAX_TAGS = 10


@dataclass(frozen=True)
class TeamNote:
    title: str
    summary: str
    content_markdown: str
    tags: list[str] = field(default_factory=list)


def extract_team_note(text: str) -> tuple[str, TeamNote | None]:
    """요약에서 팀 노트 블록을 떼어내고, 요약과 노트를 돌려준다.

    노트가 없으면 (요약, None). 리드가 이번 사이클에 새로 알아낸 것이 없다고
    판단한 경우이고, 그것은 정상이다.

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
    except (ValueError, TypeError):
        return summary, None
    if not isinstance(payload, dict):
        return summary, None
    title = _text(payload.get("title"))
    content = _text(payload.get("content_markdown"))
    # 제목과 본문이 없으면 노트가 아니다. 빈 노트를 저장하면 지난 개정을
    # 빈 것으로 덮어써서, 쓰지 않은 것보다 나쁜 결과가 된다.
    if not title or not content:
        return summary, None
    return summary, TeamNote(
        title=title,
        summary=_text(payload.get("summary")),
        content_markdown=content,
        tags=_tags(payload.get("tags")),
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags = [_text(item) for item in value]
    return [tag for tag in tags if tag][:_MAX_TAGS]
