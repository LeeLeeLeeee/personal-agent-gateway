from collections.abc import Callable
from dataclasses import dataclass

from personal_agent_gateway.archive import (
    library_draft_output_contract,
    parse_library_draft_response,
)

LIBRARY_DRAFT_CONTRACT_ID = "library_draft"


@dataclass(frozen=True)
class OutputContract:
    id: str
    instructions: str
    validate: Callable[[str], None]
    human_summary: Callable[[str], str]


def _validate_library_draft(content: str) -> None:
    parse_library_draft_response(content)


def _library_draft_human_summary(content: str) -> str:
    result_text, payload = parse_library_draft_response(content)
    prose = result_text.strip()
    if prose:
        return prose
    summary = payload.summary.strip()
    if summary:
        return summary
    return "(no summary)"


_CONTRACTS: dict[str, OutputContract] = {
    LIBRARY_DRAFT_CONTRACT_ID: OutputContract(
        id=LIBRARY_DRAFT_CONTRACT_ID,
        instructions=library_draft_output_contract(),
        validate=_validate_library_draft,
        human_summary=_library_draft_human_summary,
    ),
}


def get_output_contract(contract_id: str | None) -> OutputContract | None:
    if not contract_id:
        return None
    return _CONTRACTS.get(contract_id)
