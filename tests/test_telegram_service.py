from types import SimpleNamespace

import pytest
from telethon.tl import types

from telegram_phone_number_checker.models import CheckStatus
from telegram_phone_number_checker.telegram_service import TelegramService


class FakeClient:
    def __init__(self, response=None, error=None, delete_error=None):
        self._response = response
        self._error = error
        self._delete_error = delete_error
        self._delete_called = False

    async def __call__(self, request):
        from telethon.tl.functions.contacts import DeleteContactsRequest

        if isinstance(request, DeleteContactsRequest):
            if self._delete_error:
                raise self._delete_error
            self._delete_called = True
            return SimpleNamespace(users=[], retry_contacts=[])
        if self._error:
            raise self._error
        return self._response


def _svc(client):
    s = TelegramService("1", "hash", "+999")
    s._client = client
    return s


def test_found_with_contact_delete():
    user = types.User(
        id=123,
        is_self=False,
        contact=False,
        mutual_contact=False,
        deleted=False,
        bot=False,
        verified=False,
        restricted=False,
        min=False,
        fake=False,
        username="alice",
        first_name="Alice",
        last_name="A",
        status=types.UserStatusOnline(expires=None),
    )
    resp = SimpleNamespace(users=[user], retry_contacts=[])
    client = FakeClient(response=resp)
    svc = _svc(client)

    async def go():
        return await svc.check_phone("+84911111111", client_id=1)

    res = run(go())
    assert res.status == CheckStatus.FOUND
    assert res.telegram_user_id == 123
    assert res.username == "alice"
    assert res.user_was_online == "Currently online"


def test_users_empty_returned():
    resp = SimpleNamespace(users=[], retry_contacts=[])
    svc = _svc(FakeClient(response=resp))

    async def go():
        return await svc.check_phone("+84911111111", client_id=1)

    res = run(go())
    assert res.status == CheckStatus.NOT_DISCOVERABLE


def test_retry_contacts():
    resp = SimpleNamespace(users=[], retry_contacts=[5])
    svc = _svc(FakeClient(response=resp))

    async def go():
        return await svc.check_phone("+84911111111", client_id=5)

    res = run(go())
    assert res.status == CheckStatus.RETRY_REQUIRED


def test_floodwait():
    from telethon import errors

    svc = _svc(FakeClient(error=errors.FloodWaitError(None, capture=30)))

    async def go():
        return await svc.check_phone("+84911111111", client_id=1)

    res = run(go())
    assert res.status == CheckStatus.RATE_LIMITED
    assert res.retry_after_seconds == 30


def test_timeout():
    svc = _svc(FakeClient(error=TimeoutError()))

    async def go():
        return await svc.check_phone("+84911111111", client_id=1)

    res = run(go())
    assert res.status == CheckStatus.TEMPORARY_ERROR
    assert res.error_type == "NETWORK_TIMEOUT"


def test_delete_contact_failure_keeps_found():
    user = types.User(
        id=123,
        is_self=False,
        contact=False,
        mutual_contact=False,
        deleted=False,
        bot=False,
        verified=False,
        restricted=False,
        min=False,
        fake=False,
        username="alice",
        first_name=None,
        last_name=None,
        status=None,
    )
    resp = SimpleNamespace(users=[user], retry_contacts=[])
    client = FakeClient(response=resp, delete_error=RuntimeError("boom"))
    svc = _svc(client)

    async def go():
        return await svc.check_phone("+84911111111", client_id=1)

    res = run(go())
    assert res.status == CheckStatus.FOUND
    assert res.cleanup_error is not None


def run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_session_path_passed_to_telethon_as_string(monkeypatch, tmp_path):
    import telegram_phone_number_checker.telegram_service as module

    seen = {}

    class Client:
        def __init__(self, session, *_args, **_kwargs):
            seen["session"] = session
        async def connect(self):
            return None
        async def is_user_authorized(self):
            return True
        async def disconnect(self):
            return None

    monkeypatch.setattr(module, "TelegramClient", Client)
    svc = TelegramService("1", "hash", "+999", session_dir=str(tmp_path))
    assert run(svc.is_authorized()) is True
    assert isinstance(seen["session"], str)
