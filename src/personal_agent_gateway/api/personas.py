from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

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


class PersonaUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    responsibilities: list[str] | None = None
    constraints: list[str] | None = None
    default_backend: str | None = None
    default_model: str | None = None


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_personas(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    return {"personas": [_persona_payload(persona) for persona in request.app.state.persona_service.list_personas()]}


@router.post("")
def create_persona(request: Request, payload: PersonaRequest, _session: None = session_dependency) -> dict[str, object]:
    persona = request.app.state.persona_service.create_persona(**payload.model_dump())
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
        persona = request.app.state.persona_service.update_persona(
            persona_id,
            **{k: v for k, v in payload.model_dump().items() if v is not None},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
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
        "created_at": persona.created_at,
        "updated_at": persona.updated_at,
    }
