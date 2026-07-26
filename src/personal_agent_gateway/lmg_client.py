from urllib.parse import quote

import httpx

from personal_agent_gateway.config import AppConfig


def _lmg_headers(config: AppConfig) -> dict[str, str]:
    if config.lmg_local_token is None:
        return {}
    return {"Authorization": f"Bearer {config.lmg_local_token}"}


def fetch_capabilities(config, *, transport: httpx.BaseTransport | None = None) -> dict | None:
    """Fetch /v1/models from the local-model-gateway and return the capability
    envelope ({schema_version, providers}) the AgentRegistry expects, or None
    on any error (so the registry falls back to hardcoded defaults)."""
    url = f"{config.lmg_base_url.rstrip('/')}/v1/models"
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            response = client.get(url, headers=_lmg_headers(config))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    if not isinstance(payload.get("providers"), dict):
        return None
    return payload


def fetch_sessions(config, *, transport: httpx.BaseTransport | None = None) -> list:
    """Fetch /v1/sessions from the local-model-gateway. Returns the list, or
    [] on any error (so the dashboard never breaks when LMG is down)."""
    url = f"{config.lmg_base_url.rstrip('/')}/v1/sessions"
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            response = client.get(url, headers=_lmg_headers(config))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def delete_session(
    config,
    upstream_session_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    encoded_session_id = quote(upstream_session_id, safe="")
    url = f"{config.lmg_base_url.rstrip('/')}/v1/sessions/{encoded_session_id}"
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            response = client.delete(url, headers=_lmg_headers(config))
        response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        return False
    return True
