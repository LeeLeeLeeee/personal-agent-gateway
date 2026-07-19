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


class FakePop3Client:
    def __init__(self, messages: list[tuple[str, bytes]]) -> None:
        self._messages = messages
        self.logged_out = False

    def login(self, username: str, password: str) -> None:
        self._user = username

    def list_uids(self) -> list[tuple[int, str]]:
        return [(index, uid) for index, (uid, _) in enumerate(self._messages, start=1)]

    def fetch_rfc822(self, message_number: int) -> bytes:
        return self._messages[message_number - 1][1]

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


def _pop3_adapter(client: FakePop3Client) -> ImapEmailAdapter:
    return ImapEmailAdapter(pop_client_factory=lambda host, port: client)


CONNECTION = {"host": "imap.test", "port": 993, "username": "me@test"}
POP3_CONNECTION = {"host": "pop.test", "port": 995, "username": "me@test"}


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


def test_pop3_first_poll_sets_baseline_and_emits_nothing() -> None:
    client = FakePop3Client([("uid-1", _raw("a@b", "old", "x"))])
    result = _pop3_adapter(client).poll(POP3_CONNECTION, "pw", cursor=None, filter_config={})
    assert result.events == []
    assert result.cursor == {"protocol": "pop3", "last_uid": "uid-1"}
    assert client.logged_out is True


def test_pop3_second_poll_emits_only_messages_after_cursor() -> None:
    client = FakePop3Client(
        [
            ("uid-1", _raw("a@b", "old", "x")),
            ("uid-2", _raw("boss@corp", "urgent", "help")),
        ]
    )
    result = _pop3_adapter(client).poll(
        POP3_CONNECTION,
        "pw",
        cursor={"protocol": "pop3", "last_uid": "uid-1"},
        filter_config={},
    )
    assert [event.dedup_key for event in result.events] == ["email:pop3:uid-2"]
    assert result.events[0].payload["subject"] == "urgent"
    assert result.cursor == {"protocol": "pop3", "last_uid": "uid-2"}


def test_pop3_missing_cursor_uid_resets_baseline_without_replay() -> None:
    client = FakePop3Client([("uid-2", _raw("a@b", "current", "x"))])
    result = _pop3_adapter(client).poll(
        POP3_CONNECTION,
        "pw",
        cursor={"protocol": "pop3", "last_uid": "deleted-uid"},
        filter_config={},
    )
    assert result.events == []
    assert result.cursor == {"protocol": "pop3", "last_uid": "uid-2"}


def test_pop3_emits_first_message_after_empty_mailbox_baseline() -> None:
    client = FakePop3Client([("uid-1", _raw("a@b", "first", "x"))])
    result = _pop3_adapter(client).poll(
        POP3_CONNECTION,
        "pw",
        cursor={"protocol": "pop3", "last_uid": ""},
        filter_config={},
    )
    assert [event.dedup_key for event in result.events] == ["email:pop3:uid-1"]


def test_pop3_verify_uses_pop_client() -> None:
    client = FakePop3Client([])
    _pop3_adapter(client).verify(POP3_CONNECTION, "pw")
    assert client.logged_out is True


def test_normalize_and_filter_pure_functions() -> None:
    message = email.message_from_bytes(_raw("Boss <boss@corp>", "Q3 Report", "please review"))
    normalized = normalize_email(message)
    assert normalized["from"] == "Boss <boss@corp>"
    assert normalized["subject"] == "Q3 Report"
    assert "please review" in normalized["body_text"]
    assert passes_filter(normalized, {"subject_contains": "Q3"}) is True
    assert passes_filter(normalized, {"subject_contains": "Q4"}) is False
