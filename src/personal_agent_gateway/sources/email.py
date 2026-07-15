import email
import imaplib
from email.header import decode_header, make_header
from email.message import Message
from typing import Callable, Protocol

from personal_agent_gateway.sources.base import HookEvent, PollResult

_BODY_LIMIT = 8000


class ImapClientProtocol(Protocol):
    def login(self, username: str, password: str) -> None: ...
    def select(self, folder: str) -> int: ...
    def max_uid(self) -> int: ...
    def search_uids_after(self, uid: int) -> list[int]: ...
    def fetch_rfc822(self, uid: int) -> bytes: ...
    def logout(self) -> None: ...


class ImapEmailAdapter:
    def __init__(
        self,
        client_factory: Callable[[str, int], ImapClientProtocol] | None = None,
    ) -> None:
        self._client_factory = client_factory or _default_client_factory

    def poll(
        self,
        connection: dict[str, object],
        secret: str,
        cursor: dict[str, object] | None,
        filter_config: dict[str, object],
    ) -> PollResult:
        folder = str(filter_config.get("folder") or "INBOX")
        client = self._client_factory(str(connection["host"]), int(connection["port"]))
        try:
            client.login(str(connection["username"]), secret)
            uidvalidity = client.select(folder)
            last_uid = _cursor_last_uid(cursor, uidvalidity)
            if last_uid is None:
                return PollResult(
                    events=[],
                    cursor={"uidvalidity": uidvalidity, "last_uid": client.max_uid()},
                )
            events: list[HookEvent] = []
            highest = last_uid
            for uid in client.search_uids_after(last_uid):
                highest = max(highest, uid)
                message = email.message_from_bytes(client.fetch_rfc822(uid))
                normalized = normalize_email(message)
                if not passes_filter(normalized, filter_config):
                    continue
                events.append(
                    HookEvent(
                        dedup_key=f"email:{uidvalidity}:{uid}",
                        summary=f"메일: {normalized['subject']} — {normalized['from']}",
                        payload=normalized,
                    )
                )
            return PollResult(
                events=events,
                cursor={"uidvalidity": uidvalidity, "last_uid": highest},
            )
        finally:
            client.logout()

    def verify(self, connection: dict[str, object], secret: str, folder: str = "INBOX") -> None:
        client = self._client_factory(str(connection["host"]), int(connection["port"]))
        try:
            client.login(str(connection["username"]), secret)
            client.select(str(folder or "INBOX"))
        finally:
            client.logout()


def normalize_email(message: Message) -> dict[str, object]:
    return {
        "from": _header(message, "From"),
        "subject": _header(message, "Subject"),
        "date": _header(message, "Date"),
        "body_text": _body_text(message)[:_BODY_LIMIT],
    }


def passes_filter(normalized: dict[str, object], filter_config: dict[str, object]) -> bool:
    from_contains = filter_config.get("from_contains")
    subject_contains = filter_config.get("subject_contains")
    if from_contains and str(from_contains).lower() not in str(normalized["from"]).lower():
        return False
    if subject_contains and str(subject_contains).lower() not in str(normalized["subject"]).lower():
        return False
    return True


def _cursor_last_uid(cursor: dict[str, object] | None, uidvalidity: int) -> int | None:
    if cursor is None:
        return None
    if int(cursor.get("uidvalidity", -1)) != uidvalidity:
        return None
    return int(cursor["last_uid"])


def _header(message: Message, name: str) -> str:
    raw = message.get(name, "")
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _body_text(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return _decode_part(part)
        return ""
    return _decode_part(message)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _default_client_factory(host: str, port: int) -> ImapClientProtocol:
    return _ImapClient(host, port)


class _ImapClient:
    """imaplib 기반 실 IMAP 클라이언트. (서버 없이 단위 테스트하지 않음)"""

    def __init__(self, host: str, port: int) -> None:
        self._conn = imaplib.IMAP4_SSL(host, port, timeout=30)
        self._folder = "INBOX"

    def login(self, username: str, password: str) -> None:
        self._conn.login(username, password)

    def select(self, folder: str) -> int:
        self._folder = folder
        self._conn.select(folder, readonly=True)
        status, data = self._conn.status(folder, "(UIDVALIDITY)")
        text = data[0].decode() if data and data[0] else ""
        digits = "".join(ch for ch in text.split("UIDVALIDITY")[-1] if ch.isdigit())
        return int(digits or 0)

    def max_uid(self) -> int:
        uids = self._all_uids()
        return max(uids) if uids else 0

    def search_uids_after(self, uid: int) -> list[int]:
        status, data = self._conn.uid("search", None, f"UID {uid + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []
        found = [int(part) for part in data[0].split()]
        # IMAP 'uid+1:*' 은 마지막 메시지를 항상 포함하므로 실제 초과분만 남긴다.
        return sorted(u for u in found if u > uid)

    def fetch_rfc822(self, uid: int) -> bytes:
        status, data = self._conn.uid("fetch", str(uid), "(RFC822)")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return b""
        return data[0][1]

    def logout(self) -> None:
        try:
            self._conn.logout()
        except Exception:
            pass

    def _all_uids(self) -> list[int]:
        status, data = self._conn.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        return [int(part) for part in data[0].split()]
