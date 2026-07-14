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
        default_options={"effort": "high", "sandbox": "workspace-write"},
    )

    assert persona.id
    assert persona.name == "Frontend Designer"
    assert persona.responsibilities == ["Review layout", "Check responsive behavior"]
    assert persona.default_options == {"effort": "high", "sandbox": "workspace-write"}
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


def test_create_persona_stores_avatar(tmp_path):
    service = make_service(tmp_path)

    persona = service.create_persona(
        name="Designer",
        role="UI",
        description="Reviews UI.",
        responsibilities=[],
        constraints=[],
        avatar="designer-beret",
    )

    assert persona.avatar == "designer-beret"
    assert service.list_personas()[0].avatar == "designer-beret"


def test_create_persona_defaults_avatar_to_empty(tmp_path):
    service = make_service(tmp_path)

    persona = service.create_persona(
        name="X",
        role="r",
        description="d",
        responsibilities=[],
        constraints=[],
    )

    assert persona.avatar == ""


def test_update_persona_sets_and_preserves_avatar(tmp_path):
    service = make_service(tmp_path)
    persona = service.create_persona(
        name="X",
        role="r",
        description="d",
        responsibilities=[],
        constraints=[],
        avatar="fox",
    )

    updated = service.update_persona(persona.id, avatar="wolf")
    assert updated.avatar == "wolf"

    again = service.update_persona(persona.id, name="Y")
    assert again.name == "Y"
    assert again.avatar == "wolf"


def test_update_persona_sets_and_preserves_default_options(tmp_path):
    service = make_service(tmp_path)
    persona = service.create_persona(
        name="X",
        role="r",
        description="d",
        responsibilities=[],
        constraints=[],
        default_options={"effort": "high"},
    )

    updated = service.update_persona(
        persona.id,
        default_options={"effort": "xhigh", "approval_policy": "never"},
    )
    assert updated.default_options == {"approval_policy": "never", "effort": "xhigh"}

    again = service.update_persona(persona.id, name="Y")
    assert again.default_options == updated.default_options
