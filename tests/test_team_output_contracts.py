import pytest

from personal_agent_gateway.team_output_contracts import (
    LIBRARY_DRAFT_CONTRACT_ID,
    get_output_contract,
)

_VALID = (
    "Draft ready.\n\n"
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)

_WITH_PROSE = (
    "Here is the research methodology I recommend.\n\n"
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)

_NO_PROSE_WITH_SUMMARY = (
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)

_NO_PROSE_EMPTY_SUMMARY = (
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)


def test_unknown_and_empty_contract_ids_resolve_to_nothing() -> None:
    assert get_output_contract(None) is None
    assert get_output_contract("") is None
    assert get_output_contract("no-such-contract") is None


def test_library_draft_contract_carries_the_marker_instructions() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)

    assert contract is not None
    assert contract.id == LIBRARY_DRAFT_CONTRACT_ID
    assert "<library_draft>" in contract.instructions


def test_library_draft_contract_validates_the_final_response() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)
    assert contract is not None

    contract.validate(_VALID)

    with pytest.raises(ValueError):
        contract.validate("## 완료 요약\n\n초안을 파일로 정리했습니다.")


def test_library_draft_human_summary_returns_prose_when_present() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)
    assert contract is not None

    result = contract.human_summary(_WITH_PROSE)

    assert result == "Here is the research methodology I recommend."


def test_library_draft_human_summary_returns_summary_when_no_prose() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)
    assert contract is not None

    result = contract.human_summary(_NO_PROSE_WITH_SUMMARY)

    assert result == "A repeatable evidence check."


def test_library_draft_human_summary_returns_default_when_empty() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)
    assert contract is not None

    result = contract.human_summary(_NO_PROSE_EMPTY_SUMMARY)

    assert result == "(no summary)"
