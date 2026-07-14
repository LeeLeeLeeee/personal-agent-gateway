from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.personas import Persona


router = APIRouter(prefix="/api/personas", tags=["personas"])


class PersonaRequest(BaseModel):
    name: str
    role: str
    description: str
    responsibilities: list[str] = []
    constraints: list[str] = []
    default_backend: str = "codex"
    default_model: str = "default"
    default_options: dict[str, object] = Field(default_factory=dict)
    avatar: str = ""


class PersonaUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    responsibilities: list[str] | None = None
    constraints: list[str] | None = None
    default_backend: str | None = None
    default_model: str | None = None
    default_options: dict[str, object] | None = None
    avatar: str | None = None


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_personas(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    return {"personas": [_persona_payload(persona) for persona in request.app.state.persona_service.list_personas()]}


@router.post("")
def create_persona(request: Request, payload: PersonaRequest, _session: None = session_dependency) -> dict[str, object]:
    try:
        validated = request.app.state.agent_registry.validate_config(
            payload.default_backend,
            payload.default_model,
            payload.default_options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    persona = request.app.state.persona_service.create_persona(
        **payload.model_dump(exclude={"default_backend", "default_model", "default_options"}),
        default_backend=validated["agent_id"],
        default_model=validated["model"],
        default_options=validated["options"],
    )
    return {"persona": _persona_payload(persona)}


@router.get("/{persona_id}")
def get_persona(request: Request, persona_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        persona = request.app.state.persona_service.get_persona(persona_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    return {"persona": _persona_payload(persona)}


@router.patch("/{persona_id}")
def update_persona(request: Request, persona_id: str, payload: PersonaUpdateRequest, _session: None = session_dependency) -> dict[str, object]:
    try:
        current = request.app.state.persona_service.get_persona(persona_id)
        requested = payload.model_dump()
        next_backend = payload.default_backend or current.default_backend
        next_model = payload.default_model or current.default_model
        next_options = (
            payload.default_options
            if payload.default_options is not None
            else current.default_options
        )
        config_changed = (
            next_backend != current.default_backend
            or next_model != current.default_model
            or next_options != current.default_options
        )
        if config_changed:
            validated = request.app.state.agent_registry.validate_config(
                next_backend,
                next_model,
                next_options,
            )
            requested.update(
                default_backend=validated["agent_id"],
                default_model=validated["model"],
                default_options=validated["options"],
            )
        persona = request.app.state.persona_service.update_persona(
            persona_id,
            **{key: value for key, value in requested.items() if value is not None},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"persona": _persona_payload(persona)}


@router.delete("/{persona_id}")
def delete_persona(request: Request, persona_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        request.app.state.persona_service.delete_persona(persona_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    return {"deleted": True}


def _persona_payload(persona: Persona) -> dict[str, object]:
    return {
        "id": persona.id,
        "name": persona.name,
        "role": persona.role,
        "description": persona.description,
        "responsibilities": persona.responsibilities,
        "constraints": persona.constraints,
        "default_backend": persona.default_backend,
        "default_model": persona.default_model,
        "default_options": persona.default_options,
        "avatar": persona.avatar,
        "created_at": persona.created_at,
        "updated_at": persona.updated_at,
    }
