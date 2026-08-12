from personal_agent_gateway.team_coverage_report import extract_coverage_gaps


def test_a_valid_block_is_parsed_and_removed_from_the_summary():
    text = (
        "Built the admin backend and the study screen.\n\n"
        "```coverage-gaps\n"
        '[{"obligation": "T-04 discard a draft", '
        '"document": "docs/service-plan.md §4", "note": "no task owns this"}]\n'
        "```\n"
    )

    summary, gaps = extract_coverage_gaps(text)

    assert summary == "Built the admin backend and the study screen."
    assert gaps == [
        {
            "obligation": "T-04 discard a draft",
            "document": "docs/service-plan.md §4",
            "note": "no task owns this",
        }
    ]


def test_an_empty_list_means_the_leader_reported_no_gaps():
    """Distinct from not reporting at all: the UI says different things for
    'reported none' and 'did not report', because they mean different things."""
    summary, gaps = extract_coverage_gaps("Done.\n\n```coverage-gaps\n[]\n```\n")

    assert summary == "Done."
    assert gaps == []


def test_no_block_reports_nothing_and_leaves_the_summary_alone():
    summary, gaps = extract_coverage_gaps("Done.")

    assert summary == "Done."
    assert gaps is None


def test_malformed_json_is_treated_as_no_report_and_never_raises():
    """Synthesis is a leader stage, and a leader stage that cannot be parsed
    costs the cycle. Trading a run for a nice-to-have field is the wrong
    exchange, so a broken block degrades to 'not reported'."""
    text = "Done.\n\n```coverage-gaps\n[{oh no\n```\n"

    summary, gaps = extract_coverage_gaps(text)

    assert summary == "Done."
    assert gaps is None


def test_entries_that_are_not_objects_are_dropped_not_fatal():
    text = '```coverage-gaps\n["just a string", {"obligation": "T-09"}]\n```'

    _, gaps = extract_coverage_gaps(text)

    assert gaps == [{"obligation": "T-09", "document": "", "note": ""}]


def test_a_block_without_the_obligation_field_is_dropped():
    text = '```coverage-gaps\n[{"document": "d.md"}]\n```'

    _, gaps = extract_coverage_gaps(text)

    assert gaps == []
