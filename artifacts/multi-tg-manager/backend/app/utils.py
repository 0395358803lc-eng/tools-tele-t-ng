"""Helpers for bulk action delays and FloodWait handling."""

import asyncio
import json
import random
from typing import Awaitable, Callable, Optional

from telethon.errors import FloodWaitError

from .config import settings


async def jitter_delay(min_s: float | None = None, max_s: float | None = None):
    lo = min_s if min_s is not None else settings.RATE_MIN
    hi = max_s if max_s is not None else settings.RATE_MAX
    if hi < lo:
        hi = lo
    await asyncio.sleep(random.uniform(lo, hi))


# Map Telethon exception class names -> short, human-friendly explanations.
# Matched by class name so we don't need to import every error type.
_ERROR_MESSAGES = {
    "ChannelsTooMuchError": "Account is at the Telegram group/channel limit. No chats were left automatically; choose what to leave first.",
    "UserChannelsTooMuchError": "Account is at the Telegram group/channel limit. No chats were left automatically; choose what to leave first.",
    "ChannelPrivateError": "Channel/group is private, or this account was removed/banned from it.",
    "InviteHashExpiredError": "Invite link has expired.",
    "InviteHashInvalidError": "Invite link is invalid.",
    "InviteHashEmptyError": "Invite link is empty/invalid.",
    "UsernameNotOccupiedError": "No such username — nobody is using it.",
    "UsernameInvalidError": "Invalid username (5–32 chars, letters/digits/underscore, must start with a letter).",
    "UsernameOccupiedError": "That username is already taken.",
    "UsernamePurchaseAvailableError": "That username is reserved/for sale — pick another.",
    "ReactionInvalidError": "This chat doesn't allow that reaction.",
    "ReactionEmptyError": "No reaction was sent.",
    "ReactionsTooManyError": "Too many reactions — this chat allows fewer.",
    "ChatWriteForbiddenError": "No permission to do this here.",
    "ChatAdminRequiredError": "Admin rights are required for this action.",
    "ChatGuestSendForbiddenError": "You must join the chat before you can do this.",
    "ChatRestrictedError": "This chat is restricted for this account.",
    "ChatForbiddenError": "This account can't access this chat.",
    "MsgIdInvalidError": "Post not found (bad message id / link).",
    "MessageIdInvalidError": "Post not found (bad message id / link).",
    "PeerIdInvalidError": "Can't access this chat from this account.",
    "UserDeactivatedBanError": "This account is banned/deactivated by Telegram.",
    "UserDeactivatedError": "This account is deactivated.",
    "AuthKeyUnregisteredError": "Session expired — reconnect this account.",
    "UserBannedInChannelError": "This account is banned from sending here.",
    "UserAlreadyParticipantError": "Already a member.",
    "InviteRequestSentError": "Join request sent — waiting for an admin to approve.",
    "UserAlreadyInvitedError": "Join request already sent — waiting for approval.",
    "UsersTooMuchError": "This group/channel is full.",
    "PasswordHashInvalidError": "Wrong current 2FA password.",
    "FreshResetAuthorisationForbiddenError": "Telegram is blocking 2FA changes on a freshly-added session — try again later.",
    "PasswordTooFreshError": "2FA was changed too recently — Telegram requires a wait before changing it again.",
    "SessionTooFreshError": "This session is too new — Telegram requires a wait before changing 2FA.",
    "DocumentInvalidError": "That custom emoji isn't valid for this chat.",
}


def friendly_error(e: Exception) -> str:
    if isinstance(e, FloodWaitError):
        return f"Rate limited — wait {e.seconds}s before trying again."
    name = type(e).__name__
    if name in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[name]
    # Some Telethon errors are dynamically named like 'FloodWaitError' subclasses.
    return f"{name}: {str(e)[:140]}"


# Errors that aren't real failures — they're expected conditions where the
# action simply can't apply (account full, chat disallows the reaction, group
# full). We surface these as a soft "skipped" with a plain reason instead of a
# scary red "failed", so a bulk run still finishes cleanly.
SOFT_SKIP_ERRORS = {
    "ChannelsTooMuchError",  # account is in too many groups/channels (~500 cap)
    "UserChannelsTooMuchError",  # same, alternate name
    "ReactionInvalidError",  # this chat doesn't allow that reaction
    "ReactionEmptyError",  # reaction not accepted
    "ReactionsTooManyError",  # chat allows fewer reactions
    "UsersTooMuchError",  # group/channel is full
    "UserAlreadyParticipantError",  # already a member — nothing to do
    "DocumentInvalidError",  # custom emoji not allowed here
}


