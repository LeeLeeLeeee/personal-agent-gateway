import pytest

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.interventions import (
    InterventionStore,
    UnknownInterventionError,
    publish_intervention,
)


def test_create_prompt_starts_pending_with_no_options() -> None:
    store = InterventionStore()

    intervention = store.create_prompt("어떤 브랜치에 올릴까요?")

    assert intervention.kind == "prompt"
    assert intervention.status == "pending"
    assert intervention.prompt == "어떤 브랜치에 올릴까요?"
    assert intervention.options == ()
    assert intervention.answers == ()


def test_create_select_keeps_options_in_order() -> None:
    store = InterventionStore()

    intervention = store.create_select("어디에 배포할까요?", ["staging", "production"])

    assert intervention.kind == "select"
    assert intervention.options == ("staging", "production")
    assert intervention.multi is False


def test_create_select_rejects_empty_options() -> None:
    store = InterventionStore()

    with pytest.raises(ValueError):
        store.create_select("고르세요", [])


def test_answer_prompt_records_free_text() -> None:
    store = InterventionStore()
    created = store.create_prompt("브랜치?")

    answered = store.answer(created.id, ["feature/x"])

    assert answered.status == "answered"
    assert answered.answers == ("feature/x",)
    assert store.get(created.id) == answered


def test_answer_select_rejects_values_outside_options() -> None:
    store = InterventionStore()
    created = store.create_select("어디에?", ["staging"])

    with pytest.raises(ValueError):
        store.answer(created.id, ["production"])


def test_answer_single_select_rejects_multiple_values() -> None:
    store = InterventionStore()
    created = store.create_select("어디에?", ["a", "b"])

    with pytest.raises(ValueError):
        store.answer(created.id, ["a", "b"])


def test_answer_multi_select_accepts_several_values() -> None:
    store = InterventionStore()
    created = store.create_select("무엇을?", ["a", "b", "c"], multi=True)

    answered = store.answer(created.id, ["a", "c"])

    assert answered.answers == ("a", "c")


def test_answering_twice_is_rejected() -> None:
    store = InterventionStore()
    created = store.create_prompt("브랜치?")
    store.answer(created.id, ["main"])

    with pytest.raises(ValueError):
        store.answer(created.id, ["other"])


def test_answer_unknown_id_raises() -> None:
    with pytest.raises(UnknownInterventionError):
        InterventionStore().answer("nope", ["x"])


def test_cancel_marks_cancelled_and_blocks_answers() -> None:
    store = InterventionStore()
    created = store.create_prompt("브랜치?")

    cancelled = store.cancel(created.id)

    assert cancelled.status == "cancelled"
    with pytest.raises(ValueError):
        store.answer(created.id, ["main"])


def test_cancelling_an_answered_intervention_is_rejected() -> None:
    store = InterventionStore()
    created = store.create_prompt("브랜치?")
    answered = store.answer(created.id, ["main"])

    with pytest.raises(ValueError):
        store.cancel(created.id)

    assert store.get(created.id) == answered
    assert store.get(created.id).status == "answered"
    assert store.get(created.id).answers == ("main",)


def test_cancelling_twice_is_rejected() -> None:
    store = InterventionStore()
    created = store.create_prompt("브랜치?")
    store.cancel(created.id)

    with pytest.raises(ValueError):
        store.cancel(created.id)


def test_pending_lists_only_open_interventions() -> None:
    store = InterventionStore()
    open_one = store.create_prompt("첫 번째")
    answered = store.create_prompt("두 번째")
    store.answer(answered.id, ["x"])

    assert [item.id for item in store.pending()] == [open_one.id]


@pytest.mark.asyncio
async def test_publish_intervention_emits_a_scoped_request_event() -> None:
    bus = EventBus()
    scope = bus.scope("op-1")
    intervention = InterventionStore().create_select("어디에?", ["staging"])

    published = await publish_intervention(scope, intervention)

    assert published["type"] == "intervention.requested"
    assert published["operation_id"] == "op-1"
    assert published["intervention_id"] == intervention.id
    assert published["kind"] == "select"
    assert published["options"] == ["staging"]
