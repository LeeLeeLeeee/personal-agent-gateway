"""계획을 나눈 것이 실제로 동시 실행을 만드는지 재는 순수 함수.

실측에서 나온 문제가 이것이다. 팀원 아홉 명짜리 런이 일감을 아홉~열 개로
나누는데, 그중 예닐곱 개가 앞 일감을 기다린다. 네 개짜리 계획은 넷이 완전히
줄을 선 경우도 있었다 -- 넷에게 나눠놓고 한 명씩 도는 것과 같다.

나누는 값은 동시 실행이다. 그것이 안 나오면 인수인계 비용만 치른다: 일감마다
모델 호출과 수용 판정이 한 번씩 더 붙는데 끝나는 시각은 그대로다.

모델에게 묻지 않는다. 일감 목록과 선후 관계만 있으면 계산으로 나온다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanShape:
    task_count: int
    #: 이 계획이 끝나기까지 반드시 차례로 지나야 하는 일감의 수. 이 값이
    #: task_count 와 같으면 나눈 것이 동시 실행을 하나도 만들지 못했다.
    longest_chain: int
    #: 선행이 없어 처음부터 시작할 수 있는 일감의 수. 동시 실행 상한
    #: (MAX_CONCURRENT_WORKERS) 과 비교해서 읽는다.
    ready_at_start: int


def plan_shape(
    task_ids: Iterable[str],
    dependencies: Mapping[str, Iterable[str]],
) -> PlanShape:
    """일감과 선후 관계에서 계획의 모양을 잰다.

    ``dependencies`` 는 ``TeamRunService.list_task_dependency_map`` 이 주는
    ``{일감: [선행들]}`` 모양이다.
    """
    ids = list(task_ids)
    known = set(ids)
    # 계획 밖을 가리키는 선행은 없는 것으로 본다. 지워졌거나 다른 사이클에
    # 있는 일감이 화면 하나를 못 열게 만들면 안 된다.
    prerequisites = {
        task_id: [dep for dep in deps if dep in known]
        for task_id, deps in dependencies.items()
        if task_id in known
    }

    depth_by_task: dict[str, int] = {}

    def depth(task_id: str, walking: frozenset[str]) -> int:
        cached = depth_by_task.get(task_id)
        if cached is not None:
            return cached
        # 순환은 서버가 계획 단계에서 거절하므로 정상 경로에는 없다. 그래도
        # 여기서 막지 않으면 잘못된 계획 하나가 무한 재귀로 화면을 죽인다.
        if task_id in walking:
            return 0
        deeper = walking | {task_id}
        result = 1 + max(
            (depth(dep, deeper) for dep in prerequisites.get(task_id, ())),
            default=0,
        )
        # 순환을 지나온 계산은 캐시하지 않는다. 어디서 들어왔느냐에 따라
        # 답이 달라지므로, 그 값을 남기면 다른 경로가 그것을 물려받는다.
        if not (walking & set(prerequisites.get(task_id, ()))):
            depth_by_task[task_id] = result
        return result

    longest_chain = max((depth(task_id, frozenset()) for task_id in ids), default=0)
    ready_at_start = sum(1 for task_id in ids if not prerequisites.get(task_id))
    return PlanShape(
        task_count=len(ids),
        longest_chain=longest_chain,
        ready_at_start=ready_at_start,
    )
