import json
from pathlib import Path


class HookSecretStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, connection_ref: str, secret: str) -> None:
        path = self._path(connection_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"secret": secret}), encoding="utf-8")

    def load(self, connection_ref: str) -> str | None:
        path = self._path(connection_ref)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        secret = payload.get("secret")
        return secret if isinstance(secret, str) else None

    def delete(self, connection_ref: str) -> None:
        self._path(connection_ref).unlink(missing_ok=True)

    def _path(self, connection_ref: str) -> Path:
        return self.root / f"{connection_ref}.json"
