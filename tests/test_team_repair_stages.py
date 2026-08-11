from typing import get_args

from personal_agent_gateway.team_model_operations import OperationStage
from personal_agent_gateway.team_model_effects import (
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_model_operations import (
    _built_in_result_validators,
)
from personal_agent_gateway.team_provider_recovery import (
    _LEAD_STAGES,
    _WORKER_STAGES,
)
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


def test_every_repair_stage_validates_the_same_kinds_as_its_base() -> None:
    """A stage missing from this registry makes _result_serialization raise
    OperationResultValidationError, which the invoker converts to
    invalid_structured_output -- on a valid response."""
    # The service merges the built-ins with the effects registry
    # (team_model_operations.py:129-131), and the merged mapping is what
    # _result_serialization consults. Checking either half alone reports a gap
    # that does not exist.
    validators = dict(_built_in_result_validators())
    for stage, kinds in team_model_effect_result_validators().items():
        validators.setdefault(stage, {}).update(kinds)
    for base, repair in REPAIR_STAGE.items():
        if base == repair:
            continue
        assert repair in validators, f"{repair} has no result validators"
        assert set(validators[repair]) >= set(validators[base]), (
            f"{repair} accepts fewer result kinds than {base}"
        )


def test_every_stage_is_grouped_for_provider_recovery() -> None:
    """A stage in neither group silently skips the provider-wait source-state
    validation -- no error, weaker invariant."""
    stages = set(get_args(OperationStage))
    cycle_stages = {stage for stage in stages if stage.startswith("cycle_")}
    for stage in stages - cycle_stages:
        assert stage in _WORKER_STAGES or stage in _LEAD_STAGES, (
            f"{stage} belongs to neither worker nor lead group"
        )
