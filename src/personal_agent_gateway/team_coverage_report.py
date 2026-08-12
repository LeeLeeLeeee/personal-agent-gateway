import json
import re

_BLOCK = re.compile(
    r"```coverage-gaps\s*\n(.*?)\n?```",
    re.DOTALL,
)


def extract_coverage_gaps(
    text: str,
) -> tuple[str, list[dict[str, str]] | None]:
    """Pull an optional coverage-gaps block out of a leader's prose summary.

    Returns the summary without the block, and the gaps -- or None when the
    leader did not report. None and [] are deliberately different: one means the
    leader said nothing, the other means it claimed full coverage, and only the
    second is a claim the operator can contest.

    Nothing here raises. Synthesis is a leader stage, so a parse failure costs
    the cycle, and a block that is optional by design must not be able to do
    that.
    """
    match = _BLOCK.search(text or "")
    if match is None:
        return (text or "").strip(), None
    summary = (text[: match.start()] + text[match.end():]).strip()
    try:
        payload = json.loads(match.group(1))
    except (ValueError, TypeError):
        return summary, None
    if not isinstance(payload, list):
        return summary, None
    gaps: list[dict[str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        obligation = entry.get("obligation")
        if not isinstance(obligation, str) or not obligation.strip():
            continue
        gaps.append(
            {
                "obligation": obligation.strip(),
                "document": str(entry.get("document") or ""),
                "note": str(entry.get("note") or ""),
            }
        )
    return summary, gaps
