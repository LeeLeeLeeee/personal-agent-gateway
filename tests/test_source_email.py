import email

from personal_agent_gateway.sources.email import (
    ImapEmailAdapter,
    normalize_email,
    passes_filter,
)


class FakeImapClient:
    def __init__(self, uidvalidity: int, messages: dict[int, bytes]) -> None:
        self._uidvalidity = uidvalidity
        self._messages = messages
        self.logged_out = False

    def login(self, username: str, password: str) -> None:
        self._user = username

    def select(self, folder: str) -> int:
        return self._uidvalidity

    def max_uid(self) -> int:
        return max(self._messages) if self._messages else 0

    def search_uids_after(self, uid: int) -> list[int]:
        return sorted(u for u in self._messages if u > uid)

    def fetch_rfc822(self, uid: int) -> bytes:
        return self._messages[uid]

    def logout(self) -> None:
        self.logged_out = True


def _raw(from_addr: str, subject: str, body: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Tue, 15 Jul 2026 09:00:00 +0000\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def _adapter(client: FakeImapClient) -> ImapEmailAdapter:
    return ImapEmailAdapter(client_factory=lambda host, port: client)


CONNECTION = {"host": "imap.test", "port": 993, "username": "me@test"}


def test_first_poll_sets_baseline_and_emits_nothing() -> None:
    client = FakeImapClient(uidvalidity=100, messages={5: _raw("a@b", "hi", "x")})
    result = _adapter(client).poll(CONNECTION, "pw", cursor=None, filter_config={})
    assert result.events == []
    assert result.cursor == {"uidvalidity": 100, "last_uid": 5}
    assert client.logged_out is True


def test_second_poll_emits_new_messages() -> None:
    client = FakeImapClient(
        uidvalidity=100,
        messages={5: _raw("a@b", "old", "x"), 6: _raw("boss@corp", "urgent", "help")},
    )
    result = _adapter(client).poll(
        CONNECTION, "pw", cursor={"uidvalidity": 100, "last_uid": 5}, filter_config={}
    )
    assert [e.dedup_key for e in result.events] == ["email:100:6"]
    assert result.events[0].payload["subject"] == "urgent"
    assert result.cursor == {"uidvalidity": 100, "last_uid": 6}


def test_uidvalidity_change_resets_baseline() -> None:
    client = FakeImapClient(uidvalidity=200, messages={5: _raw("a@b", "hi", "x")})
    result = _adapter(client).poll(
        CONNECTION, "pw", cursor={"uidvalidity": 100, "last_uid": 3}, filter_config={}
    )
    assert result.events == []
    assert result.cursor == {"uidvalidity": 200, "last_uid": 5}


def test_filter_excludes_nonmatching_but_advances_cursor() -> None:
    client = FakeImapClient(
        uidvalidity=100,
        messages={
            6: _raw("noise@spam", "promo", "x"),
            7: _raw("boss@corp", "urgent", "help"),
        },
    )
    result = _adapter(client).poll(
        CONNECTION,
        "pw",
        cursor={"uidvalidity": 100, "last_uid": 5},
        filter_config={"from_contains": "boss@corp"},
    )
    assert [e.dedup_key for e in result.events] == ["email:100:7"]
    assert result.cursor == {"uidvalidity": 100, "last_uid": 7}


def test_normalize_and_filter_pure_functions() -> None:
    message = email.message_from_bytes(_raw("Boss <boss@corp>", "Q3 Report", "please review"))
    normalized = normalize_email(message)
    assert normalized["from"] == "Boss <boss@corp>"
    assert normalized["subject"] == "Q3 Report"
    assert "please review" in normalized["body_text"]
    assert passes_filter(normalized, {"subject_contains": "Q3"}) is True
    assert passes_filter(normalized, {"subject_contains": "Q4"}) is False