def is_soft_error(e: Exception) -> bool:
    return type(e).__name__ in SOFT_SKIP_ERRORS


async def bulk_stream(
    accounts: list[tuple[int, str, str]],
    action: Callable[[object, int], Awaitable[tuple[str, str]]],
    on_success: Optional[Callable[[int], None]] = None,
    concurrency: int | None = None,
):
    """Run `action` over accounts with bounded concurrency, yielding NDJSON lines.

    accounts: list of (account_id, phone, display_name).
    action(client, account_id) -> (status, detail) coroutine. status 'ok' or 'pending'.
        Raise for failures (mapped via friendly_error). Expected/non-fatal errors
        (see SOFT_SKIP_ERRORS) become a soft 'skipped' instead of 'failed'.
        Accounts that aren't connected are skipped automatically.
    on_success(account_id): optional side-effect after a non-failing action.
    concurrency: how many accounts run at once (defaults to settings.CONCURRENCY).

    Emits one `{"type":"progress", ...}` line as each account finishes and a final
    `{"type":"done", ...}` line. Telegram rate limits are per-account, so running
    different accounts in parallel is safe; each account still does a single paced
    action with a jitter delay from the Settings window.
    """
    from .tg_manager import manager  # lazy import to avoid circular import

    total = len(accounts)
    conc = (
        concurrency if concurrency is not None else getattr(settings, "CONCURRENCY", 5)
    )
    try:
        conc = min(50, max(1, int(conc)))
    except (TypeError, ValueError):
        conc = 5

    success = failed = skipped = pending = partial = 0
    results: list[dict] = []
    sem = asyncio.Semaphore(conc)
    out_q: asyncio.Queue = asyncio.Queue()

    async def worker(aid: int, phone: str, name: str):
        async with sem:
            cli = manager.get(aid)
            if not cli:
                row = {
                    "id": aid,
                    "phone": phone,
                    "name": name,
                    "status": "skipped",
                    "detail": "not connected",
                }
            else:
                try:
                    status, detail = await asyncio.wait_for(
                        action(cli, aid),
                        timeout=max(5.0, float(settings.ACTION_TIMEOUT)),
                    )
                    if status not in ("ok", "pending", "skipped", "partial"):
                        status = "ok"
                    if on_success and status in ("ok", "pending", "partial"):
                        on_success(aid)
                    row = {
                        "id": aid,
                        "phone": phone,
                        "name": name,
                        "status": status,
                        "detail": detail,
                    }
                except Exception as e:
                    soft = is_soft_error(e)
                    row = {
                        "id": aid,
                        "phone": phone,
                        "name": name,
                        "status": "skipped" if soft else "failed",
                        "detail": friendly_error(e),
                    }
        # Pace each account AFTER releasing the concurrency slot, so the next
        # account can start immediately instead of waiting out this jitter.
        await jitter_delay()
        await out_q.put(row)

    tasks = [
        asyncio.create_task(worker(aid, phone, name)) for aid, phone, name in accounts
    ]

    try:
        for done_count in range(1, total + 1):
            row = await out_q.get()
            status = row["status"]
            if status == "pending":
                pending += 1
            elif status == "ok":
                success += 1
            elif status == "partial":
                partial += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
            results.append(row)
            yield (
                json.dumps(
                    {
                        "type": "progress",
                        "current": done_count,
                        "total": total,
                        "account_name": row.get("name", ""),
                        "status": status,
                        "detail": row.get("detail", ""),
                        "success": success,
                        "failed": failed,
                        "skipped": skipped,
                        "pending": pending,
                        "partial": partial,
                    }
                )
                + "\n"
            )
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)

    yield (
        json.dumps(
            {
                "type": "done",
                "total": total,
                "success": success,
                "failed": failed,
                "skipped": skipped,
                "pending": pending,
                "partial": partial,
                "results": results,
            }
        )
        + "\n"
    )
