from typing import get_args

from personal_agent_gateway.team_model_operations import OperationStage
from personal_agent_gateway.team_repair_stages import REPAIR_STAGE, repair_stage_for


def test_every_stage_has_a_repair_target() -> None:
    """A stage with no entry inherits no repair, which is exactly how
    acceptance_lead came to have none: repair was opt-in per stage."""
    stages = set(get_args(OperationStage))
    repairs = {stage for stage in stages if stage.endswith("_repair")}
    for stage in stages - repairs:
        assert stage in REPAIR_STAGE, f"{stage} has no repair target"


def test_repair_targets_are_real_stages() -> None:
    stages = set(get_args(OperationStage))
    for base, repair in REPAIR_STAGE.items():
        assert repair in stages, f"{base} maps to unknown stage {repair}"


def test_worker_execution_keeps_its_own_stage() -> None:
    """worker_execution repairs at ordinal 1 of its own stage. Renaming it
    would move it out of the workspace-baseline set in team_runtime.py:414-419
    and change how file changes are attributed."""
    assert repair_stage_for("worker_execution") == "worker_execution"


def test_add_work_repairs_through_the_planning_repair_stage() -> None:
    assert repair_stage_for("cycle_add_work") == "cycle_planning_repair"
