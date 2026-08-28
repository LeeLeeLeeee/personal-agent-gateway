from personal_agent_gateway.team_next_cycle_report import extract_next_cycle


def test_no_block_means_the_lead_had_nothing_to_propose():
    """더 할 일이 없다는 신호다. 시리즈는 여기서 끝난다."""
    summary, instruction = extract_next_cycle("세 일감을 마쳤습니다.")

    assert summary == "세 일감을 마쳤습니다."
    assert instruction is None


def test_the_proposal_is_lifted_out_of_the_summary():
    text = (
        "두 일감을 마쳤습니다.\n"
        "```next-cycle\n"
        '{"instruction":"6문장을 다시 돌려 실제 게시 수를 재라"}\n'
        "```"
    )

    summary, instruction = extract_next_cycle(text)

    assert summary == "두 일감을 마쳤습니다."
    assert instruction == "6문장을 다시 돌려 실제 게시 수를 재라"


def test_broken_json_costs_the_proposal_and_nothing_else():
    """합성은 리드 단계라 여기서 던지면 사이클이 죽는다."""
    text = "요약입니다.\n```next-cycle\n{이건 JSON 이 아니다\n```"

    summary, instruction = extract_next_cycle(text)

    assert summary == "요약입니다."
    assert instruction is None


def test_an_empty_instruction_is_not_a_proposal():
    """빈 지시로 사이클을 열면 팀이 무엇을 하라는지 모른 채 시작한다."""
    text = '요약.\n```next-cycle\n{"instruction":"   "}\n```'

    _summary, instruction = extract_next_cycle(text)

    assert instruction is None


def test_a_proposal_does_not_collide_with_the_other_blocks():
    """세 블록 모두 선택이고 모두 펜스다. 한쪽이 다른 쪽을 먹으면 안 된다."""
    from personal_agent_gateway.team_coverage_report import extract_coverage_gaps
    from personal_agent_gateway.team_note_report import extract_team_note

    text = (
        "요약입니다.\n"
        "```next-cycle\n"
        '{"instruction":"다음 일"}\n'
        "```\n"
        "```team-note\n"
        '{"title":"노트","content_markdown":"본문"}\n'
        "```\n"
        "```coverage-gaps\n"
        '[{"obligation":"로그인","document":"spec.md §2","note":"주인 없음"}]\n'
        "```"
    )

    without_next, instruction = extract_next_cycle(text)
    without_note, note = extract_team_note(without_next)
    summary, gaps = extract_coverage_gaps(without_note)

    assert instruction == "다음 일"
    assert note.title == "노트"
    assert gaps[0]["obligation"] == "로그인"
    assert summary == "요약입니다."


def test_a_non_string_response_does_not_raise():
    assert extract_next_cycle(None) == ("", None)
