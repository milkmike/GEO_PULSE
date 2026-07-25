import asyncio
import importlib
import sys
import types

import pytest


def _install_telethon_stub_if_needed():
    """Keep unit tests focused on local control flow when Telethon is absent."""
    try:
        import telethon  # noqa: F401
    except ModuleNotFoundError:
        telethon = types.ModuleType("telethon")

        class TelegramClient:  # pragma: no cover - only used without dependency
            pass

        class _Events:
            @staticmethod
            def NewMessage(*args, **kwargs):
                return object()

        telethon.TelegramClient = TelegramClient
        telethon.events = _Events()
        tl = types.ModuleType("telethon.tl")
        tl_types = types.ModuleType("telethon.tl.types")

        class Channel:
            pass

        class MessageMediaWebPage:
            pass

        tl_types.Channel = Channel
        tl_types.MessageMediaWebPage = MessageMediaWebPage
        sys.modules.update({
            "telethon": telethon,
            "telethon.tl": tl,
            "telethon.tl.types": tl_types,
        })


_install_telethon_stub_if_needed()
telegram = importlib.import_module("src.collectors.telegram")


def test_parse_authenticated_socks5_proxy():
    proxy = telegram.parse_telegram_proxy("socks5://alice:secret@proxy.example:1080")

    assert proxy == {
        "proxy_type": "socks5",
        "addr": "proxy.example",
        "port": 1080,
        "username": "alice",
        "password": "secret",
        "rdns": True,
    }


@pytest.mark.parametrize("value", ["https://proxy.example:443", "socks5://proxy.example"])
def test_parse_telegram_proxy_rejects_unsupported_or_incomplete_urls(value):
    with pytest.raises(ValueError):
        telegram.parse_telegram_proxy(value)


def test_proxy_log_description_never_contains_credentials():
    proxy = telegram.parse_telegram_proxy("http://alice:secret@proxy.example:8080")

    rendered = telegram.describe_telegram_proxy(proxy)

    assert "alice" not in rendered
    assert "secret" not in rendered
    assert "proxy.example" in rendered


def test_make_client_does_not_log_proxy_credentials(monkeypatch, caplog):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(telegram, "TelegramClient", FakeClient)
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5://alice:secret@proxy.example:1080")

    with caplog.at_level("INFO", logger="tg-collector"):
        telegram.make_client()

    assert captured["kwargs"]["proxy"]["username"] == "alice"
    assert captured["kwargs"]["proxy"]["password"] == "secret"
    assert "alice" not in caplog.text
    assert "secret" not in caplog.text


def test_reconnects_after_network_failures_and_resets_delay():
    class Client:
        def __init__(self, failures):
            self.failures = failures
            self.disconnected = 0

        async def start(self):
            if self.failures:
                self.failures.pop(0)
                raise ConnectionError("MTProto timed out")

        async def disconnect(self):
            self.disconnected += 1

    failures = [True, True]
    clients = []
    delays = []
    entered = []

    def client_factory():
        client = Client(failures)
        clients.append(client)
        return client

    async def sleep(delay):
        delays.append(delay)

    async def collect(client):
        entered.append(client)

    asyncio.run(
        telegram.run_with_reconnect(
            client_factory,
            initial_delay=5,
            max_delay=300,
            sleep=sleep,
            collect=collect,
        )
    )

    assert delays == [5, 10]
    assert [client.disconnected for client in clients[:2]] == [1, 1]
    assert entered == [clients[2]]


def test_reconnects_when_an_established_collection_connection_drops():
    class Client:
        def __init__(self):
            self.disconnected = 0

        async def start(self):
            return None

        async def disconnect(self):
            self.disconnected += 1

    clients = []
    delays = []
    attempts = 0

    def client_factory():
        client = Client()
        clients.append(client)
        return client

    async def sleep(delay):
        delays.append(delay)

    async def collect(client):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("connection dropped")

    asyncio.run(
        telegram.run_with_reconnect(
            client_factory,
            initial_delay=5,
            max_delay=300,
            sleep=sleep,
            collect=collect,
        )
    )

    assert delays == [5]
    assert clients[0].disconnected == 1


def test_flapping_established_connections_keep_exponential_backoff():
    class Client:
        async def start(self):
            return None

        async def disconnect(self):
            return None

    delays = []
    attempts = 0

    async def sleep(delay):
        delays.append(delay)

    async def collect(client):
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise ConnectionError("connection dropped")

    asyncio.run(
        telegram.run_with_reconnect(
            Client,
            initial_delay=5,
            max_delay=300,
            sleep=sleep,
            collect=collect,
            monotonic=iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)).__next__,
            stable_connection_seconds=60,
        )
    )

    assert delays == [5, 10, 20]


def test_stable_connection_resets_reconnect_backoff():
    class Client:
        async def start(self):
            return None

        async def disconnect(self):
            return None

    delays = []
    attempts = 0

    async def sleep(delay):
        delays.append(delay)

    async def collect(client):
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise ConnectionError("connection dropped")

    asyncio.run(
        telegram.run_with_reconnect(
            Client,
            initial_delay=5,
            max_delay=300,
            sleep=sleep,
            collect=collect,
            monotonic=iter((0.0, 1.0, 2.0, 3.0, 4.0, 65.0, 66.0)).__next__,
            stable_connection_seconds=60,
        )
    )

    assert delays == [5, 10, 5]
