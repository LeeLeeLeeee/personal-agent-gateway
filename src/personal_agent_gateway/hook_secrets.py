import json
from pathlib import Path

from personal_agent_gateway.private_json import write_private_json


class HookSecretStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, connection_ref: str, secret: str) -> None:
        path = self._path(connection_ref)
        write_private_json(path, {"secret": secret})

    def load(self, connection_ref: str) -> str | None:
        path = self._path(connection_ref)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        secret = payload.get("secret")
        return secret if isinstance(secret, str) else None

    def delete(self, connection_ref: str) -> None:
        self._path(connection_ref).unlink(missing_ok=True)

    def _path(self, connection_ref: str) -> Path:
        return self.root / f"{connection_ref}.json"
