from personal_agent_gateway.team_note_report import extract_team_note


def test_no_block_means_the_lead_had_nothing_to_record():
    """이번 사이클에 새로 알아낸 것이 없는 경우다. 정상이고, 요약은 그대로 간다."""
    summary, note = extract_team_note("세 일감을 마쳤습니다.")

    assert summary == "세 일감을 마쳤습니다."
    assert note is None


def test_a_note_is_lifted_out_of_the_summary():
    text = (
        "두 일감을 마쳤습니다.\n"
        "```team-note\n"
        '{"title":"저장소 지도","summary":"어디에 뭐가 있나",'
        '"content_markdown":"팀런 상태는 teams.py 가 쓴다","tags":["구조"]}\n'
        "```"
    )

    summary, note = extract_team_note(text)

    assert summary == "두 일감을 마쳤습니다."
    assert note.title == "저장소 지도"
    assert note.content_markdown == "팀런 상태는 teams.py 가 쓴다"
    assert note.tags == ["구조"]


def test_broken_json_costs_the_note_and_nothing_else():
    """합성은 리드 단계라 여기서 던지면 사이클이 죽는다. 선택인 것이 필수인
    것을 죽여서는 안 된다."""
    text = "요약입니다.\n```team-note\n{이건 JSON 이 아니다\n```"

    summary, note = extract_team_note(text)

    assert summary == "요약입니다."
    assert note is None


def test_a_note_without_a_body_is_not_a_note():
    """빈 노트를 저장하면 지난 개정을 빈 것으로 덮어쓴다. 안 쓴 것보다 나쁘다."""
    text = '요약.\n```team-note\n{"title":"제목만 있다","content_markdown":"  "}\n```'

    _summary, note = extract_team_note(text)

    assert note is None


def test_a_note_and_a_coverage_gaps_block_do_not_collide():
    """둘 다 선택이고 둘 다 펜스 블록이다. 한쪽이 다른 쪽을 먹으면 안 된다."""
    from personal_agent_gateway.team_coverage_report import extract_coverage_gaps

    text = (
        "요약입니다.\n"
        "```team-note\n"
        '{"title":"노트","content_markdown":"본문"}\n'
        "```\n"
        "```coverage-gaps\n"
        '[{"obligation":"로그인","document":"spec.md §2","note":"주인 없음"}]\n'
        "```"
    )

    without_note, note = extract_team_note(text)
    summary, gaps = extract_coverage_gaps(without_note)

    assert note.title == "노트"
    assert summary == "요약입니다."
    assert gaps[0]["obligation"] == "로그인"


def test_a_non_string_response_does_not_raise():
    assert extract_team_note(None) == ("", None)
