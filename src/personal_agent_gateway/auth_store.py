import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

import pyotp


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    otpauth_uri: str


@dataclass(frozen=True)
class TotpSetupResult:
    enabled: bool
    recovery_codes: list[str]


class AuthStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._totp_path = root / "totp.json"
        self._recovery_codes_path = root / "recovery_codes.json"

    def start_totp_setup(self, account_name: str) -> TotpSetup:
        secret = pyotp.random_base32()
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=account_name,
            issuer_name="Personal Agent Gateway",
        )
        self._write_json(
            self._totp_path,
            {
                "enabled": False,
                "pending_secret": secret,
                "account_name": account_name,
            },
        )
        return TotpSetup(secret=secret, otpauth_uri=uri)

    def verify_totp_setup(self, code: str) -> TotpSetupResult:
        state = self._read_json(self._totp_path)
        pending_secret = state.get("pending_secret")
        if not isinstance(pending_secret, str):
            return TotpSetupResult(enabled=False, recovery_codes=[])
        if not pyotp.TOTP(pending_secret).verify(code, valid_window=1):
            return TotpSetupResult(enabled=False, recovery_codes=[])

        self._write_json(
            self._totp_path,
            {
                "enabled": True,
                "secret": pending_secret,
                "account_name": state.get("account_name", "local-owner"),
            },
        )
        recovery_codes = self.generate_recovery_codes()
        return TotpSetupResult(enabled=True, recovery_codes=recovery_codes)

    def verify_login_code(self, code: str) -> bool:
        state = self._read_json(self._totp_path)
        if state.get("enabled") is not True:
            return False
        secret = state.get("secret")
        if not isinstance(secret, str):
            return False
        return pyotp.TOTP(secret).verify(code, valid_window=1)

    def generate_recovery_codes(self) -> list[str]:
        codes = [secrets.token_urlsafe(8) for _ in range(10)]
        self._write_json(
            self._recovery_codes_path,
            {"hashes": [_hash_code(code) for code in codes]},
        )
        return codes

    def use_recovery_code(self, code: str) -> bool:
        payload = self._read_json(self._recovery_codes_path)
        hashes = payload.get("hashes", [])
        if not isinstance(hashes, list):
            return False
        code_hash = _hash_code(code)
        if code_hash not in hashes:
            return False
        self._write_json(
            self._recovery_codes_path,
            {"hashes": [value for value in hashes if value != code_hash]},
        )
        return True

    def is_totp_enabled(self) -> bool:
        return self._read_json(self._totp_path).get("enabled") is True

    def current_code_for_test(self, secret: str) -> str:
        return pyotp.TOTP(secret).now()

    def current_login_code_for_test(self) -> str:
        secret = self._read_json(self._totp_path).get("secret")
        if not isinstance(secret, str):
            raise RuntimeError("TOTP is not enabled")
        return self.current_code_for_test(secret)

    def _read_json(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items()}

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()
