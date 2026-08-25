from personal_agent_gateway.team_plan_shape import plan_shape


def test_tasks_with_no_prerequisites_can_all_start_at_once():
    shape = plan_shape(["a", "b", "c"], {})

    assert shape.task_count == 3
    assert shape.ready_at_start == 3
    assert shape.longest_chain == 1


def test_a_chain_can_only_start_one_and_takes_as_many_rounds_as_it_is_long():
    """나눈 것이 동시 실행을 만들지 못하는 경우.

    네 사람에게 나눠도 줄줄이 기다리면 한 명씩 도는 것과 같다. 이 숫자가
    화면에 보이지 않으면, 계획을 보는 사람은 일감 넷을 넷이 동시에 한다고
    읽는다.
    """
    shape = plan_shape(
        ["a", "b", "c", "d"],
        {"b": ["a"], "c": ["b"], "d": ["c"]},
    )

    assert shape.task_count == 4
    assert shape.ready_at_start == 1
    assert shape.longest_chain == 4


def test_the_longest_chain_is_the_longest_one_not_the_average():
    # a -> b -> c 는 3단계, d 는 혼자. 평균을 내면 짧아 보이지만 이 계획이
    # 끝나는 데 걸리는 것은 가장 긴 쪽이다.
    shape = plan_shape(["a", "b", "c", "d"], {"b": ["a"], "c": ["b"]})

    assert shape.longest_chain == 3
    assert shape.ready_at_start == 2


def test_a_task_waiting_on_two_others_waits_for_the_slower_one():
    shape = plan_shape(
        ["a", "b", "c", "d"],
        {"b": ["a"], "c": ["b"], "d": ["a", "c"]},
    )

    assert shape.longest_chain == 4


def test_a_dependency_on_a_task_outside_the_plan_is_ignored():
    """지워졌거나 다른 사이클에 있는 선행. 그것 때문에 계산이 죽으면 안 된다.

    없는 선행은 기다릴 것이 없으므로 그 일감도 바로 시작할 수 있다 --
    실행이 준비된 일감을 고를 때(list_dependency_ready_tasks) 쓰는 판정과
    같다. 거기서도 team_tasks 에 없는 선행은 조인이 비어 준비된 것으로 본다.
    """
    shape = plan_shape(["a", "b"], {"b": ["gone"]})

    assert shape.task_count == 2
    assert shape.longest_chain == 1
    assert shape.ready_at_start == 2


def test_a_cycle_in_the_dependencies_does_not_hang():
    """계획에 순환이 생기면 계산이 멈추면 안 된다.

    서버가 순환을 거절하므로 정상 경로에는 없다. 그래도 여기서 무한히
    돌면 화면 하나가 런 전체를 못 열게 만든다.
    """
    shape = plan_shape(["a", "b"], {"a": ["b"], "b": ["a"]})

    assert shape.task_count == 2
    assert shape.ready_at_start == 0
    assert shape.longest_chain >= 1


def test_an_empty_plan_has_nothing_to_report():
    shape = plan_shape([], {})

    assert shape.task_count == 0
    assert shape.longest_chain == 0
    assert shape.ready_at_start == 0


def test_splitting_pays_off_only_when_the_chain_is_shorter_than_the_count():
    """이 판정이 이 기능의 요점이다.

    일감 수와 최장 사슬이 같으면, 나눈 것이 동시 실행을 하나도 만들지
    못했다는 뜻이다 -- 인수인계 비용만 치르고 속도는 그대로다.
    """
    serial = plan_shape(["a", "b", "c"], {"b": ["a"], "c": ["b"]})
    parallel = plan_shape(["a", "b", "c"], {})

    assert serial.longest_chain == serial.task_count
    assert parallel.longest_chain < parallel.task_count
