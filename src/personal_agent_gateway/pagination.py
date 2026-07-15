import base64
import binascii
import json


def encode_cursor(*values: object) -> str:
    payload = json.dumps(list(values), separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str, size: int) -> list[object]:
    if not cursor or len(cursor) > 512:
        raise ValueError("Invalid cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc
    if not isinstance(values, list) or len(values) != size:
        raise ValueError("Invalid cursor")
    return values
