from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService


def make_service(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    return PersonaService(db)


def test_create_and_list_personas(tmp_path):
    service = make_service(tmp_path)

    persona = service.create_persona(
        name="Frontend Designer",
        role="UI/UX review",
        description="Reviews interface clarity and layout.",
        responsibilities=["Review layout", "Check responsive behavior"],
        constraints=["Do not change backend APIs unless assigned"],
        default_backend="codex",
        default_model="default",
    )

    assert persona.id
    assert persona.name == "Frontend Designer"
    assert persona.responsibilities == ["Review layout", "Check responsive behavior"]
    assert [item.name for item in service.list_personas()] == ["Frontend Designer"]


def test_update_persona_does_not_change_id_or_created_at(tmp_path):
    service = make_service(tmp_path)
    persona = service.create_persona(
        name="QA Tester",
        role="Quality review",
        description="Finds regression risk.",
        responsibilities=["Run tests"],
        constraints=["Report evidence"],
    )

    updated = service.update_persona(
        persona.id,
        name="Strict QA Tester",
        constraints=["Report evidence", "Do not modify product code"],
    )

    assert updated.id == persona.id
    assert updated.created_at == persona.created_at
    assert updated.name == "Strict QA Tester"
    assert updated.constraints == ["Report evidence", "Do not modify product code"]
