"""Mid-run requests for a human answer.

Shell-command approval (:mod:`personal_agent_gateway.approval`) stays separate:
its payload is a command string with its own API and UI, and folding it into a
generic shape would only add risk. This module covers the two cases that had no
representation at all — asking for free text, and asking to pick from options.

The store is in place ahead of the route and runtime wiring that will call it.
"""

from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

InterventionKind = Literal["prompt", "select"]
InterventionStatus = Literal["pending", "answered", "cancelled"]


class UnknownInterventionError(KeyError):
    """Raised when an intervention id is not in the store."""


@dataclass(frozen=True)
class Intervention:
    id: str
    kind: InterventionKind
    status: InterventionStatus
    prompt: str
    options: tuple[str, ...] = ()
    multi: bool = False
    answers: tuple[str, ...] = ()


class InterventionStore:
    def __init__(self) -> None:
        self._items: dict[str, Intervention] = {}

    def create_prompt(self, prompt: str) -> Intervention:
        return self._add(
            Intervention(id=uuid4().hex, kind="prompt", status="pending", prompt=prompt)
        )

    def create_select(
        self,
        prompt: str,
        options: list[str],
        *,
        multi: bool = False,
    ) -> Intervention:
        if not options:
            raise ValueError("a select intervention needs at least one option")
        return self._add(
            Intervention(
                id=uuid4().hex,
                kind="select",
                status="pending",
                prompt=prompt,
                options=tuple(options),
                multi=multi,
            )
        )

    def get(self, intervention_id: str) -> Intervention:
        item = self._items.get(intervention_id)
        if item is None:
            raise UnknownInterventionError(intervention_id)
        return item

    def pending(self) -> list[Intervention]:
        return [item for item in self._items.values() if item.status == "pending"]

    def answer(self, intervention_id: str, answers: list[str]) -> Intervention:
        item = self.get(intervention_id)
        if item.status != "pending":
            raise ValueError(f"intervention {intervention_id} is already {item.status}")
        if not answers:
            raise ValueError("an answer needs at least one value")
        if item.kind == "select":
            if not item.multi and len(answers) > 1:
                raise ValueError("this intervention accepts a single value")
            unknown = [value for value in answers if value not in item.options]
            if unknown:
                raise ValueError(f"values outside the offered options: {unknown}")
        answered = replace(item, status="answered", answers=tuple(answers))
        self._items[intervention_id] = answered
        return answered

    def cancel(self, intervention_id: str) -> Intervention:
        item = self.get(intervention_id)
        if item.status != "pending":
            raise ValueError(f"intervention {intervention_id} is already {item.status}")
        cancelled = replace(item, status="cancelled")
        self._items[intervention_id] = cancelled
        return cancelled

    def _add(self, item: Intervention) -> Intervention:
        self._items[item.id] = item
        return item


async def publish_intervention(scope, intervention: Intervention) -> dict[str, object]:
    """Announce that a run is waiting on a human answer."""
    return await scope.publish(
        {
            "type": "intervention.requested",
            "intervention_id": intervention.id,
            "kind": intervention.kind,
            "prompt": intervention.prompt,
            "options": list(intervention.options),
            "multi": intervention.multi,
        }
    )
