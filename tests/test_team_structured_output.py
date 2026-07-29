import pytest

from personal_agent_gateway.team_structured_output import (
    normalize_json_envelope,
)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('  {"ok": true}  ', '{"ok": true}'),
        (" \n```json\n{\"ok\": true}\n```\n ", '{"ok": true}'),
        ("```json\r\n{\"ok\": true}\r\n```", '{"ok": true}'),
        (
            '```json\n{"note": "inline ``` marker"}\n```',
            '{"note": "inline ``` marker"}',
        ),
    ],
)
def test_normalize_json_envelope_accepts_raw_or_one_outer_json_fence(
    content: str,
    expected: str,
) -> None:
    assert normalize_json_envelope(content) == expected


@pytest.mark.parametrize(
    "content",
    [
        'before\n```json\n{"ok": true}\n```',
        '```json\n{"ok": true}\n```\nafter',
        '```json\n{"ok": true}\n```\n```json\n{"other": true}\n```',
        '```json\n{"ok": true}',
        '```JSON\n{"ok": true}\n```',
        '```\n{"ok": true}\n```',
    ],
)
def test_normalize_json_envelope_leaves_ambiguous_fences_invalid(
    content: str,
) -> None:
    assert normalize_json_envelope(content) == content.strip()
