"""Which stage repairs which.

The names are declared rather than derived. OperationStage is a closed Literal,
so f"{stage}_repair" cannot type-check, and a derived name would also hide the
one stage that deliberately repairs in place.
"""

from personal_agent_gateway.team_model_operations import OperationStage

REPAIR_STAGE: dict[OperationStage, OperationStage] = {
    "cycle_planning": "cycle_planning_repair",
    # add-work replans through the same repair stage, at ordinal 2
    # (team_cycle_dispatcher.py:343-347 relies on that pairing).
    "cycle_add_work": "cycle_planning_repair",
    # worker_execution repairs at ordinal 1 of itself. It is the only stage in
    # the workspace-baseline set (team_runtime.py:414-419) that has a repair, and
    # a separate stage name would silently move it to the other baseline policy.
    "worker_execution": "worker_execution",
    "mediation_lead": "mediation_lead_repair",
    "mediation_worker": "mediation_worker_repair",
    "acceptance_lead": "acceptance_lead_repair",
    "acceptance_worker": "acceptance_worker_repair",
    "cycle_synthesis": "cycle_synthesis_repair",
    "cycle_contest": "cycle_contest_repair",
}


def repair_stage_for(stage: OperationStage) -> OperationStage:
    return REPAIR_STAGE[stage]
